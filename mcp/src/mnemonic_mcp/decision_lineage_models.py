"""Bounded factual work context for a decision change, without readiness policy changes."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator
from pydantic.json_schema import SkipJsonSchema

from .impact_models import ImpactModel
from .precedence_models import DependencyIssue

Status = Literal["pending", "deferred", "done", "wont-do", "promoted"]


class LineageLease(ImpactModel):
    purpose: Literal["implementation", "code_review"] = Field(
        default="implementation", exclude_if=lambda value: value == "implementation",
    )
    code_review_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    mode: Literal["warm", "cold"] | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    holder_client: str
    holder_session_id: str
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime


class LineageReadiness(ImpactModel):
    # The general work model imports knowledge models. Keep this wire shape independent,
    # then share canonical readiness validation after imports have completed.
    dependency_issue: DependencyIssue | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    review_status: (
        Literal["to-review", "deferred", "done", "wont-do", "promoted"] | SkipJsonSchema[None]
    ) = Field(default=None, exclude_if=lambda value: value is None)
    lifecycle_status: Status
    is_duplicate: StrictBool
    canonical_work_item_id: UUID
    is_terminal: StrictBool
    has_active_lease: StrictBool
    has_dropped_lease: StrictBool
    active_lease: LineageLease | None
    unresolved_blocker_count: StrictInt = Field(ge=0)
    is_blocked: StrictBool
    unresolved_gate_count: StrictInt = Field(ge=0)
    is_gated: StrictBool
    is_ready: StrictBool
    display_state: Literal[
        "deadlocked", "pending", "active", "to-review", "dropped", "blocked", "waiting",
        "duplicate", "deferred", "done", "wont-do", "promoted",
    ]

    @model_validator(mode="after")
    def canonical_readiness(self) -> Self:
        from .models import Readiness

        Readiness.model_validate(self.model_dump(mode="json"))
        return self


class DecisionLineageQuery(ImpactModel):
    lineage_limit: Annotated[StrictInt, Field(ge=1, le=100)] = 20
    lineage_offset: Annotated[StrictInt, Field(ge=0)] = 0


class LineageWorkPointer(ImpactModel):
    work_item_id: UUID
    project_id: UUID


class LineageOrigins(ImpactModel):
    items: list[LineageWorkPointer] = Field(max_length=20)
    total: StrictInt = Field(ge=0)
    omitted_count: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def coherent_origins(self) -> Self:
        if self.total != len(self.items) + self.omitted_count:
            raise ValueError("Discovery origin totals must match returned and omitted origins")
        if len({item.work_item_id for item in self.items}) != len(self.items):
            raise ValueError("Discovery origins must be unique")
        return self


class LineageReview(ImpactModel):
    review_id: UUID
    state: Literal["requested", "completed", "superseded"]
    status: Literal["to-review", "deferred", "done", "wont-do", "promoted"] | None


class LineageFollowUp(ImpactModel):
    follow_up_id: UUID
    state: Literal["pending", "answered", "superseded"]
    status: Literal["to-review", "deferred", "done", "wont-do", "promoted"] | None


class LineageRemediationOrigin(ImpactModel):
    review_id: UUID
    source_work_item_id: UUID
    source_project_id: UUID
    depth: StrictInt = Field(ge=1, le=2)


class DecisionLineageWork(ImpactModel):
    work_item_id: UUID
    project_id: UUID
    title: str
    status: Status
    deleted_at: datetime | None
    canonical_work_item_id: UUID
    readiness: LineageReadiness | None
    is_decision_source: StrictBool
    discovered_from: LineageOrigins
    code_review: LineageReview | None
    review_follow_up: LineageFollowUp | None
    remediation_origin: LineageRemediationOrigin | None

    @model_validator(mode="after")
    def coherent_identity(self) -> Self:
        if (self.deleted_at is None) != (self.readiness is not None):
            raise ValueError("Deleted work has no actionable readiness projection")
        if self.readiness is not None and (
            self.readiness.lifecycle_status != self.status
            or self.readiness.canonical_work_item_id != self.canonical_work_item_id
            or self.readiness.is_duplicate != (self.work_item_id != self.canonical_work_item_id)
        ):
            raise ValueError("Lineage work identity must agree with readiness")
        return self


class DecisionLineagePage(ImpactModel):
    items: list[DecisionLineageWork] = Field(max_length=100)
    total: StrictInt = Field(ge=0)
    limit: StrictInt = Field(ge=1, le=100)
    offset: StrictInt = Field(ge=0)
    next_offset: StrictInt | None = Field(ge=0)

    @model_validator(mode="after")
    def coherent_page(self) -> Self:
        expected = min(self.limit, max(0, self.total - self.offset))
        following = self.offset + len(self.items)
        if len(self.items) != expected or self.next_offset != (
            following if following < self.total else None
        ):
            raise ValueError("Lineage pagination must expose every selected member")
        if len({item.work_item_id for item in self.items}) != len(self.items):
            raise ValueError("Lineage work members must be unique")
        return self
