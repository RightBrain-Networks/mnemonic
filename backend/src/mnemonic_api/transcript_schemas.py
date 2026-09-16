"""Public transcript records never include source bytes or text implicitly."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from mnemonic_api.search_diagnostics import TermDiagnostics
from mnemonic_api.search_disclosure import SearchDisclosure
from mnemonic_api.search_exploration_schemas import SearchOptions
from mnemonic_api.search_query import QueryMode, parse_query
from mnemonic_api.search_ranking import ScoreType, SearchRanking
from mnemonic_api.transcript_locations import TranscriptLocation
from mnemonic_api.transcript_normalization import ContentKind
from mnemonic_api.transcript_read_schemas import (
    Digest,
    Nonnegative,
    Positive,
    SegmentIdentity,
    SegmentRead,
    SegmentWindow,
)
from mnemonic_api.validation_rules import validation_rule

TranscriptStatus = Literal["waiting", "pending", "processing", "ready", "failed"]


class TranscriptNormalizationRead(BaseModel):
    rank: int | None = Field(default=None, ge=1)
    score_type: ScoreType = "none"
    normalization_status: Literal["pending", "processing", "ready", "failed"] = "pending"
    normalization_error_code: str | None = None
    normalized_revision: Digest | None = None
    normalized_sha256: Digest | None = None
    normalization_schema_version: Positive | None = None
    normalized_size_bytes: Nonnegative | None = None
    normalizer_version: Positive | None = None
    segment_count: Nonnegative = 0
    normalization_incomplete: bool = False
    segment_id: SegmentIdentity | None = None
    content_kind: ContentKind | None = None
    snippet_omission_reason: Literal["matched_span_exceeds_budget"] | None = None
    matched_fields: list[Literal["metadata", "content"]] = Field(default_factory=list, max_length=2)


class TranscriptRead(TranscriptNormalizationRead):
    id: UUID
    project_id: UUID
    work_item_id: UUID | None
    lease_generation_id: UUID | None
    client: str
    session_id: str | None
    source_path: str
    filename: str
    kind: Literal["primary", "subagent", "imported"]
    status: TranscriptStatus
    copy_status: Literal["pending", "processing", "ready", "failed"]
    copy_error_code: str | None
    copied_at: datetime | None
    index_status: TranscriptStatus
    index_error_code: str | None
    indexing_started_at: datetime | None
    indexing_completed_at: datetime | None
    error_code: str | None
    size_bytes: int | None
    mime_type: str | None
    format: str | None
    sha256: Digest | None
    text_sha256: Digest | None
    metadata: dict[str, list[str]]
    truncated: bool
    created_at: datetime
    snippet: str | None = None
    score: float | None = None


class CompactTranscriptRead(TranscriptNormalizationRead):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    project_id: UUID
    work_item_id: UUID | None
    client: str
    session_id: str | None
    filename: str
    kind: Literal["primary", "subagent", "imported"]
    status: TranscriptStatus
    index_status: TranscriptStatus
    copy_status: Literal["pending", "processing", "ready", "failed"]
    truncated: bool
    snippet: str | None = None
    score: float | None = None


class TranscriptSearch(SearchOptions):
    model_config = ConfigDict(extra="forbid")
    query: str | None = Field(default=None, max_length=1000)
    query_mode: QueryMode = "terms"
    fulltext: bool = False
    detail: Literal["compact", "full"] = "compact"
    content_kinds: list[ContentKind] | None = Field(default=None, min_length=1, max_length=8)
    work_item_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)


    @model_validator(mode="after")
    def content_kind_requires_fulltext(self) -> TranscriptSearch:
        parse_query(self.query, self.query_mode)
        if self.content_kinds and not self.fulltext:
            raise validation_rule("content_kinds_requires_fulltext")
        if self.content_kinds and len(set(self.content_kinds)) != len(self.content_kinds):
            raise ValueError("content_kinds must contain unique kinds")
        return self


class TranscriptPage(SearchDisclosure, SearchRanking):
    detail: Literal["compact", "full"]
    term_diagnostics: TermDiagnostics = Field(default_factory=list)
    items: list[TranscriptRead | CompactTranscriptRead]
    total: int
    limit: int
    offset: int
    indexing_incomplete: bool
    unsegmented_content_omitted: int = Field(default=0, ge=0)


    @model_validator(mode="after")
    def projection_matches_detail(self) -> Self:
        if any(isinstance(item, CompactTranscriptRead) != (self.detail == "compact")
               for item in self.items):
            raise ValueError("Transcript search projection must match detail")
        return self


class TranscriptText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript_id: UUID
    project_id: UUID
    text: str = Field(strict=True, max_length=200_000)
    total_chars: Nonnegative
    offset: Nonnegative
    limit: int = Field(strict=True, ge=1, le=200_000)
    next_offset: Nonnegative | None
    status: TranscriptStatus
    truncated: StrictBool
    text_sha256: Digest | None
    normalized_revision: Digest | None = None
    segments: list[SegmentRead] | None = Field(default=None, min_length=1, max_length=21)
    segment_window: SegmentWindow | None = None
    next_segment_id: SegmentIdentity | None = None
    next_segment_offset: Nonnegative | None = None
    next_segment_after: int | None = Field(default=None, strict=True, ge=0, le=20)

    @model_validator(mode="after")
    def coherent_text_page(self) -> TranscriptText:
        if len(self.text) > self.limit or len(self.text) > self.total_chars:
            raise ValueError("Returned text exceeds the requested or logical range")
        continuation = (self.next_segment_id, self.next_segment_offset, self.next_segment_after)
        if any(value is None for value in continuation) and any(
            value is not None for value in continuation
        ):
            raise ValueError("A segment continuation requires its complete locator")
        if self.segments is None:
            if self.segment_window is not None or self.next_segment_id is not None:
                raise ValueError("Flat text has no segment window or continuation")
        else:
            self._check_segment_page()
        return self

    def _check_segment_page(self) -> None:
        window, segments = self.segment_window, self.segments
        if window is None or not segments or self.normalized_revision is None:
            raise ValueError("Structured text requires a normalized revision and window")
        if self.next_offset is not None or self.text != "\n\n".join(row.text for row in segments):
            raise ValueError("Structured text must agree with the returned segment fragments")
        expected = list(range(window.first_ordinal, window.first_ordinal + len(segments)))
        if ([row.ordinal for row in segments] != expected
                or segments[-1].ordinal > window.last_ordinal):
            raise ValueError("Returned segments must follow the selected window")
        if segments[0].text_offset != self.offset or any(row.text_offset for row in segments[1:]):
            raise ValueError("Segment offsets must match the requested continuation")
        if any(row.text_truncated for row in segments[:-1]):
            raise ValueError("Only the final fragment can be truncated")
        self._check_segment_continuation(window, segments[-1])

    def _check_segment_continuation(self, window: SegmentWindow, last: SegmentRead) -> None:
        if self.next_segment_id is None:
            if last.text_truncated or last.ordinal != window.last_ordinal:
                raise ValueError("An unfinished window requires a segment continuation")
            return
        next_ordinal = last.ordinal if last.text_truncated else last.ordinal + 1
        next_offset = last.text_offset + len(last.text) if last.text_truncated else 0
        if (self.next_segment_after != window.last_ordinal - next_ordinal
                or self.next_segment_offset != next_offset):
            raise ValueError("The continuation must advance within the selected window")
        if last.text_truncated and self.next_segment_id != last.segment_id:
            raise ValueError("A truncated fragment must continue the same segment")


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


class TranscriptImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_operation_id: UUID
    directory: str = Field(strict=True, min_length=1, max_length=4096)

    @field_validator("directory")
    @classmethod
    def safe_directory(cls, value: str) -> str:
        return TranscriptLocation(client="claude_code", path=value).path


class TranscriptImportRead(BaseModel):
    project_id: UUID
    client_operation_id: UUID
    directory: str
    imported: int
    existing: int
    skipped: int
