"""Authenticated raw-byte transfer; bounded metadata travels in an ASCII JSON header."""

import asyncio
import errno
import hashlib
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from typing import Annotated, Any, BinaryIO, cast
from urllib.parse import quote
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from mnemonic_api.application.artifact_policy import artifact_status, require_artifacts_enabled
from mnemonic_api.application.state import settings_of
from mnemonic_api.artifact_access_schemas import ArtifactAccessRequest
from mnemonic_api.artifact_schemas import (
    ArtifactActor,
    ArtifactHistory,
    ArtifactHistoryQuery,
    ArtifactLibraryStatus,
    ArtifactListQuery,
    ArtifactPage,
    ArtifactRead,
    ArtifactTextQuery,
    ArtifactTextRead,
    ArtifactUpdateMetadata,
    ArtifactUploadMetadata,
)
from mnemonic_api.artifact_search_schemas import ArtifactSearchPage, ArtifactSearchRequest
from mnemonic_api.artifact_storage import (
    ArtifactContentUnavailable,
    ArtifactStorage,
    ArtifactStorageOwnerMismatch,
    ArtifactTooLarge,
    InvalidArtifactFilename,
    StagedArtifact,
    UnsafeArtifactPath,
    validate_filename,
)
from mnemonic_api.database import Database
from mnemonic_api.errors import (
    ApplicationError,
    client_operation_conflict,
    client_operation_secret_echo,
)
from mnemonic_api.schemas import APIModel
from mnemonic_api.services.artifact_search import ArtifactSearchIndex, search_artifact_contents
from mnemonic_api.services.artifacts import (
    ArtifactMutation,
    artifact_history,
    artifact_read,
    list_artifacts,
    mutate_artifact,
    open_artifact,
    prepare_upload_replay,
    read_artifact_text,
    recover_artifact,
    recover_project_artifacts,
    replay_artifact_upload,
    require_artifact,
)
from mnemonic_api.services.client_operations import reject_client_operation_secret_echo

router = APIRouter(dependencies=[Depends(require_artifacts_enabled)])
status_router = APIRouter()

_APPROVAL_RESPONSE: dict[int | str, dict[str, Any]] = {
    428: {"description": (
        "STOP: explicit human approval required for sensitive content. The response includes "
        "a five-minute, single-use approval_token bound to artifact, revision, caller and "
        "request. Ask the actual human and wait for an affirmative reply, then repeat the "
        "exact request with approval_token and human_approved=true. Never auto-approve/retry."
    )},
}

_ACCESS_HEADERS = {"parameters": [{
    "name": "X-Artifact-Metadata", "in": "header", "required": False,
    "schema": {"type": "string", "maxLength": 16384},
    "description": (
        "ASCII JSON: agent_session_id, actor_client, and (only after explicit human approval) "
        "approval_token and strict boolean human_approved=true. Never put tokens in URLs."
    ),
}]}


@status_router.get("/artifacts/status", response_model=ArtifactLibraryStatus)
def get_artifact_status(request: Request, response: Response) -> ArtifactLibraryStatus:
    response.headers["Cache-Control"] = "no-store"
    return artifact_status(request)


class _ArtifactMetadataEnvelope(APIModel):
    client_operation_id: UUID
    metadata: dict[str, Any]


def _reject_metadata_echo(
    request: Request, metadata: ArtifactActor, operation_id: UUID | None = None
) -> None:
    key = settings_of(request).api_key.get_secret_value()
    values = metadata.model_dump(mode="json", exclude={"client_operation_id"})
    token = values.get("approval_token")
    if token and token in {metadata.actor_client, metadata.agent_session_id}:
        raise client_operation_secret_echo()
    if operation_id is None:
        if any(value == key for value in values.values()):
            raise client_operation_secret_echo()
        return
    secrets = {key}
    try:
        secrets.add(str(UUID(key)))
    except ValueError:
        pass
    reject_client_operation_secret_echo(
        _ArtifactMetadataEnvelope(client_operation_id=operation_id, metadata=values),
        known_secret_values=secrets,
    )


