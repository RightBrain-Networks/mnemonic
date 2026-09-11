"""Project-wide search ranks complete facet pools, then hydrates one global page."""

import logging
from contextlib import ExitStack
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import ArtifactSearchIndex
from mnemonic_api.config import DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES
from mnemonic_api.errors import semantic_unavailable
from mnemonic_api.search_schemas import (
    ArtifactSearchCoverage,
    FacetTotals,
    SearchCoverage,
    SearchFacet,
    SearchPage,
    SearchRequest,
    SearchSort,
)
from mnemonic_api.semantic import Embedder, EmbeddingCacheUpdate, persist_embedding_updates
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.search_artifacts import artifact_source
from mnemonic_api.services.search_sources import SearchCandidate, SearchSource
from mnemonic_api.services.search_transcripts import transcript_source
from mnemonic_api.services.search_work import work_source

logger = logging.getLogger(__name__)


def normalize_relevance(candidates: list[SearchCandidate]) -> None:
    """Fuse incompatible PostgreSQL/Tantivy scores using tied reciprocal ranks.

    Every source contributes equally: its first result scores 1/61, its second
    1/62, and so on. Equal native scores share their first rank. Raw engine scores
    remain available inside artifact/transcript payloads; the envelope is sortable
    across sources and independent of a caller's pagination or facet grouping.
    """
    ordered = sorted(candidates, key=lambda item: item.score, reverse=True)
    previous_score: float | None = None
    rank = 1
    for position, candidate in enumerate(ordered, start=1):
        native_score = candidate.score
        if native_score != previous_score:
            rank = position
        candidate.score = 1.0 / (60 + rank)
        previous_score = native_score


def sort_candidates(candidates: list[SearchCandidate], sort: SearchSort) -> list[SearchCandidate]:
    # Stable ties always use updated time descending, facet name and UUID. An
    # empty query has zero relevance, so defaults naturally show recent items.
    ordered = sorted(candidates, key=lambda item: (item.facet, item.id.int))
    ordered.sort(key=lambda item: item.updated_at, reverse=True)
    attribute = "score" if sort.by == "relevance" else sort.by
    ordered.sort(key=lambda item: getattr(item, attribute), reverse=sort.direction == "desc")
    return ordered


def order_candidates(sources: dict[SearchFacet, SearchSource], request: SearchRequest):
    candidates: list[SearchCandidate] = []
    grouped: set[SearchFacet] = set()
    for group in request.facet_order:
        grouped.add(group.facet)
        source = sources.get(group.facet)
        if source is not None:
            candidates.extend(sort_candidates(source.candidates, group.sort or request.sort))
    trailing = [item for facet, source in sources.items() if facet not in grouped
                for item in source.candidates]
    candidates.extend(sort_candidates(trailing, request.sort))
    return candidates


def _page(
    sources: dict[SearchFacet, SearchSource], request: SearchRequest, coverage: SearchCoverage,
) -> SearchPage:
    if request.q:
        for source in sources.values():
            normalize_relevance(source.candidates)
    ordered = order_candidates(sources, request)
    selected = ordered[request.offset:request.offset + request.limit]
    results = {}
    for facet, source in sources.items():
        page = [item for item in selected if item.facet == facet]
        if page:
            results.update({(facet, identity): hit
                            for identity, hit in source.hydrate(page).items()})
    artifact_coverage = coverage.artifacts
    indexing = artifact_coverage.indexing
    incomplete = (not artifact_coverage.enabled and "artifacts" in request.facets) or bool(
        indexing.pending or indexing.failed or indexing.truncated
        or artifact_coverage.sensitive_content_withheld
        or coverage.transcripts.indexing_incomplete
    )
    return SearchPage(
        items=[results[(item.facet, item.id)] for item in selected], total=len(ordered),
        limit=request.limit, offset=request.offset,
        facet_totals=FacetTotals(**{facet: len(source.candidates)
                                  for facet, source in sources.items()}),
        coverage=coverage, indexing_incomplete=incomplete,
    )


def _search_locked(
    database: Session, project_id: UUID, request: SearchRequest,
    artifact_index: ArtifactSearchIndex, transcript_index: ArtifactSearchIndex,
    *, artifacts_enabled: bool, human_dashboard: bool, embedder: Embedder,
    query_vector: tuple[float, ...] | None, maximum_transcript_content_bytes: int,
) -> tuple[SearchPage, list[EmbeddingCacheUpdate]]:
    as_of = database.scalar(select(func.clock_timestamp()))
    if as_of is None:
        raise RuntimeError("Database did not provide a search timestamp")
    sources: dict[SearchFacet, SearchSource] = {}
    updates: list[EmbeddingCacheUpdate] = []
    coverage = SearchCoverage(artifacts=ArtifactSearchCoverage(enabled=artifacts_enabled))
    with ExitStack() as stack:
        if "work_items" in request.facets:
            sources["work_items"], updates = work_source(
                database, project_id, request, as_of, embedder=embedder, query_vector=query_vector,
            )
        if "artifacts" in request.facets and artifacts_enabled:
            sources["artifacts"], coverage.artifacts = artifact_source(
                database, project_id, request, artifact_index, human_dashboard=human_dashboard,
            )
        if "transcripts" in request.facets:
            sources["transcripts"], coverage.transcripts = stack.enter_context(transcript_source(
                database, project_id, request, transcript_index,
                maximum_content_bytes=maximum_transcript_content_bytes,
            ))
        return _page(sources, request, coverage), updates


def search(
    database: Session, project_id: UUID, request: SearchRequest,
    artifact_index: ArtifactSearchIndex, transcript_index: ArtifactSearchIndex,
    *, artifacts_enabled: bool, human_dashboard: bool, embedder: Embedder,
    query_vector: tuple[float, ...] | None = None,
    maximum_transcript_content_bytes: int = DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES,
) -> SearchPage:
    # Published work, artifact revisions/sensitivity and transcript snapshots all
    # use this project lock. Acquire it before selecting any candidate so a queued
    # search observes preceding sensitivity changes. Keep it through page hydration
    # and the optional dashboard sensitive-read audit; embeddings publish afterward.
    with project_mutation(database, project_id, protected=True, domain_seconds=120):
        page, updates = _search_locked(
            database, project_id, request, artifact_index, transcript_index,
            artifacts_enabled=artifacts_enabled, human_dashboard=human_dashboard,
            embedder=embedder, query_vector=query_vector,
            maximum_transcript_content_bytes=maximum_transcript_content_bytes,
        )
        database.commit()
    try:
        persist_embedding_updates(database, updates)
    except Exception as exc:
        logger.error("Unified semantic cache refresh failed (%s)", type(exc).__name__)
        raise semantic_unavailable() from None
    return page
