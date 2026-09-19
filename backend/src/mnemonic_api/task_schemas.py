"""Dashboard tasks keep implementation and review lifecycles separate."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from mnemonic_api.schemas import APIModel, LeasePublic

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


class TaskCounts(APIModel):
    active: int = Field(ge=0)
    pending: int = Field(ge=0)


class TaskSummary(APIModel):
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
