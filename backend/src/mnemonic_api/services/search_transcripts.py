"""Bounded transcript facet searches retain their immutable index for page snippets."""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import ArtifactSearchIndex, SearchDocument
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
    _metadata,
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


def _corpus_key(records: list[Transcript], fulltext: bool) -> str:
    digest = hashlib.sha256(str(fulltext).encode())
    for record in records:
        digest.update(json.dumps([
            str(record.id), _metadata(record), record.generation, record.status,
            record.text_sha256, str(record.indexing_completed_at),
        ]).encode())
    return digest.hexdigest()


def _source(
    database: Session, project_id: UUID, request: SearchRequest, index: ArtifactSearchIndex,
) -> tuple[SearchSource, TranscriptSearchCoverage]:
    fulltext = bool(request.q and request.fulltext)
    rows = _bounded_records(database, _filtered_statement(project_id, request), fulltext)
    records = {str(record.id): record for record in rows}
    hits = {}
    searcher = None
    if request.q:
        result = index.search(
            _corpus_key(rows, fulltext),
            lambda: (SearchDocument(
                str(record.id), _metadata(record),
                (record.normalized_text or "") if fulltext else "",
            ) for record in rows),
            query=request.q, fulltext=fulltext, count=len(rows),
        )
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
            transcript = transcript_read(record, project_id)
            if request.q:
                hit = hits[str(item.id)]
                transcript.score = hit.score
                if hit.content and searcher is not None:
                    transcript.snippet = index.snippet(
                        record.normalized_text or "", request.q, searcher,
                    )
            rendered[item.id] = TranscriptFacetHit(**item.fields(), transcript=transcript)
        return rendered

    return SearchSource(candidates, hydrate), coverage


@contextmanager
def transcript_source(
    database: Session, project_id: UUID, request: SearchRequest, index: ArtifactSearchIndex,
) -> Iterator[tuple[SearchSource, TranscriptSearchCoverage]]:
    if not _SEARCH_SLOT.acquire(timeout=0.25):
        raise ApplicationError(503, "transcript_search_busy",
                               "Transcript search is busy. Try this read again shortly.")
    try:
        yield _source(database, project_id, request, index)
    finally:
        _SEARCH_SLOT.release()
