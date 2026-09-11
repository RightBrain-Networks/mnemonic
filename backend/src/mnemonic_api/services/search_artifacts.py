"""Artifact facet candidates share the existing current-revision search index."""

from uuid import UUID

from sqlalchemy.orm import Session

from mnemonic_api.artifact_access_schemas import ArtifactAccessRequest
from mnemonic_api.artifact_index import ArtifactSearchIndex
from mnemonic_api.artifact_search_schemas import ArtifactSearchMatch
from mnemonic_api.search_schemas import (
    ArtifactFacetHit,
    ArtifactSearchCoverage,
    SearchHit,
    SearchRequest,
)
from mnemonic_api.services.artifact_approvals import require_sensitive_access
from mnemonic_api.services.artifact_search import (
    Corpus,
    _corpus,
    _documents,
    _indexing,
    _match,
    _signature,
)
from mnemonic_api.services.artifacts import artifact_read
from mnemonic_api.services.search_sources import SearchCandidate, SearchSource


def _approved_contents(
    database: Session, corpus: Corpus, request: SearchRequest, human_dashboard: bool,
) -> set[UUID]:
    # Unified agent searches always withhold sensitive bodies, including targeted
    # filters. The dedicated read/search API owns the single-use approval flow.
    approved: set[UUID] = set()
    if not request.q or not request.fulltext or not human_dashboard:
        return approved
    for artifact, _ in corpus:
        if artifact.sensitive and artifact.deleted_at is None:
            require_sensitive_access(
                database, artifact, "search", ArtifactAccessRequest(),
                request.model_dump(mode="json"), human_dashboard=True,
            )
            approved.add(artifact.id)
    return approved


def artifact_source(
    database: Session, project_id: UUID, request: SearchRequest, index: ArtifactSearchIndex,
    *, human_dashboard: bool = False,
) -> tuple[SearchSource, ArtifactSearchCoverage]:
    corpus = _corpus(database, project_id, request.filters.artifacts)
    approved = _approved_contents(database, corpus, request, human_dashboard)
    records = {str(artifact.id): artifact for artifact, _ in corpus}
    hits = {}
    searcher = None
    if request.q:
        result = index.search(
            _signature(project_id, corpus, request.fulltext, approved),
            lambda: _documents(database, corpus, request.fulltext, approved),
            query=request.q, fulltext=request.fulltext, count=len(corpus),
        )
        hits = {hit.identity: hit for hit in result.hits}
        searcher = result.searcher
    candidates = [SearchCandidate(
        facet="artifacts", id=record.id, created_at=record.created_at,
        updated_at=record.modified_at,
        score=hits[identity].score if request.q else 0.0,
    ) for identity, record in records.items() if not request.q or identity in hits]
    coverage = ArtifactSearchCoverage(
        indexing=_indexing(corpus),
        sensitive_content_withheld=sum(
            1 for artifact, _ in corpus if request.fulltext and artifact.sensitive
            and artifact.deleted_at is None and artifact.id not in approved
        ),
    )

    def hydrate(page: list[SearchCandidate]) -> dict[UUID, SearchHit]:
        rendered: dict[UUID, SearchHit] = {}
        for item in page:
            identity = str(item.id)
            match = (_match(database, index, records, hits[identity], request.q, searcher)
                     if request.q else ArtifactSearchMatch(
                         artifact=artifact_read(database, records[identity]), score=0,
                         matched_fields=[],
                     ))
            rendered[item.id] = ArtifactFacetHit(**item.fields(), artifact=match)
        return rendered

    return SearchSource(candidates, hydrate), coverage
