"""Dedicated and unified projections of the same authorized semantic corpus."""

from uuid import UUID

from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import ArtifactSearchIndex, literal_terms
from mnemonic_api.artifact_search_schemas import ArtifactSearchPage, ArtifactSearchRequest
from mnemonic_api.errors import semantic_unavailable
from mnemonic_api.search_diagnostics import TermDiagnostic, TermMatchCounts
from mnemonic_api.search_disclosure import ArtifactAppliedFilters, search_disclosure
from mnemonic_api.search_exploration import wants_diagnostics
from mnemonic_api.search_projects import ProjectSelection, selected_project_ids
from mnemonic_api.search_ranking import completed_semantic
from mnemonic_api.search_schemas import ArtifactFacetHit, ArtifactSearchCoverage, SearchRequest
from mnemonic_api.services.artifact_search import Corpus, _documents, _indexing, _signature
from mnemonic_api.services.artifact_semantic import (
    _coverage,
    artifact_semantic_hits,
    semantic_artifact_match,
)
from mnemonic_api.services.search_sources import SearchCandidate, SearchSource


def _term_counts(database, project_id, corpus, approved, index, query, terms):
    # Diagnostics continue to describe lexical term frequencies, including while
    # vector generation is pending. They use precisely the same access scope.
    result = index.search(_signature(project_id, corpus, True, approved),
        lambda: _documents(database, corpus, True, approved),
        query=query, fulltext=True, count=len(corpus))
    return index.term_counts(terms, True, result.searcher)


def semantic_artifact_page(
    database: Session, project_id: UUID, filters: ArtifactSearchRequest,
    corpus: Corpus, approved: set[UUID], query_vector: tuple[float, ...] | None,
    index: ArtifactSearchIndex, chunk_config: str | None,
) -> ArtifactSearchPage:
    if query_vector is None or chunk_config is None:
        raise semantic_unavailable()
    hits, coverage = artifact_semantic_hits(database, corpus, approved, query_vector, chunk_config)
    records = {artifact.id: artifact for artifact, _ in corpus}
    diagnostics = []
    if wants_diagnostics(filters.diagnostics, filters.q, len(hits)):
        diagnostics = [TermDiagnostic(term=term, matches=TermMatchCounts(artifacts=count))
            for term, count in _term_counts(database, project_id, corpus, approved, index,
                filters.q, literal_terms(filters.q, fold_accents=False)).items()]
    return ArtifactSearchPage(**search_disclosure(project_id, filters.q, fulltext=True,
        diagnostics=filters.diagnostics, query_mode=filters.query_mode,
        artifacts=ArtifactAppliedFilters.model_validate(
            filters.model_dump(include=set(ArtifactAppliedFilters.model_fields)))).model_dump(),
        detail=filters.detail, match_mode="semantic_passages", total_kind="ranked_candidates",
        embedding=coverage, term_diagnostics=diagnostics, score_type="semantic_reciprocal_rank",
        semantic=completed_semantic(partial_vectors=bool(coverage.pending or coverage.processing
            or coverage.failed or coverage.unavailable)),
        items=[semantic_artifact_match(database, records[hit.artifact_id], hit, filters.detail)
               for hit in hits[filters.offset:filters.offset + filters.limit]],
        total=len(hits), limit=filters.limit, offset=filters.offset, fulltext=True,
        indexing=_indexing(corpus), sensitive_content_withheld=coverage.withheld)


def semantic_artifact_source(
    database: Session, project_id: ProjectSelection, request: SearchRequest, corpus: Corpus,
    approved: set[UUID], query_vector: tuple[float, ...] | None, index: ArtifactSearchIndex,
    chunk_config: str | None,
) -> tuple[SearchSource, ArtifactSearchCoverage]:
    if query_vector is None or chunk_config is None:
        raise semantic_unavailable()
    hits, embedding = artifact_semantic_hits(database, corpus, approved, query_vector, chunk_config)
    records = {artifact.id: artifact for artifact, _ in corpus}
    by_id = {hit.artifact_id: hit for hit in hits}
    candidates = [SearchCandidate(facet="artifacts", id=hit.artifact_id,
        project_id=records[hit.artifact_id].project_id,
        created_at=records[hit.artifact_id].created_at,
        updated_at=records[hit.artifact_id].modified_at, score=hit.score) for hit in hits]

    def hydrate(page):
        result = {}
        for item in page:
            match = semantic_artifact_match(
                database, records[item.id], by_id[item.id], request.detail)
            match.rank = item.source_rank
            result[item.id] = ArtifactFacetHit(**item.fields(), artifact=match)
        return result

    project_coverage = {}
    for identity in selected_project_ids(project_id):
        project_corpus = [(artifact, extraction) for artifact, extraction in corpus
                          if artifact.project_id == identity]
        project_embedding, _ = _coverage(database, project_corpus, approved,
                                         len(query_vector), chunk_config)
        project_coverage[identity] = ArtifactSearchCoverage(
            indexing=_indexing(project_corpus), embedding=project_embedding,
            sensitive_content_withheld=project_embedding.withheld,
        )
    source = SearchSource(candidates, hydrate, lambda terms: _term_counts(
        database, project_id, corpus, approved, index, request.q, terms),
        coverage_by_project=project_coverage)
    return source, ArtifactSearchCoverage(indexing=_indexing(corpus), embedding=embedding,
        sensitive_content_withheld=embedding.withheld)
