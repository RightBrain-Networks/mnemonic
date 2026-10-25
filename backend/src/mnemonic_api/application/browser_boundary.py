"""Pre-body browser policy and bounded JSON transport around shared handlers.

No caller credential is forwarded or used to manufacture browser authority.
Host/Origin/fetch metadata are the existing dashboard trust boundary; deployments
must preserve Host through their trusted same-origin proxy. Forwarded headers
are deliberately ignored. This is not an authentication system.
"""

import asyncio
import json
import secrets
from contextlib import suppress
from typing import NoReturn

import anyio
from starlette.datastructures import Headers, QueryParams
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from mnemonic_api.application.browser_file_transport import execute_file_request
from mnemonic_api.application.browser_policy import (
    DEFINITIVE_PROXY_ERRORS,
    allowed_query_keys,
    forbidden_control_transport,
    invalid_mutation_body,
    proxy_body_limit_bytes,
    trusted_request,
    valid_query_values,
)
from mnemonic_api.application.browser_policy.routes import route_name
from mnemonic_api.application.browser_policy.values import JS_WHITESPACE
from mnemonic_api.application.browser_responses import (
    UNREACHABLE,
    BrowserResponse,
    evidence_route,
    failure,
)
from mnemonic_api.application.browser_routes import _ADMITTED, BROWSER_PREFIX
from mnemonic_api.application.knowledge_file_transport import knowledge_file_policy
from mnemonic_api.application.state import settings_of


class BrowserRejection(Exception):
    def __init__(self, response: Response) -> None:
        self.response = response


def _reject(name: str, *, identity: bool = False) -> NoReturn:
    error = DEFINITIVE_PROXY_ERRORS[name]
    raise BrowserRejection(failure(error.status, error.detail, identity=identity))


def request_timeout(path: str, method: str, query: QueryParams) -> float:
    expensive = method == "POST" and path.split("/")[-1] in {"search", "duplicate-suggestions"}
    semantic = query.get("semantic") == "true" and bool(query.get("q", "").strip(JS_WHITESPACE))
    return 60.0 if expensive or semantic else 15.0


def _admit(request: Request, path: str) -> None:
    config = settings_of(request)
    headers = _combined_headers(request.headers)
    if not trusted_request(headers, request.method, set(config.allowed_dashboard_origins)):
        _reject("untrustedOrigin")
    if forbidden_control_transport(headers):
        _reject("forbiddenControlTransport")
    if b"%2f" in request.scope.get("raw_path", b"").lower():
        _reject("routeNotFound")
    keys = ([] if path == "runtime-config" and request.method == "GET"
            else allowed_query_keys(path, request.method))
    if keys is None:
        _reject("routeNotFound")
    for key in request.query_params:
        if key not in keys or not valid_query_values(key, request.query_params.getlist(key)):
            _reject("unsupportedQuery", identity=evidence_route(path, request.method))


def _combined_headers(headers: Headers) -> Headers:
    # Fetch Headers (the former boundary) combines repeated header values;
    # Starlette's first-value lookup must not silently trust one Host/Origin.
    return Headers({name: ("; " if name == "cookie" else ", ").join(headers.getlist(name))
                    for name in headers})


async def _bounded_body(request: Request, path: str) -> bytes:
    # The former Next GET transport never read or forwarded a caller body.
    if request.method == "GET":
        return b""
    maximum = proxy_body_limit_bytes(path)
    declared = request.headers.get("content-length", "")
    if declared.isdecimal() and int(declared) > maximum:
        _reject("bodyTooLarge")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            _reject("bodyTooLarge")
        body.extend(chunk)
    return bytes(body)


def _reject_json_constant(_: str) -> None:
    raise ValueError("Non-JSON constant")


def _validate_body(request: Request, path: str, body: bytes) -> None:
    if request.method == "GET":
        return
    if not body:
        _reject("requestBodyRequired")
    headers = _combined_headers(request.headers)
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        _reject("jsonContentTypeRequired")
    try:
        value = json.loads(body.decode("utf-8-sig"), parse_constant=_reject_json_constant)
    except (UnicodeError, ValueError, RecursionError):
        _reject("invalidJson")
        return
    if not isinstance(value, dict):
        _reject("jsonObjectRequired")
    try:
        invalid = invalid_mutation_body(path, request.method, value)
    except (ValueError, TypeError, RecursionError):
        _reject("invalidJson")
        return
    if invalid:
        raise BrowserRejection(failure(400, invalid))
    operation = value.get("client_operation_id")
    key = settings_of(request).api_key.get_secret_value()
    if isinstance(operation, str) and secrets.compare_digest(operation.encode(), key.encode()):
        _reject("clientOperationCredentialMatch")


