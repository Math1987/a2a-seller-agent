"""A2A 1.0 transport for one seller and its live multi-item catalogue.

Application actions travel in a single JSON data Part. Natural-language prices
are intentionally not interpreted here: the buyer's agent constructs the action.
The local owner CLI is the only administration interface.
"""

from __future__ import annotations

import hmac
import json
import logging
import math
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from a2a.helpers.proto_helpers import get_data_parts, new_data_message
from a2a.server.agent_execution import AgentExecutor, RequestContext, SimpleRequestContextBuilder
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    Role,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from a2a.utils.errors import UnsupportedOperationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from .storage import SellerError, SellerStore

MAX_REQUEST_BYTES = 32 * 1024
APPLICATION_VERSION = "0.1"
LOGGER = logging.getLogger(__name__)


class RetryStableContextBuilder(SimpleRequestContextBuilder):
    """Give a retried first message the same context, including after restart.

    The SDK normally allocates a random context when the client has not supplied
    one. If that first response is lost, the same messageId would otherwise be
    dispatched as a new negotiation and evade the store's idempotency scope.
    """

    def __init__(self, seller_id: str) -> None:
        super().__init__(should_populate_referred_tasks=False, task_store=None)
        self.namespace = uuid5(NAMESPACE_URL, "a2a-seller:" + seller_id)

    async def build(self, context, params=None, task_id=None, context_id=None, task=None):
        if params is not None and not context_id and not params.message.context_id:
            if params.message.message_id:
                context_id = str(uuid5(self.namespace, params.message.message_id))
        return await super().build(
            context=context, params=params, task_id=task_id, context_id=context_id, task=task
        )


def action_help() -> dict[str, Any]:
    """Return public examples without consulting private configuration."""
    return {
        "schema_version": APPLICATION_VERSION,
        "status": "instructions",
        "message": (
            "Send one structured JSON data Part (or a JSON text Part). "
            "Amounts are integer minor currency units. First get the item, "
            "then accept its current revision when submitting an offer. "
            "Use a unique random messageId for each request. Keep the returned "
            "contextId for the negotiation and reuse the same messageId when "
            "retrying the same request. Seller approval "
            "is required; no payment, reservation or shipping is performed."
        ),
        "actions": [
            {"action": "list-items"},
            {"action": "get-item", "item_id": "ITEM_ID"},
            {
                "action": "offer",
                "item_id": "ITEM_ID",
                "price_minor": 25000,
                "currency": "EUR",
                "accepted_terms_revision": 1,
            },
            {"action": "proposal-status", "proposal_id": "PROPOSAL_ID"},
        ],
    }


def _payload(context: RequestContext) -> dict[str, Any] | None:
    message = context.message
    if message is None or message.role != Role.ROLE_USER:
        raise SellerError("invalid_message", "A buyer message must use ROLE_USER.")
    if not message.message_id:
        raise SellerError("invalid_message", "messageId is required.")
    if len(message.parts) != 1:
        raise SellerError("invalid_message", "Send exactly one data or text Part.")
    part = message.parts[0]
    if part.HasField("data"):
        payload = get_data_parts(message.parts)[0]
    elif part.HasField("text"):
        value = part.text.strip()
        if value.casefold() in {"list-items", "liste", "catalogue", "catalog"}:
            return {"action": "list-items"}
        if not value.startswith("{"):
            return None
        try:
            payload = json.loads(value)
        except (ValueError, RecursionError):
            raise SellerError("invalid_message", "The JSON action is invalid.") from None
    else:
        raise SellerError("invalid_message", "Only JSON data or text Parts are supported.")
    if not isinstance(payload, dict):
        raise SellerError("invalid_message", "The action must be a JSON object.")
    # protobuf Value stores JSON numbers as doubles. Restore only the integer
    # fields of our schema, without rounding decimal values or accepting bools.
    for field in ("price_minor", "accepted_terms_revision"):
        value = payload.get(field)
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            if abs(value) <= 2**53 - 1:
                payload[field] = int(value)
    return payload


