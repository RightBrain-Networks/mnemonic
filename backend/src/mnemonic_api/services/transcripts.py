"""Transcript provenance and reads follow a work item's current project."""

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from sqlalchemy import Text, cast, func, select, update
from sqlalchemy.orm import Session, defer, undefer

from mnemonic_api.artifact_index import ArtifactSearchIndex, SearchDocument
from mnemonic_api.config import Settings
from mnemonic_api.database import rows_affected
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import Transcript, TranscriptRebuild, TranscriptSettings, WorkItem
from mnemonic_api.services.work_items import require_project
from mnemonic_api.transcript_schemas import (
    TranscriptPage,
    TranscriptRead,
    TranscriptSearch,
    TranscriptSettingsPatch,
    TranscriptSettingsRead,
)
from mnemonic_api.transcript_snapshots import empty_transcript_snapshot

# The admission slot covers corpus preflight, loading, Tantivy building and
# snippets. The index's own lock starts too late to bound concurrent DB loads.
_SEARCH_SLOT = threading.BoundedSemaphore(1)
_SEARCH_MAX_DOCUMENTS = 10_000
_SEARCH_MAX_BYTES = 32_000_000


def register_transcripts(
    database: Session, work: WorkItem, lease_generation_id: UUID, client: str,
    session_id: str, sources: list[dict[str, str]], kind: str,
) -> None:
    for source in sources:
        existing = database.scalar(select(Transcript.id).where(
            Transcript.work_item_id == work.id,
            Transcript.lease_generation_id == lease_generation_id,
            Transcript.source_path == source["path"], Transcript.kind == kind,
        ))
        if existing is None:
            database.add(Transcript(id=uuid4(), work_item_id=work.id,
                                    lease_generation_id=lease_generation_id,
                                    client=source.get("client", client), session_id=session_id,
                                    source_path=source["path"], kind=kind))
            database.flush()


def transcript_read(record: Transcript, project_id: UUID) -> TranscriptRead:
    fields = {name: getattr(record, name) for name in TranscriptRead.model_fields
              if name not in {"project_id", "filename", "metadata", "snippet", "score"}}
    return TranscriptRead(**fields, project_id=project_id,
                          filename=PurePosixPath(record.source_path).name,
                          metadata=record.extracted_metadata)


def transcript_query(project_id: UUID):
    return select(Transcript).join(WorkItem, WorkItem.id == Transcript.work_item_id).where(
        WorkItem.project_id == project_id)


def require_transcript(
    database: Session, project_id: UUID, transcript_id: UUID, *, include_text: bool = False,
) -> Transcript:
    require_project(database, project_id)
    statement = transcript_query(project_id).where(Transcript.id == transcript_id)
    if include_text:
        statement = statement.options(undefer(Transcript.normalized_text))
    record = database.scalar(statement)
    if record is None:
        raise ApplicationError(404, "transcript_not_found", "Transcript not found in this project.")
    return record


def _metadata(record: Transcript) -> str:
    return "\n".join([record.source_path, record.client, record.session_id, record.kind,
                      str(record.work_item_id), json.dumps(record.extracted_metadata)])


def _search_records(records: list[Transcript], query: str, fulltext: bool,
                    index: ArtifactSearchIndex) -> list[tuple[Transcript, str | None, float]]:
    digest = hashlib.sha256()
    for record in records:
        digest.update(f"{record.id}:{record.generation}:{record.status}:"
                      f"{record.text_sha256}:{record.indexing_completed_at}".encode())
    result = index.search(digest.hexdigest(), lambda: (
        SearchDocument(str(row.id), _metadata(row), row.normalized_text or "") for row in records
    ), query=query, fulltext=fulltext, count=len(records))
    by_id = {str(row.id): row for row in records}
    return [(record, index.snippet(record.normalized_text or "", query, result.searcher)
             if hit.content and result.searcher else None, hit.score)
            for hit in result.hits for record in [by_id[hit.identity]]]


def list_transcripts(database: Session, project_id: UUID, filters: TranscriptSearch,
                     index: ArtifactSearchIndex) -> TranscriptPage:
    require_project(database, project_id)
    statement = transcript_query(project_id)
    if filters.work_item_id is not None:
        statement = statement.where(Transcript.work_item_id == filters.work_item_id)
    incomplete = bool(database.scalar(select(func.count()).select_from(statement.where(
        (Transcript.status != "ready") | Transcript.truncated).subquery())))
    if filters.query and filters.query.strip():
        return _searched_page(database, project_id, filters, index, statement, incomplete)
    total = database.scalar(select(func.count()).select_from(statement.subquery())) or 0
    records = database.scalars(statement.options(defer(Transcript.normalized_text))
        .order_by(Transcript.created_at.desc(), Transcript.id).offset(filters.offset)
        .limit(filters.limit))
    return TranscriptPage(items=[transcript_read(row, project_id) for row in records],
                          total=total, limit=filters.limit, offset=filters.offset,
                          indexing_incomplete=incomplete)


def _searched_page(database, project_id, filters, index, statement, incomplete) -> TranscriptPage:
    if not _SEARCH_SLOT.acquire(timeout=0.25):
        raise ApplicationError(503, "transcript_search_busy",
                               "Transcript search is busy. Try this read again shortly.")
    try:
        return _searched_page_locked(database, project_id, filters, index, statement, incomplete)
    finally:
        _SEARCH_SLOT.release()


