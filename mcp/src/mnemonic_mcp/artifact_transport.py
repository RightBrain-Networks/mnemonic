"""Bounded binary HTTP transport. No server-local paths or arbitrary URLs accepted."""

import asyncio
import base64
import binascii
import hashlib
import json
from typing import cast
from uuid import UUID

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from .api import (
    MnemonicAPI,
    ResponseValidator,
    TransportEffect,
    _invalid_response_constant,
    _parse_success_response,
    _raise_for_response_error,
    _raise_request_error,
    _raise_unexpected_response,
    _response_object_without_duplicate_keys,
)
from .artifact_approval import approval_metadata
from .artifact_models import MCP_ARTIFACT_MAX_BYTES, ArtifactRead
from .transport import declared_oversize_values, identity_content_encoding_values


def decode_content(content_base64: str) -> bytes:
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error):
        raise ToolError("Artifact content must be canonical base64.") from None
    if len(content) > MCP_ARTIFACT_MAX_BYTES:
        raise ToolError("MCP artifact transfers are limited to 64 MiB; use the binary REST API.")
    if base64.b64encode(content).decode("ascii") != content_base64:
        raise ToolError("Artifact content must be canonical base64.")
    return content


async def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    if not identity_content_encoding_values(response.headers.get_list("content-encoding")):
        raise ValueError("Encoded artifact response")
    if declared_oversize_values(response.headers.get_list("content-length"), max_bytes):
        raise ValueError("Oversized artifact response")
    body = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
        if len(chunk) > max_bytes - len(body):
            raise ValueError("Oversized artifact response")
        body.extend(chunk)
    return bytes(body)


async def _request(
    api: MnemonicAPI, method: str, path: str, *,
    headers: dict[str, str], content: bytes | None, max_bytes: int,
    effect: TransportEffect,
) -> httpx.Response:
    try:
        async with asyncio.timeout(120), httpx.AsyncClient(
            base_url=f"{api.settings.api_url.rstrip('/')}/api/v1/",
            headers={"Authorization": f"Bearer {api.settings.api_key}",
                     "Accept-Encoding": "identity"},
            timeout=httpx.Timeout(120, connect=5), follow_redirects=False,
            trust_env=False, transport=api._transport,
        ) as client, client.stream(method, path, headers=headers, content=content) as response:
            body = await _read_bounded(response, max_bytes if response.is_success else 64 * 1024)
            result = httpx.Response(response.status_code, headers=response.headers,
                                    content=body, request=response.request)
    except (httpx.RequestError, TimeoutError, ValueError):
        _raise_request_error(method, effect=effect)
    _raise_for_response_error(result, method, path, semantic_read=False, effect=effect)
    return result


async def mutate_artifact(
    api: MnemonicAPI, method: str, project_id: UUID, *,
    client_operation_id: UUID, metadata: dict[str, object], content: bytes | None,
    artifact_id: UUID | None = None, expected_revision: int | None = None,
) -> ArtifactRead:
    path = f"projects/{project_id}/artifacts"
    if artifact_id is not None:
        path += f"/{artifact_id}" + ("/content" if method == "PUT" else "")
    encoded = json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))
    if len(encoded) > 16 * 1024:
        raise ToolError("Artifact metadata exceeds the 16 KiB header limit.")
    headers = {"Content-Type": "application/octet-stream", "X-Artifact-Metadata": encoded,
               "X-Client-Operation-ID": str(client_operation_id)}
    if expected_revision is not None:
        headers["X-Artifact-Expected-Revision"] = str(expected_revision)
    effect = TransportEffect.RECEIPT_PROTECTED_WRITE
    response = await _request(api, method, path, headers=headers, content=content,
                              max_bytes=64 * 1024, effect=effect)
    result = parse_artifact_mutation_response(response, method, path, client_operation_id)
    _validate_mutation(result, project_id, artifact_id, method, expected_revision, metadata, content)
    return result


def parse_artifact_mutation_response(
    response: httpx.Response, method: str, path: str, client_operation_id: UUID,
    response_validator: ResponseValidator | None = None,
) -> ArtifactRead:
    effect = TransportEffect.RECEIPT_PROTECTED_WRITE
    if response.status_code != (201 if method == "POST" else 200) or (
        response.headers.get_list("X-Client-Operation-ID") != [str(client_operation_id)]
    ):
        _raise_unexpected_response(method, path, effect=effect)
    replay_values = response.headers.get_list("X-Artifact-Operation-Replayed")
    if replay_values not in ([], ["true"], ["false"]):
        _raise_unexpected_response(method, path, effect=effect)
    if replay_values == ["true"] and method != "PATCH":
        response = _historical_replay_response(response, method, path)
    return cast(ArtifactRead, _parse_success_response(
        response, ArtifactRead, method, path, effect=effect,
        response_validator=response_validator, strict_wire_response=False,
    ))


