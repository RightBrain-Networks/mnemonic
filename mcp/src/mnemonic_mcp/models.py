"""Wire models for the REST contract; the API remains the validation authority."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Status = Literal["open", "done", "wont-do", "promoted"]
SearchStatus = Literal["open", "done", "wont-do", "promoted", "all"]


class Project(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str
    repository_url: str | None
    created_at: datetime
    updated_at: datetime


class ProjectPage(BaseModel):
    items: list[Project]
    total: int
    limit: int
    offset: int


class HandoffSummary(BaseModel):
    # Ignore extra response fields so search remains pointer-only even if an
    # upstream response accidentally contains the full prompt or source metadata.
    model_config = ConfigDict(extra="ignore")

    id: UUID
    project_id: UUID
    title: str
    summary: str
    source_client: str
    source_session_id: str
    source_model: str | None
    source_session_url: str | None
    repository_branch: str | None
    verified_against: str | None
    tags: list[str]
    status: Status
    created_at: datetime
    updated_at: datetime
    version: int


class Handoff(HandoffSummary):
    prompt: str
    source_metadata: dict[str, JsonValue]


class HandoffPage(BaseModel):
    items: list[HandoffSummary]
    total: int
    limit: int
    offset: int


class HandoffChanges(BaseModel):
    """Only supplied fields change. Explicit null clears a nullable field."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, min_length=1, max_length=1000)
    prompt: str | None = Field(default=None, min_length=1, max_length=100000)
    repository_branch: str | None = Field(default=None, max_length=200)
    verified_against: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{7,64}$")
    tags: list[str] | None = Field(default=None, max_length=20)
    source_metadata: dict[str, JsonValue] | None = None
    status: Status | None = None

    @model_validator(mode="after")
    def require_changes(self) -> "HandoffChanges":
        if not self.model_fields_set:
            raise ValueError("Supply at least one editable field in changes.")
        nullable_fields = {"repository_branch", "verified_against"}
        for name in self.model_fields_set - nullable_fields:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self


class DeletionResult(BaseModel):
    deleted: bool = True
    project_id: UUID
    handoff_id: UUID
