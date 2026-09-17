"""Project-wide search ranks complete facet pools, then hydrates one global page."""

import logging
from contextlib import ExitStack

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import ArtifactSearchIndex, literal_terms
from mnemonic_api.config import DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES
from mnemonic_api.models import Project
from mnemonic_api.search_diagnostics import SearchScope, TermDiagnostic, TermMatchCounts
from mnemonic_api.search_disclosure import (
    ArtifactAppliedFilters,
    SearchDisclosure,
    TranscriptAppliedFilters,
    WorkAppliedFilters,
    search_disclosure,
)
from mnemonic_api.search_exploration import wants_diagnostics
from mnemonic_api.search_exploration_schemas import DiagnosticsMode
from mnemonic_api.search_projects import ProjectSelection, selected_project_ids
from mnemonic_api.search_ranking import (
    FacetScoreTypes,
    FacetTotalKinds,
    SemanticDisposition,
    completed_semantic,
    search_ranking,
)
from mnemonic_api.search_schemas import (
    ArtifactSearchCoverage,
    FacetTotals,
    ProjectSearchCoverage,
    SearchCoverage,
    SearchFacet,
    SearchPage,
    SearchRequest,
    SearchSort,
    TranscriptSearchCoverage,
)
from mnemonic_api.search_timing import refresh_cache
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


def _term_diagnostics(sources: dict[SearchFacet, SearchSource], query: str,
                      mode: DiagnosticsMode = "on_empty") -> list[TermDiagnostic]:
    total = sum(len(source.candidates) for source in sources.values())
    if not wants_diagnostics(mode, query, total):
        return []
    terms = literal_terms(query, fold_accents=False)
    counts = {facet: source.term_counts(terms) for facet, source in sources.items()}
    return [TermDiagnostic(term=term, matches=TermMatchCounts(**{
        facet: matches[term] for facet, matches in counts.items()
    })) for term in terms]


def _scope(sources: dict[SearchFacet, SearchSource], request: SearchRequest) -> SearchScope:
    transcripts = "not_selected"
    if "transcripts" in sources:
        transcripts = "searched"
    elif "facets" not in request.model_fields_set:
        transcripts = "omitted_by_default"
    return SearchScope(searched_facets=list(sources), transcripts=transcripts)


def _incomplete(coverage: SearchCoverage, request: SearchRequest) -> bool:
    artifacts = coverage.artifacts
    indexing = artifacts.indexing
    return (not artifacts.enabled and "artifacts" in request.facets) or bool(
        indexing.pending or indexing.failed or indexing.truncated
        or artifacts.sensitive_content_withheld or coverage.transcripts.indexing_incomplete
        or (artifacts.embedding is not None and artifacts.embedding.state != "ready")
    )


def _project_coverage(
    projects: list[Project], sources, request, aggregate,
) -> list[ProjectSearchCoverage]:
    result = []
    for project in projects:
        coverage = SearchCoverage(artifacts=ArtifactSearchCoverage(
            enabled=aggregate.artifacts.enabled,
        ))
        for source in sources.values():
            facet_coverage = source.coverage_by_project.get(project.id)
            if isinstance(facet_coverage, ArtifactSearchCoverage):
                coverage.artifacts = facet_coverage
            elif isinstance(facet_coverage, TranscriptSearchCoverage):
                coverage.transcripts = facet_coverage
        result.append(ProjectSearchCoverage(
            project_id=project.id, project_name=project.name, project_slug=project.slug,
            facet_totals=FacetTotals(**{
                facet: sum(item.project_id == project.id for item in source.candidates)
                for facet, source in sources.items()
            }), coverage=coverage, indexing_incomplete=_incomplete(coverage, request),
        ))
    return result


def _semantic_disposition(sources: dict[SearchFacet, SearchSource], request: SearchRequest,
                          coverage: SearchCoverage) -> SemanticDisposition:
    if not (("work_items" in sources and request.filters.work_items.semantic)
            or ("artifacts" in sources and request.filters.artifacts.semantic)):
        return SemanticDisposition()
    embedding = coverage.artifacts.embedding
    partial = embedding is not None and bool(
        embedding.pending or embedding.processing or embedding.failed or embedding.unavailable)
    return completed_semantic(partial_vectors=partial)


