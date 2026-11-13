"""Independent request and strict response contracts for compact project deltas."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from .phase12_models import DecimalString, Phase12Wire, PositiveDecimalString
from .search_exploration import SearchDate
from .tag_models import TagMode, TagNames, tags_match

ActivityFacet = Literal[
    "work_items", "artifacts", "decisions", "project", "settings", "knowledge_domains",
    "tags", "transcripts",
]
ACTIVITY_FACETS: tuple[ActivityFacet, ...] = (
    "work_items", "artifacts", "decisions", "project", "settings", "knowledge_domains",
    "tags", "transcripts",
)
ActivityToken = Annotated[StrictStr, Field(min_length=1, max_length=2048)]
ActivityFacets = Annotated[list[ActivityFacet], Field(min_length=1, max_length=8)]
ActivityLimit = Annotated[StrictInt, Field(ge=1, le=100)]
ActivityView = Literal["resources", "events"]
WorkActivityStatus = Literal[
    "pending", "active", "to-review", "dropped", "deferred", "done", "wont-do", "promoted",
]
ActivityDecisionState = Literal[
    "proposed", "rejected", "accepted", "superseded", "partially-superseded",
]


class ActivityTagFilters(Phase12Wire):
    tags: TagNames | None = Field(
        default=None,
        description="Current catalog tags; work membership also includes intrinsic remediation/dir.",
    )
    tag_mode: TagMode = "or"


class ActivityWorkFilters(ActivityTagFilters):
    statuses: list[WorkActivityStatus] | None = Field(
        default=None, min_length=1, max_length=8,
        description="Current effective queue states, rechecked on each page; not historical states.",
    )


class ActivityDecisionFilters(Phase12Wire):
    domain_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)
    domain_prefixes: list[Annotated[str, Field(min_length=1, max_length=40)]] | None = Field(
        default=None, min_length=1, max_length=100,
        description="Current knowledge-domain prefixes, for example ADR.",
    )
    states: list[ActivityDecisionState] | None = Field(default=None, min_length=1, max_length=5)


class ActivityFilters(Phase12Wire):
    work_items: ActivityWorkFilters | None = None
    artifacts: ActivityTagFilters | None = None
    decisions: ActivityDecisionFilters | None = None


class QueryActivityRequest(Phase12Wire):
    since: SearchDate | None = None
    until: SearchDate | None = None
    after: ActivityToken | None = None
    cursor: ActivityToken | None = None
    start: Literal["now"] | None = None
    facets: ActivityFacets = Field(default_factory=lambda: list(ACTIVITY_FACETS))
    filters: ActivityFilters = Field(default_factory=ActivityFilters)
    view: ActivityView = "resources"
    limit: ActivityLimit = 20

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


class ActivityCurrentState(Phase12Wire):
    exists: StrictBool
    deleted: StrictBool = False
    title: StrictStr | None = None
    revision: StrictStr | None = None
    status: StrictStr | None = None
    tags: list[StrictStr] | None = None
    system_tags: list[Literal["remediation", "dir"]] = Field(default_factory=list)
    domain_id: UUID | None = None
    domain_prefix: StrictStr | None = None
    state: StrictStr | None = None


class ActivityChangeRead(Phase12Wire):
    resource_type: ActivityFacet
    resource_id: UUID
    title: StrictStr
    revision: StrictStr | None
    first_sequence: PositiveDecimalString
    last_sequence: PositiveDecimalString
    first_occurred_at: AwareDatetime
    last_occurred_at: AwareDatetime
    recorded_at: AwareDatetime
    change_count: StrictInt = Field(ge=1)
    operations: list[StrictStr]
    changed_fields: list[StrictStr]
    before_state: dict[str, JsonValue]
    after_state: dict[str, JsonValue]
    summary: StrictStr
    origins: list[Literal["live", "history_import"]]
    current: ActivityCurrentState | None

    @model_validator(mode="after")
    def coherent_range(self) -> Self:
        if int(self.first_sequence) > int(self.last_sequence):
            raise ValueError("Activity sequence range is reversed")
        if self.first_occurred_at > self.last_occurred_at:
            raise ValueError("Activity source-time range is reversed")
        if self.change_count > int(self.last_sequence) - int(self.first_sequence) + 1:
            raise ValueError("Activity count exceeds its sequence range")
        return self


class ActivityFacetCounts(Phase12Wire):
    changes: StrictInt = Field(ge=0)
    resources: StrictInt = Field(ge=0)


class ActivityQueryCounts(ActivityFacetCounts):
    by_facet: dict[ActivityFacet, ActivityFacetCounts]

    @model_validator(mode="after")
    def coherent_totals(self) -> Self:
        if self.changes != sum(item.changes for item in self.by_facet.values()):
            raise ValueError("Activity change counts disagree")
        if self.resources != sum(item.resources for item in self.by_facet.values()):
            raise ValueError("Activity resource counts disagree")
        if self.resources > self.changes:
            raise ValueError("Activity resource count exceeds change count")
        return self


class ActivityQueryCoverage(Phase12Wire):
    capture_started_at: AwareDatetime
    historical_coverage: Literal["partial"] = "partial"
    imported_changes: StrictInt = Field(ge=0)
    known_gaps: list[StrictStr]
    timestamps: Literal["occurred_at_source_time_not_commit_time"] = (
        "occurred_at_source_time_not_commit_time"
    )


class QueryActivityPage(Phase12Wire):
    project_id: UUID
    stream_id: UUID
    view: ActivityView
    items: list[ActivityChangeRead] = Field(max_length=100)
    through_sequence: DecimalString
    next_cursor: ActivityToken | None
    checkpoint: ActivityToken | None
    has_more: StrictBool
    counts: ActivityQueryCounts
    coverage: ActivityQueryCoverage
    filter_semantics: Literal["current_state_rechecked_each_page"] = (
        "current_state_rechecked_each_page"
    )
    page_limited_by_bytes: StrictBool = False
    byte_limit: Literal[32768] = 32768

    @model_validator(mode="after")
    def coherent_page(self) -> Self:
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("Activity continuation disagrees with has_more")
        if self.has_more and self.checkpoint is not None:
            raise ValueError("Only a final activity page may return a checkpoint")
        if self.has_more and not self.items:
            raise ValueError("A continuing activity page must make progress")
        if any(int(item.last_sequence) > int(self.through_sequence) for item in self.items):
            raise ValueError("Activity exceeds its frozen upper sequence")
        self._check_items()
        return self

    def _check_items(self) -> None:
        sequences = [int(item.last_sequence) for item in self.items]
        if sequences != sorted(set(sequences)):
            raise ValueError("Activity items must be unique and ascending by last sequence")
        if self.view == "resources":
            identities = [(item.resource_type, item.resource_id) for item in self.items]
            if len(identities) != len(set(identities)):
                raise ValueError("Activity resource groups must be unique")
        elif any(item.change_count != 1 or item.first_sequence != item.last_sequence
                 for item in self.items):
            raise ValueError("Event view must return individual changes")


def activity_query_matches(
    page: QueryActivityPage, project_id: UUID, request: QueryActivityRequest,
) -> bool:
    if page.project_id != project_id or page.view != request.view or len(page.items) > request.limit:
        return False
    if request.start == "now" and (page.items or page.has_more):
        return False
    if not page.has_more and (page.checkpoint is None) != (request.until is not None):
        return False
    return all(item.resource_type in request.facets
               and (request.since is None or item.first_occurred_at >= request.since)
               and (request.until is None or item.last_occurred_at < request.until)
               and _matches_filters(item, request.filters)
               for item in page.items)


def _matches_filters(item: ActivityChangeRead, filters: ActivityFilters) -> bool:
    selected = getattr(filters, item.resource_type, None)
    if selected is None:
        return True
    current = item.current
    if current is None or not current.exists or current.deleted:
        return False
    system_tags = current.system_tags if isinstance(selected, ActivityWorkFilters) else []
    if isinstance(selected, ActivityTagFilters) and not tags_match(
        [*(current.tags or []), *system_tags], selected.tags, selected.tag_mode,
    ):
        return False
    if isinstance(selected, ActivityWorkFilters):
        return selected.statuses is None or current.status in selected.statuses
    if isinstance(selected, ActivityDecisionFilters):
        return all((
            selected.domain_ids is None or current.domain_id in selected.domain_ids,
            selected.domain_prefixes is None or current.domain_prefix in selected.domain_prefixes,
            selected.states is None or current.state in selected.states,
        ))
    return True
