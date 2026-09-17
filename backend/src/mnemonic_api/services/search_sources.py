"""Lightweight candidates are ranked before any page payloads are hydrated."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from mnemonic_api.search_exploration_schemas import TagCountPage, TagCountRequest
from mnemonic_api.search_ranking import ScoreType
from mnemonic_api.search_schemas import (
    ArtifactSearchCoverage,
    SearchFacet,
    SearchHit,
    TranscriptSearchCoverage,
)


@dataclass
class SearchCandidate:
    facet: SearchFacet
    id: UUID
    project_id: UUID
    created_at: datetime
    updated_at: datetime
    score: float = 0.0
    priority: int = 0
    source_rank: int = 0
    rank: int = 1
    score_type: ScoreType = "none"

    def fields(self) -> dict:
        return {
            "id": self.id, "project_id": self.project_id, "created_at": self.created_at,
            "updated_at": self.updated_at, "score": self.score,
            "rank": self.rank, "score_type": self.score_type,
        }


@dataclass
class SearchSource:
    candidates: list[SearchCandidate]
    hydrate: Callable[[list[SearchCandidate]], dict[UUID, SearchHit]]
    term_counts: Callable[[list[str]], dict[str, int]]
    tag_counts: Callable[[TagCountRequest], TagCountPage] | None = None
    coverage_by_project: Mapping[UUID, ArtifactSearchCoverage | TranscriptSearchCoverage] = field(
        default_factory=dict,
    )
