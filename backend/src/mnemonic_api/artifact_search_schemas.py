"""Literal, opt-in content search over the current project artifact library."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from mnemonic_api.artifact_access_schemas import ArtifactAccessRequest
from mnemonic_api.artifact_schemas import ArtifactModel, ArtifactRead


class ArtifactSearchRequest(ArtifactAccessRequest):
    q: str = Field(min_length=1, max_length=200)
    fulltext: bool = False
    artifact_id: UUID | None = None
    work_item_id: UUID | None = None
    include_deleted: bool = False
    limit: int = Field(default=50, ge=1, le=100)
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


class ArtifactSearchPage(ArtifactModel):
    items: list[ArtifactSearchMatch]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    fulltext: bool
    indexing: ArtifactIndexingStatus
    sensitive_content_withheld: int = Field(default=0, ge=0)
