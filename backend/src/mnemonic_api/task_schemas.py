"""Dashboard tasks keep implementation and review lifecycles separate."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from mnemonic_api.schemas import APIModel, LeasePublic, WorkSummary

TaskKind = Literal["work_item", "code_review"]
TaskStatus = Literal[
    "pending", "active", "dropped", "deferred", "done", "wont-do", "promoted", "superseded"
]


class TaskListQuery(APIModel):
    kind: TaskKind | None = None
    task_id: UUID | None = None
    status: TaskStatus | Literal["all"] = "active"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    q: str | None = Field(default=None, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    tag_mode: Literal["or", "and"] = "or"
    sort: Literal["updated", "created", "priority"] = "updated"
    include_summary: bool = False


class TaskCounts(APIModel):
    active: int = Field(ge=0)
    pending: int = Field(ge=0)


class TaskSummary(APIModel):
    tags: list[str] | None = None
    system_tags: list[str] = Field(default_factory=list)
    created_at: datetime
    priority: int
    work_summary: WorkSummary | None = Field(default=None, exclude_if=lambda value: value is None)
    decision_review_project_id: UUID | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    decision_review_change_id: UUID | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    decision_change_ids: list[UUID] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
    decision_review_revision_id: UUID | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    id: UUID
    kind: TaskKind
    project_id: UUID
    work_item_id: UUID
    work_version: int = Field(ge=1)
    title: str
    summary: str
    status: TaskStatus
    review_state: Literal["requested", "completed", "superseded"] | None
    updated_at: datetime
    lease: LeasePublic | None


class TaskPage(APIModel):
    project_id: UUID
    work_items: TaskCounts
    code_reviews: TaskCounts
    next_lease_expires_at: datetime | None
    items: list[TaskSummary]
    total: int = Field(ge=0)
    limit: int
    offset: int
