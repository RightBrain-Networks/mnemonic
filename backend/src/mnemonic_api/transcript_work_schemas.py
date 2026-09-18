"""Bounded transcript references for navigating from exact work reads."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkTranscriptLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    project_id: UUID
    work_item_id: UUID
    filename: str = Field(min_length=1, max_length=4096)
    client: str = Field(min_length=1, max_length=200)
    kind: Literal["primary", "subagent"]
    status: Literal["waiting", "pending", "processing", "ready", "failed"]
    last_updated_at: datetime | None
    session_ids: list[str] = Field(max_length=8)
    models: list[str] = Field(max_length=8)


class WorkTranscriptLinks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[WorkTranscriptLink] = Field(max_length=20)
    total: int = Field(ge=0)
    omitted_count: int = Field(ge=0)

    @model_validator(mode="after")
    def coherent_page(self) -> Self:
        if (
            len(self.items) != min(self.total, 20)
            or self.total != len(self.items) + self.omitted_count
            or len({row.id for row in self.items}) != len(self.items)
        ):
            raise ValueError("Linked transcript counts and identities must agree")
        return self

    def require_scope(self, project_id: UUID, work_item_id: UUID) -> None:
        if any(
            (row.project_id, row.work_item_id) != (project_id, work_item_id) for row in self.items
        ):
            raise ValueError("Linked transcripts must belong to the requested work item")
