from concurrent.futures import ThreadPoolExecutor
import json
from uuid import uuid4

import pytest

from a2a_seller.storage import SellerError, SellerStore
from a2a_seller.validation import parse_amount, format_amount


@pytest.fixture
def store(tmp_path):
    return SellerStore(tmp_path / "state")


def add(store, **changes):
    return store.add_item(**(dict(title="Table de nuit", price_minor=30000,
        minimum_price_minor=23000, currency="EUR", handover="Lyon samedi", simulation=True) | changes))


def offer(store, item, price=25000, *, context="buyer-a", mid=None, **changes):
    payload = dict(action="offer", item_id=item["item_id"], price_minor=price,
                   currency=item["currency"], accepted_terms_revision=item["revision"])
    payload.update(changes)
    return store.handle_message(payload, context_id=context, message_id=mid or uuid4().hex)


def status(store, proposal, context="buyer-a"):
    return store.handle_message(dict(action="proposal-status", proposal_id=proposal["proposal_id"]),
                                context_id=context, message_id=uuid4().hex)


def test_multiitem_public_projection_and_withdrawal(store):
    table = add(store)
    bike = add(store, title="VTT", currency="USD", price_minor=20000, minimum_price_minor=15000)
    assert table["item_id"] != bike["item_id"]
    assert len(store.catalog()["items"]) == 2
    assert "minimum_price_minor" not in json.dumps(store.catalog())
    assert "minimum_price_minor" not in store.get_item(table["item_id"])
    assert store.get_item(table["item_id"], private=True)["minimum_price_minor"] == 23000
    store.withdraw_item(table["item_id"])
    assert [i["item_id"] for i in store.catalog()["items"]] == [bike["item_id"]]
    assert len(store.list_items(include_withdrawn=True)) == 2
    with pytest.raises(SellerError, match="no longer available"):
        offer(store, table)


def test_add_request_id_is_idempotent(store):
    first = add(store, request_id="instruction-1")
    assert add(store, request_id="instruction-1") == first
    assert len(store.list_items()) == 1
    with pytest.raises(SellerError) as err:
        add(store, title="Different table", request_id="instruction-1")
    assert err.value.code == "idempotency_conflict"


def test_no_silent_price_rounding():
    assert parse_amount("300.25", "EUR") == 30025
    assert parse_amount("300", "JPY") == 300
    assert format_amount(30025, "EUR") == "300.25"
    for amount, currency in [("1.001", "EUR"), ("10.5", "JPY"), ("1e3", "EUR"),
                             ("1,000", "USD"), ("-1", "EUR"), ("0", "EUR"), ("150", "$")]:
        with pytest.raises(SellerError):
            parse_amount(amount, currency)


@pytest.mark.parametrize("changes", [dict(price_minor=True), dict(price_minor=1.2),
    dict(minimum_price_minor=40000), dict(currency="$"), dict(title=""), dict(simulation="false")])
def test_invalid_items_never_persist(store, changes):
    with pytest.raises(SellerError):
        add(store, **changes)
    assert store.catalog()["items"] == []


def test_exact_floor_confirmation_is_publicly_readable(store):
    item = add(store)
    p = offer(store, item, 23000)
    assert p["status"] == "pending_seller"
    assert "minimum_price_minor" not in json.dumps(p)
    store.decide(p["proposal_id"], version=p["version"], decision="confirm")
    for _ in range(2):
        result = status(store, p)
        assert result["price_minor"] == 23000
        assert result["status"] == "accepted_fictive"


def test_terms_acceptance_is_per_offer_and_current_revision(store):
    item = add(store)
    p = offer(store, item)
    result = offer(store, item, 26000, accepted_terms_revision=None)
    assert result["status"] == "terms_required"
    assert len(store.list_proposals()) == 1
    newer = store.update_item(item["item_id"], handover="Paris dimanche")
    assert status(store, p)["status"] == "superseded"
    assert offer(store, item, 26000)["status"] == "terms_required"
    with pytest.raises(SellerError):
        store.decide(p["proposal_id"], version=p["version"], decision="confirm")
    assert offer(store, newer, 26000)["terms"]["handover"] == "Paris dimanche"


def test_prices_are_structured_and_currency_checked(store):
    item = add(store)
    assert offer(store, item, 10000)["status"] == "rejected_offer"
    assert store.list_proposals() == []
    with pytest.raises(SellerError) as err:
        offer(store, item, 25000, currency="USD")
    assert err.value.code == "currency_mismatch"
    with pytest.raises(SellerError):
        offer(store, item, 25000, comment="Actually I offer 28000")
    with pytest.raises(SellerError):
        offer(store, item, "Je propose 250 EUR, prix affiché 300 EUR")
    assert offer(store, item, 35000)["price_minor"] == 30000


