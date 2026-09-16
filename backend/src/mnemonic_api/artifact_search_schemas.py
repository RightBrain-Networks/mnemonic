"""Literal, opt-in content search over the current project artifact library."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from mnemonic_api.artifact_access_schemas import ArtifactAccessRequest
from mnemonic_api.artifact_schemas import ArtifactModel, ArtifactRead
from mnemonic_api.search_diagnostics import TermDiagnostics
from mnemonic_api.search_disclosure import SearchDisclosure


class ArtifactSearchRequest(ArtifactAccessRequest):
    q: str = Field(min_length=1, max_length=200)
    fulltext: bool = False
    detail: Literal["compact", "full"] = "compact"
    artifact_id: UUID | None = None
    work_item_id: UUID | None = None
    include_deleted: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("q")
    @classmethod
    def valid_query(cls, value: str) -> str:
        value.encode("utf-8")
        if not value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("Provide nonblank search text without controls")
        return value


class ArtifactIndexingStatus(ArtifactModel):
    pending: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    ready: int = Field(default=0, ge=0)
    truncated: int = Field(default=0, ge=0)


class ArtifactSearchMatch(ArtifactModel):
    artifact: ArtifactRead
    score: float = Field(ge=0, allow_inf_nan=False)
    snippet: str | None = Field(default=None, max_length=1000)
    matched_fields: list[Literal["metadata", "content"]]


class CompactExtractionStatus(ArtifactModel):
    status: Literal["pending", "processing", "ready", "failed", "superseded", "deleted"]
    truncated: bool
    error_code: str | None


class CompactArtifactRead(ArtifactModel):
    id: UUID
    project_id: UUID
    filename: str
    revision: int
    sensitive: bool
    deleted_at: datetime | None
    content_available: bool
    extraction: CompactExtractionStatus


class CompactArtifactMatch(ArtifactModel):
    artifact: CompactArtifactRead
    score: float = Field(ge=0, allow_inf_nan=False)
    snippet: str | None = Field(default=None, max_length=1000)
    matched_fields: list[Literal["metadata", "content"]]


class ArtifactSearchPage(ArtifactModel, SearchDisclosure):
    detail: Literal["compact", "full"]
    match_mode: Literal["all_terms"] = "all_terms"
    term_diagnostics: TermDiagnostics
    items: list[ArtifactSearchMatch | CompactArtifactMatch]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    fulltext: bool
    indexing: ArtifactIndexingStatus
    sensitive_content_withheld: int = Field(default=0, ge=0)


    @model_validator(mode="after")
    def projection_matches_detail(self) -> Self:
        if any(isinstance(item, CompactArtifactMatch) != (self.detail == "compact")
               for item in self.items):
            raise ValueError("Artifact search projection must match detail")
        return self
