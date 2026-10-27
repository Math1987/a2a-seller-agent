"""Private, transactional multi-item state. No model or natural-language parsing.

Public responses are explicit projections. Administrative mutations are local-only.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from .validation import SellerError, normalize_currency, positive_int, text


PUBLIC_FIELDS = ("item_id", "title", "description", "condition", "handover", "area",
                 "price_minor", "currency", "status", "simulation", "revision",
                 "created_at", "updated_at")
ITEM_CHANGES = {"title", "description", "condition", "handover", "area", "price_minor",
                "minimum_price_minor", "currency", "simulation"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class SellerStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.data_dir / "seller.db"
        # Restrict permissions before SQLite writes any private content.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        if os.name == "posix":
            self.path.chmod(0o600)
        with closing(self._connect()) as c, c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS items(item_id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proposals(
                    proposal_id TEXT NOT NULL, version INTEGER NOT NULL,
                    context_id TEXT NOT NULL, item_id TEXT NOT NULL, status TEXT NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(proposal_id,version));
                CREATE INDEX IF NOT EXISTS proposal_context ON proposals(context_id,item_id);
                CREATE TABLE IF NOT EXISTS requests(
                    scope TEXT NOT NULL, request_id TEXT NOT NULL, input TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(scope,request_id));
                CREATE TABLE IF NOT EXISTS messages(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, context_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    request TEXT NOT NULL, response TEXT NOT NULL);
            """)
            c.execute("INSERT OR IGNORE INTO metadata VALUES('schema_version','1')")
            version = c.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
            if version != "1":
                raise SellerError("schema_version", "Unsupported data schema. Preserve the data directory and use a compatible release.")
            c.execute("INSERT OR IGNORE INTO metadata VALUES('seller_id',?)", ("seller_" + uuid4().hex,))

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=10000")
        return c

    @contextmanager
    def _transaction(self):
        c = self._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def _public(item):
        return {k: item[k] for k in PUBLIC_FIELDS}

    @staticmethod
    def _item(c, item_id):
        text(item_id, "item_id", limit=128, required=True)
        row = c.execute("SELECT data FROM items WHERE item_id=?", (item_id,)).fetchone()
        if not row:
            raise SellerError("not_found", "Unknown item.")
        return json.loads(row[0])

    @staticmethod
    def _save_item(c, item):
        c.execute("INSERT OR REPLACE INTO items VALUES(?,?)", (item["item_id"], encoded(item)))

    @staticmethod
    def _validate_item(item):
        item["title"] = text(item["title"], "title", limit=200, required=True)
        for field in ("description", "condition", "handover", "area"):
            item[field] = text(item[field], field)
        item["currency"] = normalize_currency(item["currency"])
        positive_int(item["price_minor"], "price_minor")
        positive_int(item["minimum_price_minor"], "minimum_price_minor")
        if item["minimum_price_minor"] > item["price_minor"]:
            raise SellerError("invalid_price", "Minimum price must not exceed asking price.")
        if not isinstance(item["simulation"], bool):
            raise SellerError("invalid_value", "simulation must be boolean.")

    @staticmethod
    def _cached(c, scope, request_id, request):
        if request_id is None:
            return None
        text(request_id, "request_id", limit=256, required=True)
        row = c.execute("SELECT input,result FROM requests WHERE scope=? AND request_id=?", (scope, request_id)).fetchone()
        if row:
            if row["input"] != encoded(request):
                raise SellerError("idempotency_conflict", "This request ID was already used with different parameters.")
            return json.loads(row["result"])
        return None

    @staticmethod
    def _cache(c, scope, request_id, request, result):
        if request_id is not None:
            c.execute("INSERT INTO requests VALUES(?,?,?,?)", (scope, request_id, encoded(request), encoded(result)))

    def add_item(self, *, title, price_minor, minimum_price_minor, currency,
                 description="", condition="", handover="", area="", simulation=True, request_id=None):
        request = dict(title=title, price_minor=price_minor, minimum_price_minor=minimum_price_minor,
                       currency=currency, description=description, condition=condition,
                       handover=handover, area=area, simulation=simulation)
        self._validate_item(request)
        with self._transaction() as c:
            cached = self._cached(c, "local", request_id, {"operation": "add", **request})
            if cached is not None:
                return cached
            ts = now()
            item = dict(request, item_id="item_" + uuid4().hex, status="available", revision=1,
                        created_at=ts, updated_at=ts)
            self._save_item(c, item)
            self._cache(c, "local", request_id, {"operation": "add", **request}, item)
            return item

    def update_item(self, item_id, *, request_id=None, **changes):
        if not changes or changes.keys() - ITEM_CHANGES:
            raise SellerError("invalid_update", "Supply supported item fields to update.")
        request = {"operation": "update", "item_id": item_id, "changes": changes}
        with self._transaction() as c:
            cached = self._cached(c, "local", request_id, request)
            if cached is not None:
                return cached
            item = self._item(c, item_id)
            if ("currency" in changes and normalize_currency(changes["currency"]) != item["currency"]
                    and not {"price_minor", "minimum_price_minor"}.issubset(changes)):
                raise SellerError("invalid_update", "Changing currency requires both new prices; existing amounts are not converted.")
            item.update(changes)
            self._validate_item(item)
            item["revision"] += 1
            item["updated_at"] = now()
            self._invalidate(c, item_id, "superseded")
            self._save_item(c, item)
            self._cache(c, "local", request_id, request, item)
            return item

    def withdraw_item(self, item_id, *, request_id=None):
        request = {"operation": "withdraw", "item_id": item_id}
        with self._transaction() as c:
            cached = self._cached(c, "local", request_id, request)
            if cached is not None:
                return cached
            item = self._item(c, item_id)
            if item["status"] != "withdrawn":
                item.update(status="withdrawn", revision=item["revision"] + 1, updated_at=now())
                self._save_item(c, item)
                self._invalidate(c, item_id, "withdrawn")
            self._cache(c, "local", request_id, request, item)
            return item

    def list_items(self, *, include_withdrawn=False, private=True):
        with self._transaction() as c:
            items = [json.loads(r[0]) for r in c.execute("SELECT data FROM items ORDER BY item_id")]
            return [i if private else self._public(i) for i in items
                    if include_withdrawn or i["status"] == "available"]

    def get_item(self, item_id, *, private=False):
        with self._transaction() as c:
            item = self._item(c, item_id)
            return item if private else self._public(item)

    def _catalog(self, c):
        items = [json.loads(r[0]) for r in c.execute("SELECT data FROM items ORDER BY item_id")]
        return {"schema_version": "0.1", "seller_id": c.execute(
            "SELECT value FROM metadata WHERE key='seller_id'").fetchone()[0],
            "items": [self._public(i) for i in items if i["status"] == "available"]}

    def catalog(self):
        with self._transaction() as c:
            return self._catalog(c)

    @staticmethod
    def _save_proposal(c, p):
        c.execute("INSERT OR REPLACE INTO proposals VALUES(?,?,?,?,?,?)",
                  (p["proposal_id"], p["version"], p["context_id"], p["item_id"], p["status"], encoded(p)))

    @classmethod
    def _invalidate(cls, c, item_id, status):
        rows = c.execute("SELECT data FROM proposals WHERE item_id=? AND status IN ('pending_seller','countered')", (item_id,)).fetchall()
        for row in rows:
            p = json.loads(row[0])
            p.update(status=status, updated_at=now())
            cls._save_proposal(c, p)

    @staticmethod
    def _latest(c, proposal_id):
        row = c.execute("SELECT data FROM proposals WHERE proposal_id=? ORDER BY version DESC LIMIT 1", (proposal_id,)).fetchone()
        if not row:
            raise SellerError("not_found", "Unknown proposal.")
        return json.loads(row[0])

    @staticmethod
    def _public_proposal(p):
        return {k: v for k, v in p.items() if k not in {"notified_at"}}

    def handle_message(self, payload, *, context_id, message_id):
        text(context_id, "context_id", limit=256, required=True)
        text(message_id, "message_id", limit=256, required=True)
        if not isinstance(payload, dict):
            raise SellerError("invalid_request", "Expected one structured operation object.")
        with self._transaction() as c:
            cached = self._cached(c, "a2a:" + context_id, message_id, payload)
            if cached is not None:
                return cached
            result = self._dispatch(c, payload, context_id)
            self._cache(c, "a2a:" + context_id, message_id, payload, result)
            c.execute("INSERT INTO messages(context_id,message_id,created_at,request,response) VALUES(?,?,?,?,?)",
                      (context_id, message_id, now(), encoded(payload), encoded(result)))
            return result

    def _dispatch(self, c, payload, context_id):
        action = payload.get("action")
        allowed = {
            "list-items": {"action"},
            "get-item": {"action", "item_id"},
            "offer": {"action", "item_id", "price_minor", "currency", "accepted_terms_revision"},
            "proposal-status": {"action", "proposal_id"},
        }
        if not isinstance(action, str) or action not in allowed:
            raise SellerError("invalid_action", "Supported actions: list-items, get-item, offer, proposal-status.")
        if payload.keys() - allowed[action]:
            raise SellerError("invalid_request", "Unexpected operation fields; clarify the request before submitting it.")
        if action == "list-items":
            return self._catalog(c)
        if action == "proposal-status":
            pid = text(payload.get("proposal_id"), "proposal_id", limit=128, required=True)
            p = self._latest(c, pid)
            if p["context_id"] != context_id:
                raise SellerError("not_found", "Unknown proposal in this context.")
            return self._public_proposal(p)
        item = self._item(c, payload.get("item_id"))
        if action == "get-item":
            return self._public(item)
        if item["status"] != "available":
            raise SellerError("unavailable", "This item is no longer available.")
        price = positive_int(payload.get("price_minor"), "price_minor")
        if normalize_currency(payload.get("currency")) != item["currency"]:
            raise SellerError("currency_mismatch", "The offer currency must match the item currency.")
        revision = payload.get("accepted_terms_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision != item["revision"]:
            return {"status": "terms_required", "item": self._public(item)}
        if price < item["minimum_price_minor"]:
            return {"status": "rejected_offer", "item_id": item["item_id"],
                    "counter_price_minor": item["price_minor"], "currency": item["currency"],
                    "message": "This offer is not acceptable. You may submit a new offer."}
        row = c.execute("SELECT data FROM proposals WHERE context_id=? AND item_id=? ORDER BY version DESC LIMIT 1",
                        (context_id, item["item_id"])).fetchone()
        old = json.loads(row[0]) if row else None
        if old and old["status"] in ("accepted", "accepted_fictive"):
            raise SellerError("already_agreed", "This negotiation already has a confirmed proposal; use its status.")
        if old and old["status"] in ("pending_seller", "countered"):
            old.update(status="superseded", updated_at=now())
            self._save_proposal(c, old)
        ts = now()
        p = {"proposal_id": old["proposal_id"] if old else "proposal_" + uuid4().hex,
             "version": old["version"] + 1 if old else 1,
             "context_id": context_id, "item_id": item["item_id"], "item_revision": item["revision"],
             "price_minor": min(price, item["price_minor"]), "currency": item["currency"],
             "terms": self._public(item), "status": "pending_seller", "created_at": ts,
             "updated_at": ts, "notified_at": None}
        self._save_proposal(c, p)
        return self._public_proposal(p)

    def list_proposals(self, *, pending_only=False):
        with self._transaction() as c:
            query = "SELECT data FROM proposals"
            if pending_only:
                query += " WHERE status='pending_seller'"
            return [json.loads(r[0]) for r in c.execute(query + " ORDER BY rowid")]

    def pending_notifications(self):
        return [p for p in self.list_proposals(pending_only=True) if p["notified_at"] is None]

    def ack_notification(self, proposal_id, *, version):
        positive_int(version, "version")
        with self._transaction() as c:
            p = self._latest(c, proposal_id)
            if p["version"] != version or p["status"] != "pending_seller":
                raise SellerError("stale_proposal", "Proposal changed; read the current pending proposals.")
            if p["notified_at"] is None:
                p["notified_at"] = now()
                self._save_proposal(c, p)
            return p

    def decide(self, proposal_id, *, version, decision, price_minor=None):
        positive_int(version, "version")
        if decision not in {"confirm", "reject", "counter"}:
            raise SellerError("invalid_decision", "Choose confirm, reject or counter.")
        with self._transaction() as c:
            p = self._latest(c, proposal_id)
            if p["version"] != version or p["status"] != "pending_seller":
                raise SellerError("stale_proposal", "Only the latest pending proposal may be decided.")
            item = self._item(c, p["item_id"])
            if item["status"] != "available" or p["item_revision"] != item["revision"]:
                raise SellerError("stale_terms", "The item or its terms changed; a new offer is required.")
            if decision == "counter":
                positive_int(price_minor, "price_minor")
                if not item["minimum_price_minor"] <= price_minor <= item["price_minor"]:
                    raise SellerError("invalid_price", "Counter price must respect the current private mandate and asking price.")
                p.update(status="superseded", updated_at=now())
                self._save_proposal(c, p)
                p = dict(p, version=version + 1, status="countered", price_minor=price_minor,
                         created_at=now(), updated_at=now(), notified_at=None)
            else:
                p.update(status=("accepted_fictive" if item["simulation"] else "accepted")
                         if decision == "confirm" else "rejected", updated_at=now())
            self._save_proposal(c, p)
            return p