def _domain_scope(scope: Scope, path: str) -> Scope:
    child = dict(scope)
    # Match the old Next hop's closed header set. In particular, caller-supplied
    # Authorization, cookies and artifact-human controls never reach a handler.
    headers = Headers(scope=scope)
    child["headers"] = [(b"accept", b"application/json")]
    if scope["method"] != "GET":
        child["headers"].append((b"content-type", b"application/json"))
    if path.endswith("/search") and scope["method"] == "POST":
        child["headers"].append((b"x-artifact-access", b"human-dashboard"))
    # Host remains useful to framework URLs, never trusted forwarding metadata.
    if "host" in headers:
        child["headers"].append((b"host", headers["host"].encode("latin-1")))
    child.setdefault("state", {})["browser_admission"] = _ADMITTED
    return child


def _replay(body: bytes, disconnected: asyncio.Event) -> Receive:
    consumed = False

    async def replay():
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    return replay


async def _watch_disconnect(receive: Receive, disconnected: asyncio.Event) -> None:
    while (await receive())["type"] != "http.disconnect":
        pass
    disconnected.set()


def _safe_read(path: str, method: str) -> bool:
    if method == "GET":
        return True
    return method == "POST" and (
        path.split("/")[-1] in {"search", "duplicate-suggestions"}
        or "/prompts/" in path and path.endswith("/render")
    )


class BrowserBoundaryMiddleware:
    def __init__(
        self, app: ASGIApp, *, pending_tasks: set[asyncio.Task[None]] | None = None,
    ) -> None:
        self.app = app
        self.in_flight = pending_tasks if pending_tasks is not None else set()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        browser = path == BROWSER_PREFIX or path.startswith(BROWSER_PREFIX + "/")
        if scope["type"] != "http" or not browser:
            await self.app(scope, receive, send)
            return
        route = path.removeprefix(BROWSER_PREFIX + "/")
        request = Request(scope, receive)
        try:
            _admit(request, route)
            if route_name(route, request.method) in {
                "knowledge_domain_export", "knowledge_decision_download",
            }:
                await execute_file_request(
                    self.app, _domain_scope(scope, route), receive, send,
                    policy=knowledge_file_policy(), pending_tasks=self.in_flight,
                )
                return
            timeout = request_timeout(route, request.method, request.query_params)
            async with asyncio.timeout(timeout):
                body = await _bounded_body(request, route)
                _validate_body(request, route, body)
                response = await self._execute(scope, receive, route, body)
        except BrowserRejection as exc:
            response = exc.response
        except ClientDisconnect:
            return
        except Exception:
            response = failure(502, UNREACHABLE, identity=evidence_route(route, request.method))
        await response(scope, receive, send)

    async def _execute(self, scope: Scope, receive: Receive, path: str, body: bytes) -> Response:
        buffered = BrowserResponse(path, scope["method"])
        child = _domain_scope(scope, path)
        payload = b"" if scope["method"] == "GET" else body
        disconnected = asyncio.Event()
        execution = anyio.CancelScope()

        async def execute() -> None:
            with execution:
                await self.app(child, _replay(payload, disconnected), buffered.capture)

        task = asyncio.create_task(execute())
        self.in_flight.add(task)
        task.add_done_callback(self._finished)
        watch = asyncio.create_task(_watch_disconnect(receive, disconnected))
        try:
            done, _ = await asyncio.wait((task, watch), return_when=asyncio.FIRST_COMPLETED)
            if task not in done:
                raise ClientDisconnect()
            await task
            return buffered.response()
        finally:
            watch.cancel()
            disconnected.set()
            # Reads release their waiters; inference middleware retains native
            # slots until actual work returns. A write already executing must
            # finish its receipt/publication once, despite a lost response.
            if _safe_read(path, scope["method"]) and not task.done():
                # Respect the shield around a synchronous route's native worker:
                # dependencies cannot close until that worker actually returns.
                # Its tracked cleanup must not extend the wire budget.
                execution.cancel()
            with suppress(asyncio.CancelledError):
                await watch

    def _finished(self, task: asyncio.Task[None]) -> None:
        self.in_flight.discard(task)
        with suppress(asyncio.CancelledError):
            task.exception()
