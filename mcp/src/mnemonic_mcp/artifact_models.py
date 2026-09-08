"""Independent, bounded artifact wire contracts; file content is always untrusted."""

import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

MCP_ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
ArtifactContent = Annotated[str, Field(max_length=4 * ((MCP_ARTIFACT_MAX_BYTES + 2) // 3))]
ArtifactSort = Literal["filename", "created_at", "modified_at", "size_bytes", "revision"]
ArtifactLimit = Annotated[StrictInt, Field(ge=1, le=100)]
ArtifactOffset = Annotated[StrictInt, Field(ge=0, le=1_000_000)]
ArtifactRevision = Annotated[StrictInt, Field(ge=1)]
ArtifactQuery = Annotated[str, Field(min_length=1, max_length=200)]
ArtifactSession = Annotated[str, Field(min_length=1, max_length=200)]
ArtifactClient = Annotated[str, Field(min_length=1, max_length=80)]
ArtifactFilename = Annotated[str, Field(min_length=1, max_length=255)]
ArtifactDescription = Annotated[str, Field(max_length=4000)]
ArtifactLinks = Annotated[list[UUID], Field(max_length=50)]
ArtifactStoredLinks = Annotated[list[UUID], Field(max_length=51)]


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactLibraryStatus(ArtifactModel):
    enabled: StrictBool
    max_bytes: Annotated[StrictInt, Field(ge=0, le=1024 * 1024 * 1024)]
    message: Annotated[str, Field(max_length=500)]

    @model_validator(mode="after")
    def consistent_enabled(self) -> ArtifactLibraryStatus:
        if self.enabled != (self.max_bytes > 0):
            raise ValueError("Inconsistent artifact configuration")
        return self


class ArtifactToolStatus(ArtifactLibraryStatus):
    mcp_transfer_max_bytes: int = MCP_ARTIFACT_MAX_BYTES
    effective_upload_max_bytes: int


class ArtifactExtraction(ArtifactModel):
    status: Literal["pending", "processing", "ready", "failed", "superseded", "deleted"] = "pending"
    metadata: dict[Annotated[str, Field(min_length=1, max_length=128)],
                   Annotated[list[Annotated[str, Field(max_length=512)]],
                             Field(max_length=8)]] = Field(default_factory=dict)
    truncated: StrictBool = False
    error_code: Annotated[str, Field(max_length=100)] | None = None
    extracted_at: datetime | None = None

    @model_validator(mode="after")
    def bounded_metadata(self) -> ArtifactExtraction:
        if len(self.metadata) > 64 or len(json.dumps(self.metadata, ensure_ascii=True)) > 8192:
            raise ValueError("Oversized extracted artifact metadata")
        return self


class ArtifactRead(ArtifactModel):
    id: UUID
    project_id: UUID
    filename: ArtifactFilename
    description: ArtifactDescription
    revision: ArtifactRevision
    size_bytes: Annotated[StrictInt, Field(ge=0, le=1024 * 1024 * 1024)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mime_type: Annotated[str, Field(max_length=100)] | None
    created_by_agent_session_id: ArtifactSession | None
    created_by_client: ArtifactClient | None
    originating_work_item_id: UUID | None
    related_work_item_ids: ArtifactStoredLinks
    created_at: datetime
    modified_at: datetime
    deleted_at: datetime | None
    content_available: StrictBool
    extraction: ArtifactExtraction = Field(default_factory=ArtifactExtraction)


class ArtifactRevisionRead(ArtifactModel):
    artifact_id: UUID
    revision: ArtifactRevision
    filename: ArtifactFilename
    description: ArtifactDescription
    size_bytes: Annotated[StrictInt, Field(ge=0, le=1024 * 1024 * 1024)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mime_type: Annotated[str, Field(max_length=100)] | None
    agent_session_id: ArtifactSession | None
    actor_client: ArtifactClient | None
    related_work_item_ids: ArtifactStoredLinks
    created_at: datetime
    extraction: ArtifactExtraction = Field(default_factory=ArtifactExtraction)


class ArtifactAuditRead(ArtifactModel):
    id: Annotated[StrictInt, Field(ge=1)]
    artifact_id: UUID
    revision: ArtifactRevision
    action: Literal["uploaded", "replaced", "deleted", "downloaded"]
    agent_session_id: ArtifactSession | None
    actor_client: ArtifactClient | None
    filename: ArtifactFilename
    description: ArtifactDescription
    created_at: datetime


class ArtifactPage[T](ArtifactModel):
    items: Annotated[list[T], Field(max_length=100)]
    total: Annotated[StrictInt, Field(ge=0)]
    limit: ArtifactLimit
    offset: ArtifactOffset


class ArtifactHistory(ArtifactModel):
    revisions: ArtifactPage[ArtifactRevisionRead]
    audit: ArtifactPage[ArtifactAuditRead]


class ArtifactDownload(ArtifactModel):
    artifact: ArtifactRead
    content_base64: ArtifactContent


class ArtifactSearchMatch(ArtifactModel):
    artifact: ArtifactRead
    score: Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
    snippet: Annotated[str, Field(max_length=1000)] | None
    matched_fields: Annotated[list[Literal["metadata", "content"]], Field(min_length=1, max_length=2)]

    @model_validator(mode="after")
    def unique_fields(self) -> ArtifactSearchMatch:
        if len(set(self.matched_fields)) != len(self.matched_fields):
            raise ValueError("Duplicate artifact match fields")
        return self


class ArtifactIndexingStatus(ArtifactModel):
    pending: Annotated[StrictInt, Field(ge=0)]
    failed: Annotated[StrictInt, Field(ge=0)]
    ready: Annotated[StrictInt, Field(ge=0)]
    truncated: Annotated[StrictInt, Field(ge=0)]


class ArtifactContentSearch(ArtifactModel):
    items: Annotated[list[ArtifactSearchMatch], Field(max_length=100)]
    total: Annotated[StrictInt, Field(ge=0)]
    limit: ArtifactLimit
    offset: ArtifactOffset
    fulltext: StrictBool
    indexing: ArtifactIndexingStatus


class ArtifactToolRead(ArtifactRead):
    artifact_library: ArtifactToolStatus


class ArtifactToolPage(ArtifactPage[ArtifactRead]):
    artifact_library: ArtifactToolStatus


class ArtifactToolHistory(ArtifactHistory):
    artifact_library: ArtifactToolStatus


class ArtifactToolDownload(ArtifactDownload):
    artifact_library: ArtifactToolStatus


class ArtifactToolContentSearch(ArtifactContentSearch):
    artifact_library: ArtifactToolStatus
