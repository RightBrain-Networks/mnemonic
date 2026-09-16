"""Canonical identity, kind taxonomy, and bounded segment continuations."""

import hashlib

import pytest
from pydantic import TypeAdapter, ValidationError

from mnemonic_mcp.transcript_segments import (
    ContentKinds,
    SegmentRead,
    SegmentWindow,
    continuation_matches,
    segments_fit_budget,
    segments_match_window,
)

REVISION = "a" * 64


def segment(ordinal=0, **changes):
    event = hashlib.sha256(f"{REVISION}:{ordinal + 1}".encode()).hexdigest()[:24]
    return SegmentRead.model_validate({
        "segment_id": hashlib.sha256(f"{event}:0".encode()).hexdigest()[:24],
        "event_id": event, "ordinal": ordinal, "source_record": ordinal + 1,
        "source_block": "0", "content_kind": "assistant_text", "text": "Hello 🌲",
        "text_offset": 0, "text_truncated": False, **changes,
    })


@pytest.mark.parametrize("kinds", [[], ["tool_result", "tool_result"], ["user"], [True]])
def test_content_kind_taxonomy_is_bounded_and_distinct(kinds):
    with pytest.raises(ValidationError):
        TypeAdapter(ContentKinds).validate_python(kinds)


@pytest.mark.parametrize("patch", [
    {"source_record": 0}, {"ordinal": True}, {"segment_id": "a" * 64},
    {"source_block": ""}, {"text": "a" * 20001}, {"role": "a" * 4097},
    {"is_error": 1}, {"dispositions": ["a" * 81]}, {"unexpected": "private"},
])
def test_segment_dto_rejects_invalid_or_unbounded_metadata(patch):
    with pytest.raises(ValidationError):
        segment(**patch)


def test_revision_derivation_prevents_valid_looking_segment_substitution():
    first = segment()
    window = SegmentWindow(anchor_segment_id=first.segment_id, anchor_ordinal=0,
                           first_ordinal=0, last_ordinal=1)
    assert segments_match_window([first, segment(1)], window, REVISION, 0)
    assert not segments_match_window([first], window, "b" * 64, 0)
    altered = first.model_copy(update={"source_record": 3})
    assert not segments_match_window([altered], window, REVISION, 0)
    assert not segments_match_window([segment(1), first], window, REVISION, 0)
    assert not segments_match_window([first, first], window, REVISION, 0)


def test_before_context_can_exhaust_budget_before_anchor_without_losing_window():
    first, anchor = segment(text_truncated=True), segment(1)
    window = SegmentWindow(anchor_segment_id=anchor.segment_id, anchor_ordinal=1,
                           first_ordinal=0, last_ordinal=2)
    assert window.matches_request(anchor.segment_id, 1, 1)
    assert segments_match_window([first], window, REVISION, 0)
    assert continuation_matches([first], window, first.segment_id, len(first.text), 2)
    assert not continuation_matches([first], window, anchor.segment_id, 0, 1)
    assert not continuation_matches([first], window, first.segment_id, 0, 2)


def test_continuation_requires_progress_and_retains_remaining_surroundings():
    first, second = segment(), segment(1)
    window = SegmentWindow(anchor_segment_id=first.segment_id, anchor_ordinal=0,
                           first_ordinal=0, last_ordinal=1)
    assert continuation_matches([first], window, second.segment_id, 0, 0)
    assert not continuation_matches([first], window, first.segment_id, 0, 0)
    assert not continuation_matches([first], window, second.segment_id, 0, 1)
    assert continuation_matches([first, second], window, None, None, None)
    assert not continuation_matches([first, second], window, None, 0, None)


def test_segment_offsets_can_continue_beyond_flat_index_cap():
    first = segment(text_offset=8_000_001)
    window = SegmentWindow(anchor_segment_id=first.segment_id, anchor_ordinal=0,
                           first_ordinal=0, last_ordinal=0)
    assert segments_match_window([first], window, REVISION, 8_000_001)


def test_segment_payload_and_optional_metadata_share_explicit_budgets():
    assert segments_fit_budget([segment(payload={"ok": True})], 30)
    assert not segments_fit_budget([segment(payload="a" * 100)], 30)
    assert not segments_fit_budget([segment(role="a" * 3000), segment(1, role="a" * 3000)], 30)
    assert not segments_fit_budget([segment(role="user",
        dispositions=["metadata_omitted_for_budget"])], 30)
    assert not segments_fit_budget([segment(payload={"leak": True},
        dispositions=["payload_omitted_for_budget"])], 30)
