"""Bounded semantic passages and honest embedding coverage at the agent boundary."""

import hashlib
import json
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt, model_validator
from pydantic_core import PydanticCustomError

from .input_errors import InputValidationError
from .search_query import QueryMode, constrained_query

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[StrictInt, Field(ge=0)]
EMBEDDING_COUNTS = ("ready", "pending", "processing", "failed", "unavailable", "withheld",
                    "empty", "passages", "truncated")


def validate_artifact_semantic(query: str | None, mode: QueryMode, fulltext: bool,
                               selected: bool = True) -> None:
    if constrained_query(query, mode):
        raise PydanticCustomError("semantic_requires_unconstrained_artifact_query",
                                  "Semantic artifact search requires unconstrained terms")
    if not fulltext:
        raise PydanticCustomError("artifact_semantic_requires_fulltext",
                                  "Artifact semantic search requires fulltext")
    if not (query or "").strip() or not selected:
        raise PydanticCustomError("artifact_semantic_requires_query_and_facet",
                                  "Artifact semantic search requires query and facet")


def _false_probability(value: object) -> Literal[False]:
    if value is not False:
        raise ValueError("Semantic scores are not probabilities")
    return False


class ArtifactPassageEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passage_id: Digest
    artifact_revision: Annotated[StrictInt, Field(ge=1)]
    text_sha256: Digest
    start_offset: Annotated[StrictInt, Field(ge=0, le=8_000_000)]
    end_offset: Annotated[StrictInt, Field(ge=1, le=8_000_000)]
    model: Annotated[str, Field(min_length=1, max_length=300)]
    chunk_config: Annotated[str, Field(min_length=1, max_length=100)]
    token_count: Annotated[StrictInt, Field(ge=1, le=8192)]
    token_limit: Annotated[StrictInt, Field(ge=1, le=8192)]
    cosine_similarity: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False, strict=True)]
    score_is_probability: Annotated[Literal[False], BeforeValidator(_false_probability)] = False
    score_type: Literal["cosine_similarity"] = "cosine_similarity"

    @model_validator(mode="after")
    def bounded_span(self) -> Self:
        if not 0 < self.end_offset - self.start_offset <= 1500 or (
            self.token_count > self.token_limit or self.score_is_probability
        ):
            raise ValueError("Passage evidence must describe a bounded, non-probabilistic span")
        return self

    def matches_identity(self, identity: UUID, revision: int) -> bool:
        expected = hashlib.sha256(json.dumps([
            str(identity), revision, self.text_sha256, self.model, self.chunk_config,
            self.start_offset, self.end_offset,
        ], separators=(",", ":")).encode()).hexdigest()
        return self.artifact_revision == revision and self.passage_id == expected


class ArtifactEmbeddingCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Annotated[str, Field(min_length=1, max_length=300)]
    chunk_config: Annotated[str, Field(min_length=1, max_length=100)]
    state: Literal["ready", "incomplete", "unavailable"]
    ready: Count = 0
    pending: Count = 0
    processing: Count = 0
    failed: Count = 0
    unavailable: Count = 0
    withheld: Count = 0
    empty: Count = 0
    passages: Count = 0
    truncated: Count = 0
    total_kind: Literal["ranked_candidates"] = "ranked_candidates"
    score_type: Literal["semantic_reciprocal_rank"] = "semantic_reciprocal_rank"

    @property
    def partial_vectors(self) -> bool:
        return bool(self.pending or self.processing or self.failed or self.unavailable)

    @model_validator(mode="after")
    def coherent_coverage(self) -> Self:
        incomplete = self.partial_vectors or self.withheld or self.truncated
        expected = "unavailable" if not self.ready and self.unavailable else (
            "incomplete" if incomplete else "ready")
        available = sum(getattr(self, name) for name in (
            "ready", "pending", "processing", "failed", "unavailable", "empty"))
        if (self.state != expected or self.passages < self.ready or self.truncated > available
                or not self.ready and self.passages):
            raise ValueError("Embedding coverage counts disagree with its state")
        return self


class ArtifactMatchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence: Literal["lexical", "semantic"] = "lexical"
    passage: ArtifactPassageEvidence | None = None

    @model_validator(mode="after")
    def coherent_evidence(self) -> Self:
        if (self.evidence == "semantic") != (self.passage is not None):
            raise ValueError("Only semantic artifact evidence carries a passage")
        return self


def artifact_evidence_matches(item, semantic: bool,
                              embedding: ArtifactEmbeddingCoverage | None = None) -> bool:
    if item.evidence != ("semantic" if semantic else "lexical"):
        return False
    if not semantic:
        return item.passage is None
    passage, artifact = item.passage, item.artifact
    if passage is None or embedding is None:
        return False
    return (item.matched_fields == ["content"] and artifact.deleted_at is None
            and artifact.content_available and artifact.extraction.status == "ready"
            and passage.matches_identity(artifact.id, artifact.revision)
            and (passage.model, passage.chunk_config) == (embedding.model, embedding.chunk_config)
            and item.snippet is not None
            and len(item.snippet) == min(1000, passage.end_offset - passage.start_offset))


def validate_tool_artifact_semantic(query: str | None, mode: QueryMode, fulltext: bool) -> None:
    from .validation_rules import VALIDATION_RULES

    try:
        validate_artifact_semantic(query, mode, fulltext)
    except PydanticCustomError as error:
        field, message = VALIDATION_RULES[error.type]
        raise InputValidationError(f"Mnemonic rejected the input. Check: {field} "
                        f"({error.type}). {message}") from None