def _write_contract(*, content: bool, replacement: bool = False) -> dict:
    parameters = [
        {
            "name": "X-Client-Operation-ID",
            "in": "header",
            "required": True,
            "schema": {"type": "string", "format": "uuid"},
            "description": (
                "Permanent artifact receipt identity. Retry unchanged requests with this ID."
            ),
        },
        {
            "name": "X-Artifact-Metadata",
            "in": "header",
            "required": content,
            "schema": {"type": "string", "maxLength": 16384},
            "description": (
                "ASCII JSON object; encode Unicode with JSON escapes. Fields: filename "
                "(required for upload/replace, immutable original safe basename), "
                "description (optional, max4000), "
                "agent_session_id (optional, max200), actor_client (optional, max80), work_item_id "
                "(optional originating UUID, immutable), related_work_item_ids "
                "(optional UUID array, "
                "max50; adds links). Delete accepts only actor_client and agent_session_id."
            ),
        },
    ]
    if replacement or not content:
        parameters.append(
            {
                "name": "X-Artifact-Expected-Revision",
                "in": "header",
                "required": True,
                "schema": {"type": "integer", "minimum": 1},
            }
        )
    result: dict = {"parameters": parameters}
    if content:
        result["requestBody"] = {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
            "description": (
                "Raw file bytes, bounded by MNEMONIC_ARTIFACT_MAX_BYTES (default 64 MiB; "
                "zero disables all artifact operations). GET /api/v1/artifacts/status "
                "reports availability and the configured byte maximum."
            ),
        }
    return result


def storage_of(request: Request) -> ArtifactStorage:
    return cast(ArtifactStorage, request.app.state.artifact_storage)


def _storage_failure_cause(error: OSError | UnsafeArtifactPath) -> str:
    if isinstance(error, ArtifactStorageOwnerMismatch):
        return "storage_owner_mismatch"
    if isinstance(error, (ArtifactContentUnavailable, UnsafeArtifactPath)):
        return "storage_integrity"
    return {
        errno.EACCES: "storage_permission_denied",
        errno.EPERM: "storage_permission_denied",
        errno.ENOSPC: "storage_full",
        errno.EDQUOT: "storage_full",
        errno.EROFS: "storage_read_only",
    }.get(error.errno, "storage_unavailable")


@contextmanager
def storage_errors(request: Request, *, attempt_not_committed: bool = False) -> Iterator[None]:
    try:
        yield
    except ArtifactTooLarge:
        maximum = settings_of(request).artifact_max_bytes
        raise ApplicationError(
            413,
            "artifact_too_large",
            f"Artifact exceeds the configured maximum upload size of {maximum} bytes "
            "(MNEMONIC_ARTIFACT_MAX_BYTES).",
            context={"max_bytes": maximum},
        ) from None
    except InvalidArtifactFilename:
        raise ApplicationError(
            422, "artifact_filename_unsafe", "Provide a safe original basename."
        ) from None
    except (OSError, UnsafeArtifactPath) as error:
        raise ApplicationError(
            503, "artifact_storage_unavailable", "Artifact content storage is unavailable.",
            context={
                "cause": _storage_failure_cause(error),
                "attempt_not_committed": attempt_not_committed,
            },
        ) from None


def _single_header(request: Request, name: str, *, required: bool = False) -> str | None:
    values = request.headers.getlist(name)
    if len(values) > 1 or (required and not values):
        raise ApplicationError(
            422, "artifact_header_invalid", "Required artifact header is invalid."
        )
    return values[0] if values else None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Repeated metadata keys are forbidden")
        result[key] = value
    return result


def metadata_of[Metadata: ArtifactActor](
    request: Request, model: type[Metadata], *, required: bool = False
) -> Metadata:
    raw = _single_header(request, "x-artifact-metadata", required=required) or "{}"
    if len(raw) > 16_384 or not raw.isascii():
        raise ApplicationError(
            422, "artifact_metadata_invalid", "Metadata header exceeds its bounds."
        )
    try:
        return model.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
    except ValueError, ValidationError, RecursionError:
        raise ApplicationError(
            422, "artifact_metadata_invalid", "Artifact metadata is invalid."
        ) from None


def operation_id_of(request: Request) -> UUID:
    raw = _single_header(request, "x-client-operation-id", required=True)
    try:
        return UUID(raw or "")
    except ValueError:
        raise ApplicationError(
            422, "artifact_operation_id_invalid", "Provide an operation UUID."
        ) from None


def expected_revision_of(request: Request) -> int:
    raw = _single_header(request, "x-artifact-expected-revision", required=True) or ""
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 10 or int(raw) < 1:
        raise ApplicationError(422, "artifact_revision_invalid", "Provide a positive revision.")
    return int(raw)


