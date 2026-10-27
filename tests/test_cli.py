"""Exercise owner workflows and errors through the actual CLI entry point."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from a2a_seller.cli import main
from a2a_seller.storage import SellerStore


@pytest.fixture
def command(tmp_path, capsys):
    data = tmp_path / "state"

    def invoke(*args, expected=0):
        code = main(["--data-dir", str(data), *args])
        captured = capsys.readouterr()
        assert code == expected, (captured.out, captured.err)
        if expected:
            assert captured.out == ""
            return json.loads(captured.err)["error"]
        assert captured.err == ""
        return json.loads(captured.out)

    invoke.data_dir = data
    return invoke


def add(command, **values):
    fields = {"title": "Table de nuit", "price": "300", "minimum-price": "230", "currency": "EUR"}
    fields.update(values)
    args = ["item", "add"]
    for name, value in fields.items():
        args.extend(["--" + name, value])
    return command(*args)


def test_add_replay_update_and_withdraw_keep_other_item(command):
    first = add(command, **{"request-id": "nightstand-1"})
    repeated = add(command, **{"request-id": "nightstand-1"})
    assert repeated == first
    second = add(command, title="VTT", price="200", **{"minimum-price": "150", "currency": "USD"})
    assert first["item_id"] != second["item_id"]
    assert len(command("item", "list")) == 2
    changed = command("item", "update", "--id", first["item_id"], "--price", "280", "--title", "Table en bois")
    assert changed["item_id"] == first["item_id"]
    assert changed["revision"] == first["revision"] + 1
    assert changed["price_minor"] == 28000
    withdrawn = command("item", "withdraw", "--id", first["item_id"])
    assert withdrawn["status"] == "withdrawn"
    assert command("item", "list") == [second]
    assert len(command("item", "list", "--all")) == 2
    public = SellerStore(command.data_dir).catalog()
    assert len(public["items"]) == 1
    assert "minimum_price_minor" not in public["items"][0]


def test_explicit_currency_and_exact_amount(command):
    item = add(command, price="300.10", **{"minimum-price": "230.01", "currency": "usd"})
    assert item["price_minor"] == 30010
    assert item["minimum_price_minor"] == 23001
    assert item["currency"] == "USD"
    assert item["simulation"] is True
    yen = add(command, price="300", **{"minimum-price": "230", "currency": "JPY"})
    assert yen["price_minor"] == 300


@pytest.mark.parametrize("flag,value", [
    ("--currency", "$"), ("--price", "3e2"), ("--price", "300.001"),
    ("--price", "300 EUR"), ("--minimum-price", "301"), ("--price", "0"),
])
def test_invalid_money_is_not_silently_coerced(command, flag, value):
    args = ["item", "add", "--title", "Test", "--price", "300", "--minimum-price", "230", "--currency", "EUR"]
    args[args.index(flag) + 1] = value
    assert command(*args, expected=2)["code"]
    assert command("item", "list") == []


def test_currency_change_requires_explicit_repricing(command):
    item = add(command)
    command("item", "update", "--id", item["item_id"], "--currency", "JPY", expected=2)
    assert command("item", "show", "--id", item["item_id"])["currency"] == "EUR"
    changed = command("item", "update", "--id", item["item_id"], "--currency", "JPY",
                      "--price", "50000", "--minimum-price", "40000")
    assert changed["currency"] == "JPY"
    assert changed["price_minor"] == 50000


def test_real_is_an_explicit_choice(command):
    item = command("item", "add", "--title", "Lampe", "--price", "30", "--minimum-price", "20",
                   "--currency", "EUR", "--real")
    assert item["simulation"] is False
    assert command("item", "update", "--id", item["item_id"], "--simulation")["simulation"] is True


def make_offer(command):
    item = add(command)
    store = SellerStore(command.data_dir)
    proposal = store.handle_message({"action": "offer", "item_id": item["item_id"],
        "price_minor": 25000, "currency": "EUR", "accepted_terms_revision": item["revision"]},
        context_id="buyer-test", message_id="offer-1")
    return store, item, proposal


def test_notifications_require_ack_and_confirmation_is_exact(command):
    store, _, proposal = make_offer(command)
    initial = command("notifications", "pending")
    assert len(initial) == 1
    assert command("notifications", "pending") == initial
    command("notifications", "ack", "--id", proposal["proposal_id"], "--version", str(proposal["version"]))
    assert command("notifications", "pending") == []
    command("proposal", "confirm", "--id", proposal["proposal_id"], "--version", "999", expected=2)
    confirmed = command("proposal", "confirm", "--id", proposal["proposal_id"], "--version", str(proposal["version"]))
    assert confirmed["status"] == "accepted_fictive"
    public = store.handle_message({"action": "proposal-status", "proposal_id": proposal["proposal_id"]},
                                  context_id="buyer-test", message_id="status-1")
    assert public["status"] == "accepted_fictive"
    assert public["price_minor"] == 25000


def test_counter_requires_new_buyer_offer_and_withdraw_blocks_confirmation(command):
    store, item, proposal = make_offer(command)
    counter = command("proposal", "counter", "--id", proposal["proposal_id"], "--version", "1", "--price", "270")
    assert counter["version"] == 2
    assert counter["price_minor"] == 27000
    assert counter["status"] == "countered"
    command("proposal", "confirm", "--id", proposal["proposal_id"], "--version", "2", expected=2)
    fresh = store.handle_message({"action": "offer", "item_id": item["item_id"], "price_minor": 27000,
        "currency": "EUR", "accepted_terms_revision": item["revision"]}, context_id="buyer-test", message_id="offer-2")
    command("item", "withdraw", "--id", item["item_id"])
    command("proposal", "confirm", "--id", fresh["proposal_id"], "--version", str(fresh["version"]), expected=2)


def test_argparse_errors_are_machine_readable(command):
    assert command("item", "add", expected=2)["code"] == "invalid_argument"
    assert command("proposal", "confirm", "--id", "unknown", "--version", "NaN", expected=2)["code"] == "invalid_argument"


def test_skill_show_is_cwd_independent_and_does_not_create_state(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "must-not-exist"
    assert main(["--data-dir", str(state), "skill", "show"]) == 0
    output = capsys.readouterr()
    assert output.out.startswith("---\n")
    assert "a2a-seller" in output.out
    assert output.err == ""
    assert not state.exists()


def test_serve_rejects_invalid_token_without_printing_secret(command, tmp_path):
    token = tmp_path / "token"
    token.write_text("secret-value-with spaces")
    token.chmod(0o600)
    error = command("serve", "--public-url", "https://seller.example", "--token-file", str(token), expected=2)
    assert error["code"] == "invalid_token_file"
    assert "secret-value" not in json.dumps(error)


def test_python_module_entrypoint(tmp_path):
    # Exercise the executable module instead of only calling the function.
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run([sys.executable, "-m", "a2a_seller", "--data-dir", str(tmp_path / "data"),
                             "item", "list"], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
