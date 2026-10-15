"""Artifact browser transport, registered independently from machine authentication."""

import asyncio
import re
import secrets
from typing import Any, NoReturn
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from mnemonic_api.application.browser_artifacts_policy import (
    CONTENT,
    DISABLED_MESSAGE,
    HISTORY,
    SEARCH,
    TEXT,
    artifact_query_keys,
    artifact_size,
    artifact_text_pins,
    safe_artifact_disposition,
    valid_artifact_search,
    valid_artifact_update,
)
from mnemonic_api.application.browser_file_transport import (
    FILE_MAXIMUM,
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
from mnemonic_api.application.browser_policy.transport import (
    forbidden_control_transport,
    trusted_request,
)
from mnemonic_api.application.browser_policy.values import integer, text, uuid
from mnemonic_api.application.routes import artifacts
from mnemonic_api.application.state import settings_of
from mnemonic_api.artifact_schemas import ArtifactLibraryStatus

ARTIFACT_BROWSER_PREFIX = "/api/artifacts"
_ARTIFACT_ADMITTED = object()
ARTIFACT_BROWSER_HANDLERS = frozenset({
    "upload_artifact", "replace_artifact", "delete_artifact", "get_artifacts",
    "search_contents", "get_artifact", "get_artifact_history", "get_artifact_text",
    "download_artifact", "update_artifact_metadata",
})
_UNREACHABLE = (
    "The artifact request could not be completed. "
    "Retry the same pending action if its outcome is unknown."
)


def require_artifact_browser_admission(request: Request) -> None:
    if getattr(request.state, "artifact_browser_admission", None) is not _ARTIFACT_ADMITTED:
        raise HTTPException(403, "This dashboard request is not from a trusted origin.")


def artifact_browser_router() -> APIRouter:
    router = APIRouter(prefix=ARTIFACT_BROWSER_PREFIX, include_in_schema=False,
                       dependencies=[Depends(require_artifact_browser_admission)])
    router.add_api_route("/status", artifacts.get_artifact_status, methods=["GET"],
                         response_model=ArtifactLibraryStatus)
    selected = [route for route in artifacts.router.routes
                if isinstance(route, APIRoute) and route.name in ARTIFACT_BROWSER_HANDLERS]
    if {route.name for route in selected} != ARTIFACT_BROWSER_HANDLERS:
        raise RuntimeError("Artifact browser handler inventory changed")
    subset = APIRouter()
    subset.routes.extend(selected)
    router.include_router(subset)
    return router


class _Rejection(Exception):
    def __init__(self, response: Response) -> None:
        self.response = response


def _reject(status: int, detail: str) -> NoReturn:
    raise _Rejection(file_failure(status, detail))


def _limit_failure(maximum: int) -> NoReturn:
    message = (DISABLED_MESSAGE if maximum == 0 else
               f"Artifact content exceeds the {artifact_size(maximum)} "
               f"({maximum:,} bytes) per-file limit.")
    raise _Rejection(JSONResponse({"detail": {
        "code": "artifact_library_disabled" if maximum == 0 else "artifact_too_large",
        "message": message, "context": {"max_bytes": maximum},
    }}, status_code=503 if maximum == 0 else 413, headers=SECURITY_HEADERS))


def _admit(request: Request, route: str, headers: Headers) -> None:
    config = settings_of(request)
    if not trusted_request(headers, request.method, config.allowed_dashboard_origins):
        _reject(403, "This dashboard request is not from a trusted origin.")
    keys = artifact_query_keys(route, request.method)
    if keys is None or b"%2f" in request.scope.get("raw_path", b"").lower():
        _reject(404, "Route not found.")
    query = request.query_params
    if len(urlencode(query.multi_items(), safe="*").replace("~", "%7E")) > 16_000:
        _reject(400, "Artifact metadata is too large.")
    if any(key not in keys or len(query.getlist(key)) != 1 for key in query):
        _reject(400, "The artifact query contains an unsupported or repeated field.")
    if TEXT.fullmatch(route) and not artifact_text_pins(query):
        _reject(400, "Select an artifact passage with a revision and text hash.")
    if config.artifact_max_bytes == 0 and route != "status":
        _limit_failure(0)


def _metadata(headers: Headers, method: str, mutation: bool) -> dict[str, Any]:
    raw = headers.get("x-artifact-metadata")
    if not mutation or not raw:
        return {}
    if len(raw) > 16_384 or re.search(r"[^\x20-\x7e]", raw):
        _reject(400, "Artifact metadata must be bounded ASCII JSON.")
    allowed = {"agent_session_id", "actor_client"}
    if method != "DELETE":
        allowed |= {"filename", "description", "work_item_id", "related_work_item_ids",
                    "sensitive", "related_artifact_ids"}
    try:
        value = decode_file_json(raw.encode("ascii"))
        if not isinstance(value, dict) or not value.keys() <= allowed:
            raise ValueError()
        return value
    except (ValueError, RecursionError):
        _reject(400, "Artifact metadata does not match the dashboard allowlist.")


def _controls(request: Request, headers: Headers, mutation: bool) -> str:
    control = dict(headers)
    operation = control.get("x-client-operation-id", "")
    if mutation:
        control.pop("x-client-operation-id", None)
    if forbidden_control_transport(control):
        _reject(400, "The request contains a forbidden control header.")
    if mutation and not uuid(operation):
        _reject(400, "An artifact operation UUID is required.")
    revision = headers.get("x-artifact-expected-revision", "")
    if request.method in {"PUT", "DELETE"} and (
        not re.fullmatch(r"[1-9][0-9]{0,9}", revision) or int(revision) > 2**31 - 1
    ):
        _reject(400, "An expected artifact revision is required.")
    key = settings_of(request).api_key.get_secret_value()
    if secrets.compare_digest(operation.lower().encode(), key.lower().encode()):
        _reject(422, "The client operation ID cannot match a request credential.")
    return operation


def _body_headers(headers: Headers, metadata: dict[str, Any], upload: bool) -> None:
    if upload:
        length = headers.get("content-length", "")
        if re.fullmatch(r"[0-9]+", length):
            try:
                declared_length(headers, FILE_MAXIMUM)
            except ValueError:
                _limit_failure(FILE_MAXIMUM)
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
            "application/octet-stream"
        ):
            _reject(415, "Send artifact bytes as application/octet-stream.")
        if not isinstance(metadata.get("filename"), str) or not metadata["filename"]:
            _reject(400, "An artifact filename is required.")
    encoding = headers.get("content-encoding")
    if encoding and encoding.lower() != "identity":
        _reject(415, "Encoded artifact request bodies are not supported.")