def _mutation_transport(request: Request) -> None:
    if request.query_params:
        raise ApplicationError(
            422, "artifact_query_forbidden", "Mutation metadata belongs in headers."
        )
    if (_single_header(request, "content-encoding") or "identity").lower() != "identity":
        raise ApplicationError(415, "artifact_encoding_unsupported", "Upload unencoded file bytes.")


def _check_length(request: Request) -> None:
    length = _single_header(request, "content-length")
    if length is None:
        return
    if not length.isascii() or not length.isdecimal() or len(length) > 12:
        raise ApplicationError(422, "artifact_length_invalid", "Content length is invalid.")
    if int(length) > settings_of(request).artifact_max_bytes:
        raise ArtifactTooLarge()


@asynccontextmanager
async def _upload_budget(request: Request) -> AsyncIterator[None]:
    semaphore = cast(asyncio.Semaphore, request.app.state.artifact_upload_slots)
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=0.25)
    except TimeoutError:
        raise ApplicationError(
            503, "artifact_upload_busy", "Artifact upload capacity is busy."
        ) from None
    try:
        async with asyncio.timeout(180):
            yield
    except TimeoutError:
        raise ApplicationError(
            408, "artifact_upload_timeout", "Artifact upload timed out."
        ) from None
    finally:
        semaphore.release()


async def _stage_upload(
    request: Request, project_id: UUID, artifact_id: UUID, filename: str
) -> StagedArtifact:
    async with _upload_budget(request):
        return await storage_of(request).stage_async(
            project_id, artifact_id, filename, request.stream()
        )


async def _verify_replay_bytes(request: Request, expected: tuple[str, int]) -> None:
    digest = hashlib.sha256()
    size = 0
    async with _upload_budget(request):
        async for chunk in request.stream():
            size += len(chunk)
            if size > expected[1]:
                raise client_operation_conflict()
            await run_in_threadpool(digest.update, chunk)
    if size != expected[1] or digest.hexdigest() != expected[0]:
        raise client_operation_conflict()


def _mutation_response(
    request: Request, database: Database, mutation: ArtifactMutation, replay_only: bool = False
) -> JSONResponse:
    with storage_errors(request):
        execute = replay_artifact_upload if replay_only else mutate_artifact
        body, replayed = execute(database, storage_of(request), mutation)
    return JSONResponse(
        body,
        status_code=201 if mutation.kind == "upload" else 200,
        headers={
            "X-Client-Operation-ID": str(mutation.client_operation_id),
            "X-Artifact-Operation-Replayed": str(replayed).lower(),
            "Cache-Control": "no-store",
        },
    )


async def _upload(
    request: Request, database: Database, project_id: UUID, artifact_id: UUID | None
) -> JSONResponse:
    _mutation_transport(request)
    metadata = metadata_of(request, ArtifactUploadMetadata, required=True)
    operation_id = operation_id_of(request)
    _reject_metadata_echo(request, metadata, operation_id)
    expected = expected_revision_of(request) if artifact_id is not None else None
    target = artifact_id or uuid5(project_id, f"artifact:{operation_id}")
    mutation = ArtifactMutation(
        project_id=project_id,
        artifact_id=target,
        client_operation_id=operation_id,
        kind="replace" if artifact_id is not None else "upload",
        metadata=metadata,
        expected_revision=expected,
    )
    replay = await run_in_threadpool(prepare_upload_replay, database, mutation)
    # Release the read transaction before waiting for a client to send bytes.
    await run_in_threadpool(database.rollback)
    if replay is not None:
        await _verify_replay_bytes(request, replay)
        mutation = replace(mutation, content_sha256=replay[0], content_size_bytes=replay[1])
        return await run_in_threadpool(_mutation_response, request, database, mutation, True)
    # This invocation has not entered mutation execution. The same operation UUID
    # may already have a durable intent or receipt from another concurrent attempt.
    with storage_errors(request, attempt_not_committed=True):
        validate_filename(metadata.filename)
        _check_length(request)
        staged = await _stage_upload(request, project_id, target, metadata.filename)
    mutation = replace(mutation, staged=staged)
    return await run_in_threadpool(_mutation_response, request, database, mutation)


@router.post(
    "/projects/{project_id}/artifacts",
    response_model=ArtifactRead,
    status_code=201,
    openapi_extra=_write_contract(content=True),
)
async def upload_artifact(project_id: UUID, request: Request, database: Database) -> JSONResponse:
    return await _upload(request, database, project_id, None)


