"""Bounded metadata contracts; artifact bytes never enter a JSON database field."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ArtifactLibraryStatus(ArtifactModel):
    enabled: bool
    max_bytes: int = Field(ge=0, le=1_073_741_824)
    message: str


class ArtifactActor(ArtifactModel):
    agent_session_id: str | None = Field(default=None, min_length=1, max_length=200)
    actor_client: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("agent_session_id", "actor_client")
    @classmethod
    def no_controls(cls, value: str | None) -> str | None:
        if value is not None:
            value.encode("utf-8")
        if value is not None and (not value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("Actor identifiers must be nonblank and contain no controls")
        return value


class ArtifactUploadMetadata(ArtifactActor):
    filename: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    work_item_id: UUID | None = None
    related_work_item_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @field_validator("description")
    @classmethod
    def no_nul(cls, value: str | None) -> str | None:
        if value is not None:
            value.encode("utf-8")
        if value is not None and "\x00" in value:
            raise ValueError("Description cannot contain NUL")
        return value


class ArtifactRead(ArtifactModel):
    id: UUID
    project_id: UUID
    filename: str
    description: str
    revision: int
    size_bytes: int
    sha256: str
    mime_type: str | None
    created_by_agent_session_id: str | None
    created_by_client: str | None
    originating_work_item_id: UUID | None
    related_work_item_ids: list[UUID] = Field(max_length=50)
    created_at: datetime
    modified_at: datetime
    deleted_at: datetime | None
    content_available: bool


class ArtifactRevisionRead(ArtifactActor):
    artifact_id: UUID
    revision: int
    filename: str
    description: str
    size_bytes: int
    sha256: str
    mime_type: str | None
    related_work_item_ids: list[UUID] = Field(max_length=50)
    created_at: datetime


class ArtifactAuditRead(ArtifactActor):
    id: int
    artifact_id: UUID
    revision: int
    action: Literal["uploaded", "replaced", "deleted", "downloaded"]
    filename: str
    description: str
    created_at: datetime


class ArtifactPage[T](ArtifactModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ArtifactHistory(ArtifactModel):
    revisions: ArtifactPage[ArtifactRevisionRead]
    audit: ArtifactPage[ArtifactAuditRead]


class ArtifactListQuery(ArtifactModel):
    q: str | None = Field(default=None, min_length=1, max_length=200)
    work_item_id: UUID | None = None
    include_deleted: bool = False
    sort: Literal["filename", "created_at", "modified_at", "size_bytes", "revision"] = "filename"
    order: Literal["asc", "desc"] = "asc"
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1_000_000)


class ArtifactHistoryQuery(ArtifactModel):
    q: str | None = Field(default=None, min_length=1, max_length=200)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1_000_000)
