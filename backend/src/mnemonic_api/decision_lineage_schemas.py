"""Bounded factual work context for a decision change, without readiness policy changes."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from mnemonic_api.schemas import APIModel, Readiness, Status


class DecisionLineageQuery(APIModel):
    lineage_limit: Annotated[int, Field(ge=1, le=100)] = 20
    lineage_offset: Annotated[int, Field(ge=0)] = 0


class LineageWorkPointer(APIModel):
    work_item_id: UUID
    project_id: UUID


class LineageOrigins(APIModel):
    items: list[LineageWorkPointer] = Field(max_length=20)
    total: int = Field(ge=0)
    omitted_count: int = Field(ge=0)

    @model_validator(mode="after")
    def coherent_origins(self) -> Self:
        if self.total != len(self.items) + self.omitted_count:
            raise ValueError("Discovery origin totals must match returned and omitted origins")
        if len({item.work_item_id for item in self.items}) != len(self.items):
            raise ValueError("Discovery origins must be unique")
        return self


class LineageReview(APIModel):
    review_id: UUID
    state: Literal["requested", "completed", "superseded"]
    status: Literal["to-review", "deferred", "done", "wont-do", "promoted"] | None


class LineageFollowUp(APIModel):
    follow_up_id: UUID
    state: Literal["pending", "answered", "superseded"]
    status: Literal["to-review", "deferred", "done", "wont-do", "promoted"] | None


class LineageRemediationOrigin(APIModel):
    review_id: UUID
    source_work_item_id: UUID
    source_project_id: UUID
    depth: int = Field(ge=1, le=2)


class DecisionLineageWork(APIModel):
    work_item_id: UUID
    project_id: UUID
    title: str
    status: Status
    deleted_at: datetime | None
    canonical_work_item_id: UUID
    readiness: Readiness | None
    is_decision_source: bool
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


class DecisionLineagePage(APIModel):
    items: list[DecisionLineageWork] = Field(max_length=100)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    next_offset: int | None = Field(ge=0)

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