@router.put(
    "/projects/{project_id}/artifacts/{artifact_id}/content",
    response_model=ArtifactRead,
    openapi_extra=_write_contract(content=True, replacement=True),
)
async def replace_artifact(
    project_id: UUID, artifact_id: UUID, request: Request, database: Database
) -> JSONResponse:
    return await _upload(request, database, project_id, artifact_id)


@router.delete(
    "/projects/{project_id}/artifacts/{artifact_id}",
    response_model=ArtifactRead,
    openapi_extra=_write_contract(content=False),
)
async def delete_artifact(
    project_id: UUID, artifact_id: UUID, request: Request, database: Database
) -> JSONResponse:
    _mutation_transport(request)
    mutation = ArtifactMutation(
        project_id=project_id,
        artifact_id=artifact_id,
        client_operation_id=operation_id_of(request),
        kind="delete",
        metadata=metadata_of(request, ArtifactActor),
        expected_revision=expected_revision_of(request),
    )
    _reject_metadata_echo(request, mutation.metadata, mutation.client_operation_id)
    await _reject_delete_body(request)
    return await run_in_threadpool(_mutation_response, request, database, mutation)


async def _reject_delete_body(request: Request) -> None:
    if _single_header(request, "content-length") not in {None, "0"}:
        raise ApplicationError(422, "artifact_body_forbidden", "Delete does not accept a body.")
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if chunk:
                    raise ApplicationError(
                        422, "artifact_body_forbidden", "Delete does not accept a body."
                    )
    except TimeoutError:
        raise ApplicationError(
            408, "artifact_upload_timeout", "Delete body validation timed out."
        ) from None


@router.get("/projects/{project_id}/artifacts", response_model=ArtifactPage[ArtifactRead])
def get_artifacts(
    project_id: UUID,
    request: Request,
    database: Database,
    filters: Annotated[ArtifactListQuery, Query()],
) -> ArtifactPage[ArtifactRead]:
    with storage_errors(request):
        recover_project_artifacts(database, storage_of(request), project_id)
    return list_artifacts(database, project_id, filters)


@router.post(
    "/projects/{project_id}/artifacts/search-content",
    response_model=ArtifactSearchPage,
    responses=_APPROVAL_RESPONSE,
    openapi_extra={
        "x-mnemonic-effect": "safe_read",
        "requestBody": {
            "required": True,
            "description": (
                "Bounded JSON (4096 bytes). Literal case/accent-insensitive terms, all required. "
                "Metadata only by default; fulltext=true also searches extracted current content. "
                "No wildcards, operators, parser configuration or operation UUID."
            ),
            "content": {"application/json": {"schema": ArtifactSearchRequest.model_json_schema()}},
        },
    },
)
async def search_contents(
    project_id: UUID, request: Request, response: Response, database: Database,
) -> ArtifactSearchPage:
    payload = await _search_payload(request)
    _reject_metadata_echo(request, payload)
    response.headers["Cache-Control"] = "no-store"

    def search() -> ArtifactSearchPage:
        with storage_errors(request):
            recover_project_artifacts(database, storage_of(request), project_id)
        database.rollback()
        index = cast(ArtifactSearchIndex, request.app.state.artifact_search_index)
        return search_artifact_contents(
            database, project_id, payload, index, human_dashboard=_human_dashboard(request),
        )

    return await run_in_threadpool(search)


async def _search_payload(request: Request) -> ArtifactSearchRequest:
    # Enabled/auth guards run before consuming this bounded body. Safe search
    # accepts neither operation UUIDs nor caller-controlled parser options.
    if request.query_params or request.headers.get("content-encoding", "identity") != "identity":
        raise ApplicationError(422, "artifact_search_invalid", "Use an unencoded JSON body.")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise ApplicationError(415, "artifact_search_invalid", "Use an application/json body.")
    body = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(body) + len(chunk) > 4096:
                    raise ApplicationError(
                        413, "artifact_search_too_large", "Search JSON exceeds 4096 bytes."
                    )
                body.extend(chunk)
    except TimeoutError:
        raise ApplicationError(
            408, "artifact_search_timeout", "Search request timed out."
        ) from None
    try:
        return ArtifactSearchRequest.model_validate(
            json.loads(body, object_pairs_hook=_unique_object)
        )
    except ValueError, RecursionError:
        raise ApplicationError(
            422, "artifact_search_invalid", "Provide valid artifact search parameters."
        ) from None


