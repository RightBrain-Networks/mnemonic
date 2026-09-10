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
ArtifactApprovalToken = Annotated[str, Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
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


class ArtifactExtractionStatus(ArtifactModel):
    status: Literal["pending", "processing", "ready", "failed", "superseded", "deleted"] = "pending"
    truncated: StrictBool = False
    error_code: Annotated[str, Field(max_length=100)] | None = None
    extracted_at: datetime | None = None

class ArtifactExtraction(ArtifactExtractionStatus):
    metadata: dict[Annotated[str, Field(min_length=1, max_length=128)],
                   Annotated[list[Annotated[str, Field(max_length=512)]],
                             Field(max_length=8)]] = Field(default_factory=dict)

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
    related_artifact_ids: ArtifactLinks
    sensitive: StrictBool
    created_at: datetime
    modified_at: datetime
    deleted_at: datetime | None
    content_available: StrictBool
    extraction: ArtifactExtraction = Field(default_factory=ArtifactExtraction)

    @model_validator(mode="after")
    def sensitive_properties_withheld(self) -> ArtifactRead:
        if self.sensitive and self.extraction.metadata:
            raise ValueError("Sensitive artifact properties must be withheld")
        return self


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
    related_artifact_ids: ArtifactLinks
    sensitive: StrictBool
    created_at: datetime
    extraction: ArtifactExtraction = Field(default_factory=ArtifactExtraction)

    @model_validator(mode="after")
    def sensitive_properties_withheld(self) -> ArtifactRevisionRead:
        if self.sensitive and self.extraction.metadata:
            raise ValueError("Sensitive artifact properties must be withheld")
        return self


class ArtifactAuditRead(ArtifactModel):
    id: Annotated[StrictInt, Field(ge=1)]
    artifact_id: UUID
    revision: ArtifactRevision
    details: dict[str, object] = Field(default_factory=dict)
    action: Literal[
        "uploaded", "replaced", "deleted", "downloaded", "metadata_updated", "linked",
        "approval_required", "approval_granted", "approval_rejected",
        "sensitive_downloaded", "sensitive_text_read", "sensitive_searched",
    ]
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
    sensitive_content_withheld: Annotated[StrictInt, Field(ge=0)]


class ArtifactToolRead(ArtifactRead):
    artifact_library: ArtifactToolStatus


class ArtifactToolPage(ArtifactPage[ArtifactRead]):
    artifact_library: ArtifactToolStatus


class ArtifactToolHistory(ArtifactHistory):
    artifact_library: ArtifactToolStatus


class ArtifactSummary(ArtifactModel):
    sensitive: StrictBool
    id: UUID
    project_id: UUID
    filename: ArtifactFilename
    revision: ArtifactRevision
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[StrictInt, Field(ge=0, le=1024 * 1024 * 1024)]
    mime_type: Annotated[str, Field(max_length=100)] | None
    deleted_at: datetime | None
    content_available: StrictBool
    extraction: ArtifactExtractionStatus

    @classmethod
    def from_artifact(cls, artifact: ArtifactRead) -> ArtifactSummary:
        values = artifact.model_dump(include=set(cls.model_fields))
        values["extraction"].pop("metadata")
        return cls(**values)


class ArtifactToolDownload(ArtifactModel):
    artifact: ArtifactSummary
    content_base64: ArtifactContent
    artifact_library: ArtifactToolStatus


class ArtifactToolSearchMatch(ArtifactModel):
    artifact: ArtifactSummary
    score: Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
    snippet: Annotated[str, Field(max_length=1000)] | None
    matched_fields: Annotated[list[Literal["metadata", "content"]], Field(min_length=1, max_length=2)]


class ArtifactToolContentSearch(ArtifactPage[ArtifactToolSearchMatch]):
    fulltext: StrictBool
    indexing: ArtifactIndexingStatus
    sensitive_content_withheld: Annotated[StrictInt, Field(ge=0)]
    artifact_library: ArtifactToolStatus
