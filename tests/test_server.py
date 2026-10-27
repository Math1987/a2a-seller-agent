"""Transport-level checks through the actual A2A JSON-RPC routes."""

import json
import uuid

import pytest
from a2a.helpers.proto_helpers import new_data_part
from a2a.types import Message, Part, Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict
from starlette.testclient import TestClient

from a2a_seller.server import MAX_REQUEST_BYTES, create_app
from a2a_seller.storage import SellerStore


@pytest.fixture
def store(tmp_path):
    return SellerStore(tmp_path / "seller")


@pytest.fixture
def item(store):
    return store.add_item(
        title="Table de nuit", price_minor=30000, minimum_price_minor=23000,
        currency="EUR", description="Table de test.", handover="Lyon, samedi.",
    )


@pytest.fixture
def client(store):
    with TestClient(create_app(store, "https://seller.example")) as value:
        yield value


def rpc(client, payload=None, *, text=None, context_id=None, message_id=None, token=None, parts=None):
    if parts is None:
        parts = [Part(text=text)] if text is not None else [new_data_part(payload)]
    message = Message(
        message_id=message_id or str(uuid.uuid4()), role=Role.ROLE_USER, parts=parts,
    )
    if context_id:
        message.context_id = context_id
    headers = {"A2A-Version": "1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    response = client.post(
        "/", headers=headers,
        json={
            "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "SendMessage",
            "params": MessageToDict(SendMessageRequest(message=message)),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    message = body["result"]["message"]
    return message["parts"][0]["data"], message["contextId"]


def offer(item, price_minor=25000):
    return {
        "action": "offer", "item_id": item["item_id"], "price_minor": price_minor,
        "currency": item["currency"], "accepted_terms_revision": item["revision"],
    }


def test_one_card_live_catalogue_multiple_items(client, store, item):
    before = client.get("/.well-known/agent-card.json").json()
    lamp = store.add_item(title="Lampe", price_minor=4000, minimum_price_minor=3000, currency="EUR")
    after = client.get("/.well-known/agent-card.json").json()
    assert before == after
    assert {s["id"] for s in before["skills"]} == {"list-items", "get-item", "negotiate-item"}
    assert before["supportedInterfaces"][0]["url"] == "https://seller.example/"
    catalog = client.get("/catalog.json").json()
    assert {x["item_id"] for x in catalog["items"]} == {item["item_id"], lamp["item_id"]}
    assert {x["price_minor"] for x in catalog["items"]} == {30000, 4000}
    assert "minimum_price_minor" not in json.dumps(catalog)
    a2a_catalog, _ = rpc(client, {"action": "list-items"})
    assert a2a_catalog == catalog
    assert client.get("/.well-known/ai-catalog.json").json()["entries"][0]["url"].endswith("/.well-known/agent-card.json")


def test_offer_approval_round_trip_at_floor(client, store, item):
    public_item, context_id = rpc(client, {"action": "get-item", "item_id": item["item_id"]})
    assert public_item["revision"] == item["revision"]
    proposal, same_context = rpc(client, offer(item, 23000), context_id=context_id)
    assert same_context == context_id
    assert proposal["price_minor"] == 23000
    assert proposal["status"] == "pending_seller"
    assert "minimum_price_minor" not in json.dumps(proposal)
    # JSON values in protobuf data Parts use number (double); the local CLI
    # supplies an integer version when recording the owner's decision.
    store.decide(proposal["proposal_id"], version=int(proposal["version"]), decision="confirm")
    result, _ = rpc(client, {"action": "proposal-status", "proposal_id": proposal["proposal_id"]}, context_id=context_id)
    assert result["status"] == "accepted_fictive"
    assert result["price_minor"] == 23000


def test_missing_or_stale_terms_never_submit(client, store, item):
    payload = offer(item)
    payload.pop("accepted_terms_revision")
    result, context = rpc(client, payload)
    assert result["status"] == "terms_required"
    payload["accepted_terms_revision"] = 42
    result, _ = rpc(client, payload, context_id=context)
    assert result["status"] == "terms_required"
    assert store.list_proposals() == []


def test_retry_same_message_is_idempotent(client, store, item):
    _, context = rpc(client, {"action": "list-items"})
    first, _ = rpc(client, offer(item), context_id=context, message_id="retry-once")
    repeat, _ = rpc(client, offer(item), context_id=context, message_id="retry-once")
    assert repeat == first
    assert len(store.list_proposals()) == 1
    changed, _ = rpc(client, offer(item, 26000), context_id=context, message_id="retry-once")
    assert "error" in changed
    assert len(store.list_proposals()) == 1


def test_first_offer_retry_without_context_survives_server_restart(client, store, item):
    first, original_context = rpc(client, offer(item), message_id="first-offer-response-lost")
    repeat, repeated_context = rpc(client, offer(item), message_id="first-offer-response-lost")
    assert repeated_context == original_context
    assert repeat == first
    with TestClient(create_app(SellerStore(store.data_dir), "https://new-tunnel.example")) as restarted:
        again, context_after_restart = rpc(restarted, offer(item), message_id="first-offer-response-lost")
        assert context_after_restart == original_context
        assert again == first
        conflict, _ = rpc(restarted, offer(item, 26000), message_id="first-offer-response-lost")
        assert conflict["error"]["code"] == "idempotency_conflict"
    assert len(store.list_proposals()) == 1


def test_distinct_first_messages_get_distinct_contexts(client):
    _, first = rpc(client, {"action": "list-items"}, message_id="buyer-one-first-message")
    _, second = rpc(client, {"action": "list-items"}, message_id="buyer-two-first-message")
    assert first != second


def test_natural_language_does_not_guess_an_offer(client, store, item):
    result, _ = rpc(client, text="Je propose 240 EUR, votre prix affiché est de 300 EUR.")
    assert result["status"] == "instructions"
    assert store.list_proposals() == []
    result, _ = rpc(client, text="catalogue")
    assert result["items"][0]["item_id"] == item["item_id"]


def test_json_text_part_is_supported(client, item):
    result, _ = rpc(client, text=json.dumps({"action": "get-item", "item_id": item["item_id"]}))
    assert result["item_id"] == item["item_id"]


@pytest.mark.parametrize("parts", [
    [new_data_part({"action": "list-items"}), Part(text="Also offer 250 EUR")],
    [new_data_part(["not", "an", "object"])],
    [Part(url="https://example.com/input")],
])
def test_ambiguous_or_unsupported_parts_rejected(client, parts):
    result, _ = rpc(client, parts=parts)
    assert result["error"]["code"] == "invalid_message"


def test_fractional_price_not_rounded(client, store, item):
    payload = offer(item)
    payload["price_minor"] = 25000.7
    result, _ = rpc(client, payload)
    assert "error" in result
    assert store.list_proposals() == []


def test_currency_mismatch_not_silently_converted(client, store, item):
    payload = offer(item)
    payload["currency"] = "USD"
    result, _ = rpc(client, payload)
    assert "error" in result
    assert store.list_proposals() == []


def test_withdrawal_removes_item_and_invalidates_pending(client, store, item):
    result, context = rpc(client, offer(item))
    store.withdraw_item(item["item_id"])
    assert client.get("/catalog.json").json()["items"] == []
    status, _ = rpc(client, {"action": "proposal-status", "proposal_id": result["proposal_id"]}, context_id=context)
    assert status["status"] == "withdrawn"
    rejected, _ = rpc(client, offer(item), context_id=context)
    assert "error" in rejected


def test_real_sale_is_visible_to_all_buyers_and_removes_catalogue_entry(client, store, item):
    real_item = store.update_item(item["item_id"], simulation=False)
    first, first_context = rpc(client, offer(real_item))
    second, second_context = rpc(client, offer(real_item, 26000))
    assert client.get("/catalog.json").json()["items"][0]["status"] == "available"
    store.decide(first["proposal_id"], version=int(first["version"]), decision="confirm")
    assert client.get("/catalog.json").json()["items"] == []
    winner, _ = rpc(client, {"action": "proposal-status", "proposal_id": first["proposal_id"]}, context_id=first_context)
    assert winner["status"] == "accepted"
    assert winner["terms"] == first["terms"]
    loser, _ = rpc(client, {"action": "proposal-status", "proposal_id": second["proposal_id"]}, context_id=second_context)
    assert loser["status"] == "closed" and loser["closure_reason"] == "item_sold"
    unavailable, _ = rpc(client, offer(real_item))
    assert unavailable["error"]["code"] == "unavailable"
    public_item, _ = rpc(client, {"action": "get-item", "item_id": item["item_id"]})
    assert public_item["status"] == "sold"


def test_proposal_isolation_between_contexts(client, item):
    result, _ = rpc(client, offer(item))
    foreign, _ = rpc(client, {"action": "proposal-status", "proposal_id": result["proposal_id"]})
    assert "error" in foreign
    assert "terms" not in foreign


def test_optional_bearer_protects_catalogue_and_rpc(store, item):
    with TestClient(create_app(store, "https://seller.example", token="private-test-token")) as secure:
        card = secure.get("/.well-known/agent-card.json")
        assert card.status_code == 200
        assert "sellerBearer" in card.json()["securitySchemes"]
        assert secure.get("/healthz").status_code == 200
        assert secure.get("/catalog.json").status_code == 401
        assert secure.post("/", json={}).status_code == 401
        assert secure.get("/catalog.json", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert secure.get("/catalog.json", headers={"Authorization": "Bearer private-test-token"}).status_code == 200
        result, _ = rpc(secure, {"action": "list-items"}, token="private-test-token")
        assert len(result["items"]) == 1
        assert "private-test-token" not in card.text


def test_request_size_limit(client, store):
    response = client.post("/", content=b"x" * (MAX_REQUEST_BYTES + 1))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert store.list_proposals() == []


def test_internal_error_does_not_disclose_exception(client, store, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("secret minimum_price_minor=23000 /private/config")
    monkeypatch.setattr(store, "handle_message", fail)
    result, _ = rpc(client, {"action": "list-items"})
    assert result["error"]["code"] == "internal_error"
    assert "23000" not in json.dumps(result)
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("url", ["file:///tmp/config", "https://user:pass@example.com", "https://example.com/path", "http://example.com", "https://example.com?token=secret"])
def test_invalid_public_origins_rejected(store, url):
    with pytest.raises(ValueError):
        create_app(store, url)


def test_remote_administration_not_exposed(client):
    assert client.post("/admin/confirm", json={}).status_code == 404