async def _json_body(request: Request, headers: Headers, operation: str, update: bool) -> bytes:
    noun = "changes" if update else "search"
    if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        _reject(415, f"Send artifact {noun} as application/json.")
    maximum = 32768 if update else 4096
    try:
        body = await read_file_json(request, maximum)
        if not body:
            _reject(400, "Artifact changes are required." if update else
                    "An artifact search request is required.")
        value = decode_file_json(body)
    except (UnicodeError, ValueError, RecursionError):
        prefix = "Artifact changes" if update else "Artifact search"
        _reject(400, f"{prefix} must be valid JSON within {maximum} bytes.")
    valid = valid_artifact_update(value, operation) if update else valid_artifact_search(value)
    if not valid:
        prefix = "The artifact changes" if update else "The artifact search"
        _reject(400, f"{prefix} do not match the dashboard allowlist." if update else
                f"{prefix} does not match the dashboard allowlist.")
    return body


def _domain_scope(request: Request, headers: Headers, mutation: bool, search: bool) -> Scope:
    child = dict(request.scope)
    safe = {"accept-encoding": "identity"}
    if "host" in headers:
        safe["host"] = headers["host"]
    if mutation:
        for name in ("x-artifact-metadata", "x-artifact-expected-revision"):
            if headers.get(name):
                safe[name] = headers[name]
        if request.method != "PATCH":
            safe["x-client-operation-id"] = headers["x-client-operation-id"]
    if search or request.method == "GET":
        safe["x-artifact-access"] = "human-dashboard"
    if request.method == "PATCH" or search:
        safe["content-type"] = "application/json"
    elif request.method in {"POST", "PUT"}:
        safe["content-type"] = "application/octet-stream"
    child["headers"] = Headers(safe).raw
    child["state"] = {**child.get("state", {}), "artifact_browser_admission": _ARTIFACT_ADMITTED}
    return child


