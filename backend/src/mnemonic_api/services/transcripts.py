"""Transcript provenance and reads follow a work item's current project."""

import hashlib
import json
import threading
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

import tantivy
from sqlalchemy import Text, case, cast, func, literal, select, update
from sqlalchemy.orm import Session, defer, undefer

from mnemonic_api.artifact_index import (
    ArtifactSearchIndex,
    IndexSearchResult,
    SearchDocument,
    SearchHit,
    literal_terms,
)
from mnemonic_api.config import DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES, Settings
from mnemonic_api.database import rows_affected
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import Transcript, TranscriptRebuild, TranscriptSettings, WorkItem
from mnemonic_api.search_diagnostics import TermDiagnostic, TermMatchCounts
from mnemonic_api.search_disclosure import TranscriptAppliedFilters, search_disclosure
from mnemonic_api.search_exploration import date_conditions, wants_diagnostics
from mnemonic_api.search_exploration_schemas import DiagnosticsMode
from mnemonic_api.search_projects import ProjectSelection, project_scope
from mnemonic_api.search_query import QueryMode, parse_query
from mnemonic_api.search_ranking import search_ranking
from mnemonic_api.services.work_items import require_project
from mnemonic_api.transcript_exact_search import (
    exact_documents,
    exact_evidence,
    literal_hits,
    preflight_segments,
)
from mnemonic_api.transcript_normalized_storage import SEGMENTS
from mnemonic_api.transcript_schemas import (
    CompactTranscriptRead,
    TranscriptNormalizationRead,
    TranscriptPage,
    TranscriptRead,
    TranscriptSearch,
    TranscriptSettingsPatch,
    TranscriptSettingsRead,
)
from mnemonic_api.transcript_segment_search import filtered_documents, matching_segment
from mnemonic_api.transcript_snapshots import empty_transcript_snapshot
from mnemonic_api.transcript_storage import validate_transcript_assertion

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
            settings = database.info.get("transcript_settings")
            if settings is not None:
                validate_transcript_assertion(source["path"], settings.transcript_allowed_roots)
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
              if name not in {"project_id", "filename", "metadata", "snippet", "score",
                              "index_status", "index_error_code", "segment_id", "content_kind",
                              "matched_fields", "snippet_omission_reason", "rank", "score_type"}}
    fields["index_status"] = record.reindex_status or record.status
    return TranscriptRead(**fields, project_id=project_id,
                          index_error_code=record.reindex_error_code or record.error_code,
                          filename=PurePosixPath(record.source_path).name,
                          metadata=record.extracted_metadata)



def transcript_search_read(
    record: Transcript, project_id: UUID, detail: Literal["compact", "full"],
    *, rank: int | None = None,
) -> TranscriptRead | CompactTranscriptRead:
    if detail == "full":
        rendered = transcript_read(record, project_id)
        rendered.rank = rank
        return rendered
    return CompactTranscriptRead.model_validate({
        "id": record.id, "project_id": project_id, "work_item_id": record.work_item_id,
        "client": record.client, "session_id": record.session_id,
        "filename": PurePosixPath(record.source_path).name, "kind": record.kind,
        "status": record.status, "index_status": record.reindex_status or record.status,
        "copy_status": record.copy_status, "truncated": record.truncated, "rank": rank,
        **{name: getattr(record, name) for name in TranscriptNormalizationRead.model_fields
           if name not in {"segment_id", "content_kind", "matched_fields",
                           "snippet_omission_reason", "rank", "score_type"}},
    })


def transcript_project_id():
    return func.coalesce(WorkItem.project_id, Transcript.import_project_id)


def transcript_query(project_id: ProjectSelection):
    return select(Transcript).outerjoin(WorkItem, WorkItem.id == Transcript.work_item_id).where(
        project_scope(transcript_project_id(), project_id))


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


def _metadata_parts(record: Transcript) -> tuple[str, ...]:
    return (record.source_path, record.client, record.session_id or "", record.kind,
            str(record.work_item_id) if record.work_item_id else "",
            *(value for values in record.extracted_metadata.values() for value in values))


def _corpus_key(records: list[Transcript], fulltext: bool,
                content_kinds: Sequence[str] | None = None, *, exact: bool = False) -> str:
    digest = hashlib.sha256(json.dumps([fulltext, sorted(content_kinds or []), exact]).encode())
    for record in records:
        digest.update(json.dumps([
            str(record.id), _metadata(record), record.generation, record.status,
            record.text_sha256, str(record.indexing_completed_at), record.normalized_revision,
        ]).encode())
    return digest.hexdigest()


