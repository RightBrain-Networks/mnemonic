"""Independent, revision-bound contracts for normalized conversation fragments."""

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    model_validator,
)

ContentKind = Literal[
    "human_text", "assistant_text", "tool_call", "tool_result", "system_text",
    "reasoning", "summary", "unsupported",
]
SegmentIdentity = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{24}$")]
NormalizedRevision = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
SegmentOffset = Annotated[StrictInt, Field(ge=0, le=1_073_741_824)]
SurroundingSegments = Annotated[StrictInt, Field(ge=0, le=20)]
NativeString = Annotated[str, Field(strict=True, max_length=4096)]


def _unique_kinds(value: list[ContentKind]) -> list[ContentKind]:
    if len(set(value)) != len(value):
        raise ValueError("Content kinds must be distinct")
    return value


ContentKinds = Annotated[
    list[ContentKind], Field(min_length=1, max_length=8), AfterValidator(_unique_kinds),
]


class SegmentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: SegmentIdentity
    event_id: SegmentIdentity
    ordinal: Annotated[StrictInt, Field(ge=0)]
    source_record: Annotated[StrictInt, Field(ge=1)]
    source_block: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    role: NativeString | None = None
    content_kind: ContentKind
    text: Annotated[str, Field(strict=True, max_length=20_000)]
    timestamp: NativeString | None = None
    native_event_id: NativeString | None = None
    native_parent_id: NativeString | None = None
    native_branch_id: NativeString | None = None
    channel: NativeString | None = None
    is_sidechain: StrictBool | None = None
    is_error: StrictBool | None = None
    tool_name: NativeString | None = None
    call_id: NativeString | None = None
    payload: JsonValue = None
    dispositions: Annotated[
        list[Annotated[str, Field(strict=True, min_length=1, max_length=80)]], Field(max_length=16),
    ] = Field(default_factory=list)
    related_segment_id: SegmentIdentity | None = None
    text_offset: SegmentOffset
    text_truncated: StrictBool

    def belongs_to_revision(self, revision: str) -> bool:
        event = hashlib.sha256(f"{revision}:{self.source_record}".encode()).hexdigest()[:24]
        segment = hashlib.sha256(f"{event}:{self.source_block}".encode()).hexdigest()[:24]
        return self.event_id == event and self.segment_id == segment


class SegmentWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor_segment_id: SegmentIdentity
    anchor_ordinal: Annotated[StrictInt, Field(ge=0)]
    first_ordinal: Annotated[StrictInt, Field(ge=0)]
    last_ordinal: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def ordered_window(self) -> Self:
        if not self.first_ordinal <= self.anchor_ordinal <= self.last_ordinal:
            raise ValueError("The anchor must be inside the selected segment window")
        if self.last_ordinal - self.first_ordinal > 20:
            raise ValueError("A selected segment window contains at most 21 segments")
        return self

    def matches_request(self, segment_id: str, before: int, after: int) -> bool:
        return (
            self.anchor_segment_id == segment_id
            and self.first_ordinal == max(0, self.anchor_ordinal - before)
            and self.last_ordinal <= self.anchor_ordinal + after
        )


def segments_match_window(
    segments: list[SegmentRead], window: SegmentWindow, revision: str, offset: int,
) -> bool:
    if not segments or len(segments) > window.last_ordinal - window.first_ordinal + 1:
        return False
    for index, segment in enumerate(segments):
        ordinal = window.first_ordinal + index
        if (segment.ordinal != ordinal or not segment.belongs_to_revision(revision)
                or segment.text_offset != (offset if ordinal == window.anchor_ordinal else 0)
                or (ordinal == window.anchor_ordinal
                    and segment.segment_id != window.anchor_segment_id)
                or (index < len(segments) - 1 and segment.text_truncated)):
            return False
    return len({segment.segment_id for segment in segments}) == len(segments)


def continuation_matches(
    segments: list[SegmentRead], window: SegmentWindow,
    next_segment_id: str | None, next_segment_offset: int | None, next_segment_after: int | None,
) -> bool:
    last = segments[-1]
    if last.text_truncated:
        return (
            bool(last.text) and next_segment_id == last.segment_id
            and next_segment_offset == last.text_offset + len(last.text)
            and next_segment_after == window.last_ordinal - last.ordinal
        )
    if last.ordinal < window.last_ordinal:
        return (
            next_segment_id is not None and next_segment_id not in {
                segment.segment_id for segment in segments
            } and next_segment_offset == 0
            and next_segment_after == window.last_ordinal - last.ordinal - 1
        )
    return next_segment_id is None and next_segment_offset is None and next_segment_after is None


_NATIVE_FIELDS = (
    "role", "timestamp", "native_event_id", "native_parent_id", "native_branch_id",
    "channel", "tool_name", "call_id",
)


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8"))


def segments_fit_budget(segments: list[SegmentRead], limit: int) -> bool:
    """Use compact JSON as a lower bound on PostgreSQL's charged JSON payload bytes."""
    content_size = sum(len(item.text) for item in segments) + 2 * (len(segments) - 1)
    metadata_size = 0
    for item in segments:
        native = {name: getattr(item, name) for name in _NATIVE_FIELDS
                  if getattr(item, name) is not None}
        if (("metadata_omitted_for_budget" in item.dispositions and native)
                or ("payload_omitted_for_budget" in item.dispositions and item.payload is not None)):
            return False
        if "metadata_omitted_for_budget" not in item.dispositions:
            metadata_size += _json_bytes(native)
        if item.payload is not None:
            content_size += _json_bytes(item.payload)
    return content_size <= limit and metadata_size <= 4096