def _download_headers(status: int, headers: Headers) -> dict[str, str] | None:
    if not 200 <= status < 300:
        return None
    try:
        length = declared_length(headers, FILE_MAXIMUM)
        if status != 200 or length is None:
            raise ValueError()
    except ValueError:
        raise FileTransportError("Mnemonic's API returned an invalid artifact response.") from None
    return {"Content-Type": "application/octet-stream", "Content-Length": headers["content-length"],
            "Content-Disposition": safe_artifact_disposition(headers.get("content-disposition"))}


def _status(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or value.keys() != {"enabled", "max_bytes", "message"}
            or type(value["enabled"]) is not bool
            or not integer(value["max_bytes"], 0, FILE_MAXIMUM)
            or value["enabled"] != (value["max_bytes"] > 0)
            or not text(value["message"], 1000)):
        raise ValueError("Invalid artifact status")
    maximum = value["max_bytes"]
    return {"enabled": maximum > 0, "max_bytes": maximum,
            "message": DISABLED_MESSAGE if maximum == 0 else
            f"Artifact library enabled. Up to {artifact_size(maximum)} "
            f"({maximum:,} bytes) per file."}


def _response_policy(route: str, method: str, mutation: bool, operation: str) -> FileResponsePolicy:
    def receipt_headers(headers: Headers) -> dict[str, str]:
        result = {}
        if mutation and headers.get("x-client-operation-id") == operation:
            result["X-Client-Operation-ID"] = operation
            if headers.get("x-artifact-operation-replayed") == "true":
                result["X-Artifact-Operation-Replayed"] = "true"
        return result

    def status_headers(status: int, headers: Headers) -> None:
        if status != 200 or "application/json" not in headers.get("content-type", ""):
            raise FileTransportError("Artifact status is unavailable.")

    maximum = 1024 * 1024 if mutation else 6 * 1024 * 1024 if HISTORY.fullmatch(route) else (
        4 * 1024 * 1024
    )
    policy = FileResponsePolicy(
        json_maximum=maximum, failure_detail=_UNREACHABLE,
        redirect_detail="Mnemonic's API returned an unexpected redirect.",
        encoding_detail="Mnemonic's API returned an encoded artifact response.",
        response_headers=receipt_headers,
    )
    if route == "status":
        policy.json_maximum = 16 * 1024
        policy.transform_json = _status
        policy.stream = status_headers
    elif method == "GET" and CONTENT.fullmatch(route):
        policy.stream = _download_headers
    elif SEARCH.fullmatch(route):
        policy.failure_detail = "Artifact search could not be completed. Try the search again."
    return policy


class ArtifactBrowserMiddleware:
    def __init__(
        self, app: ASGIApp, *, pending_tasks: set[asyncio.Task[None]] | None = None,
    ) -> None:
        self.app = app
        self.pending_tasks = pending_tasks if pending_tasks is not None else set()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (
            path == ARTIFACT_BROWSER_PREFIX or path.startswith(ARTIFACT_BROWSER_PREFIX + "/")
        ):
            await self.app(scope, receive, send)
            return
        route = path.removeprefix(ARTIFACT_BROWSER_PREFIX + "/")
        request = Request(scope, receive)
        try:
            await self._request(request, route, receive, send)
        except _Rejection as exc:
            await exc.response(scope, receive, send)
        except ClientDisconnect:
            return

    async def _request(self, request: Request, route: str, receive: Receive, send: Send) -> None:
        headers = combined_headers(request.headers)
        _admit(request, route, headers)
        search = SEARCH.fullmatch(route) is not None and request.method == "POST"
        update = request.method == "PATCH"
        mutation = request.method != "GET" and not search
        if (search or update) and any(name in headers for name in (
            "x-artifact-metadata", "x-artifact-expected-revision"
        )):
            _reject(400, "JSON artifact requests do not accept binary metadata headers.")
        metadata = _metadata(headers, request.method, mutation)
        operation = _controls(request, headers, mutation)
        upload = mutation and request.method in {"POST", "PUT"}
        _body_headers(headers, metadata, upload)
        body: bytes | None = None if upload else b""
        if update or search:
            try:
                async with asyncio.timeout(300):
                    body = await _json_body(request, headers, operation, update)
            except TimeoutError:
                _reject(502, _UNREACHABLE)
        child = _domain_scope(request, headers, mutation, search)
        policy = _response_policy(route, request.method, mutation, operation)
        await execute_file_request(
            self.app, child, receive, send, policy=policy, body=body,
            upload_maximum=FILE_MAXIMUM if upload else None, timeout=300,
            mutation=mutation, pending_tasks=self.pending_tasks,
        )
