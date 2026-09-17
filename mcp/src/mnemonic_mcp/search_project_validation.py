"""Validate selected-project provenance and aggregation before exposing search results."""

from uuid import UUID

from .artifact_semantic import EMBEDDING_COUNTS, artifact_evidence_matches
from .search_models import ArtifactFacetHit, SearchPage, SearchRequest, TranscriptFacetHit
from .search_query import constrained_query


def project_coverage_matches(
    page: SearchPage, selection: UUID | tuple[UUID, ...], request: SearchRequest,
) -> bool:
    expected = {selection} if isinstance(selection, UUID) else set(selection)
    projects = {row.project_id: row for row in page.project_coverage}
    if set(projects) != expected or len(projects) != len(page.project_coverage):
        return False
    if page.indexing_incomplete != any(row.indexing_incomplete for row in projects.values()):
        return False
    if any(hit.project_id not in projects for hit in page.items):
        return False
    return (_totals_match(page, projects) and _aggregate_matches(page, projects)
            and all(_coverage_is_honest(row, request) for row in projects.values())
            and _returned_coverage_matches(page, projects, request))


def _totals_match(page: SearchPage, projects) -> bool:
    for facet, total in page.facet_totals.model_dump().items():
        if sum(getattr(row.facet_totals, facet) for row in projects.values()) != total:
            return False
        if any(sum(hit.project_id == identity and hit.facet == facet for hit in page.items)
               > getattr(row.facet_totals, facet) for identity, row in projects.items()):
            return False
    return True


def _aggregate_matches(page: SearchPage, projects) -> bool:
    aggregate = page.coverage.artifacts
    if any(row.coverage.artifacts.enabled != aggregate.enabled for row in projects.values()):
        return False
    for name, count in aggregate.indexing.model_dump().items():
        if sum(getattr(row.coverage.artifacts.indexing, name) for row in projects.values()) != count:
            return False
    if sum(row.coverage.artifacts.sensitive_content_withheld for row in projects.values()) != (
        aggregate.sensitive_content_withheld
    ):
        return False
    return (page.coverage.transcripts.indexing_incomplete == any(
        row.coverage.transcripts.indexing_incomplete for row in projects.values()
    ) and page.coverage.transcripts.unsegmented_content_omitted == sum(
        row.coverage.transcripts.unsegmented_content_omitted for row in projects.values()
    ) and _embedding_aggregate(aggregate.embedding, projects))



def _coverage_is_honest(row, request: SearchRequest) -> bool:
    artifact = row.coverage.artifacts
    expected_embedding = (request.filters.artifacts.semantic and "artifacts" in request.facets
                          and artifact.enabled)
    if (artifact.embedding is not None) != expected_embedding:
        return False
    if artifact.embedding is not None and (
        row.facet_totals.artifacts > artifact.embedding.ready
        or artifact.embedding.withheld != artifact.sensitive_content_withheld
    ):
        return False
    transcripts = row.coverage.transcripts
    if transcripts.unsegmented_content_omitted and (
        not transcripts.indexing_incomplete or not request.fulltext
        or not constrained_query(request.q, request.query_mode) or "transcripts" not in request.facets
    ):
        return False
    if not request.fulltext and artifact.sensitive_content_withheld:
        return False
    incomplete = "artifacts" in request.facets and (
        not artifact.enabled or artifact.indexing.pending or artifact.indexing.failed
        or artifact.indexing.truncated or artifact.sensitive_content_withheld
    ) or row.coverage.transcripts.indexing_incomplete or (
        artifact.embedding is not None and artifact.embedding.state != "ready"
    )
    return not incomplete or row.indexing_incomplete


def _returned_coverage_matches(page: SearchPage, projects, request: SearchRequest) -> bool:
    for project_id, row in projects.items():
        hits = [hit for hit in page.items if hit.project_id == project_id]
        incomplete_transcripts = any(
            isinstance(hit, TranscriptFacetHit) and (
                hit.transcript.status != "ready" or hit.transcript.copy_status != "ready"
                or hit.transcript.index_status != "ready" or hit.transcript.truncated
                or hit.transcript.normalization_status != "ready"
                or hit.transcript.normalization_incomplete
            ) for hit in hits
        )
        if incomplete_transcripts and not row.coverage.transcripts.indexing_incomplete:
            return False
        if any(isinstance(hit, ArtifactFacetHit) and not artifact_evidence_matches(
            hit.artifact, request.filters.artifacts.semantic, row.coverage.artifacts.embedding,
        ) for hit in hits):
            return False
        artifacts = [hit.artifact.artifact for hit in hits if isinstance(hit, ArtifactFacetHit)
                     and hit.artifact.artifact.deleted_at is None]
        if not _returned_artifact_counts(artifacts, row.coverage.artifacts, request):
            return False
    return True


def _returned_artifact_counts(artifacts, coverage, request: SearchRequest) -> bool:
    expected = {"pending": 0, "ready": 0, "failed": 0, "truncated": 0}
    for artifact in artifacts:
        extraction = artifact.extraction
        status = "pending" if extraction.status == "processing" else extraction.status
        if status in {"pending", "ready", "failed"}:
            expected[status] += 1
        expected["truncated"] += int(status == "ready" and extraction.truncated)
    withheld = sum(artifact.sensitive for artifact in artifacts) if request.fulltext else 0
    return (all(getattr(coverage.indexing, name) >= count for name, count in expected.items())
            and coverage.sensitive_content_withheld >= withheld)


def _embedding_aggregate(aggregate, projects) -> bool:
    embeddings = [row.coverage.artifacts.embedding for row in projects.values()]
    if aggregate is None:
        return all(coverage is None for coverage in embeddings)
    if any(coverage is None or (coverage.model, coverage.chunk_config)
           != (aggregate.model, aggregate.chunk_config) for coverage in embeddings):
        return False
    return all(sum(getattr(coverage, name) for coverage in embeddings) == getattr(aggregate, name)
               for name in EMBEDDING_COUNTS)