class SellerExecutor(AgentExecutor):
    def __init__(self, store: SellerStore) -> None:
        self.store = store

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            payload = _payload(context)
            if payload is None:
                result = action_help()
            else:
                result = await run_in_threadpool(
                    self.store.handle_message,
                    payload,
                    context_id=context.context_id,
                    message_id=context.message.message_id,
                )
        except SellerError as error:
            result = {"error": {"code": error.code, "message": error.message}}
        except Exception as error:
            # Exception messages can include database paths or private values.
            LOGGER.error("Seller request failed (%s)", type(error).__name__)
            result = {"error": {"code": "internal_error", "message": "The request could not be processed."}}
        await event_queue.enqueue_event(
            new_data_message(result, media_type="application/json", context_id=context.context_id)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise UnsupportedOperationError(message="This seller uses message-only interactions.")


class RequestBoundaryMiddleware:
    """Enforce bearer access and bound request bodies, including chunked input."""

    def __init__(self, app: Any, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        protected = path == "/catalog.json" or (
            path == "/" and scope.get("method") == "POST"
        )
        if protected and self.token is not None:
            headers = dict(scope.get("headers", []))
            supplied = headers.get(b"authorization", b"")
            expected = ("Bearer " + self.token).encode("utf-8")
            if not hmac.compare_digest(supplied, expected):
                response = JSONResponse(
                    {"error": {"code": "unauthorized", "message": "Bearer authentication required."}},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        if scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        total = 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            chunk = event.get("body", b"")
            total += len(chunk)
            if total > MAX_REQUEST_BYTES:
                response = JSONResponse(
                    {"error": {"code": "request_too_large", "message": "Request body exceeds 32768 bytes."}},
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            if not event.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False

        async def buffered_receive() -> dict:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)


def _base_url(public_url: str) -> str:
    try:
        parsed = urlsplit(public_url)
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError("public_url must be an HTTP(S) origin.") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port == 0
    ):
        raise ValueError("public_url must be an HTTP(S) origin without path, query or credentials.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Public endpoints require HTTPS; HTTP is allowed only for loopback tests.")
    return public_url.rstrip("/")


def create_app(store: SellerStore, public_url: str, token: str | None = None) -> Starlette:
    """Build a seller app. The caller owns the process, tunnel and data directory."""
    base = _base_url(public_url)
    if token is not None and (not isinstance(token, str) or not token or token.strip() != token or "\n" in token):
        raise ValueError("token must be a nonempty secret without surrounding whitespace.")
    card = AgentCard(
        name="A2A second-hand seller",
        description=(
            "One seller with a live catalogue of independently identified items. "
            "Use list-items before negotiating. Public catalogue: " + base + "/catalog.json. "
            "Requests use one JSON data Part; responses contain structured data. "
            "Prices use integer minor currency units. Offers require the exact current "
            "item revision and explicit seller approval. No payment or delivery is performed."
        ),
        version="0.1.0",
        supported_interfaces=[AgentInterface(url=base + "/", protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id="list-items", name="List available items",
                description="List current public articles, their IDs, prices, currency and terms revisions. Also available at " + base + "/catalog.json.",
                tags=["catalogue", "second-hand"], examples=['{"action":"list-items"}'],
            ),
            AgentSkill(
                id="get-item", name="Inspect one item",
                description="Read an item's public facts and current revision. Unknown facts remain unknown.",
                tags=["item", "second-hand"], examples=['{"action":"get-item","item_id":"ITEM_ID"}'],
            ),
            AgentSkill(
                id="negotiate-item", name="Propose and follow an offer",
                description=(
                    "Submit an offer in integer minor currency units with accepted_terms_revision "
                    "from get-item. Keep contextId and messageId for retries. Query proposal-status "
                    "in the same context to retrieve the owner's decision. Natural-language offers "
                    "must be translated to structured actions by the buyer's agent."
                ),
                tags=["offer", "negotiation"],
                examples=[json.dumps(action_help()["actions"][2]), json.dumps(action_help()["actions"][3])],
            ),
        ],
    )
    if token is not None:
        card.security_schemes["sellerBearer"].CopyFrom(
            SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="bearer"))
        )
        card.security_requirements.append(
            SecurityRequirement(schemes={"sellerBearer": StringList()})
        )

    async def catalog(_request: Any) -> JSONResponse:
        return JSONResponse(await run_in_threadpool(store.catalog))

    async def discovery(_request: Any) -> JSONResponse:
        return JSONResponse(
            {
                "specVersion": "1.0",
                "host": {"displayName": "A2A second-hand seller", "identifier": urlsplit(base).netloc},
                "entries": [{
                    "identifier": "a2a-seller",
                    "displayName": "A2A second-hand seller",
                    "type": "application/a2a-agent-card+json",
                    "url": base + "/.well-known/agent-card.json",
                    "description": "Discover the seller; list-items returns its current articles.",
                }],
            },
            media_type="application/ai-catalog+json",
        )

    async def health(_request: Any) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    handler = DefaultRequestHandler(
        agent_executor=SellerExecutor(store), task_store=InMemoryTaskStore(), agent_card=card,
        request_context_builder=RetryStableContextBuilder(store.catalog()["seller_id"]),
    )
    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/") + [
        Route("/catalog.json", catalog),
        Route("/.well-known/ai-catalog.json", discovery),
        Route("/.well-known/ard.json", discovery),
        Route("/healthz", health),
    ]
    return Starlette(routes=routes, middleware=[Middleware(RequestBoundaryMiddleware, token=token)])
