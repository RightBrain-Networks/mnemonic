"""Structured transcript responses reject malformed locators and unbounded native fields."""

from dataclasses import asdict
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mnemonic_api.transcript_normalization import normalize_transcript
from mnemonic_api.transcript_read_schemas import SegmentRead, SegmentWindow


def _segment():
    record = normalize_transcript(b'{"role":"user","content":"hello"}', "claude-code", uuid4())
    return asdict(record.segments[0]) | {"text_offset": 0, "text_truncated": False}


@pytest.mark.parametrize("values", [
    {"segment_id": "0" * 23}, {"event_id": "x" * 24}, {"related_segment_id": "0" * 64},
    {"ordinal": -1}, {"ordinal": True}, {"source_record": 0}, {"source_block": "x" * 65},
    {"content_kind": "user"}, {"native_event_id": "x" * 4097}, {"text": "x" * 200_001},
    {"text_offset": -1}, {"text_truncated": "false"}, {"payload": object()}, {"extra": "value"},
])
def test_segment_read_rejects_invalid_public_values(values):
    with pytest.raises(ValidationError):
        SegmentRead.model_validate(_segment() | values)


@pytest.mark.parametrize("anchor,first,last", [(0, 1, 2), (3, 0, 2), (0, 0, 21)])
def test_window_requires_ordered_bounded_anchor(anchor, first, last):
    with pytest.raises(ValidationError):
        SegmentWindow(anchor_segment_id="0" * 24, anchor_ordinal=anchor,
                      first_ordinal=first, last_ordinal=last)
