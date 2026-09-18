"""Project-scoped transcript search, normalized text retrieval and index settings."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from mnemonic_api.application.guards import (
    reject_empty_read_request,
    reject_read_body_and_duplicate_query,
)
from mnemonic_api.application.state import settings_of
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import Transcript
from mnemonic_api.services import transcript_imports, transcripts
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.transcript_segments import segment_range
from mnemonic_api.transcript_discovery import discover_transcripts
from mnemonic_api.transcript_health import TranscriptHealthRead, transcript_health
from mnemonic_api.transcript_schemas import (
    TranscriptImportRead,
    TranscriptImportRequest,
    TranscriptPage,
    TranscriptRead,
    TranscriptRebuildRead,
    TranscriptRebuildRequest,
    TranscriptSearch,
    TranscriptSettingsPatch,
    TranscriptSettingsRead,
    TranscriptText,
)

router = APIRouter()
_collection = "/projects/{project_id}/transcripts"


@router.get(_collection, response_model=TranscriptPage,
            dependencies=[Depends(reject_read_body_and_duplicate_query)])
def list_transcripts(project_id: UUID, filters: Annotated[TranscriptSearch, Query()],
                     database: Database, request: Request) -> TranscriptPage:
    begin_coherent_read(database)
    return transcripts.list_transcripts(
        database, project_id, filters, request.app.state.transcript_search_index,
        maximum_content_bytes=settings_of(request).transcript_search_max_bytes)


@router.post(_collection + "/search-content", response_model=TranscriptPage)
def search_transcripts(project_id: UUID, payload: TranscriptSearch,
                       database: Database, request: Request) -> TranscriptPage:
    begin_coherent_read(database)
    return transcripts.list_transcripts(
        database, project_id, payload, request.app.state.transcript_search_index,
        maximum_content_bytes=settings_of(request).transcript_search_max_bytes)


@router.post(_collection + "/rebuild", response_model=TranscriptRebuildRead)
def rebuild_transcripts(project_id: UUID, payload: TranscriptRebuildRequest,
                        database: Database, request: Request) -> TranscriptRebuildRead:
    with project_mutation(database, project_id):
        queued = transcripts.rebuild_transcripts(database, project_id, payload.client_operation_id)
        database.commit()
    request.app.state.transcript_search_index.clear()
    return TranscriptRebuildRead(queued=queued, project_id=project_id,
                                 client_operation_id=payload.client_operation_id)


@router.post(_collection + "/import", response_model=TranscriptImportRead)
def import_transcripts(project_id: UUID, payload: TranscriptImportRequest,
                       database: Database, request: Request) -> TranscriptImportRead:
    replay = transcript_imports.replay_import(database, project_id, payload)
    if replay is not None:
        return replay
    # Do not hold database locks or a read transaction while walking the filesystem.
    database.rollback()
    settings = settings_of(request)
    scan = discover_transcripts(payload.directory, settings.transcript_allowed_roots,
                                excluded_roots=(settings.transcript_root,))
    with project_mutation(database, project_id):
        result = transcript_imports.import_transcripts(database, project_id, payload, scan)
        database.commit()
    return result


@router.get(_collection + "/health", response_model=TranscriptHealthRead,
            dependencies=[Depends(reject_empty_read_request)])
def get_transcript_health(project_id: UUID, database: Database,
                          request: Request) -> TranscriptHealthRead:
    return transcript_health(database, project_id, settings_of(request),
                             request.app.state.transcript_search_index)


@router.get(_collection + "/{transcript_id}", response_model=TranscriptRead,
            dependencies=[Depends(reject_empty_read_request)])
def get_transcript(project_id: UUID, transcript_id: UUID, database: Database) -> TranscriptRead:
    return transcripts.transcript_read(
        transcripts.require_transcript(database, project_id, transcript_id), project_id)


def _text_record(database, project_id, transcript_id, expected_sha256) -> Transcript:
    record = transcripts.require_transcript(
        database, project_id, transcript_id)
    if record.status != "ready":
        raise ApplicationError(409, "transcript_not_indexed", "Transcript text is not indexed yet.")
    if expected_sha256 is not None and record.text_sha256 != expected_sha256:
        raise conflict("transcript_content_changed", "The indexed transcript changed; reload it.")
    return record


@router.get(_collection + "/{transcript_id}/text", response_model=TranscriptText,
            dependencies=[Depends(reject_read_body_and_duplicate_query)])
def get_transcript_text(
    project_id: UUID, transcript_id: UUID, database: Database,
    offset: int = Query(default=0, ge=0, le=1_073_741_824),
    limit: int = Query(default=20_000, ge=1, le=200_000),
    expected_sha256: str | None = Query(default=None, pattern="^[0-9a-f]{64}$"),
    segment_id: str | None = Query(default=None, pattern="^[0-9a-f]{24}$"),
    expected_normalized_revision: str | None = Query(default=None, pattern="^[0-9a-f]{64}$"),
    before: int = Query(default=0, ge=0, le=20),
    after: int = Query(default=0, ge=0, le=20),
) -> TranscriptText:
    begin_coherent_read(database)
    if segment_id is not None:
        record = transcripts.require_transcript(database, project_id, transcript_id)
        if expected_sha256 is not None and record.text_sha256 != expected_sha256:
            raise conflict("transcript_content_changed",
                           "The indexed transcript changed; reload it.")
        return segment_range(database, record, project_id, segment_id,
                             expected_normalized_revision, before, after, offset, limit)
    if before or after or expected_normalized_revision is not None:
        raise ApplicationError(422, "transcript_segment_required",
                               "Supply segment_id for normalized revision and surrounding context.")
    record = _text_record(database, project_id, transcript_id, expected_sha256)
    value, total = database.execute(select(
        func.substr(Transcript.normalized_text, offset + 1, limit),
        func.length(Transcript.normalized_text)).where(Transcript.id == record.id)).one()
    value, total = value or "", total or 0
    end = min(total, offset + limit)
    return TranscriptText(transcript_id=transcript_id, project_id=project_id,
        text=value, total_chars=total, offset=offset, limit=limit,
        next_offset=end if end < total else None, status="ready",
        truncated=record.truncated, text_sha256=record.text_sha256,
        normalized_revision=record.normalized_revision)


@router.get(_collection + "/{transcript_id}/content",
            dependencies=[Depends(reject_read_body_and_duplicate_query)])
def download_transcript(
    project_id: UUID, transcript_id: UUID, database: Database,
    expected_sha256: str | None = Query(default=None, pattern="^[0-9a-f]{64}$"),
) -> StreamingResponse:
    begin_coherent_read(database)
    record = _text_record(database, project_id, transcript_id, expected_sha256)
    chars, size = database.execute(select(func.length(Transcript.normalized_text),
        func.octet_length(Transcript.normalized_text)).where(Transcript.id == record.id)).one()

    def chunks():
        for offset in range(0, chars or 0, 65_536):
            value = database.scalar(select(func.substr(Transcript.normalized_text,
                offset + 1, 65_536)).where(Transcript.id == record.id))
            yield (value or "").encode("utf-8")

    return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="transcript-{transcript_id}.txt"',
        "Content-Length": str(size or 0), "X-Content-SHA256": record.text_sha256 or "",
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


@router.get("/projects/{project_id}/transcript-settings", response_model=TranscriptSettingsRead,
            dependencies=[Depends(reject_empty_read_request)])
def get_transcript_settings(project_id: UUID, database: Database,
                            request: Request) -> TranscriptSettingsRead:
    return transcripts.settings_read(database, project_id, settings_of(request))


@router.patch("/projects/{project_id}/transcript-settings", response_model=TranscriptSettingsRead)
def update_transcript_settings(project_id: UUID, payload: TranscriptSettingsPatch,
                               database: Database, request: Request) -> TranscriptSettingsRead:
    with project_mutation(database, project_id):
        result = transcripts.update_settings(database, project_id, payload, settings_of(request))
        database.commit()
    return result
