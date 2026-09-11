"""Bounded transcript facet searches retain their immutable index for page snippets."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import ArtifactSearchIndex
from mnemonic_api.config import DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Transcript
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
    transcript_query,
    transcript_read,
)


def _filtered_statement(project_id: UUID, request: SearchRequest):
    statement = transcript_query(project_id)
    fields = {
        "work_item_id": Transcript.work_item_id, "agent_session_id": Transcript.session_id,
        "client": Transcript.client, "kind": Transcript.kind, "status": Transcript.status,
    }
    for name, column in fields.items():
        value = getattr(request.filters.transcripts, name)
        if value is not None:
            statement = statement.where(column == value)
    return statement


def _source(
    database: Session, project_id: UUID, request: SearchRequest, index: ArtifactSearchIndex,
    maximum_content_bytes: int,
) -> tuple[SearchSource, TranscriptSearchCoverage]:
    fulltext = bool(request.q and request.fulltext)
    rows = _bounded_records(database, _filtered_statement(project_id, request), fulltext,
                            maximum_content_bytes)
    records = {str(record.id): record for record in rows}
    hits = {}
    searcher = None
    if request.q:
        result = _search_records(database, rows, request.q, fulltext, index,
                                 maximum_content_bytes)
        hits = {hit.identity: hit for hit in result.hits}
        searcher = result.searcher
    candidates = [SearchCandidate(
        facet="transcripts", id=record.id, created_at=record.created_at,
        updated_at=record.indexing_completed_at or record.created_at,
        score=hits[identity].score if request.q else 0.0,
    ) for identity, record in records.items() if not request.q or identity in hits]
    coverage = TranscriptSearchCoverage(indexing_incomplete=any(
        record.status != "ready" or record.truncated for record in rows
    ))

    def hydrate(page: list[SearchCandidate]) -> dict[UUID, SearchHit]:
        rendered: dict[UUID, SearchHit] = {}
        for item in page:
            record = records[str(item.id)]
            transcript = (_search_read(database, project_id, record, hits[str(item.id)],
                                       request.q, index, searcher) if request.q else
                          transcript_read(record, project_id))
            rendered[item.id] = TranscriptFacetHit(**item.fields(), transcript=transcript)
        return rendered

    return SearchSource(candidates, hydrate), coverage


@contextmanager
def transcript_source(
    database: Session, project_id: UUID, request: SearchRequest, index: ArtifactSearchIndex,
    *, maximum_content_bytes: int = DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES,
) -> Iterator[tuple[SearchSource, TranscriptSearchCoverage]]:
    if not _SEARCH_SLOT.acquire(timeout=0.25):
        raise ApplicationError(503, "transcript_search_busy",
                               "Transcript search is busy. Try this read again shortly.")
    try:
        yield _source(database, project_id, request, index, maximum_content_bytes)
    finally:
        _SEARCH_SLOT.release()
