"""Ordering signals and inference availability are not calibrated probabilities."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

ScoreType = Literal[
    "none", "literal_presence", "postgresql_lexical", "tantivy_relevance",
    "hybrid_reciprocal_rank", "unified_reciprocal_rank", "semantic_reciprocal_rank",
    "cosine_similarity",
]
TotalKind = Literal["lexical_matches", "ranked_candidates", "browsed_records"]
SemanticReason = Literal["capacity_exhausted", "deadline_exceeded", "model_failure"]
CandidateScope = Literal["none", "full_scope", "lexical_shortlist"]


class RankingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticInference(RankingModel):
    status: Literal["not_requested", "completed", "unavailable"] = "not_requested"
    reason: SemanticReason | None = None

    @model_validator(mode="after")
    def coherent_reason(self) -> Self:
        if (self.status == "unavailable") != (self.reason is not None):
            raise ValueError("Only unavailable inference carries a reason")
        return self


class SemanticRetry(RankingModel):
    max_attempts: StrictInt = Field(default=1, ge=1, le=1)
    after_seconds: StrictInt = Field(default=1, ge=1, le=1)


class CacheRefresh(RankingModel):
    status: Literal["not_needed", "completed", "failed"] = "not_needed"
    reason: Literal["cache_refresh_failed"] | None = None

    @model_validator(mode="after")
    def coherent_reason(self) -> Self:
        if (self.status == "failed") != (self.reason is not None):
            raise ValueError("Only a failed cache refresh carries a reason")
        return self


class SemanticDisposition(RankingModel):
    cache_refresh: CacheRefresh = Field(default_factory=CacheRefresh)
    inference: SemanticInference = Field(default_factory=SemanticInference)
    candidate_scope: CandidateScope = "none"
    partial_vectors: StrictBool = False
    comparison_incomplete: StrictBool = False
    retry: SemanticRetry | None = None

    @model_validator(mode="after")
    def coherent_disposition(self) -> Self:
        status = self.inference.status
        if (status == "completed") != (self.candidate_scope != "none"):
            raise ValueError("Only completed inference names a semantic candidate scope")
        expected = status == "unavailable" or self.partial_vectors or (
            self.candidate_scope == "lexical_shortlist")
        if self.comparison_incomplete != expected:
            raise ValueError("Comparison completeness must agree with inference and scope")
        if (status == "unavailable") != (self.retry is not None):
            raise ValueError("Only unavailable inference supplies bounded retry guidance")
        if status != "completed" and (self.partial_vectors
                                     or self.cache_refresh.status != "not_needed"):
            raise ValueError("Vector coverage and cache refresh require completed inference")
        return self


class SearchRanking(RankingModel):
    score_type: ScoreType
    total_kind: TotalKind
    semantic: SemanticDisposition = Field(default_factory=SemanticDisposition)


class SearchHitRanking(RankingModel):
    rank: StrictInt = Field(default=1, ge=1)
    score: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)
    score_type: ScoreType = "none"


class FacetTotalKinds(RankingModel):
    work_items: TotalKind | None = None
    artifacts: TotalKind | None = None
    transcripts: TotalKind | None = None


class FacetScoreTypes(RankingModel):
    work_items: ScoreType | None = None
    artifacts: ScoreType | None = None
    transcripts: ScoreType | None = None



def source_ranking(query: str | None, mode: str, *, work: bool = False,
                   semantic: bool = False) -> tuple[ScoreType, TotalKind]:
    if not (query or "").strip():
        return "none", "browsed_records"
    if semantic:
        return ("hybrid_reciprocal_rank" if work else "semantic_reciprocal_rank"), "ranked_candidates"
    score: ScoreType = "postgresql_lexical" if work else (
        "literal_presence" if mode == "literal" else "tantivy_relevance")
    return score, "lexical_matches"


def semantic_disclosed(actual: SemanticDisposition) -> bool:
    parts = [actual, actual.inference, actual.cache_refresh]
    if actual.retry is not None:
        parts.append(actual.retry)
    return all(part.model_fields_set == set(type(part).model_fields) for part in parts)


def semantic_matches(actual: SemanticDisposition, requested: bool) -> bool:
    if not semantic_disclosed(actual):
        return False
    return (actual.inference.status == "completed" and actual.candidate_scope == "full_scope"
            if requested else actual.inference.status == "not_requested")


def ranking_matches(page: SearchRanking, query: str | None, mode: str, *, work: bool = False,
                    semantic: bool = False) -> bool:
    return ({"score_type", "total_kind", "semantic"} <= page.model_fields_set
            and (page.score_type, page.total_kind) == source_ranking(
        query, mode, work=work, semantic=semantic,
    ) and semantic_matches(page.semantic, semantic and bool((query or "").strip())))