def _searched_page_locked(database, project_id, filters, index, statement, incomplete):
    records = _bounded_records(database, statement, filters.fulltext)
    # Metadata-only requests must not load content through a deferred attribute.
    if not filters.fulltext:
        def documents():
            return (SearchDocument(str(row.id), _metadata(row)) for row in records)
        key = hashlib.sha256(json.dumps([(str(r.id), _metadata(r)) for r in records]).encode())
        hits = index.search(key.hexdigest(), documents, query=filters.query,
                            fulltext=False, count=len(records)).hits
        by_id = {str(row.id): row for row in records}
        matches = [(by_id[hit.identity], None, hit.score) for hit in hits]
    else:
        matches = _search_records(records, filters.query, True, index)
    items = [transcript_read(record, project_id).model_copy(update={"snippet": snippet,
                                                               "score": score})
             for record, snippet, score in matches[filters.offset:filters.offset + filters.limit]]
    return TranscriptPage(items=items, total=len(matches), limit=filters.limit,
                          offset=filters.offset, indexing_incomplete=incomplete)


def _capacity_error() -> ApplicationError:
    return ApplicationError(503, "transcript_search_capacity",
                            "Transcript search capacity reached; narrow by work item.")


def _preflight_corpus(database, statement, fulltext) -> None:
    # JSON escaping can expand non-ASCII metadata. Use a conservative upper bound
    # so this scalar aggregate can reject a corpus without returning any bodies.
    metadata_bytes = (func.octet_length(Transcript.source_path)
        + func.octet_length(Transcript.client) + func.octet_length(Transcript.session_id)
        + func.octet_length(cast(Transcript.extracted_metadata, Text)) * 6 + 128)
    text_bytes = func.coalesce(func.octet_length(Transcript.normalized_text), 0) if fulltext else 0
    corpus = statement.with_only_columns(
        Transcript.id, (metadata_bytes + text_bytes).label("size_bytes")).subquery()
    count, size = database.execute(select(
        func.count(corpus.c.id), func.coalesce(func.sum(corpus.c.size_bytes), 0))).one()
    if count > _SEARCH_MAX_DOCUMENTS or size > _SEARCH_MAX_BYTES:
        raise _capacity_error()


def _bounded_records(database, statement, fulltext) -> list[Transcript]:
    _preflight_corpus(database, statement, fulltext)
    load_text = (undefer(Transcript.normalized_text) if fulltext
                 else defer(Transcript.normalized_text))
    rows = database.scalars(statement.options(load_text).order_by(Transcript.id)
                            .execution_options(yield_per=1))
    records = []
    size = 0
    try:
        for record in rows:
            size += len(_metadata(record).encode("utf-8"))
            if fulltext:
                size += len((record.normalized_text or "").encode("utf-8"))
            # Recheck every row: a concurrent commit may enlarge the corpus
            # between preflight and this READ COMMITTED cursor's snapshot.
            if len(records) >= _SEARCH_MAX_DOCUMENTS or size > _SEARCH_MAX_BYTES:
                raise _capacity_error()
            records.append(record)
    finally:
        rows.close()
    return records


def settings_read(
    database: Session, project_id: UUID, settings: Settings,
) -> TranscriptSettingsRead:
    require_project(database, project_id)
    record = database.get(TranscriptSettings, project_id)
    return TranscriptSettingsRead(enabled=record.enabled if record else True,
        max_file_size_bytes=min(record.max_file_size_bytes, settings.transcript_max_bytes)
            if record else settings.transcript_max_bytes,
        revision=record.revision if record else 1,
        allowed_roots=[str(root) for root in settings.transcript_allowed_roots],
        operator_max_file_size_bytes=settings.transcript_max_bytes)


def update_settings(database: Session, project_id: UUID, payload: TranscriptSettingsPatch,
                    settings: Settings) -> TranscriptSettingsRead:
    current = settings_read(database, project_id, settings)
    if payload.expected_revision != current.revision:
        raise conflict("transcript_settings_conflict", "Transcript settings changed; reload them.")
    if payload.max_file_size_bytes > settings.transcript_max_bytes:
        raise ApplicationError(422, "transcript_size_limit",
                               "The file limit exceeds the operator-configured maximum.")
    record = database.get(TranscriptSettings, project_id)
    if record is None:
        record = TranscriptSettings(project_id=project_id)
        database.add(record)
    record.enabled = payload.enabled
    record.max_file_size_bytes = payload.max_file_size_bytes
    record.revision = current.revision + 1
    database.flush()
    return settings_read(database, project_id, settings)


def rebuild_transcripts(database: Session, project_id: UUID, operation_id: UUID) -> int:
    require_project(database, project_id)
    receipt = database.get(TranscriptRebuild, (project_id, operation_id))
    if receipt is not None:
        return receipt.queued
    result = database.execute(update(Transcript).where(Transcript.work_item_id.in_(
        select(WorkItem.id).where(WorkItem.project_id == project_id))).values(
            **empty_transcript_snapshot(), generation=Transcript.generation + 1,
            status="waiting", lease_token=None, lease_expires_at=None,
            indexing_started_at=None, indexing_completed_at=None, error_code=None,
            attempts=0, next_attempt_at=datetime.now(UTC),
        ).execution_options(synchronize_session=False))
    queued = rows_affected(result)
    database.add(TranscriptRebuild(project_id=project_id, client_operation_id=operation_id,
                                   queued=queued))
    database.flush()
    return queued