def _documents(database: Session, records: list[Transcript], fulltext: bool,
               maximum_content_bytes: int,
               content_kinds: Sequence[str] | None = None) -> Iterator[SearchDocument]:
    if fulltext and content_kinds:
        yield from filtered_documents(database, records, content_kinds, maximum_content_bytes,
                                      _metadata)
        return
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
                    index: ArtifactSearchIndex, maximum_content_bytes: int,
                    content_kinds: Sequence[str] | None = None,
                    query_mode: QueryMode = "terms",
                    diagnostics: DiagnosticsMode = "on_empty") -> IndexSearchResult:
    intent = parse_query(query, query_mode)
    if intent.constrained and fulltext:
        preflight_segments(database, records, content_kinds, maximum_content_bytes)
    documents = (lambda: exact_documents(
        database, records, fulltext, content_kinds, _metadata_parts)
    ) if intent.constrained else (lambda: _documents(
        database, records, fulltext, maximum_content_bytes, content_kinds))
    return index.search(
        _corpus_key(records, fulltext, content_kinds, exact=intent.constrained), documents,
        query=query, fulltext=fulltext, count=len(records), query_mode=query_mode,
        diagnostics=diagnostics,
        literal_matches=lambda: literal_hits(database, records, intent.text, fulltext,
                                             content_kinds, _metadata_parts),
    )


def _search_read(
    database: Session, project_id: UUID, record: Transcript, hit: SearchHit, query: str,
    index: ArtifactSearchIndex, searcher: tantivy.Searcher | None,
    content_kinds: Sequence[str] | None = None,
    *, rank: int = 1, detail: Literal["compact", "full"] = "full", query_mode: QueryMode = "terms",
) -> TranscriptRead | CompactTranscriptRead:
    rendered = transcript_search_read(record, project_id, detail)
    rendered.rank = rank
    rendered.score_type = search_ranking(query, query_mode).score_type
    rendered.score = hit.score
    rendered.matched_fields = (["metadata"] if hit.metadata else []) + (
        ["content"] if hit.content else [])
    intent = parse_query(query, query_mode)
    if hit.content and intent.constrained:
        match = exact_evidence(database, record, intent, content_kinds)
        if match is not None:
            rendered.segment_id, rendered.content_kind, rendered.snippet = match
            if rendered.snippet is None:
                rendered.snippet_omission_reason = "matched_span_exceeds_budget"
        return rendered
    if hit.content and searcher is not None:
        # Hydrate only the returned page, one body at a time, using the same
        # database snapshot and immutable Tantivy searcher that produced the hit.
        match = matching_segment(database, record, query, content_kinds)
        if match is not None:
            segment, searchable = match
            rendered.snippet = index.snippet(searchable, query, searcher)
            rendered.segment_id = segment.segment_id
            rendered.content_kind = segment.content_kind
        elif not content_kinds:
            content = database.scalar(select(Transcript.normalized_text).where(
                Transcript.id == record.id))
            rendered.snippet = index.snippet(content or "", query, searcher)
    return rendered


def has_content_kind(content_kinds: Sequence[str]):
    """Only the published canonical revision can satisfy a content-kind filter."""
    return select(SEGMENTS.c.transcript_id).where(
        SEGMENTS.c.transcript_id == Transcript.id,
        SEGMENTS.c.revision == Transcript.normalized_revision,
        SEGMENTS.c.content_kind.in_(content_kinds),
    ).exists()


def list_transcripts(database: Session, project_id: UUID, filters: TranscriptSearch,
                     index: ArtifactSearchIndex, *,
                     maximum_content_bytes: int = DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES,
) -> TranscriptPage:
    require_project(database, project_id)
    statement = transcript_query(project_id).where(*date_conditions(
        filters, Transcript.created_at,
        func.coalesce(Transcript.indexing_completed_at, Transcript.created_at)))
    if filters.work_item_id is not None:
        statement = statement.where(Transcript.work_item_id == filters.work_item_id)
    incomplete = bool(database.scalar(select(func.count()).select_from(statement.where(
        (Transcript.status != "ready") | (Transcript.copy_status != "ready")
        | Transcript.reindex_status.is_not(None) | Transcript.truncated
        | (Transcript.normalization_status != "ready")
        | Transcript.normalization_incomplete).subquery())))
    intent = parse_query(filters.query, filters.query_mode)
    legacy_omitted = (database.scalar(select(func.count()).select_from(statement.where(
        Transcript.status == "ready", Transcript.normalized_revision.is_(None)).subquery())) or 0
        ) if filters.fulltext and intent.constrained else 0
    if filters.content_kinds:
        statement = statement.where(has_content_kind(filters.content_kinds))
    if filters.query and filters.query.strip():
        return _searched_page(database, project_id, filters, index, statement, incomplete,
                              maximum_content_bytes, legacy_omitted)
    total = database.scalar(select(func.count()).select_from(statement.subquery())) or 0
    records = database.scalars(statement.options(defer(Transcript.normalized_text))
        .order_by(Transcript.created_at.desc(), Transcript.id).offset(filters.offset)
        .limit(filters.limit))
    return TranscriptPage(**search_ranking(filters.query, filters.query_mode).model_dump(),
                          **search_disclosure(
                              project_id, filters.query, fulltext=filters.fulltext,
                              query_mode=filters.query_mode,
                              diagnostics=filters.diagnostics,
                              transcripts=TranscriptAppliedFilters.model_validate(
                                  filters.model_dump(
                                      include=set(TranscriptAppliedFilters.model_fields)),
                              ),
                          ).model_dump(), detail=filters.detail,
                          items=[transcript_search_read(row, project_id, filters.detail, rank=rank)
                                 for rank, row in enumerate(records, filters.offset + 1)],
                          total=total, limit=filters.limit, offset=filters.offset,
                          indexing_incomplete=incomplete)