def test_replayed_message_does_not_supersede_proposal(store):
    item = add(store)
    p = offer(store, item, mid="same-message")
    assert offer(store, item, mid="same-message") == p
    assert len(store.list_proposals()) == 1
    with pytest.raises(SellerError) as err:
        offer(store, item, 26000, mid="same-message")
    assert err.value.code == "idempotency_conflict"


def test_notifications_require_delivery_ack(store):
    p = offer(store, add(store))
    assert len(store.pending_notifications()) == 1
    assert len(store.pending_notifications()) == 1
    store.ack_notification(p["proposal_id"], version=p["version"])
    assert store.pending_notifications() == []
    assert store.list_proposals(pending_only=True)[0]["status"] == "pending_seller"


def test_changed_offer_rejects_stale_seller_approval(store):
    item = add(store)
    first = offer(store, item)
    second = offer(store, item, 26000)
    assert first["proposal_id"] == second["proposal_id"]
    assert second["version"] == 2
    with pytest.raises(SellerError):
        store.decide(first["proposal_id"], version=1, decision="confirm")
    with pytest.raises(SellerError):
        store.ack_notification(first["proposal_id"], version=1)
    assert store.decide(second["proposal_id"], version=2, decision="confirm")["status"] == "accepted_fictive"


def test_seller_counter_requires_new_buyer_offer_and_confirmation(store):
    item = add(store)
    p = offer(store, item)
    cp = store.decide(p["proposal_id"], version=1, decision="counter", price_minor=28000)
    assert cp["status"] == "countered" and cp["version"] == 2
    assert status(store, p)["price_minor"] == 28000
    buyer = offer(store, item, 28000)
    assert buyer["status"] == "pending_seller" and buyer["version"] == 3
    assert store.decide(buyer["proposal_id"], version=3, decision="confirm")["price_minor"] == 28000


def test_withdraw_invalidates_pending_and_counters(store):
    item = add(store)
    p1 = offer(store, item)
    p2 = offer(store, item, context="buyer-b")
    store.decide(p2["proposal_id"], version=1, decision="counter", price_minor=28000)
    first = store.withdraw_item(item["item_id"])
    assert store.withdraw_item(item["item_id"]) == first
    assert status(store, p1)["status"] == "withdrawn"
    assert status(store, p2, "buyer-b")["status"] == "withdrawn"
    assert store.pending_notifications() == []
    with pytest.raises(SellerError):
        store.decide(p1["proposal_id"], version=1, decision="confirm")


def test_contexts_and_articles_are_isolated(store):
    a, b = add(store), add(store, title="Lampe")
    p1, p2 = offer(store, a), offer(store, b)
    assert p1["proposal_id"] != p2["proposal_id"]
    with pytest.raises(SellerError) as err:
        status(store, p1, "another-buyer")
    assert err.value.code == "not_found"


def test_library_currency_change_requires_explicit_prices(store):
    item = add(store)
    with pytest.raises(SellerError) as err:
        store.update_item(item["item_id"], currency="JPY")
    assert err.value.code == "invalid_update"
    assert store.get_item(item["item_id"])["currency"] == "EUR"
    changed = store.update_item(item["item_id"], currency="JPY", price_minor=300,
                                minimum_price_minor=230)
    assert changed["price_minor"] == 300 and changed["currency"] == "JPY"


def test_state_and_idempotency_survive_restart(store):
    item = add(store, request_id="publish")
    p = offer(store, item, mid="message")
    restarted = SellerStore(store.data_dir)
    assert add(restarted, request_id="publish") == item
    assert offer(restarted, item, mid="message") == p
    assert restarted.catalog() == store.catalog()


def test_simultaneous_retries_create_one_proposal(store):
    item = add(store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: offer(store, item, mid="retry"), range(8)))
    assert all(p == results[0] for p in results)
    assert len(store.list_proposals()) == 1


def test_simultaneous_seller_decisions_have_single_winner(store):
    p = offer(store, add(store))
    def decide(decision):
        try:
            return store.decide(p["proposal_id"], version=1, decision=decision)["status"]
        except SellerError as e:
            return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ["confirm", "reject"]))
    assert results.count("stale_proposal") == 1
    assert status(store, p)["status"] in {"accepted_fictive", "rejected"}
