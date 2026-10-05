"""X-Request-ID: echo a sane client value or mint one; exposed on request.state and every response."""

import re
import uuid

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_VALID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def request_id_of(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


class RequestIdMiddleware:
    """Pure ASGI (no BaseHTTPMiddleware) so streaming/WS responses are untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        incoming = Request(scope).headers.get(REQUEST_ID_HEADER, "")
        rid = incoming if _VALID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
            await send(message)

        await self.app(scope, receive, send_with_id)