def _historical_replay_response(
    response: httpx.Response, method: str, path: str,
) -> httpx.Response:
    try:
        body = json.loads(
            response.content.decode("utf-8", errors="strict"),
            object_pairs_hook=_response_object_without_duplicate_keys,
            parse_constant=_invalid_response_constant,
        )
        if not isinstance(body, dict):
            raise TypeError("Historical artifact receipt must be an object")
        # Only permanent pre-0029 receipts may omit these fields. Never infer them
        # for fresh execution or erase a supplied value that fails strict validation.
        body.setdefault("sensitive", False)
        body.setdefault("related_artifact_ids", [])
    except (ValueError, TypeError, RecursionError):
        _raise_unexpected_response(method, path, effect=TransportEffect.RECEIPT_PROTECTED_WRITE)
    return httpx.Response(response.status_code, headers=response.headers,
                          content=json.dumps(body).encode(), request=response.request)


async def update_artifact_metadata(
    api: MnemonicAPI, project_id: UUID, artifact_id: UUID, client_operation_id: UUID,
    body: dict[str, object], response_validator: ResponseValidator,
) -> ArtifactRead:
    path = f"projects/{project_id}/artifacts/{artifact_id}"
    headers = {"Content-Type": "application/json"}
    response = await _request(
        api, "PATCH", path, headers=headers, content=json.dumps(body, ensure_ascii=True).encode(),
        max_bytes=64 * 1024, effect=TransportEffect.RECEIPT_PROTECTED_WRITE,
    )
    return parse_artifact_mutation_response(
        response, "PATCH", path, client_operation_id, response_validator,
    )


def _validate_mutation(
    result: ArtifactRead, project_id: UUID, artifact_id: UUID | None,
    method: str, expected_revision: int | None, metadata: dict[str, object], content: bytes | None,
) -> None:
    valid = result.project_id == project_id and (artifact_id is None or result.id == artifact_id)
    if content is not None:
        revision = 1 if expected_revision is None else expected_revision + 1
        valid = valid and result.revision == revision and result.filename == metadata["filename"]
        valid = valid and result.size_bytes == len(content) and result.sha256 == (
            hashlib.sha256(content).hexdigest()
        ) and result.deleted_at is None and result.content_available
        valid = valid and _metadata_matches(result, metadata, created=method == "POST")
    if method == "DELETE":
        valid = valid and result.deleted_at is not None and not result.content_available and (
            result.revision == expected_revision
        )
    if not valid:
        _raise_unexpected_response(method, "artifacts", effect=TransportEffect.RECEIPT_PROTECTED_WRITE)


def _metadata_matches(result: ArtifactRead, metadata: dict[str, object], *, created: bool) -> bool:
    if created and (result.created_by_agent_session_id != metadata["agent_session_id"] or (
        result.created_by_client != metadata["actor_client"]
    )):
        return False
    if "description" in metadata and result.description != metadata["description"]:
        return False
    if "work_item_id" in metadata and str(result.originating_work_item_id) != metadata["work_item_id"]:
        return False
    requested_links = cast(list[str], metadata.get("related_work_item_ids", []))
    stored_links = {str(value) for value in result.related_work_item_ids}
    if result.originating_work_item_id is not None:
        stored_links.add(str(result.originating_work_item_id))
    return (set(requested_links) <= stored_links
            and _additional_metadata_matches(result, metadata))


def _additional_metadata_matches(result: ArtifactRead, metadata: dict[str, object]) -> bool:
    requested = cast(list[str], metadata.get("related_artifact_ids", []))
    return (set(requested) <= {str(value) for value in result.related_artifact_ids}
            and ("sensitive" not in metadata or result.sensitive == metadata["sensitive"]))


async def download_content(
    api: MnemonicAPI, artifact: ArtifactRead, *, agent_session_id: str, actor_client: str,
    approval_token: str | None = None, human_approved: bool = False,
) -> bytes:
    if not artifact.content_available or artifact.deleted_at is not None:
        raise ToolError("Artifact content is unavailable; only retained metadata can be read.")
    if artifact.size_bytes > MCP_ARTIFACT_MAX_BYTES:
        raise ToolError("MCP artifact transfers are limited to 64 MiB; use the binary REST API.")
    path = (f"projects/{artifact.project_id}/artifacts/{artifact.id}/content"
            f"?expected_revision={artifact.revision}")
    metadata = approval_metadata(agent_session_id, actor_client, approval_token, human_approved)
    encoded = json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))
    response = await _request(api, "GET", path, headers={"X-Artifact-Metadata": encoded}, content=None,
                              max_bytes=MCP_ARTIFACT_MAX_BYTES, effect=TransportEffect.SAFE_READ)
    if response.status_code != 200 or len(response.content) != artifact.size_bytes or (
        hashlib.sha256(response.content).hexdigest() != artifact.sha256
    ):
        raise ToolError("Artifact bytes did not match the requested revision. Read its metadata again.")
    return response.content
