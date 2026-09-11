"""Bounded transcript contracts; stored prose is untrusted historical context."""

import json
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)
from pydantic.experimental.missing_sentinel import MISSING

TranscriptLimit = Annotated[StrictInt, Field(ge=1, le=100)]
TranscriptOffset = Annotated[StrictInt, Field(ge=0, le=10_000)]
TranscriptTextOffset = Annotated[StrictInt, Field(ge=0, le=8_000_000)]
TranscriptTextLimit = Annotated[StrictInt, Field(ge=1, le=20_000)]
TranscriptHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
TranscriptQuery = Annotated[str, Field(min_length=1, max_length=200)]
TranscriptStatus = Literal["waiting", "pending", "processing", "ready", "failed"]


class TranscriptModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranscriptLocation(TranscriptModel):
    client: Annotated[str, Field(min_length=1, max_length=80)]
    path: Annotated[str, Field(min_length=1, max_length=4096)]

    @field_validator("client")
    @classmethod
    def valid_client(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("Transcript client must be a nonempty client identifier")
        return value

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        if (not PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts
                or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
            raise ValueError("Transcript path must be an absolute filesystem path")
        return value


def _distinct_sources(sources: list[TranscriptLocation]) -> list[TranscriptLocation]:
    if len({item.path for item in sources}) != len(sources):
        raise ValueError("Each subagent transcript path may be reported only once")
    return sources


SubagentTranscripts = Annotated[
    list[TranscriptLocation], Field(min_length=1, max_length=100), AfterValidator(_distinct_sources),
] | None


type SubagentTranscriptArgument = SubagentTranscripts | MISSING


def subagent_transcript_payload(locations: SubagentTranscriptArgument) -> dict[str, object]:
    """Preserve omitted assertions for historical receipt lookup."""
    if locations is MISSING:
        return {}
    return {"subagent_transcripts": transcript_locations_payload(locations)}


def transcript_locations_payload(locations: SubagentTranscripts) -> list[dict[str, object]] | None:
    return None if locations is None else [item.model_dump(mode="json") for item in locations]


class TranscriptRead(TranscriptModel):
    id: UUID
    project_id: UUID
    work_item_id: UUID | None
    lease_generation_id: UUID | None
    client: Annotated[str, Field(min_length=1, max_length=80)]
    session_id: Annotated[str, Field(min_length=1, max_length=200)] | None
    source_path: Annotated[str, Field(max_length=4096)]
    filename: Annotated[str, Field(max_length=4096)]
    kind: Literal["primary", "subagent", "imported"]
    status: TranscriptStatus
    indexing_started_at: datetime | None
    indexing_completed_at: datetime | None
    error_code: Annotated[str, Field(max_length=100)] | None
    size_bytes: Annotated[StrictInt, Field(ge=0, le=1024 * 1024 * 1024)] | None
    mime_type: Annotated[str, Field(max_length=100)] | None
    format: Annotated[str, Field(max_length=100)] | None
    sha256: TranscriptHash | None
    text_sha256: TranscriptHash | None
    metadata: dict[Annotated[str, Field(max_length=128)],
                   Annotated[list[Annotated[str, Field(max_length=512)]], Field(max_length=8)]]
    truncated: StrictBool
    created_at: datetime
    snippet: Annotated[str, Field(max_length=1000)] | None = None
    score: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def coherent_provenance(self) -> Self:
        provenance = (self.work_item_id, self.lease_generation_id, self.session_id)
        if self.kind == "imported":
            valid = all(value is None for value in provenance)
        else:
            valid = all(value is not None for value in provenance)
        if not valid:
            raise ValueError("Transcript provenance does not match its source kind")
        return self

    @field_validator("metadata")
    @classmethod
    def bounded_metadata(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if len(value) > 64 or len(json.dumps(value, ensure_ascii=True)) > 8192:
            raise ValueError("Oversized extracted transcript metadata")
        return value


class TranscriptPage(TranscriptModel):
    items: Annotated[list[TranscriptRead], Field(max_length=100)]
    total: Annotated[StrictInt, Field(ge=0)]
    limit: TranscriptLimit
    offset: TranscriptOffset
    indexing_incomplete: StrictBool


class TranscriptTextPage(TranscriptModel):
    project_id: UUID
    transcript_id: UUID
    text_sha256: TranscriptHash | None
    text: Annotated[str, Field(max_length=20_000)] | None
    total_chars: TranscriptTextOffset | None
    offset: TranscriptTextOffset
    limit: TranscriptTextLimit
    next_offset: TranscriptTextOffset | None
    status: TranscriptStatus
    truncated: StrictBool


class TranscriptDownload(TranscriptModel):
    transcript: TranscriptRead
    content_base64: Annotated[str, Field(max_length=4 * ((32 * 1024 * 1024 + 2) // 3))]
