"""Registry publication uses only a mock HTTP transport and temporary seller state."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
import os
from threading import Event

import httpx
import pytest
from starlette.applications import Starlette

from a2a_seller.registry import Registry, RegistryError, attach_registry_lifespan, refresh_loop
from a2a_seller.storage import PUBLIC_FIELDS, SellerStore


def item(store, *, simulation=False, **values):
    return store.add_item(**{"title": "VTT", "price_minor": 20000, "minimum_price_minor": 15000,
                             "currency": "EUR", "simulation": simulation, **values})


def receipt(body):
    return {"seller_id": body["seller_id"], "sync_revision": body["sync_revision"],
            "item_count": len(body["items"]), "last_synced_at": "2026-10-27T12:00:00+00:00",
            "expires_at": "2026-10-27T12:15:00+00:00"}


class MockRegistry:
    def __init__(self):
        self.requests = []
        self.latest = None

    def __call__(self, request):
        self.requests.append(request)
        assert request.method == "PUT"
        assert str(request.url) == "https://registry.example/v1/me/catalog"
        assert request.headers["Authorization"] == "Bearer publication-secret"
        body = json.loads(request.content)
        if self.latest and (body["sync_revision"] < self.latest["sync_revision"]
                            or (body["sync_revision"] == self.latest["sync_revision"] and body != self.latest)):
            return httpx.Response(409, json={"error": {"code": "conflict", "message": "outdated"}})
        self.latest = body
        return httpx.Response(200, json=receipt(body))


@pytest.fixture
def setup(tmp_path):
    store = SellerStore(tmp_path / "state")
    token_file = tmp_path / "publication-token"
    token_file.write_text("publication-secret\n")
    token_file.chmod(0o600)
    remote = MockRegistry()
    registry = Registry(store.data_dir, transport=httpx.MockTransport(remote))
    registry.configure(store, url="https://registry.example", agent_card_url="https://seller.example/.well-known/agent-card.json",
                       token_file=token_file)
    return store, registry, remote, token_file


def test_public_projection_excludes_simulations_and_all_private_fields(setup):
    store, registry, remote, _ = setup
    real = item(store)
    item(store, simulation=True, title="Simulation")
    gone = item(store, title="Retiré")
    store.withdraw_item(gone["item_id"])
    status = registry.sync(store)
    assert not status["pending"]
    body = remote.latest
    assert set(body) == {"schema_version", "seller_id", "sync_revision", "agent_card_url", "items"}
    assert len(body["items"]) == 1
    assert set(body["items"][0]) == set(PUBLIC_FIELDS)
    assert body["items"][0]["item_id"] == real["item_id"]
    assert "minimum_price_minor" not in remote.requests[-1].content.decode()
    assert "publication-secret" not in registry.path.read_bytes().decode(errors="ignore")
    if os.name == "posix":
        assert registry.path.stat().st_mode & 0o077 == 0


def test_changed_real_to_simulation_and_last_withdrawal_send_empty_snapshot(setup):
    store, registry, remote, _ = setup
    real = item(store)
    registry.sync(store)
    old_revision = remote.latest["sync_revision"]
    store.update_item(real["item_id"], simulation=True)
    registry.sync(store)
    assert remote.latest["items"] == []
    assert remote.latest["sync_revision"] > old_revision
    store.update_item(real["item_id"], simulation=False)
    registry.sync(store)
    store.withdraw_item(real["item_id"])
    registry.sync(store)
    assert remote.latest["items"] == []


def test_real_sale_confirmation_removes_item_from_snapshot(setup):
    store, registry, remote, _ = setup
    real = item(store)
    registry.sync(store)
    proposal = store.handle_message({"action": "offer", "item_id": real["item_id"],
        "price_minor": 18000, "currency": "EUR", "accepted_terms_revision": real["revision"]},
        context_id="buyer", message_id="offer")
    store.decide(proposal["proposal_id"], version=proposal["version"], decision="confirm")
    registry.sync(store)
    assert remote.latest["items"] == []


def test_prepare_reuses_identical_payload_after_restart_and_heartbeat(setup):
    store, registry, remote, _ = setup
    item(store)
    first = registry.prepare(store)
    restarted = Registry(store.data_dir, transport=httpx.MockTransport(remote))
    assert restarted.prepare(store) == first
    restarted.sync(store)
    restarted.sync(store)
    assert remote.requests[0].content == remote.requests[1].content == first.body
    assert restarted.status()["acked_revision"] == first.revision


def test_lost_response_retries_same_revision_and_payload(setup):
    store, registry, remote, _ = setup
    item(store)
    def accept_then_disconnect(request):
        remote(request)
        raise httpx.ReadTimeout("secret server details", request=request)
    registry.transport = httpx.MockTransport(accept_then_disconnect)
    with pytest.raises(RegistryError, match="remains pending") as failure:
        registry.sync(store)
    assert "secret server details" not in str(failure.value)
    assert registry.status()["pending"]
    registry.transport = httpx.MockTransport(remote)
    assert not registry.sync(store)["pending"]
    assert remote.requests[0].content == remote.requests[1].content


def test_old_ack_does_not_clear_newer_pending_snapshot(setup):
    store, registry, remote, _ = setup
    real = item(store)
    def mutate_during_request(request):
        response = remote(request)
        store.update_item(real["item_id"], price_minor=22000)
        registry.prepare(store)
        return response
    registry.transport = httpx.MockTransport(mutate_during_request)
    status = registry.sync(store)
    assert status["pending"]
    assert status["sync_revision"] > remote.latest["sync_revision"]
    assert status["acked_revision"] < status["sync_revision"]
    registry.transport = httpx.MockTransport(remote)
    assert not registry.sync(store)["pending"]
    assert remote.latest["items"][0]["price_minor"] == 22000


def test_prepare_captures_catalogue_inside_shared_outbox_lock(setup, monkeypatch):
    store, registry, _, _ = setup
    real = item(store, title="Initial")
    registry.prepare(store)
    store.update_item(real["item_id"], title="First")
    captured, release = Event(), Event()
    original = store.catalog
    calls = []
    def capture():
        result = original()
        calls.append(result)
        if len(calls) == 1:
            captured.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(store, "catalog", capture)
    with ThreadPoolExecutor(max_workers=2) as workers:
        older = workers.submit(registry.prepare, store)
        assert captured.wait(5)
        store.update_item(real["item_id"], title="Second")
        newer = workers.submit(Registry(store.data_dir).prepare, store)
        release.set()
        old_snapshot, new_snapshot = older.result(5), newer.result(5)
    assert old_snapshot.revision < new_snapshot.revision
    assert json.loads(old_snapshot.body)["items"][0]["title"] == "First"
    assert json.loads(new_snapshot.body)["items"][0]["title"] == "Second"


def test_url_change_invalidates_old_ack_and_increments_revision(setup):
    store, registry, remote, _ = setup
    item(store)
    registry.sync(store)
    first = registry.prepare(store)
    registry.update_agent_card_url("https://new-tunnel.example/.well-known/agent-card.json")
    registry._ack(first, receipt(json.loads(first.body)))
    assert registry.status()["pending"]
    updated = registry.prepare(store)
    assert updated.revision > first.revision
    registry.sync(store)
    assert remote.latest["agent_card_url"].startswith("https://new-tunnel.example/")


@pytest.mark.parametrize("status,code", [(401, "registry_unauthorized"), (403, "registry_forbidden"),
    (409, "registry_conflict"), (429, "registry_rate_limited"), (503, "registry_unavailable")])
def test_http_errors_are_sanitized_and_keep_snapshot_pending(setup, status, code):
    store, registry, _, _ = setup
    registry.transport = httpx.MockTransport(lambda request: httpx.Response(status,
        headers={"Retry-After": "17"}, json={"error": {"message": request.headers["Authorization"]}}))
    with pytest.raises(RegistryError) as failure:
        registry.sync(store)
    assert failure.value.code == code
    assert failure.value.retry_after == 17
    state = registry.status()
    assert state["pending"]
    assert "publication-secret" not in json.dumps(state)
    assert state["last_error"]["code"] == code


def test_redirect_does_not_send_bearer_to_another_origin(setup):
    store, registry, _, _ = setup
    requests = []
    def redirect(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://other.example/steal"})
    registry.transport = httpx.MockTransport(redirect)
    with pytest.raises(RegistryError):
        registry.sync(store)
    assert len(requests) == 1
    assert requests[0].url.host == "registry.example"


@pytest.mark.parametrize("response", [[], {}, {"seller_id": "different"}, "bad-json"])
def test_malformed_receipt_does_not_ack_snapshot(setup, response):
    store, registry, _, _ = setup
    registry.transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    with pytest.raises(RegistryError):
        registry.sync(store)
    assert registry.status()["pending"]


def test_item_limit_and_byte_limit_block_publication(setup, monkeypatch):
    store, registry, remote, _ = setup
    real = item(store)
    base = store.catalog()
    monkeypatch.setattr(store, "catalog", lambda: {**base, "items": [base["items"][0]] * 501})
    with pytest.raises(RegistryError, match="500"):
        registry.sync(store)
    assert registry.status()["pending"]
    monkeypatch.setattr(store, "catalog", lambda: {**base, "items": [{**real, "description": "a" * (1024 * 1024)}]})
    with pytest.raises(RegistryError, match="1 MiB"):
        registry.sync(store)
    assert remote.requests == []


def test_search_is_public_validates_currency_and_never_creates_local_state(tmp_path):
    state = tmp_path / "no-state"
    requests = []
    def search(request):
        requests.append(request)
        assert request.method == "GET"
        assert "authorization" not in request.headers
        assert request.url.params["q"] == "vélo"
        assert request.url.params["currency"] == "EUR"
        assert request.url.params["max_asking_price_minor"] == "20000"
        return httpx.Response(200, json={"schema_version": "0.1", "items": [], "next_cursor": None})
    registry = Registry(state, transport=httpx.MockTransport(search))
    assert registry.status() == {"configured": False}
    assert registry.search(query="vélo", currency="eur", max_asking_price_minor=20000)["items"] == []
    assert requests[0].url.host == "aithos.app"
    with pytest.raises(RegistryError):
        registry.search(max_asking_price_minor=20000)
    assert not state.exists()


@pytest.mark.parametrize("url", ["http://registry.example", "https://user:password@registry.example",
    "https://registry.example/path", "https://registry.example?key=secret", "https://registry.example/#x"])
def test_registry_url_rejects_insecure_and_credential_urls(setup, url):
    store, registry, _, token = setup
    with pytest.raises(RegistryError):
        registry.configure(store, url=url, agent_card_url="https://seller.example/card", token_file=token)


def test_loopback_dev_url_and_token_permissions(setup):
    store, registry, _, token = setup
    status = registry.configure(store, url="http://127.0.0.1:8080", agent_card_url="http://localhost:9999/card", token_file=token)
    assert status["registry_url"] == "http://127.0.0.1:8080"
    if os.name == "posix":
        token.chmod(0o644)
        with pytest.raises(RegistryError, match="owner-only"):
            registry.sync(store)


def test_worker_retries_without_an_llm_and_stops_cleanly():
    async def scenario():
        stop = asyncio.Event()
        calls = []
        class FakeRegistry:
            def sync(self, _store):
                calls.append(True)
                if len(calls) == 1:
                    raise RegistryError("registry_unavailable", "Unavailable")
                loop.call_soon_threadsafe(stop.set)
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(refresh_loop(FakeRegistry(), object(), stop, interval=0.001), timeout=2)
        assert len(calls) == 2
    asyncio.run(scenario())


def test_lifespan_preserves_app_context_refreshes_url_and_joins_worker(setup, monkeypatch):
    store, registry, remote, _ = setup
    monkeypatch.setattr("a2a_seller.registry.Registry", lambda _data_dir: registry)
    events = []
    @asynccontextmanager
    async def previous(_app):
        events.append("start")
        yield {"original": True}
        events.append("stop")
    app = Starlette(lifespan=previous)
    attach_registry_lifespan(app, store, "https://restarted.example")
    async def scenario():
        async with app.router.lifespan_context(app) as state:
            assert state == {"original": True}
            for _ in range(100):
                if remote.requests:
                    break
                await asyncio.sleep(0.005)
            assert remote.requests
        assert events == ["start", "stop"]
    asyncio.run(scenario())
    assert remote.latest["agent_card_url"] == "https://restarted.example/.well-known/agent-card.json"


def test_unconfigured_lifespan_does_not_create_registry_state(tmp_path):
    store = SellerStore(tmp_path / "state")
    app = Starlette()
    previous = app.router.lifespan_context
    attach_registry_lifespan(app, store, "https://seller.example")
    assert app.router.lifespan_context is previous
    assert not (store.data_dir / "registry.db").exists()
