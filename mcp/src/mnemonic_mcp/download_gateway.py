"""Bounded raw-byte egress; the API durably consumes each capability once."""

import asyncio
import hashlib
import json
import re
import tempfile
from contextlib import ExitStack
from typing import BinaryIO

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from .api import MnemonicAPI, _response_object_without_duplicate_keys
from .artifact_transport import _read_bounded
from .download_grants import DOWNLOAD_SCHEME, DownloadIntent
from .upload_errors import rejection, upstream_failure
from .upload_gateway import UploadRejected, _chunks


def _one(request: Request, name: str) -> str:
    values = request.headers.getlist(name)
    if len(values) != 1:
        raise UploadRejected(400, "artifact_download_headers_invalid")
    return values[0]


def _intent(request: Request) -> tuple[DownloadIntent, str]:
    scheme, _, token = _one(request, "authorization").partition(" ")
    raw = _one(request, "x-artifact-download-intent")
    if scheme.casefold() != DOWNLOAD_SCHEME.casefold() or not re.fullmatch(
        r"[A-Za-z0-9_-]{43}", token
    ) or len(raw) > 2048:
        raise UploadRejected(401, "artifact_download_grant_invalid")
    if request.url.query or request.headers.get("content-length", "0") != "0" or any(
        field in request.headers for field in ("transfer-encoding", "content-encoding")
    ):
        raise UploadRejected(400, "artifact_download_headers_invalid")
    try:
        return DownloadIntent.model_validate(json.loads(
            raw, object_pairs_hook=_response_object_without_duplicate_keys,
        )), token
    except ValueError, TypeError, RecursionError:
        raise UploadRejected(400, "artifact_download_headers_invalid") from None


async def _receive(response: httpx.Response, content: BinaryIO, intent: DownloadIntent) -> None:
    expected = {"content-type": "application/octet-stream", "content-length": str(intent.size_bytes),
                "x-artifact-revision": str(intent.revision), "etag": f'"{intent.sha256}"'}
    if any(response.headers.get_list(name) != [value] for name, value in expected.items()) or (
        response.headers.get("content-encoding", "identity") != "identity"
    ):
        raise UploadRejected(502, "artifact_download_integrity_error")
    size, digest = 0, hashlib.sha256()
    async for chunk in response.aiter_raw(64 * 1024):
        size += len(chunk)
        if size > intent.size_bytes:
            raise UploadRejected(502, "artifact_download_integrity_error")
        digest.update(chunk)
        await asyncio.to_thread(content.write, chunk)
    if size != intent.size_bytes or digest.hexdigest() != intent.sha256:
        raise UploadRejected(502, "artifact_download_integrity_error")
    content.seek(0)


async def _fetch(api: MnemonicAPI, intent: DownloadIntent, token: str, content: BinaryIO):
    path = f"projects/{intent.project_id}/artifacts/{intent.artifact_id}/content"
    async with (
        httpx.AsyncClient(
            base_url=f"{api.settings.api_url.rstrip('/')}/api/v1/",
            headers={"Authorization": f"Bearer {api.settings.api_key}",
                     "Accept-Encoding": "identity", "X-Artifact-Download-Grant": token},
            timeout=httpx.Timeout(120, connect=5), follow_redirects=False,
            trust_env=False, transport=api._transport,
        ) as client,
        client.stream("GET", path, params={"expected_revision": intent.revision}) as response,
    ):
        if response.status_code != 200:
            body = await _read_bounded(response, 64 * 1024)
            return upstream_failure(httpx.Response(
                response.status_code, headers=response.headers, content=body,
            ), fallback_code="artifact_download_failed")
        await _receive(response, content, intent)
    return None


class DownloadGateway:
    def __init__(self, api: MnemonicAPI):
        self.api = api
        self.slots = asyncio.Semaphore(2)

    async def __call__(self, request: Request) -> Response:
        try:
            intent, token = _intent(request)
            async with asyncio.timeout(0.1):
                await self.slots.acquire()
        except UploadRejected as exc:
            return rejection(exc.status, exc.code)
        except TimeoutError:
            return rejection(503, "artifact_download_busy")
        try:
            with ExitStack() as staging:
                staging.callback(self.slots.release)
                content = staging.enter_context(tempfile.TemporaryFile())
                return await self._download(intent, token, content, staging)
        except OSError:
            return rejection(502, "artifact_download_failed")

    async def _download(
        self, intent: DownloadIntent, token: str, content: BinaryIO, staging: ExitStack,
    ) -> Response:
        try:
            async with asyncio.timeout(300):
                failure = await _fetch(self.api, intent, token, content)
            if failure is not None:
                return failure
            response = DownloadResponse(
                _chunks(content), media_type="application/octet-stream",
                headers={"Content-Length": str(intent.size_bytes), "Cache-Control": "no-store",
                         "X-Artifact-Revision": str(intent.revision), "ETag": f'"{intent.sha256}"',
                         "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
                cleanup=staging.pop_all(),
            )
            return response
        except UploadRejected as exc:
            return rejection(exc.status, exc.code)
        except httpx.RequestError, TimeoutError, OSError, ValueError, ToolError:
            return rejection(502, "artifact_download_failed")


class DownloadResponse(StreamingResponse):
    """Release admission and disk on completion, disconnect, timeout or cancellation."""

    def __init__(self, *args, cleanup: ExitStack, **kwargs):
        super().__init__(*args, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            async with asyncio.timeout(300):
                await super().__call__(scope, receive, send)
        finally:
            self.cleanup.close()
