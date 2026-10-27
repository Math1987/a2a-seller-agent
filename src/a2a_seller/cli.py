"""Local owner CLI. Mutations never go through the public A2A endpoint."""
from __future__ import annotations

import argparse
from importlib.resources import files
import json
from pathlib import Path
import stat
import sys


class CLIError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CLIError("invalid_argument", message)


def _emit(value, *, error=False):
    print(json.dumps(value, ensure_ascii=False), file=sys.stderr if error else sys.stdout)


def _item_fields(parser, *, required=False):
    parser.add_argument("--title", required=required)
    parser.add_argument("--price", required=required, help="Decimal amount, e.g. 300.00; no currency symbol")
    parser.add_argument("--minimum-price", required=required, help="Private minimum, in the same currency")
    parser.add_argument("--currency", required=required, help="Explicit ISO currency, e.g. EUR or USD")
    for field in ("description", "condition", "handover", "area"):
        parser.add_argument("--" + field)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--real", dest="simulation", action="store_false", default=None,
                      help="Publish a real listing; does not enable payment or reservations")
    mode.add_argument("--simulation", dest="simulation", action="store_true", default=None)
    parser.add_argument("--request-id", help="Stable identifier for retrying the same owner mutation")


def parser():
    root = Parser(prog="a2a-seller", description=__doc__)
    root.add_argument("--data-dir", type=Path, default=Path.home() / ".local/share/a2a-seller",
                      help="Private seller state directory (place before the subcommand)")
    from . import __version__
    root.add_argument("--version", action="version", version=__version__)
    groups = root.add_subparsers(dest="group", required=True)

    item = groups.add_parser("item", help="Manage the owner's articles locally")
    items = item.add_subparsers(dest="command", required=True)
    _item_fields(items.add_parser("add"), required=True)
    listing = items.add_parser("list")
    listing.add_argument("--all", action="store_true", help="Include withdrawn articles")
    show = items.add_parser("show")
    show.add_argument("--id", required=True)
    update = items.add_parser("update")
    update.add_argument("--id", required=True)
    _item_fields(update)
    withdraw = items.add_parser("withdraw")
    withdraw.add_argument("--id", required=True)
    withdraw.add_argument("--request-id")

    proposals = groups.add_parser("proposal").add_subparsers(dest="command", required=True)
    listing = proposals.add_parser("list")
    listing.add_argument("--pending", action="store_true")
    for command in ("confirm", "reject", "counter"):
        decision = proposals.add_parser(command)
        decision.add_argument("--id", required=True)
        decision.add_argument("--version", required=True, type=int)
        if command == "counter":
            decision.add_argument("--price", required=True)

    notifications = groups.add_parser("notifications").add_subparsers(dest="command", required=True)
    notifications.add_parser("pending")
    ack = notifications.add_parser("ack")
    ack.add_argument("--id", required=True)
    ack.add_argument("--version", required=True, type=int)

    serve = groups.add_parser("serve", help="Run the A2A server on loopback; expose with your own tunnel")
    serve.add_argument("--public-url", required=True, help="Actual public base URL announced by the agent")
    serve.add_argument("--port", type=int, default=9999)
    serve.add_argument("--token-file", type=Path,
                       help="Owner-only file containing the A2A bearer token (not the ngrok authtoken)")

    groups.add_parser("skill").add_subparsers(dest="command", required=True).add_parser("show")
    return root


def _skill_text():
    resource = files("a2a_seller").joinpath("SKILL.md")
    if resource.is_file():
        return resource.read_text(encoding="utf-8")
    # Editable checkout: wheel builds copy the canonical source into package data.
    source = Path(__file__).resolve().parents[2] / "skills/a2a-seller/SKILL.md"
    if source.is_file():
        return source.read_text(encoding="utf-8")
    raise CLIError("missing_skill", "The installed distribution does not contain its skill.")


def _item_values(args, store):
    from .validation import normalize_currency, parse_amount
    values = {key: getattr(args, key) for key in (
        "title", "description", "condition", "handover", "area", "simulation"
    ) if getattr(args, key) is not None}
    if args.command == "add":
        values.setdefault("simulation", True)
        currency = normalize_currency(args.currency)
    else:
        current = store.get_item(args.id, private=True)
        currency = normalize_currency(args.currency or current["currency"])
        if currency != current["currency"] and (args.price is None or args.minimum_price is None):
            raise CLIError("invalid_argument", "Changing currency requires both --price and --minimum-price.")
    if args.currency is not None:
        values["currency"] = currency
    for flag, key in (("price", "price_minor"), ("minimum_price", "minimum_price_minor")):
        amount = getattr(args, flag)
        if amount is not None:
            values[key] = parse_amount(amount, currency)
    if not values:
        raise CLIError("invalid_argument", "Provide at least one field to update.")
    values["request_id"] = args.request_id
    return values


def _token(path):
    if path is None:
        return None
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
        raise CLIError("invalid_token_file", "Token must be stored in a regular file of at most 4096 bytes.")
    if sys.platform != "win32" and info.st_mode & 0o077:
        raise CLIError("invalid_token_file", "Token file must be accessible only to its owner (chmod 600).")
    token = path.read_text(encoding="utf-8").strip()
    if not token or any(char.isspace() for char in token):
        raise CLIError("invalid_token_file", "Token must be a nonempty single value without whitespace.")
    return token


def run(args):
    if args.group == "skill":
        # Intentional text output: this command is the instruction document itself.
        print(_skill_text(), end="")
        return
    from .storage import SellerStore
    store = SellerStore(args.data_dir.expanduser())
    if args.group == "item":
        if args.command == "add":
            result = store.add_item(**_item_values(args, store))
        elif args.command == "update":
            result = store.update_item(args.id, **_item_values(args, store))
        elif args.command == "withdraw":
            result = store.withdraw_item(args.id, request_id=args.request_id)
        elif args.command == "show":
            result = store.get_item(args.id, private=True)
        else:
            result = store.list_items(include_withdrawn=args.all, private=True)
    elif args.group == "proposal":
        if args.command == "list":
            result = store.list_proposals(pending_only=args.pending)
        else:
            price_minor = None
            if args.command == "counter":
                from .validation import parse_amount
                proposal = max((p for p in store.list_proposals() if p["proposal_id"] == args.id),
                               key=lambda p: p["version"], default=None)
                if proposal is None:
                    raise CLIError("not_found", "Proposal not found.")
                price_minor = parse_amount(args.price, proposal["currency"])
            result = store.decide(args.id, version=args.version, decision=args.command, price_minor=price_minor)
    elif args.group == "notifications":
        result = (store.pending_notifications() if args.command == "pending" else
                  store.ack_notification(args.id, version=args.version))
    else:
        if not 1 <= args.port <= 65535:
            raise CLIError("invalid_argument", "Port must be between 1 and 65535.")
        from .server import create_app
        import uvicorn
        app = create_app(store, public_url=args.public_url, token=_token(args.token_file))
        uvicorn.run(app, host="127.0.0.1", port=args.port)
        return
    _emit(result)


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        run(args)
        return 0
    except ValueError as exc:
        _emit({"error": {"code": getattr(exc, "code", "invalid_argument"),
                         "message": getattr(exc, "message", str(exc))}}, error=True)
        return 2
    except (OSError, UnicodeError):
        _emit({"error": {"code": "io_error", "message": "Unable to access the local state or requested file."}}, error=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
