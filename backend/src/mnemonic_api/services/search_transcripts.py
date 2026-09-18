"""Bounded transcript facet searches retain their immutable index for page snippets."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import ArtifactSearchIndex
from mnemonic_api.config import DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Transcript
from mnemonic_api.search_exploration import date_conditions
from mnemonic_api.search_projects import ProjectSelection, selected_project_ids
from mnemonic_api.search_query import parse_query
from mnemonic_api.search_schemas import (
    SearchHit,
    SearchRequest,
    TranscriptFacetHit,
    TranscriptSearchCoverage,
)
from mnemonic_api.services.search_sources import SearchCandidate, SearchSource
from mnemonic_api.services.transcripts import (
    _SEARCH_SLOT,
    _bounded_records,
    _search_read,
    _search_records,
    has_content_kind,
    transcript_project_id,
    transcript_query,
    transcript_search_read,
)
from mnemonic_api.transcript_exact_search import omitted_legacy


def _filtered_statement(project_id: ProjectSelection, request: SearchRequest):
    statement = transcript_query(project_id).where(*date_conditions(
        request.filters.transcripts, Transcript.created_at,
        func.coalesce(Transcript.last_updated_at, Transcript.created_at)))
    fields = {
        "work_item_id": Transcript.work_item_id, "agent_session_id": Transcript.session_id,
        "client": Transcript.client, "kind": Transcript.kind, "status": Transcript.status,
    }
    for name, column in fields.items():
        value = getattr(request.filters.transcripts, name)
        if value is not None:
            statement = statement.where(column == value)
    return statement


def _incomplete_records(rows) -> bool:
    return any(record.status != "ready" or record.copy_status != "ready"
        or record.reindex_status is not None or record.truncated
        or record.normalization_status != "ready" or record.normalization_incomplete
        or record.extracted_metadata.get("transcript:metadata_limited") == ["true"]
        for record in rows)


def _coverage(rows, fulltext, intent) -> TranscriptSearchCoverage:
    return TranscriptSearchCoverage(indexing_incomplete=_incomplete_records(rows),
        unsegmented_content_omitted=omitted_legacy(rows, fulltext, intent))


def _source(
    database: Session, project_id: ProjectSelection, request: SearchRequest,
    index: ArtifactSearchIndex,
    maximum_content_bytes: int,
) -> tuple[SearchSource, TranscriptSearchCoverage]:
    intent = parse_query(request.q, request.query_mode)
    fulltext = bool(request.q and request.fulltext)
    content_kinds = request.filters.transcripts.content_kinds
    rows = _bounded_records(database, _filtered_statement(project_id, request),
                            fulltext and not content_kinds and not intent.constrained,
                            maximum_content_bytes)
    owners = {identity: owner for identity, owner in database.execute(
        transcript_query(project_id).with_only_columns(Transcript.id, transcript_project_id())
        .where(Transcript.id.in_([record.id for record in rows])))
    }
    coverage = _coverage(rows, fulltext, intent)
    project_coverage = {
        identity: _coverage([record for record in rows if owners[record.id] == identity],
                            fulltext, intent)
        for identity in selected_project_ids(project_id)
    }
    if content_kinds:
        eligible = set(database.scalars(select(Transcript.id).where(
            Transcript.id.in_([row.id for row in rows]), has_content_kind(content_kinds),
        )))
        rows = [row for row in rows if row.id in eligible]
    records = {str(record.id): record for record in rows}
    hits = {}
    searcher = None
    if request.q:
        result = _search_records(database, rows, request.q, fulltext, index,
                                 maximum_content_bytes, content_kinds, request.query_mode,
                                 request.diagnostics)
        hits = {hit.identity: hit for hit in result.hits}
        searcher = result.searcher
    candidates = [SearchCandidate(
        facet="transcripts", id=record.id, project_id=owners[record.id],
        created_at=record.created_at,
        updated_at=record.last_updated_at or record.created_at,
        score=hits[identity].score if request.q else 0.0,
    ) for identity, record in records.items() if not request.q or identity in hits]


    def hydrate(page: list[SearchCandidate]) -> dict[UUID, SearchHit]:
        rendered: dict[UUID, SearchHit] = {}
        for item in page:
            record = records[str(item.id)]
            transcript = (_search_read(database, item.project_id, record, hits[str(item.id)],
                                       request.q, index, searcher, content_kinds,
                                       detail=request.detail, query_mode=request.query_mode)
                          if request.q else
                          transcript_search_read(record, item.project_id, request.detail))
            transcript.rank = item.source_rank
            rendered[item.id] = TranscriptFacetHit(**item.fields(), transcript=transcript)
        return rendered

    return SearchSource(
        candidates, hydrate, lambda terms: index.term_counts(terms, fulltext, searcher),
        coverage_by_project=project_coverage,
    ), coverage


@contextmanager
def transcript_source(
    database: Session, project_id: ProjectSelection, request: SearchRequest,
    index: ArtifactSearchIndex,
    *, maximum_content_bytes: int = DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES,
) -> Iterator[tuple[SearchSource, TranscriptSearchCoverage]]:
    if not _SEARCH_SLOT.acquire(timeout=0.25):
        raise ApplicationError(503, "transcript_search_busy",
                               "Transcript search is busy. Try this read again shortly.")
    try:
        yield _source(database, project_id, request, index, maximum_content_bytes)
    finally:
        _SEARCH_SLOT.release()
