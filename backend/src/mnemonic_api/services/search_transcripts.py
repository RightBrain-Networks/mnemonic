"""Bounded transcript facet searches retain their immutable index for page snippets."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import ArtifactSearchIndex
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
    _search_read,
    _search_records,
    has_content_kind,
    transcript_project_id,
    transcript_query,
    transcript_search_read,
)
from mnemonic_api.transcript_search_corpus import TranscriptCorpus


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


def _coverage(database, statement, project_id, fulltext, intent):
    incomplete = (Transcript.status != "ready") | (Transcript.copy_status != "ready") | (
        Transcript.reindex_status.is_not(None)) | Transcript.truncated | (
        Transcript.normalization_status != "ready") | Transcript.normalization_incomplete | (
        Transcript.extracted_metadata.contains({"transcript:metadata_limited": ["true"]}))
    legacy = (Transcript.status == "ready") & Transcript.normalized_revision.is_(None)
    rows = database.execute(statement.with_only_columns(
        transcript_project_id(), func.bool_or(incomplete), func.sum(case((legacy, 1), else_=0)))
        .group_by(transcript_project_id()))
    projects = {identity: TranscriptSearchCoverage()
                for identity in selected_project_ids(project_id)}
    for identity, unfinished, omitted in rows:
        projects[identity] = TranscriptSearchCoverage(indexing_incomplete=bool(unfinished),
            unsegmented_content_omitted=omitted if fulltext and intent.constrained else 0)
    return TranscriptSearchCoverage(
        indexing_incomplete=any(value.indexing_incomplete for value in projects.values()),
        unsegmented_content_omitted=sum(value.unsegmented_content_omitted
                                       for value in projects.values()),
    ), projects


def _source(
    database: Session, project_id: ProjectSelection, request: SearchRequest,
    index: ArtifactSearchIndex,
) -> tuple[SearchSource, TranscriptSearchCoverage]:
    intent = parse_query(request.q, request.query_mode)
    fulltext = bool(request.q and request.fulltext)
    content_kinds = request.filters.transcripts.content_kinds
    statement = _filtered_statement(project_id, request)
    coverage, project_coverage = _coverage(database, statement, project_id, fulltext, intent)
    if content_kinds:
        statement = statement.where(has_content_kind(content_kinds))
    corpus = TranscriptCorpus(database, statement)
    hits = {}
    searcher = None
    matches = corpus
    if request.q:
        result = _search_records(database, corpus, request.q, fulltext, index,
                                 content_kinds, request.query_mode, request.diagnostics)
        hits = {hit.identity: hit for hit in result.hits}
        searcher = result.searcher
        matches = corpus.matching([UUID(identity) for identity in hits])
    # Only small ranking tuples survive the cursor. Full metadata and snippets
    # are fetched for the globally selected page, never the complete library.
    rows = database.execute(matches.statement.with_only_columns(
        Transcript.id, transcript_project_id(), Transcript.created_at,
        func.coalesce(Transcript.last_updated_at, Transcript.created_at))
        .execution_options(yield_per=100))
    try:
        candidates = [SearchCandidate(
            facet="transcripts", id=identity, project_id=owner, created_at=created,
            updated_at=updated, score=hits[str(identity)].score if request.q else 0.0,
        ) for identity, owner, created, updated in rows]
    finally:
        rows.close()

    def hydrate(page: list[SearchCandidate]) -> dict[UUID, SearchHit]:
        records = {record.id: record for record in corpus.matching([item.id for item in page])}
        rendered: dict[UUID, SearchHit] = {}
        for item in page:
            record = records[item.id]
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
) -> Iterator[tuple[SearchSource, TranscriptSearchCoverage]]:
    if not _SEARCH_SLOT.acquire(timeout=0.25):
        raise ApplicationError(503, "transcript_search_busy",
                               "Transcript search is busy. Try this read again shortly.")
    try:
        yield _source(database, project_id, request, index)
    finally:
        _SEARCH_SLOT.release()
