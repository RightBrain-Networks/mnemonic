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

from .search_diagnostics import TermDiagnostics
from .search_disclosure import SearchDetail, SearchDisclosure
from .search_ranking import ScoreType, SearchRanking
from .transcript_segments import (
    ContentKind,
    NormalizedRevision,
    SegmentIdentity,
    SegmentOffset,
    SegmentRead,
    SegmentWindow,
)

TranscriptLimit = Annotated[StrictInt, Field(ge=1, le=100)]
TranscriptOffset = Annotated[StrictInt, Field(ge=0, le=10_000)]
TranscriptTextOffset = Annotated[StrictInt, Field(ge=0, le=1_073_741_824)]
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


class TranscriptNormalization(TranscriptModel):
    last_updated_at: datetime | None = None
    index_created_at: datetime | None = None
    session_ids: list[str] = Field(default_factory=list, max_length=8)
    models: list[str] = Field(default_factory=list, max_length=8)
    matched_fields: Annotated[list[Literal["metadata", "content"]], Field(max_length=2)] = Field(default_factory=list)
    snippet_omission_reason: Literal["matched_span_exceeds_budget"] | None = None
    rank: Annotated[StrictInt, Field(ge=1)] | None = None
    score_type: ScoreType = "none"
    normalization_status: Literal["pending", "processing", "ready", "failed"] = "pending"
    normalization_error_code: Annotated[str, Field(max_length=100)] | None = None
    normalized_revision: NormalizedRevision | None = None
    normalized_sha256: TranscriptHash | None = None
    normalization_schema_version: Annotated[StrictInt, Field(ge=1)] | None = None
    normalized_size_bytes: Annotated[StrictInt, Field(ge=0)] | None = None
    normalizer_version: Annotated[StrictInt, Field(ge=1)] | None = None
    segment_count: Annotated[StrictInt, Field(ge=0)] = 0
    normalization_incomplete: StrictBool = False
    segment_id: SegmentIdentity | None = None
    content_kind: ContentKind | None = None

    @model_validator(mode="after")
    def coherent_locator(self) -> Self:
        if (self.segment_id is None) != (self.content_kind is None):
            raise ValueError("A transcript match requires both segment identity and content kind")
        if self.segment_id is not None and self.normalized_revision is None:
            raise ValueError("A transcript segment locator requires its normalized revision")
        return self


class TranscriptRead(TranscriptNormalization):
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
    index_status: TranscriptStatus
    index_error_code: Annotated[str, Field(max_length=100)] | None
    copy_status: Literal["pending", "processing", "ready", "failed"]
    copy_error_code: Annotated[str, Field(max_length=100)] | None
    copied_at: datetime | None
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


class CompactTranscriptRead(TranscriptNormalization):
    id: UUID
    project_id: UUID
    work_item_id: UUID | None
    client: Annotated[str, Field(min_length=1, max_length=80)]
    session_id: Annotated[str, Field(min_length=1, max_length=200)] | None
    filename: Annotated[str, Field(max_length=4096)]
    kind: Literal["primary", "subagent", "imported"]
    status: TranscriptStatus
    index_status: TranscriptStatus
    copy_status: Literal["pending", "processing", "ready", "failed"]
    truncated: StrictBool
    snippet: Annotated[str, Field(max_length=1000)] | None = None
    score: Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)] | None = None

    @model_validator(mode="after")
    def coherent_provenance(self) -> Self:
        absent = self.work_item_id is None and self.session_id is None
        present = self.work_item_id is not None and self.session_id is not None
        if not (absent if self.kind == "imported" else present):
            raise ValueError("Compact transcript provenance does not match source kind")
        return self


class TranscriptPage(TranscriptModel, SearchDisclosure, SearchRanking):
    sort_by: Literal["name", "size", "session", "indexing", "updated"] | None = None
    sort_direction: Literal["asc", "desc"] = "desc"
    unsegmented_content_omitted: Annotated[StrictInt, Field(ge=0)] = 0
    detail: SearchDetail
    term_diagnostics: TermDiagnostics
    items: Annotated[list[TranscriptRead | CompactTranscriptRead], Field(max_length=100)]
    total: Annotated[StrictInt, Field(ge=0)]
    limit: TranscriptLimit
    offset: TranscriptOffset
    indexing_incomplete: StrictBool


class TranscriptTextPage(TranscriptModel):
    project_id: UUID
    transcript_id: UUID
    text_sha256: TranscriptHash | None
    text: Annotated[str, Field(max_length=20_000)] | None
    total_chars: Annotated[StrictInt, Field(ge=0)] | None
    offset: SegmentOffset
    limit: TranscriptTextLimit
    next_offset: TranscriptTextOffset | None
    status: TranscriptStatus
    truncated: StrictBool
    normalized_revision: NormalizedRevision | None = None
    segments: Annotated[list[SegmentRead], Field(min_length=1, max_length=21)] | None = None
    segment_window: SegmentWindow | None = None
    next_segment_id: SegmentIdentity | None = None
    next_segment_offset: SegmentOffset | None = None
    next_segment_after: Annotated[StrictInt, Field(ge=0, le=20)] | None = None


class TranscriptDownload(TranscriptModel):
    transcript: TranscriptRead
    content_base64: Annotated[str, Field(max_length=4 * ((32 * 1024 * 1024 + 2) // 3))]
