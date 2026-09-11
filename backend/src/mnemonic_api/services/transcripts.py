"""Transcript provenance and reads follow a work item's current project."""

import hashlib
import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import UUID, uuid4

import tantivy
from sqlalchemy import Text, case, cast, func, literal, select, update
from sqlalchemy.orm import Session, defer, undefer

from mnemonic_api.artifact_index import (
    ArtifactSearchIndex,
    IndexSearchResult,
    SearchDocument,
    SearchHit,
)
from mnemonic_api.config import DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES, Settings
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
# Only lightweight metadata is retained in Python. Bodies have a separate budget.
_SEARCH_MAX_BYTES = 32_000_000


def register_transcripts(
    database: Session, work: WorkItem, lease_generation_id: UUID, client: str,
    session_id: str, sources: list[dict[str, str]], kind: str,
) -> None:
    from mnemonic_api.services.transcript_imports import take_imported_transcript

    for source in sources:
        existing = database.scalar(select(Transcript.id).where(
            Transcript.work_item_id == work.id,
            Transcript.lease_generation_id == lease_generation_id,
            Transcript.source_path == source["path"], Transcript.kind == kind,
        ))
        if existing is None:
            record = take_imported_transcript(database, work.project_id, source["path"])
            if record is None:
                record = Transcript(id=uuid4())
                database.add(record)
            record.work_item_id = work.id
            record.lease_generation_id = lease_generation_id
            record.client = source.get("client", client)
            record.session_id = session_id
            record.source_path = source["path"]
            record.kind = kind
            database.flush()


def transcript_read(record: Transcript, project_id: UUID) -> TranscriptRead:
    fields = {name: getattr(record, name) for name in TranscriptRead.model_fields
              if name not in {"project_id", "filename", "metadata", "snippet", "score"}}
    return TranscriptRead(**fields, project_id=project_id,
                          filename=PurePosixPath(record.source_path).name,
                          metadata=record.extracted_metadata)


def transcript_project_id():
    return func.coalesce(WorkItem.project_id, Transcript.import_project_id)


def transcript_query(project_id: UUID):
    return select(Transcript).outerjoin(WorkItem, WorkItem.id == Transcript.work_item_id).where(
        transcript_project_id() == project_id)


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
    return "\n".join([record.source_path, record.client, record.session_id or "", record.kind,
                      str(record.work_item_id), json.dumps(record.extracted_metadata)])


def _corpus_key(records: list[Transcript], fulltext: bool) -> str:
    digest = hashlib.sha256(str(fulltext).encode())
    for record in records:
        digest.update(json.dumps([
            str(record.id), _metadata(record), record.generation, record.status,
            record.text_sha256, str(record.indexing_completed_at),
        ]).encode())
    return digest.hexdigest()


def _documents(database: Session, records: list[Transcript], fulltext: bool,
               maximum_content_bytes: int) -> Iterator[SearchDocument]:
    # Only a cache miss calls this generator. Never attach the streamed bodies
    # to the ORM instances retained for ranking and metadata hydration.
    by_id = {record.id: record for record in records}
    content_ids = {record.id for record in records if fulltext and record.status == "ready"}
    for record in records:
        if record.id not in content_ids:
            yield SearchDocument(str(record.id), _metadata(record))
    if not content_ids:
        return
    rows = database.execute(select(Transcript.id, Transcript.normalized_text).where(
        Transcript.id.in_(content_ids)).order_by(Transcript.id).execution_options(yield_per=1))
    size = 0
    try:
        for identity, content in rows:
            content = content or ""
            size += len(content.encode("utf-8"))
            if size > maximum_content_bytes:
                raise _capacity_error(content=True)
            yield SearchDocument(str(identity), _metadata(by_id[identity]), content)
    finally:
        rows.close()


def _search_records(database: Session, records: list[Transcript], query: str, fulltext: bool,
                    index: ArtifactSearchIndex, maximum_content_bytes: int) -> IndexSearchResult:
    return index.search(
        _corpus_key(records, fulltext),
        lambda: _documents(database, records, fulltext, maximum_content_bytes),
        query=query, fulltext=fulltext, count=len(records),
    )


def _search_read(
    database: Session, project_id: UUID, record: Transcript, hit: SearchHit, query: str,
    index: ArtifactSearchIndex, searcher: tantivy.Searcher | None,
) -> TranscriptRead:
    rendered = transcript_read(record, project_id)
    rendered.score = hit.score
    if hit.content and searcher is not None:
        # Hydrate only the returned page, one body at a time, using the same
        # database snapshot and immutable Tantivy searcher that produced the hit.
        content = database.scalar(select(Transcript.normalized_text).where(
            Transcript.id == record.id))
        rendered.snippet = index.snippet(content or "", query, searcher)
    return rendered


