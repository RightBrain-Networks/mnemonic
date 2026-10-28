"""Trusted browser transcript transport over the existing domain handlers."""

import asyncio
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from mnemonic_api.application.browser_file_transport import (
    SECURITY_HEADERS,
    FileResponsePolicy,
    FileTransportError,
    combined_headers,
    declared_length,
    decode_file_json,
    execute_file_request,
    file_failure,
    read_file_json,
)
from mnemonic_api.application.browser_policy import forbidden_control_transport, trusted_request
from mnemonic_api.application.browser_transcripts_policy import (
    MUTATIONS,
    TRANSCRIPT_JSON_MAX_BYTES,
    TRANSCRIPT_MAX_BYTES,
    transcript_route,
    valid_transcript_body,
    valid_transcript_query,
)
from mnemonic_api.application.routes.transcripts import router as domain_router
from mnemonic_api.application.state import settings_of

TRANSCRIPT_BROWSER_PREFIX = "/api/transcripts"
_ADMITTED = object()
_HANDLERS = frozenset({
    "list_transcripts", "rebuild_transcripts", "import_transcripts", "get_transcript_health",
    "get_transcript", "get_transcript_text", "download_transcript", "get_transcript_settings",
    "update_transcript_settings", "browse_transcript_directories",
})
FAILURE_DETAIL = (
    "The transcript request could not be completed. "
    "For a pending import or rebuild, retry the preserved request."
)


def _require_admission(request: Request) -> None:
    if getattr(request.state, "transcript_browser_admission", None) is not _ADMITTED:
        raise HTTPException(403, "This dashboard request is not from a trusted origin.")


def transcript_browser_router() -> APIRouter:
    router = APIRouter(prefix=TRANSCRIPT_BROWSER_PREFIX,
                       dependencies=[Depends(_require_admission)], include_in_schema=False)
    selected = [route for route in domain_router.routes
                if isinstance(route, APIRoute) and route.name in _HANDLERS]
    if {route.name for route in selected} != _HANDLERS:
        raise RuntimeError("Transcript browser handler inventory changed")
    subset = APIRouter()
    subset.routes.extend(selected)
    router.include_router(subset)
    return router


class TranscriptRejection(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.response = JSONResponse(
            {"detail": {"code": "transcript_proxy_rejected", "message": message}},
            status_code=status, headers=SECURITY_HEADERS)


def _reject(status: int, message: str) -> NoReturn:
    raise TranscriptRejection(status, message)


def _headers(request: Request) -> Headers:
    return combined_headers(request.headers)


def _admit(request: Request, route: str) -> str:
    headers = _headers(request)
    if not trusted_request(headers, request.method,
                           set(settings_of(request).allowed_dashboard_origins)):
        _reject(403, "This dashboard request is not from a trusted origin.")
    action = transcript_route(route, request.method)
    if action is None or b"%2f" in request.scope.get("raw_path", b"").lower():
        _reject(404, "Route not found.")
    if forbidden_control_transport(headers):
        _reject(400, "The request contains a forbidden control header.")
    if not valid_transcript_query(request.query_params, action):
        _reject(400, "Invalid transcript query.")
    encoding = headers.get("content-encoding")
    if encoding and encoding.lower() != "identity":
        _reject(415, "Encoded requests are not supported.")
    return action


async def _read_body(request: Request, maximum: int) -> bytes:
    async with asyncio.timeout(10):
        return await read_file_json(request, maximum)


async def _request_body(request: Request, action: str) -> bytes:
    if action not in MUTATIONS:
        try:
            return await _read_body(request, 0)
        except (ValueError, TimeoutError):
            _reject(400, "Transcript reads do not accept a body.")
    content_type = _headers(request).get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        _reject(415, "Send transcript settings as JSON.")
    try:
        body = await _read_body(request, 512 * 1024 if action == "save" else 32 * 1024)
        value = decode_file_json(body)
    except (ValueError, UnicodeError, RecursionError, TimeoutError):
        _reject(400, "Invalid or oversized transcript settings request.")
    if not valid_transcript_body(value, action):
        _reject(400, "Invalid transcript settings request.")
    return body


def _domain_scope(scope: Scope) -> Scope:
    child = dict(scope)
    child["state"] = {**scope.get("state", {}), "transcript_browser_admission": _ADMITTED}
    child["headers"] = [(b"accept-encoding", b"identity")]
    if scope["method"] != "GET":
        child["headers"].append((b"content-type", b"application/json"))
    host = Headers(scope=scope).get("host")
    if host:
        child["headers"].append((b"host", host.encode("latin-1")))
    return child


def _download_headers(status: int, headers: Headers, transcript_id: str) -> dict[str, str] | None:
    if not 200 <= status < 300:
        return None
    if status != 200:
        raise FileTransportError("Mnemonic returned incomplete transcript content.")
    length = headers.get("content-length")
    try:
        declared_length(headers, TRANSCRIPT_MAX_BYTES)
    except ValueError:
        raise FileTransportError("Mnemonic returned an invalid transcript size.") from None
    return {"Content-Type": "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{transcript_id}.txt"',
            **({"Content-Length": length} if length is not None else {})}


def _validate_response(value: object) -> object:
    detail = value.get("detail") if isinstance(value, dict) else None
    if isinstance(detail, dict) and detail.get("code") == "transcript_proxy_rejected":
        raise FileTransportError("Mnemonic returned an invalid rejection response.")
    return value


def _response_policy(action: str, route: str) -> FileResponsePolicy:
    return FileResponsePolicy(
        json_maximum=1024 * 1024 if action == "text" else TRANSCRIPT_JSON_MAX_BYTES,
        failure_detail=FAILURE_DETAIL,
        redirect_detail="Mnemonic returned an unexpected redirect.",
        encoding_detail="Mnemonic returned an encoded response.",
        stream=(lambda status, headers: _download_headers(status, headers, route.split("/")[3]))
        if action == "content" else None,
        transform_json=_validate_response, stream_maximum=TRANSCRIPT_MAX_BYTES,
        require_json_content_type=False)


class TranscriptBrowserMiddleware:
    def __init__(self, app: ASGIApp, *,
                 pending_tasks: set[asyncio.Task[None]] | None = None) -> None:
        self.app = app
        self.pending_tasks = pending_tasks

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        matches = (path == TRANSCRIPT_BROWSER_PREFIX
                   or path.startswith(TRANSCRIPT_BROWSER_PREFIX + "/"))
        if scope["type"] != "http" or not matches:
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        route = path.removeprefix(TRANSCRIPT_BROWSER_PREFIX + "/")
        try:
            action = _admit(request, route)
            body = await _request_body(request, action)
        except TranscriptRejection as exc:
            await exc.response(scope, receive, send)
            return
        except ClientDisconnect:
            return
        except Exception:
            await file_failure(502, FAILURE_DETAIL)(scope, receive, send)
            return
        await execute_file_request(
            self.app, _domain_scope(scope), receive, send,
            policy=_response_policy(action, route), body=body, mutation=action in MUTATIONS,
            pending_tasks=self.pending_tasks)