@router.get("/projects/{project_id}/artifacts/{artifact_id}", response_model=ArtifactRead)
def get_artifact(
    project_id: UUID, artifact_id: UUID, request: Request, database: Database
) -> ArtifactRead:
    with storage_errors(request):
        recover_artifact(database, storage_of(request), project_id, artifact_id)
    return artifact_read(database, require_artifact(database, project_id, artifact_id))


@router.get(
    "/projects/{project_id}/artifacts/{artifact_id}/history", response_model=ArtifactHistory
)
def get_artifact_history(
    project_id: UUID,
    artifact_id: UUID,
    request: Request,
    database: Database,
    filters: Annotated[ArtifactHistoryQuery, Query()],
) -> ArtifactHistory:
    with storage_errors(request):
        recover_artifact(database, storage_of(request), project_id, artifact_id)
    return artifact_history(database, project_id, artifact_id, filters)


@router.get(
    "/projects/{project_id}/artifacts/{artifact_id}/text",
    response_model=ArtifactTextRead,
    responses=_APPROVAL_RESPONSE,
    openapi_extra={"x-mnemonic-effect": "safe_read", **_ACCESS_HEADERS},
)
def get_artifact_text(
    project_id: UUID,
    artifact_id: UUID,
    request: Request,
    response: Response,
    database: Database,
    filters: Annotated[ArtifactTextQuery, Query()],
) -> ArtifactTextRead:
    """Page untrusted current normalized text by Unicode character, pinned to a revision.

    Pending, processing, and failed extractions return null text and counts. A ready
    extraction may be empty or truncated at extraction time; total_chars describes
    the retained normalized text, and next_offset indicates another available page.
    """
    response.headers["Cache-Control"] = "no-store"
    access = metadata_of(request, ArtifactAccessRequest)
    _reject_metadata_echo(request, access)
    with storage_errors(request):
        return read_artifact_text(
            database, storage_of(request), project_id, artifact_id, filters,
            access=access, human_dashboard=_human_dashboard(request),
        )


def _file_chunks(content: BinaryIO) -> Iterator[bytes]:
    try:
        while chunk := content.read(65_536):
            yield chunk
    finally:
        content.close()


@router.get(
    "/projects/{project_id}/artifacts/{artifact_id}/content",
    responses=_APPROVAL_RESPONSE, openapi_extra=_ACCESS_HEADERS,
)
def download_artifact(
    project_id: UUID,
    artifact_id: UUID,
    request: Request,
    database: Database,
    expected_revision: Annotated[int | None, Query(ge=1)] = None,
) -> StreamingResponse:
    access = metadata_of(request, ArtifactAccessRequest)
    _reject_metadata_echo(request, access)
    actor = ArtifactActor(
        agent_session_id=access.agent_session_id, actor_client=access.actor_client,
    )
    with storage_errors(request):
        metadata, content = open_artifact(
            database,
            storage_of(request),
            project_id,
            artifact_id,
            expected_revision,
            actor, access=access, human_dashboard=_human_dashboard(request),
        )
    return StreamingResponse(
        _file_chunks(content),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(metadata.filename, safe='')}"
            ),
            "Content-Length": str(metadata.size_bytes),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "X-Artifact-Revision": str(metadata.revision),
            "ETag": f'"{metadata.sha256}"',
        },
        background=BackgroundTask(content.close),
    )


def _human_dashboard(request: Request) -> bool:
    # An asserted human UI context, deliberately not an authentication mechanism.
    return _single_header(request, "x-artifact-access") == "human-dashboard"


@router.patch("/projects/{project_id}/artifacts/{artifact_id}", response_model=ArtifactRead)
def update_artifact_metadata(
    project_id: UUID, artifact_id: UUID, payload: ArtifactUpdateMetadata,
    request: Request, database: Database,
) -> JSONResponse:
    _reject_metadata_echo(request, payload, payload.client_operation_id)
    mutation = ArtifactMutation(
        project_id=project_id,
        artifact_id=artifact_id,
        client_operation_id=payload.client_operation_id,
        kind="update",
        metadata=payload,
        expected_revision=payload.expected_revision,
    )
    return _mutation_response(request, database, mutation)
