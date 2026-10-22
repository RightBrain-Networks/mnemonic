"""Bounded revision locators for discovery; document bodies use decision-get."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from mnemonic_api.search_exploration_schemas import DateBounds
from mnemonic_api.search_ranking import SearchHitRanking

DecisionState = Literal[
    "proposed", "rejected", "accepted", "superseded", "partially-superseded",
]


class DecisionSearchFilters(DateBounds):
    model_config = ConfigDict(extra="forbid")
    domain_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)
    all_revisions: bool = False
    state: DecisionState | None = None

    @field_validator("domain_ids")
    @classmethod
    def unique_domains(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("domain_ids must contain unique domain IDs")
        return value


class DecisionSearchMatch(SearchHitRanking):
    id: UUID
    project_id: UUID
    decision_id: str
    reference: str
    domain_id: UUID
    domain_name: str
    prefix: str
    ordinal: int = Field(ge=1)
    revision: int = Field(ge=0)
    title: str
    state: DecisionState
    media_type: Literal["text/plain", "text/markdown"]
    size_bytes: int = Field(ge=0)
    sha256: str
    created_at: datetime
    updated_at: datetime
    author_kind: Literal["human", "agent"]
    agent_session_id: str | None
    model_id: str | None
    snippet: str | None = Field(default=None, max_length=960)
    matched_fields: list[Literal["metadata", "content"]] = Field(default_factory=list)