def _searched_page(database, project_id, filters, index, statement, incomplete,
                   maximum_content_bytes, legacy_omitted=0) -> TranscriptPage:
    if not _SEARCH_SLOT.acquire(timeout=0.25):
        raise ApplicationError(503, "transcript_search_busy",
                               "Transcript search is busy. Try this read again shortly.")
    try:
        return _searched_page_locked(database, project_id, filters, index, statement, incomplete,
                                     maximum_content_bytes, legacy_omitted)
    finally:
        _SEARCH_SLOT.release()


def _searched_page_locked(database, project_id, filters, index, statement, incomplete,
                          maximum_content_bytes, legacy_omitted=0):
    intent = parse_query(filters.query, filters.query_mode)
    records = _bounded_records(database, statement, filters.fulltext and not filters.content_kinds
                               and not intent.constrained,
                               maximum_content_bytes)
    result = _search_records(database, records, filters.query, filters.fulltext, index,
                             maximum_content_bytes, filters.content_kinds, filters.query_mode,
                             filters.diagnostics)
    by_id = {str(record.id): record for record in records}
    items = [_search_read(database, project_id, by_id[hit.identity], hit, filters.query,
                          index, result.searcher, filters.content_kinds, detail=filters.detail,
                          query_mode=filters.query_mode, rank=rank)
             for rank, hit in enumerate(
                 result.hits[filters.offset:filters.offset + filters.limit], filters.offset + 1)]
    return TranscriptPage(**search_ranking(filters.query, filters.query_mode).model_dump(),
                          **search_disclosure(
                              project_id, filters.query, fulltext=filters.fulltext,
                              query_mode=filters.query_mode,
                              diagnostics=filters.diagnostics,
                              transcripts=TranscriptAppliedFilters.model_validate(
                                  filters.model_dump(
                                      include=set(TranscriptAppliedFilters.model_fields)),
                              ),
                          ).model_dump(), detail=filters.detail, items=items,
                          total=len(result.hits), limit=filters.limit,
                          offset=filters.offset, indexing_incomplete=incomplete,
                          unsegmented_content_omitted=legacy_omitted,
                          term_diagnostics=[TermDiagnostic(
                              term=term, matches=TermMatchCounts(transcripts=count),
                          ) for term, count in index.term_counts(
                              literal_terms(filters.query, fold_accents=False),
                              filters.fulltext, result.searcher,
                          ).items()] if wants_diagnostics(
                              filters.diagnostics, filters.query, len(result.hits)) else [])


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
    retained = Transcript.status == "ready"
    snapshot = {name: case((retained, getattr(Transcript, name)),
                          else_=literal(value, type_=getattr(Transcript, name).type))
                for name, value in empty_transcript_snapshot().items()}
    result = database.execute(update(Transcript).where(Transcript.id.in_(
        transcript_query(project_id).with_only_columns(Transcript.id))).values(
            **snapshot, generation=Transcript.generation + 1,
            status=case((retained, "ready"), else_="waiting"),
            reindex_status=case((retained, "pending"), else_=None), reindex_error_code=None,
            lease_token=None, lease_expires_at=None,
            indexing_started_at=case((retained, Transcript.indexing_started_at), else_=None),
            indexing_completed_at=case((retained, Transcript.indexing_completed_at), else_=None),
            error_code=None,
            attempts=0, next_attempt_at=datetime.now(UTC),
            copy_status=case((Transcript.copy_status == "ready", "ready"), else_="pending"),
            copy_error_code=None, copy_attempts=0, copy_next_attempt_at=datetime.now(UTC),
            copy_lease_token=None, copy_lease_expires_at=None,
        ).execution_options(synchronize_session=False))
    queued = rows_affected(result)
    database.add(TranscriptRebuild(project_id=project_id, client_operation_id=operation_id,
                                   queued=queued))
    database.flush()
    return queued
