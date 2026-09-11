"""Public transcript records never include source bytes or text implicitly."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TranscriptStatus = Literal["waiting", "pending", "processing", "ready", "failed"]


class TranscriptRead(BaseModel):
    id: UUID
    project_id: UUID
    work_item_id: UUID
    lease_generation_id: UUID
    client: str
    session_id: str
    source_path: str
    filename: str
    kind: Literal["primary", "subagent"]
    status: TranscriptStatus
    indexing_started_at: datetime | None
    indexing_completed_at: datetime | None
    error_code: str | None
    size_bytes: int | None
    mime_type: str | None
    format: str | None
    sha256: str | None
    text_sha256: str | None
    metadata: dict[str, list[str]]
    truncated: bool
    created_at: datetime
    snippet: str | None = None
    score: float | None = None


class TranscriptSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str | None = Field(default=None, max_length=1000)
    fulltext: bool = False
    work_item_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)


class TranscriptPage(BaseModel):
    items: list[TranscriptRead]
    total: int
    limit: int
    offset: int
    indexing_incomplete: bool


class TranscriptText(BaseModel):
    transcript_id: UUID
    project_id: UUID
    text: str
    total_chars: int
    offset: int
    limit: int
    next_offset: int | None
    status: TranscriptStatus
    truncated: bool
    text_sha256: str | None


class TranscriptSettingsRead(BaseModel):
    enabled: bool
    max_file_size_bytes: int
    revision: int
    allowed_roots: list[str]
    operator_max_file_size_bytes: int


class TranscriptSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    max_file_size_bytes: int = Field(ge=1, le=268_435_456)
    expected_revision: int = Field(ge=1)


class TranscriptRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_operation_id: UUID


class TranscriptRebuildRead(BaseModel):
    queued: int
    project_id: UUID
    client_operation_id: UUID
