"""Optional HTTP discovery registry, with a durable public snapshot outbox.

The registry never runs negotiations. Tokens and private seller data stay local.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import sqlite3
import stat
from urllib.parse import urlsplit

import httpx

from .storage import PUBLIC_FIELDS, encoded
from .validation import SellerError, normalize_currency, text


DEFAULT_REGISTRY_URL = "https://api.a2a-market.com"
MAX_BODY_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 500
HEARTBEAT_SECONDS = 300
logger = logging.getLogger(__name__)


class RegistryError(SellerError):
    def __init__(self, code, message, *, retry_after=None):
        super().__init__(code, message)
        self.retry_after = retry_after


def validate_url(value, *, origin=False):
    try:
        if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
            raise ValueError
        parsed = urlsplit(value)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.port == 0
                or (origin and parsed.path not in {"", "/"})
                or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError
    except (TypeError, ValueError):
        raise RegistryError("invalid_registry_url", "Use HTTPS without credentials, query or fragment; HTTP is allowed only on loopback. Registry URLs must be origins.") from None
    return value.rstrip("/") if origin else value


def read_token(path):
    """Read a separately provisioned publication token; never include it in errors."""
    try:
        path = Path(path)
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 4096
                or (os.name == "posix" and info.st_mode & 0o077)):
            raise ValueError
        value = path.read_text(encoding="utf-8").strip()
        if not value or any(c.isspace() for c in value) or not value.isascii():
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError):
        raise RegistryError("invalid_registry_token", "Publication token requires a readable owner-only file (chmod 600) with one nonempty ASCII value.") from None


@dataclass(frozen=True)
class PreparedSnapshot:
    registry_url: str
    token_file: str
    config_version: int
    revision: int
    seller_id: str
    item_count: int
    body: bytes


class Registry:
    """Local optional configuration. Construction and unconfigured reads create nothing."""

    def __init__(self, data_dir, *, transport=None):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.path = self.data_dir / "registry.db"
        self.transport = transport

    @contextmanager
    def _transaction(self, *, create=False):
        if create:
            self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            if os.name == "posix":
                self.path.chmod(0o600)
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            c.execute("BEGIN IMMEDIATE")
            if create:
                c.execute("""CREATE TABLE IF NOT EXISTS registry_state(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    registry_url TEXT NOT NULL, agent_card_url TEXT NOT NULL, token_file TEXT NOT NULL,
                    config_version INTEGER NOT NULL DEFAULT 1, sync_revision INTEGER NOT NULL DEFAULT 0,
                    digest TEXT, payload TEXT, ack_revision INTEGER NOT NULL DEFAULT 0,
                    last_synced_at TEXT, expires_at TEXT, last_error TEXT)""")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def _read(self):
        if not self.path.exists():
            return None
        with self._transaction() as c:
            return c.execute("SELECT * FROM registry_state WHERE singleton=1").fetchone()

    @property
    def configured(self):
        return self._read() is not None

    def configure(self, store, *, url=DEFAULT_REGISTRY_URL, agent_card_url, token_file):
        url = validate_url(url, origin=True)
        agent_card_url = validate_url(agent_card_url)
        token_file = str(Path(token_file).expanduser().resolve())
        read_token(token_file)
        with self._transaction(create=True) as c:
            previous = c.execute("SELECT * FROM registry_state WHERE singleton=1").fetchone()
            if previous is None:
                c.execute("INSERT INTO registry_state(singleton,registry_url,agent_card_url,token_file) VALUES(1,?,?,?)",
                          (url, agent_card_url, token_file))
            elif any(previous[k] != v for k, v in (
                ("registry_url", url), ("agent_card_url", agent_card_url), ("token_file", token_file)
            )):
                c.execute("""UPDATE registry_state SET registry_url=?, agent_card_url=?, token_file=?,
                    config_version=config_version+1, digest=NULL, payload=NULL, ack_revision=0,
                    last_synced_at=NULL, expires_at=NULL, last_error=NULL WHERE singleton=1""",
                          (url, agent_card_url, token_file))
        self.prepare(store)
        return self.status()

    def update_agent_card_url(self, agent_card_url):
        if not self.configured:
            return
        agent_card_url = validate_url(agent_card_url)
        with self._transaction() as c:
            c.execute("""UPDATE registry_state SET agent_card_url=?, config_version=config_version+1,
                digest=NULL, payload=NULL, last_error=NULL WHERE singleton=1 AND agent_card_url<>?""",
                      (agent_card_url, agent_card_url))

    def prepare(self, store):
        if not self.configured:
            raise RegistryError("registry_not_configured", "Configure a registry and publication token before synchronizing.")
        # Acquire the outbox lock BEFORE reading seller state: two processes cannot
        # publish an older captured catalogue with a higher revision.
        with self._transaction() as c:
            state = c.execute("SELECT * FROM registry_state WHERE singleton=1").fetchone()
            catalog = store.catalog()
            items = [{key: item[key] for key in PUBLIC_FIELDS} for item in catalog["items"]
                     if item["status"] == "available" and item["simulation"] is False]
            items.sort(key=lambda item: item["item_id"])
            if len(items) > MAX_ITEMS:
                raise RegistryError("registry_catalog_too_large", "The registry accepts at most 500 real available items.")
            public = {"schema_version": "0.1", "seller_id": catalog["seller_id"],
                      "agent_card_url": state["agent_card_url"], "items": items}
            digest = hashlib.sha256(encoded(public).encode("utf-8")).hexdigest()
            revision = state["sync_revision"]
            if state["digest"] != digest:
                revision += 1
                payload = encoded({**public, "sync_revision": revision})
                if len(payload.encode("utf-8")) > MAX_BODY_BYTES:
                    raise RegistryError("registry_catalog_too_large", "The public registry snapshot exceeds 1 MiB.")
                c.execute("UPDATE registry_state SET digest=?,payload=?,sync_revision=?,last_error=NULL WHERE singleton=1",
                          (digest, payload, revision))
            else:
                payload = state["payload"]
            return PreparedSnapshot(state["registry_url"], state["token_file"], state["config_version"],
                                    revision, catalog["seller_id"], len(items), payload.encode("utf-8"))

    def status(self):
        state = self._read()
        if state is None:
            return {"configured": False}
        payload = json.loads(state["payload"]) if state["payload"] else None
        return {"configured": True, "registry_url": state["registry_url"],
                "agent_card_url": state["agent_card_url"],
                "seller_id": payload["seller_id"] if payload else None,
                "sync_revision": state["sync_revision"], "acked_revision": state["ack_revision"],
                "pending": payload is None or state["sync_revision"] > state["ack_revision"] or state["last_error"] is not None,
                "item_count": len(payload["items"]) if payload else None,
                "last_synced_at": state["last_synced_at"], "expires_at": state["expires_at"],
                "last_error": json.loads(state["last_error"]) if state["last_error"] else None}

    def _error(self, prepared, error):
        with self._transaction() as c:
            c.execute("""UPDATE registry_state SET last_error=? WHERE singleton=1
                AND config_version=? AND sync_revision=?""",
                      (encoded({"code": error.code, "message": error.message}),
                       prepared.config_version, prepared.revision))

    def _ack(self, prepared, receipt):
        with self._transaction() as c:
            # An old response must never clear or acknowledge a newer outbox.
            c.execute("""UPDATE registry_state SET ack_revision=MAX(ack_revision,?),
                last_synced_at=?,expires_at=?,last_error=NULL WHERE singleton=1
                AND config_version=? AND sync_revision=?""",
                      (prepared.revision, receipt["last_synced_at"], receipt["expires_at"],
                       prepared.config_version, prepared.revision))

    def sync(self, store):
        try:
            prepared = self.prepare(store)
        except RegistryError as error:
            if self.configured:
                with self._transaction() as c:
                    c.execute("UPDATE registry_state SET last_error=? WHERE singleton=1",
                              (encoded({"code": error.code, "message": error.message}),))
            raise
        try:
            token = read_token(prepared.token_file)
            with httpx.Client(transport=self.transport, timeout=10, follow_redirects=False) as client:
                response = client.put(prepared.registry_url + "/v1/me/catalog", content=prepared.body,
                                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            _check_response(response)
            receipt = _json_response(response)
            try:
                if (receipt["seller_id"] != prepared.seller_id or type(receipt["sync_revision"]) is not int
                        or receipt["sync_revision"] != prepared.revision or type(receipt["item_count"]) is not int
                        or receipt["item_count"] != prepared.item_count):
                    raise ValueError
                synced = datetime.fromisoformat(receipt["last_synced_at"].replace("Z", "+00:00"))
                expires = datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00"))
                if synced.tzinfo is None or expires.tzinfo is None or expires <= synced:
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError):
                raise RegistryError("registry_invalid_response", "Registry returned an invalid publication receipt.") from None
        except httpx.HTTPError:
            error = RegistryError("registry_unavailable", "Registry request failed; the public snapshot remains pending for retry.")
            self._error(prepared, error)
            raise error from None
        except RegistryError as error:
            self._error(prepared, error)
            raise
        self._ack(prepared, receipt)
        return self.status()

    def search(self, *, url=None, query=None, area=None, currency=None,
               max_asking_price_minor=None, limit=20, cursor=None):
        if url is None:
            state = self._read()
            url = state["registry_url"] if state else DEFAULT_REGISTRY_URL
        url = validate_url(url, origin=True)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise RegistryError("invalid_search", "Search limit must be between 1 and 100.")
        params = {"limit": limit}
        for key, value in (("q", query), ("area", area), ("cursor", cursor)):
            if value is not None:
                params[key] = text(value, key, limit=4000 if key == "cursor" else 256, required=True)
        if currency is not None:
            params["currency"] = normalize_currency(currency)
        if max_asking_price_minor is not None:
            if (currency is None or type(max_asking_price_minor) is not int
                    or not 0 <= max_asking_price_minor <= 10**12):
                raise RegistryError("invalid_search", "Maximum price needs an explicit currency and a nonnegative integer in minor units.")
            params["max_asking_price_minor"] = max_asking_price_minor
        try:
            with httpx.Client(transport=self.transport, timeout=10, follow_redirects=False) as client:
                response = client.get(url + "/v1/items", params=params)
            _check_response(response)
            result = _json_response(response)
        except httpx.HTTPError:
            raise RegistryError("registry_unavailable", "Registry search is temporarily unavailable.") from None
        if (result.get("schema_version") != "0.1" or not isinstance(result.get("items"), list)
                or "next_cursor" not in result
                or (result["next_cursor"] is not None and not isinstance(result["next_cursor"], str))):
            raise RegistryError("registry_invalid_response", "Registry returned an invalid search response.")
        return result


def _json_response(response):
    try:
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ValueError
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError):
        raise RegistryError("registry_invalid_response", "Registry returned an invalid JSON response.") from None


def _check_response(response):
    if response.status_code == 200:
        return
    # Never echo an untrusted error body: it may reflect the Authorization header.
    code, message = {
        401: ("registry_unauthorized", "Publication token was rejected; check its provisioning."),
        403: ("registry_forbidden", "Publication token is not authorized for this seller."),
        409: ("registry_conflict", "Registry revision conflicts with local state; check for another publisher or a restored database."),
        413: ("registry_catalog_too_large", "The registry rejected the snapshot size."),
        429: ("registry_rate_limited", "Registry rate limit reached; retry later."),
    }.get(response.status_code, ("registry_unavailable", "Registry request failed; retry later.")
          if response.status_code >= 500 else ("registry_rejected", "Registry rejected the request; check its configuration and schema."))
    retry_after = None
    try:
        retry_after = max(1, min(HEARTBEAT_SECONDS, int(response.headers.get("Retry-After", ""))))
    except ValueError:
        pass
    raise RegistryError(code, message, retry_after=retry_after)


async def refresh_loop(registry, store, stop, *, interval=HEARTBEAT_SECONDS):
    """A process worker, not an LLM task. SQLite survives restarts and CLI retries."""
    failures = 0
    while not stop.is_set():
        try:
            await asyncio.to_thread(registry.sync, store)
            failures = 0
            delay = interval
        except RegistryError as error:
            failures += 1
            logger.warning("Registry synchronization: %s", error.code)
            if error.code in {"registry_unauthorized", "registry_forbidden", "registry_conflict",
                              "registry_rejected", "invalid_registry_token", "registry_catalog_too_large"}:
                delay = interval
            else:
                delay = min(interval, 5 * 2 ** min(failures - 1, 6))
                delay *= random.uniform(0.8, 1.0)
            if error.retry_after is not None:
                delay = max(delay, error.retry_after)
        except (OSError, sqlite3.Error):
            logger.warning("Registry synchronization: local state unavailable")
            delay = interval
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


def attach_registry_lifespan(app, store, public_url):
    """Add optional renewal to a Starlette app while preserving its own lifecycle."""
    registry = Registry(store.data_dir)
    if not registry.configured:
        return
    registry.update_agent_card_url(public_url.rstrip("/") + "/.well-known/agent-card.json")
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application) as state:
            stop = asyncio.Event()
            worker = asyncio.create_task(refresh_loop(registry, store, stop))
            try:
                yield state
            finally:
                stop.set()
                # Let the bounded request finish; cancelling to_thread would leave
                # its network operation running after the app has shut down.
                await worker

    app.router.lifespan_context = lifespan
