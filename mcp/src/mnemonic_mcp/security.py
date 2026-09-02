"""Explicit Host/Origin checks and shared-key authentication for local HTTP MCP."""

import secrets

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings

MAX_REQUEST_BYTES = 2 * 1024 * 1024


class LocalAccessMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)

        rejection = self._host_or_origin_rejection(headers)
        if rejection is not None:
            await rejection(scope, receive, send)
            return
        if self._is_health_check(scope):
            await self.app(scope, receive, send)
            return

        rejection = self._authorization_rejection(headers)
        if rejection is not None:
            await rejection(scope, receive, send)
            return
        rejection = self._content_length_rejection(headers)
        if rejection is not None:
            await rejection(scope, receive, send)
            return

        await self._dispatch_bounded_body(scope, receive, send)

    def _host_or_origin_rejection(self, headers: Headers) -> JSONResponse | None:
        if len(headers.getlist("host")) != 1 or headers["host"] not in self.settings.allowed_hosts:
            return JSONResponse({"detail": "Untrusted host."}, status_code=421)
        origins = headers.getlist("origin")
        if len(origins) > 1 or (origins and origins[0] not in self.settings.allowed_origins):
            return JSONResponse({"detail": "Untrusted origin."}, status_code=403)
        return None

    @staticmethod
    def _is_health_check(scope: Scope) -> bool:
        return scope["path"] == "/healthz" and scope["method"] in {"GET", "HEAD"}

    def _authorization_rejection(self, headers: Headers) -> JSONResponse | None:
        credentials = headers.getlist("authorization")
        scheme, _, token = (credentials[0] if len(credentials) == 1 else "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            token.encode("utf-8"), self.settings.api_key.encode("utf-8")
        ):
            return JSONResponse(
                {"detail": "A valid bearer API key is required."},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="Mnemonic"'},
            )
        return None

    @staticmethod
    def _content_length_rejection(headers: Headers) -> JSONResponse | None:
        try:
            size_hint = int(headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Invalid content length."}, status_code=400)
        if size_hint < 0 or size_hint > MAX_REQUEST_BYTES:
            return JSONResponse({"detail": "Request body is too large."}, status_code=413)
        return None

    async def _dispatch_bounded_body(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        # Bound the authenticated body too: a malformed/compromised client must
        # not fill memory before the SDK can parse its JSON-RPC envelope.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                await JSONResponse({"detail": "Request body is too large."}, status_code=413)(
                    scope, receive, send
                )
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def buffered_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)
