"""Stream current, authorized passage vectors and hydrate only selected evidence."""

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import any_, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as SQLUUID
from sqlalchemy.orm import Session

from mnemonic_api import artifact_passages as passages
from mnemonic_api.artifact_semantic_schemas import (
    ArtifactEmbeddingCoverage,
    ArtifactPassageEvidence,
)
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Artifact, ArtifactExtraction
from mnemonic_api.semantic import cosine_similarity

MAX_SEARCH_PASSAGES = 100_000
VECTOR_STREAM_BATCH = 64


@dataclass(frozen=True)
class SemanticArtifactHit:
    artifact_id: UUID
    evidence: ArtifactPassageEvidence
    score: float
    rank: int


def _identities(column, values):
    # One typed array avoids PostgreSQL's bind-parameter ceiling on large corpora.
    return column == any_(cast(list(values), ARRAY(SQLUUID)))


def _coverage(database: Session, corpus, approved: set[UUID], dimensions: int,
              chunk_config: str):
    available = {artifact.id: (artifact, extraction) for artifact, extraction in corpus
                 if artifact.deleted_at is None
                 and (not artifact.sensitive or artifact.id in approved)}
    indexes = database.execute(select(passages.INDEXES).where(
        _identities(passages.INDEXES.c.artifact_id, available))) if available else []
    by_id = {row.artifact_id: row._mapping for row in indexes}
    coverage = ArtifactEmbeddingCoverage(model=passages.PASSAGE_MODEL,
        chunk_config=chunk_config, state="ready", withheld=sum(
            1 for artifact, _ in corpus if artifact.deleted_at is None
            and artifact.sensitive and artifact.id not in approved))
    ready = {}
    for identity, (artifact, extraction) in available.items():
        index = by_id.get(identity)
        state = _index_state(artifact, extraction, index, dimensions, chunk_config)
        setattr(coverage, state, getattr(coverage, state) + 1)
        if extraction and extraction.truncated:
            coverage.truncated += 1
        if state == "ready" and index is not None:
            ready[index["id"]] = index
            coverage.passages += index["next_ordinal"]
    if coverage.pending or coverage.processing or coverage.failed or coverage.unavailable \
            or coverage.withheld or coverage.truncated:
        coverage.state = "unavailable" if not ready and coverage.unavailable else "incomplete"
    return coverage, ready


def _index_state(artifact: Artifact, extraction: ArtifactExtraction | None,
                 index, dimensions: int, chunk_config: str) -> str:
    if extraction is None or extraction.status in {"pending", "processing"}:
        return "pending"
    if extraction.status != "ready":
        return "failed"
    witness = (artifact.revision, extraction.text_sha256, passages.PASSAGE_MODEL,
               chunk_config)
    if index is None or tuple(index[key] for key in (
        "revision", "text_sha256", "model", "chunk_config")) != witness:
        return "pending"
    if index["status"] == "ready":
        if not index["total_chars"]:
            return "empty"
        return "ready" if index["dimensions"] == dimensions else "unavailable"
    if index["status"] == "failed" and index["error_code"] == "artifact_embedding_unavailable":
        return "unavailable"
    return index["status"]


def artifact_semantic_hits(database: Session, corpus, approved: set[UUID],
                           query_vector: tuple[float, ...], chunk_config: str):
    coverage, ready = _coverage(database, corpus, approved, len(query_vector), chunk_config)
    if not ready:
        return [], coverage
    columns = passages.PASSAGES.c
    count = database.scalar(select(func.count()).select_from(passages.PASSAGES)
                            .where(_identities(columns.index_id, ready))) or 0
    if count > MAX_SEARCH_PASSAGES:
        raise ApplicationError(503, "artifact_semantic_capacity",
                               "Artifact semantic capacity reached; narrow the source filters.")
    best: dict[UUID, tuple[float, Any]] = {}
    rows = database.execute(select(passages.PASSAGES).where(_identities(columns.index_id, ready))
        .order_by(columns.index_id, columns.ordinal)
        .execution_options(yield_per=VECTOR_STREAM_BATCH))
    try:
        for row in rows:
            index = ready[row.index_id]
            score = max(-1.0, min(1.0, cosine_similarity(query_vector, row.vector)))
            identity = index["artifact_id"]
            if identity not in best or score > best[identity][0]:
                # Do not retain the vector beyond this bounded cursor batch.
                best[identity] = score, ArtifactPassageEvidence(
                    passage_id=row.passage_id, artifact_revision=index["revision"],
                    text_sha256=index["text_sha256"], start_offset=row.start_offset,
                    end_offset=row.end_offset, model=index["model"],
                    chunk_config=index["chunk_config"], cosine_similarity=score,
                    token_count=row.token_count, token_limit=index["token_limit"])
    finally:
        rows.close()
    ordered = sorted(best, key=lambda identity: (-best[identity][0], identity.int))
    hits = []
    rank, previous_score = 1, None
    for position, identity in enumerate(ordered, 1):
        if best[identity][0] != previous_score:
            rank, previous_score = position, best[identity][0]
        hits.append(SemanticArtifactHit(identity, best[identity][1], 1.0 / rank, position))
    return hits, coverage


def semantic_artifact_match(database: Session, artifact: Artifact, hit: SemanticArtifactHit,
                            detail: Literal["compact", "full"]):
    # Imports here avoid a service cycle; these projections never select bodies.
    from mnemonic_api.artifact_search_schemas import ArtifactSearchMatch, CompactArtifactMatch
    from mnemonic_api.services.artifact_search import compact_artifact_read
    from mnemonic_api.services.artifacts import artifact_read

    evidence = hit.evidence
    snippet = database.scalar(select(func.substr(ArtifactExtraction.normalized_text,
        evidence.start_offset + 1, min(1000, evidence.end_offset - evidence.start_offset))).where(
            ArtifactExtraction.artifact_id == artifact.id,
            ArtifactExtraction.revision == evidence.artifact_revision,
            ArtifactExtraction.text_sha256 == evidence.text_sha256))
    fields: dict[str, Any] = {"score": hit.score, "snippet": snippet, "matched_fields": ["content"],
              "evidence": "semantic", "passage": evidence, "rank": hit.rank,
              "score_type": "semantic_reciprocal_rank"}
    if detail == "compact":
        return CompactArtifactMatch(artifact=compact_artifact_read(database, artifact), **fields)
    return ArtifactSearchMatch(artifact=artifact_read(database, artifact), **fields)
