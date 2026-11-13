"""Bounded project change queries and explicit current-state filtering."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    Field,
    JsonValue,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from mnemonic_api.decision_search_schemas import DecisionState
from mnemonic_api.schemas import APIModel
from mnemonic_api.tag_types import AssignedTags

ActivityFacet = Literal[
    "work_items", "artifacts", "decisions", "project", "settings", "knowledge_domains",
    "tags", "transcripts",
]
ACTIVITY_FACETS: tuple[ActivityFacet, ...] = (
    "work_items", "artifacts", "decisions", "project", "settings", "knowledge_domains",
    "tags", "transcripts",
)
ActivityToken = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
ActivitySequence = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]{0,18})$")]
WorkActivityStatus = Literal[
    "pending", "active", "to-review", "dropped", "deferred", "done", "wont-do", "promoted",
]


class ActivityTagFilters(APIModel):
    tags: AssignedTags | None = None
    tag_mode: Literal["or", "and"] = "or"


class ActivityWorkFilters(ActivityTagFilters):
    statuses: list[WorkActivityStatus] | None = Field(default=None, min_length=1, max_length=8)


class ActivityDecisionFilters(APIModel):
    domain_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)
    domain_prefixes: list[Annotated[str, StringConstraints(min_length=1, max_length=40)]] | None = (
        Field(default=None, min_length=1, max_length=100)
    )
    states: list[DecisionState] | None = Field(default=None, min_length=1, max_length=5)


class ActivityFilters(APIModel):
    work_items: ActivityWorkFilters | None = None
    artifacts: ActivityTagFilters | None = None
    decisions: ActivityDecisionFilters | None = None


class QueryActivityRequest(APIModel):
    since: datetime | None = None
    until: datetime | None = None
    after: ActivityToken | None = None
    cursor: ActivityToken | None = None
    start: Literal["now"] | None = None
    facets: list[ActivityFacet] = Field(
        default_factory=lambda: list(ACTIVITY_FACETS), min_length=1, max_length=8,
    )
    filters: ActivityFilters = Field(default_factory=ActivityFilters)
    view: Literal["resources", "events"] = "resources"
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 20

    @field_validator("since", "until")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Activity bounds must include a timezone")
        return value

    @field_validator("facets")
    @classmethod
    def unique_facets(cls, value: list[ActivityFacet]) -> list[ActivityFacet]:
        if len(value) != len(set(value)):
            raise ValueError("Activity facets must be unique")
        return value

    @model_validator(mode="after")
    def compatible_position(self) -> Self:
        dates = self.since is not None or self.until is not None
        if self.start is not None and (dates or self.after is not None or self.cursor is not None):
            raise ValueError("start=now cannot accompany dates, after, or cursor")
        if self.after is not None and dates:
            raise ValueError("after cannot accompany since or until")
        if self.since is not None and self.until is not None and self.since >= self.until:
            raise ValueError("since must be earlier than until")
        active = self.filters.model_dump(exclude_none=True)
        if any(facet not in self.facets for facet in active):
            raise ValueError("Filters may only target selected facets")
        return self


class ActivityCurrentState(APIModel):
    """Current metadata; absent when the identity no longer exists in this project."""

    exists: bool
    deleted: bool = False
    title: str | None = None
    revision: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    system_tags: list[str] = Field(default_factory=list)
    decision_id: str | None = None
    domain_id: UUID | None = None
    domain_prefix: str | None = None
    state: str | None = None


class ActivityChangeRead(APIModel):
    resource_type: ActivityFacet
    resource_id: UUID
    title: str
    revision: str | None
    first_sequence: ActivitySequence
    last_sequence: ActivitySequence
    first_occurred_at: datetime
    last_occurred_at: datetime
    recorded_at: datetime
    change_count: int = Field(ge=1)
    operations: list[str]
    changed_fields: list[str]
    before_state: dict[str, JsonValue]
    after_state: dict[str, JsonValue]
    summary: str
    origins: list[Literal["live", "history_import"]]
    current: ActivityCurrentState | None


class ActivityFacetCounts(APIModel):
    changes: int = Field(ge=0)
    resources: int = Field(ge=0)


class ActivityQueryCounts(ActivityFacetCounts):
    by_facet: dict[ActivityFacet, ActivityFacetCounts]


class ActivityQueryCoverage(APIModel):
    capture_started_at: datetime
    historical_coverage: Literal["partial"] = "partial"
    imported_changes: int = Field(ge=0)
    known_gaps: list[str]
    timestamps: Literal["occurred_at_source_time_not_commit_time"] = (
        "occurred_at_source_time_not_commit_time"
    )


class QueryActivityPage(APIModel):
    project_id: UUID
    stream_id: UUID
    view: Literal["resources", "events"]
    items: list[ActivityChangeRead] = Field(max_length=100)
    through_sequence: ActivitySequence
    next_cursor: ActivityToken | None
    checkpoint: ActivityToken | None
    has_more: bool
    counts: ActivityQueryCounts
    coverage: ActivityQueryCoverage
    filter_semantics: Literal["current_state_rechecked_each_page"] = (
        "current_state_rechecked_each_page"
    )
    page_limited_by_bytes: bool = False
    byte_limit: Literal[32768] = 32768
