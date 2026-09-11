"""Project-scoped transcript search, normalized text retrieval and index settings."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from mnemonic_api.application.guards import (
    reject_empty_read_request,
    reject_read_body_and_duplicate_query,
)
from mnemonic_api.application.state import settings_of
from mnemonic_api.artifact_index import ArtifactSearchIndex
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import Transcript
from mnemonic_api.services import transcript_imports, transcripts
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.transcript_discovery import discover_transcripts
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
    request.app.state.transcript_search_index = ArtifactSearchIndex()
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
    scan = discover_transcripts(payload.directory, settings_of(request).transcript_allowed_roots)
    with project_mutation(database, project_id):
        result = transcript_imports.import_transcripts(database, project_id, payload, scan)
        database.commit()
    return result


@router.get(_collection + "/{transcript_id}", response_model=TranscriptRead,
            dependencies=[Depends(reject_empty_read_request)])
def get_transcript(project_id: UUID, transcript_id: UUID, database: Database) -> TranscriptRead:
    return transcripts.transcript_read(
        transcripts.require_transcript(database, project_id, transcript_id), project_id)


def _text_record(database, project_id, transcript_id, expected_sha256) -> Transcript:
    record = transcripts.require_transcript(
        database, project_id, transcript_id, include_text=True)
    if record.status != "ready":
        raise ApplicationError(409, "transcript_not_indexed", "Transcript text is not indexed yet.")
    if expected_sha256 is not None and record.text_sha256 != expected_sha256:
        raise conflict("transcript_content_changed", "The indexed transcript changed; reload it.")
    return record


@router.get(_collection + "/{transcript_id}/text", response_model=TranscriptText,
            dependencies=[Depends(reject_read_body_and_duplicate_query)])
def get_transcript_text(
    project_id: UUID, transcript_id: UUID, database: Database,
    offset: int = Query(default=0, ge=0, le=8_000_000),
    limit: int = Query(default=20_000, ge=1, le=200_000),
    expected_sha256: str | None = Query(default=None, pattern="^[0-9a-f]{64}$"),
) -> TranscriptText:
    record = _text_record(database, project_id, transcript_id, expected_sha256)
    value = record.normalized_text or ""
    end = min(len(value), offset + limit)
    return TranscriptText(transcript_id=transcript_id, project_id=project_id,
        text=value[offset:end], total_chars=len(value), offset=offset, limit=limit,
        next_offset=end if end < len(value) else None, status="ready",
        truncated=record.truncated, text_sha256=record.text_sha256)


@router.get(_collection + "/{transcript_id}/content",
            dependencies=[Depends(reject_read_body_and_duplicate_query)])
def download_transcript(
    project_id: UUID, transcript_id: UUID, database: Database,
    expected_sha256: str | None = Query(default=None, pattern="^[0-9a-f]{64}$"),
) -> Response:
    record = _text_record(database, project_id, transcript_id, expected_sha256)
    return Response(record.normalized_text or "", media_type="text/plain; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="transcript-{transcript_id}.txt"',
        "X-Content-SHA256": record.text_sha256 or "",
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