def list_transcripts(database: Session, project_id: UUID, filters: TranscriptSearch,
                     index: ArtifactSearchIndex, *,
                     maximum_content_bytes: int = DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES,
) -> TranscriptPage:
    require_project(database, project_id)
    statement = transcript_query(project_id)
    if filters.work_item_id is not None:
        statement = statement.where(Transcript.work_item_id == filters.work_item_id)
    incomplete = bool(database.scalar(select(func.count()).select_from(statement.where(
        (Transcript.status != "ready") | Transcript.truncated).subquery())))
    if filters.query and filters.query.strip():
        return _searched_page(database, project_id, filters, index, statement, incomplete,
                              maximum_content_bytes)
    total = database.scalar(select(func.count()).select_from(statement.subquery())) or 0
    records = database.scalars(statement.options(defer(Transcript.normalized_text))
        .order_by(Transcript.created_at.desc(), Transcript.id).offset(filters.offset)
        .limit(filters.limit))
    return TranscriptPage(items=[transcript_read(row, project_id) for row in records],
                          total=total, limit=filters.limit, offset=filters.offset,
                          indexing_incomplete=incomplete)


def _searched_page(database, project_id, filters, index, statement, incomplete,
                   maximum_content_bytes) -> TranscriptPage:
    if not _SEARCH_SLOT.acquire(timeout=0.25):
        raise ApplicationError(503, "transcript_search_busy",
                               "Transcript search is busy. Try this read again shortly.")
    try:
        return _searched_page_locked(database, project_id, filters, index, statement, incomplete,
                                     maximum_content_bytes)
    finally:
        _SEARCH_SLOT.release()


def _searched_page_locked(database, project_id, filters, index, statement, incomplete,
                          maximum_content_bytes):
    records = _bounded_records(database, statement, filters.fulltext, maximum_content_bytes)
    result = _search_records(database, records, filters.query, filters.fulltext, index,
                             maximum_content_bytes)
    by_id = {str(record.id): record for record in records}
    items = [_search_read(database, project_id, by_id[hit.identity], hit, filters.query,
                          index, result.searcher)
             for hit in result.hits[filters.offset:filters.offset + filters.limit]]
    return TranscriptPage(items=items, total=len(result.hits), limit=filters.limit,
                          offset=filters.offset, indexing_incomplete=incomplete)


def _capacity_error(*, content: bool = False) -> ApplicationError:
    message = ("Transcript content search exceeds the server's configured size limit. "
               "Ask your operator to increase transcript search capacity." if content else
               "Transcript search capacity reached; narrow the search scope.")
    return ApplicationError(503, "transcript_search_capacity", message)


def _preflight_corpus(database, statement, fulltext, maximum_content_bytes) -> None:
    # Reject an oversized scope before transferring any bodies. JSON escaping
    # can expand non-ASCII metadata, so its estimate stays conservative.
    metadata_bytes = (func.octet_length(Transcript.source_path)
        + func.octet_length(Transcript.client)
        + func.coalesce(func.octet_length(Transcript.session_id), 0)
        + func.octet_length(cast(Transcript.extracted_metadata, Text)) * 6 + 128)
    text_bytes = (case((Transcript.status == "ready",
                       func.coalesce(func.octet_length(Transcript.normalized_text), 0)), else_=0)
                  if fulltext else literal(0))
    corpus = statement.with_only_columns(
        Transcript.id, metadata_bytes.label("metadata_bytes"),
        text_bytes.label("text_bytes")).subquery()
    count, size, content_size = database.execute(select(
        func.count(corpus.c.id), func.coalesce(func.sum(corpus.c.metadata_bytes), 0),
        func.coalesce(func.sum(corpus.c.text_bytes), 0))).one()
    if count > _SEARCH_MAX_DOCUMENTS or size > _SEARCH_MAX_BYTES:
        raise _capacity_error()
    if content_size > maximum_content_bytes:
        raise _capacity_error(content=True)


def _bounded_records(database, statement, fulltext, maximum_content_bytes) -> list[Transcript]:
    _preflight_corpus(database, statement, fulltext, maximum_content_bytes)
    rows = database.scalars(statement.options(defer(Transcript.normalized_text))
                            .order_by(Transcript.id).execution_options(yield_per=1))
    records = []
    size = 0
    try:
        for record in rows:
            size += len(_metadata(record).encode("utf-8"))
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
    result = database.execute(update(Transcript).where(Transcript.id.in_(
        transcript_query(project_id).with_only_columns(Transcript.id))).values(
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
