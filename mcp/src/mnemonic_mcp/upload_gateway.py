"""Bounded raw-byte ingress using an operation capability on the MCP endpoint."""

import asyncio
import hashlib
import hmac
import json
import tempfile
from collections.abc import AsyncIterator
from typing import BinaryIO
from uuid import uuid5

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response

from .api import MnemonicAPI, _response_object_without_duplicate_keys
from .artifact_transport import _metadata_matches, _read_bounded, parse_artifact_mutation_response
from .upload_errors import rejection, staging_failure, upstream_failure
from .upload_grants import INTENT_MAX_BYTES, UPLOAD_SCHEME, UploadIntent, canonical, verify_token

CHUNK_BYTES = 64 * 1024
UPLOAD_SECONDS = 300
UPLOAD_SLOTS = 2


class UploadRejected(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code


def _one(request: Request, header: str) -> str:
    values = request.headers.getlist(header)
    if len(values) != 1:
        raise UploadRejected(400, "artifact_upload_headers_invalid")
    return values[0]


def _intent(request: Request, api: MnemonicAPI) -> tuple[UploadIntent, str]:
    authorization = _one(request, "authorization")
    scheme, _, token = authorization.partition(" ")
    raw = _one(request, "x-artifact-upload-intent")
    if len(raw) > INTENT_MAX_BYTES or scheme.casefold() != UPLOAD_SCHEME.casefold():
        raise UploadRejected(401, "artifact_upload_grant_invalid")
    try:
        body = json.loads(raw, object_pairs_hook=_response_object_without_duplicate_keys)
        intent = UploadIntent.model_validate(body)
    except ValueError, TypeError, RecursionError:
        raise UploadRejected(401, "artifact_upload_grant_invalid") from None
    if not verify_token(token, intent, api.settings.api_key):
        raise UploadRejected(401, "artifact_upload_grant_invalid")
    return intent, token


def _transport(request: Request, intent: UploadIntent) -> None:
    forbidden = (
        "transfer-encoding",
        "content-encoding",
        "x-artifact-metadata",
        "x-client-operation-id",
        "x-artifact-expected-revision",
    )
    if request.url.query or any(header in request.headers for header in forbidden):
        raise UploadRejected(400, "artifact_upload_headers_invalid")
    if _one(request, "content-type") != "application/octet-stream":
        raise UploadRejected(415, "artifact_upload_content_type_invalid")
    if _one(request, "content-length") != str(intent.size_bytes):
        raise UploadRejected(400, "artifact_upload_length_invalid")


async def _receive(request: Request, content: BinaryIO, intent: UploadIntent) -> None:
    size, digest = 0, hashlib.sha256()
    async for chunk in request.stream():
        size += len(chunk)
        if size > intent.size_bytes:
            raise UploadRejected(413, "artifact_upload_length_invalid")
        digest.update(chunk)
        await asyncio.to_thread(content.write, chunk)
    if size != intent.size_bytes or not hmac.compare_digest(digest.hexdigest(), intent.sha256):
        raise UploadRejected(422, "artifact_upload_content_mismatch")
    content.seek(0)


async def _chunks(content: BinaryIO) -> AsyncIterator[bytes]:
    while chunk := await asyncio.to_thread(content.read, CHUNK_BYTES):
        yield chunk


def _upstream(intent: UploadIntent) -> tuple[str, str, dict[str, str]]:
    path = f"projects/{intent.project_id}/artifacts"
    method = "POST"
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(intent.size_bytes),
        "X-Client-Operation-ID": str(intent.client_operation_id),
        "X-Artifact-Metadata": canonical(intent.metadata),
    }
    if intent.artifact_id is not None:
        method = "PUT"
        path += f"/{intent.artifact_id}/content"
        headers["X-Artifact-Expected-Revision"] = str(intent.expected_revision)
    return method, path, headers


def _verify_receipt(response: httpx.Response, intent: UploadIntent, method: str, path: str) -> None:
    result = parse_artifact_mutation_response(response, method, path, intent.client_operation_id)
    target = intent.artifact_id or uuid5(
        intent.project_id, f"artifact:{intent.client_operation_id}"
    )
    metadata = intent.metadata.model_dump(mode="json", exclude_unset=True)
    valid = (
        result.id == target
        and result.project_id == intent.project_id
        and result.revision == (intent.expected_revision or 0) + 1
        and result.size_bytes == intent.size_bytes
        and result.sha256 == intent.sha256
        and result.filename == intent.metadata.filename
        and result.content_available
        and result.deleted_at is None
        and _metadata_matches(result, metadata, created=method == "POST")
    )
    if not valid:
        raise UploadRejected(502, "artifact_upload_outcome_unknown")


async def _forward(api: MnemonicAPI, content: BinaryIO, intent: UploadIntent) -> Response:
    method, path, headers = _upstream(intent)
    async with (
        httpx.AsyncClient(
            base_url=f"{api.settings.api_url.rstrip('/')}/api/v1/",
            headers={
                "Authorization": f"Bearer {api.settings.api_key}",
                "Accept-Encoding": "identity",
            },
            timeout=httpx.Timeout(120, connect=5),
            follow_redirects=False,
            trust_env=False,
            transport=api._transport,
        ) as client,
        client.stream(method, path, headers=headers, content=_chunks(content)) as response,
    ):
        body = await _read_bounded(response, 64 * 1024)
        buffered = httpx.Response(
            response.status_code, headers=response.headers, content=body, request=response.request
        )
    if buffered.is_success:
        _verify_receipt(buffered, intent, method, path)
    else:
        return upstream_failure(buffered)
    outgoing = {"Cache-Control": "no-store", "Content-Type": "application/json"}
    for name in ("X-Client-Operation-ID", "X-Artifact-Operation-Replayed"):
        if name in buffered.headers:
            outgoing[name] = buffered.headers[name]
    return Response(body, status_code=buffered.status_code, headers=outgoing)


class UploadGateway:
    def __init__(self, api: MnemonicAPI):
        self.api = api
        self.slots = asyncio.Semaphore(UPLOAD_SLOTS)

    async def __call__(self, request: Request) -> Response:
        try:
            intent, token = _intent(request, self.api)
            _transport(request, intent)
            return await self._admitted(request, intent, token)
        except UploadRejected as exc:
            return rejection(exc.status, exc.code)
        except httpx.RequestError, TimeoutError, ValueError, ToolError, OSError, ClientDisconnect:
            # A lost upstream response never proves the mutation was not committed.
            return rejection(502, "artifact_upload_outcome_unknown")

    async def _admitted(self, request: Request, intent: UploadIntent, token: str) -> Response:
        try:
            async with asyncio.timeout(0.1):
                await self.slots.acquire()
        except TimeoutError:
            raise UploadRejected(503, "artifact_upload_busy") from None
        try:
            async with asyncio.timeout(UPLOAD_SECONDS):
                return await self._transfer(request, intent, token)
        finally:
            self.slots.release()

    async def _transfer(self, request: Request, intent: UploadIntent, token: str) -> Response:
        forwarding = False
        try:
            with tempfile.TemporaryFile() as content:
                await _receive(request, content, intent)
                if not verify_token(token, intent, self.api.settings.api_key):
                    raise UploadRejected(401, "artifact_upload_grant_invalid")
                forwarding = True
                return await _forward(self.api, content, intent)
        except OSError as error:
            if forwarding:
                # Cleanup/read errors after forwarding cannot establish non-commit.
                raise
            # Buffered cleanup may fail again after a staging write/flush failure.
            return staging_failure(error)
