"""Typed, bounded projections of immutable normalized conversation records."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, model_validator

from mnemonic_api.transcript_normalization import ContentKind

Digest = Annotated[str, Field(strict=True, pattern="^[0-9a-f]{64}$")]
SegmentIdentity = Annotated[str, Field(strict=True, pattern="^[0-9a-f]{24}$")]
Nonnegative = Annotated[int, Field(strict=True, ge=0)]
Positive = Annotated[int, Field(strict=True, ge=1)]
NativeString = Annotated[str, Field(strict=True, max_length=4096)]
Disposition = Annotated[str, Field(strict=True, min_length=1, max_length=80)]


class SegmentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: SegmentIdentity
    event_id: SegmentIdentity
    ordinal: Nonnegative
    source_record: Positive
    source_block: str = Field(strict=True, min_length=1, max_length=64)
    role: NativeString | None = None
    content_kind: ContentKind
    text: str = Field(strict=True, max_length=200_000)
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
    dispositions: list[Disposition] = Field(default_factory=list, max_length=16)
    related_segment_id: SegmentIdentity | None = None
    text_offset: Nonnegative
    text_truncated: StrictBool


class SegmentWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor_segment_id: SegmentIdentity
    anchor_ordinal: Nonnegative
    first_ordinal: Nonnegative
    last_ordinal: Nonnegative

    @model_validator(mode="after")
    def ordered_window(self) -> Self:
        if not self.first_ordinal <= self.anchor_ordinal <= self.last_ordinal:
            raise ValueError("The anchor must be inside the selected segment window")
        if self.last_ordinal - self.first_ordinal > 20:
            raise ValueError("A selected segment window contains at most 21 segments")
        return self