def _page(
    sources: dict[SearchFacet, SearchSource], request: SearchRequest, coverage: SearchCoverage,
    project_id: ProjectSelection, projects: list[Project],
) -> SearchPage:
    if request.q:
        for source in sources.values():
            normalize_relevance(source.candidates)
    ordered = order_candidates(sources, request)
    ranks: dict[SearchFacet, int] = {}
    for position, candidate in enumerate(ordered, 1):
        candidate.rank = position
        candidate.score_type = "unified_reciprocal_rank" if request.q else "none"
        ranks[candidate.facet] = ranks.get(candidate.facet, 0) + 1
        candidate.source_rank = ranks[candidate.facet]
    selected = ordered[request.offset:request.offset + request.limit]
    results = {}
    for facet, source in sources.items():
        page = [item for item in selected if item.facet == facet]
        if page:
            results.update({(facet, identity): hit
                            for identity, hit in source.hydrate(page).items()})
    incomplete = _incomplete(coverage, request)
    ranking = {facet: search_ranking(
        request.q, request.query_mode, work=facet == "work_items",
        semantic=(facet == "work_items" and request.filters.work_items.semantic)
        or (facet == "artifacts" and request.filters.artifacts.semantic))
        for facet in sources}
    kinds = {value.total_kind for value in ranking.values()}
    return SearchPage(
        score_type="unified_reciprocal_rank" if request.q else "none",
        total_kind="mixed" if len(kinds) > 1 else next(
            iter(kinds), "lexical_matches" if request.q else "browsed_records"),
        facet_total_kinds=FacetTotalKinds(**{facet: value.total_kind
                                          for facet, value in ranking.items()}),
        facet_score_types=FacetScoreTypes(**{facet: value.score_type
                                          for facet, value in ranking.items()}),
        semantic=_semantic_disposition(sources, request, coverage),
        work_rank_scope="work_items",
        project_coverage=_project_coverage(projects, sources, request, coverage),
        **_disclosure(project_id, sources, request).model_dump(),
        detail=request.detail,
        search_scope=_scope(sources, request),
        term_diagnostics=_term_diagnostics(sources, request.q, request.diagnostics),
        tag_counts=(sources["work_items"].tag_counts(request.tag_counts)
                    if request.tag_counts is not None
                    and sources["work_items"].tag_counts else None),
        items=[results[(item.facet, item.id)] for item in selected], total=len(ordered),
        limit=request.limit, offset=request.offset,
        facet_totals=FacetTotals(**{facet: len(source.candidates)
                                  for facet, source in sources.items()}),
        coverage=coverage, indexing_incomplete=incomplete,
    )


def _disclosure(
    project_id: ProjectSelection, sources: dict[SearchFacet, SearchSource], request: SearchRequest,
) -> SearchDisclosure:
    filters = request.filters
    return search_disclosure(
        project_id, request.q, fulltext=request.fulltext, semantic=filters.work_items.semantic,
        query_mode=request.query_mode, diagnostics=request.diagnostics,
        work_items=WorkAppliedFilters.model_validate(
            filters.work_items.model_dump(exclude={"semantic"}),
        ) if "work_items" in sources else None,
        artifacts=ArtifactAppliedFilters.model_validate(
            filters.artifacts.model_dump(),
        ) if "artifacts" in sources else None,
        transcripts=TranscriptAppliedFilters.model_validate(
            filters.transcripts.model_dump(),
        ) if "transcripts" in sources else None,
    )


def _search_locked(
    database: Session, project_id: ProjectSelection, request: SearchRequest,
    artifact_index: ArtifactSearchIndex, transcript_index: ArtifactSearchIndex,
    *, artifacts_enabled: bool, human_dashboard: bool, embedder: Embedder,
    query_vector: tuple[float, ...] | None, maximum_transcript_content_bytes: int,
    artifact_chunk_config: str | None,
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
                database, project_id, request, as_of, embedder=embedder,
                query_vector=query_vector if request.filters.work_items.semantic else None,
            )
        if "artifacts" in request.facets and artifacts_enabled:
            sources["artifacts"], coverage.artifacts = artifact_source(
                database, project_id, request, artifact_index, human_dashboard=human_dashboard,
                query_vector=query_vector if request.filters.artifacts.semantic else None,
                artifact_chunk_config=artifact_chunk_config,
            )
        if "transcripts" in request.facets:
            sources["transcripts"], coverage.transcripts = stack.enter_context(transcript_source(
                database, project_id, request, transcript_index,
                maximum_content_bytes=maximum_transcript_content_bytes,
            ))
        projects = list(database.scalars(select(Project).where(
            Project.id.in_(selected_project_ids(project_id)),
        ).order_by(Project.id)))
        return _page(sources, request, coverage, project_id, projects), updates


def search(
    database: Session, project_id: ProjectSelection, request: SearchRequest,
    artifact_index: ArtifactSearchIndex, transcript_index: ArtifactSearchIndex,
    *, artifacts_enabled: bool, human_dashboard: bool, embedder: Embedder,
    query_vector: tuple[float, ...] | None = None,
    artifact_chunk_config: str | None = None,
    maximum_transcript_content_bytes: int = DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES,
) -> SearchPage:
    # Published work, artifact revisions/sensitivity and transcript snapshots all
    # use this project lock. Acquire it before selecting any candidate so a queued
    # search observes preceding sensitivity changes. Keep it through page hydration
    # and the optional dashboard sensitive-read audit; embeddings publish afterward.
    projects = selected_project_ids(project_id)
    with project_mutation(database, projects[0], additional_project_ids=projects[1:],
                          protected=True, domain_seconds=120):
        page, updates = _search_locked(
            database, project_id, request, artifact_index, transcript_index,
            artifacts_enabled=artifacts_enabled, human_dashboard=human_dashboard,
            embedder=embedder, query_vector=query_vector,
            maximum_transcript_content_bytes=maximum_transcript_content_bytes,
            artifact_chunk_config=artifact_chunk_config,
        )
        database.commit()
    page.semantic.cache_refresh = refresh_cache(
        "work_semantic", bool(updates), lambda: persist_embedding_updates(database, updates))
    return page
