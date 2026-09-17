"""Semantic passages identify evidence and coverage without implying lexical matches."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArtifactPassageEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passage_id: str = Field(pattern="^[0-9a-f]{64}$")
    artifact_revision: int = Field(ge=1)
    text_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    start_offset: int = Field(ge=0, le=8_000_000)
    end_offset: int = Field(ge=1, le=8_000_000)
    model: str = Field(min_length=1, max_length=300)
    chunk_config: str = Field(min_length=1, max_length=100)
    token_count: int = Field(ge=1, le=8192)
    token_limit: int = Field(ge=1, le=8192)
    cosine_similarity: float = Field(ge=-1, le=1, allow_inf_nan=False)
    score_is_probability: Literal[False] = False
    score_type: Literal["cosine_similarity"] = "cosine_similarity"

    @model_validator(mode="after")
    def bounded_span(self) -> Self:
        if (not 0 < self.end_offset - self.start_offset <= 1500
                or self.token_count > self.token_limit):
            raise ValueError("Passage evidence must identify one bounded source span")
        return self


class ArtifactEmbeddingCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=300)
    chunk_config: str = Field(min_length=1, max_length=100)
    state: Literal["ready", "incomplete", "unavailable"]
    ready: int = Field(default=0, ge=0)
    pending: int = Field(default=0, ge=0)
    processing: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    unavailable: int = Field(default=0, ge=0)
    withheld: int = Field(default=0, ge=0)
    empty: int = Field(default=0, ge=0)
    passages: int = Field(default=0, ge=0)
    truncated: int = Field(default=0, ge=0)
    total_kind: Literal["ranked_candidates"] = "ranked_candidates"
    score_type: Literal["semantic_reciprocal_rank"] = "semantic_reciprocal_rank"
