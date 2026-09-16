"""Revision-bound bounded conversation retrieval; no client parser runs on reads."""

from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import Transcript
from mnemonic_api.services.transcript_segment_projection import (
    OPTIONAL_METADATA_BYTES,
    read_segment,
)
from mnemonic_api.transcript_normalized_storage import SEGMENTS
from mnemonic_api.transcript_read_schemas import SegmentRead, SegmentWindow
from mnemonic_api.transcript_schemas import TranscriptStatus, TranscriptText


def _scope(record: Transcript, revision: str | None):
    return (SEGMENTS.c.transcript_id == record.id) & (SEGMENTS.c.revision == revision)


def _ordinal(database: Session, record: Transcript, segment_id: str,
             expected_revision: str | None) -> int:
    revision = record.normalized_revision
    if revision is None:
        raise ApplicationError(409, "transcript_not_normalized",
                               "Structured transcript coverage is not available yet.")
    if expected_revision != revision:
        raise conflict("transcript_normalization_changed",
                       "Supply the current normalized revision with the segment locator.")
    ordinal = database.scalar(select(SEGMENTS.c.ordinal).where(
        _scope(record, revision), SEGMENTS.c.segment_id == segment_id))
    if ordinal is None:
        raise ApplicationError(404, "transcript_segment_not_found",
                               "The segment does not exist in this normalized revision.")
    return ordinal


def _window(database: Session, scope, window: SegmentWindow,
            offset: int, limit: int) -> tuple[list[SegmentRead], int, dict]:
    first, last = window.first_ordinal, window.last_ordinal
    total = database.scalar(select(func.sum(func.length(SEGMENTS.c.text))).where(
        scope, SEGMENTS.c.ordinal.between(first, last))) or 0
    anchor_size = database.scalar(select(func.length(SEGMENTS.c.text)).where(
        scope, SEGMENTS.c.ordinal == window.anchor_ordinal)) or 0
    total += 2 * (last - first) - min(offset, anchor_size)
    rendered, remaining, metadata_remaining = [], limit, OPTIONAL_METADATA_BYTES
    continuation = {}
    for position in range(first, last + 1):
        start = offset if position == window.anchor_ordinal else 0
        result = read_segment(database, scope, position, start, remaining, metadata_remaining)
        item = result.item
        rendered.append(item)
        remaining = max(0, result.remaining - 2)
        metadata_remaining = result.metadata_remaining
        if item.text_truncated or not remaining:
            next_position = position if item.text_truncated else position + 1
            if next_position <= last:
                continuation = {
                    "next_segment_id": database.scalar(select(SEGMENTS.c.segment_id).where(
                        scope, SEGMENTS.c.ordinal == next_position)),
                    "next_segment_offset": start + len(item.text) if item.text_truncated else 0,
                    "next_segment_after": last - next_position,
                }
            break
    return rendered, total, continuation


def segment_range(database: Session, record: Transcript, project_id: UUID, segment_id: str,
                  expected_revision: str | None, before: int, after: int,
                  offset: int, limit: int) -> TranscriptText:
    if before + after > 20:
        raise ApplicationError(422, "transcript_segment_window_too_large",
                               "Select at most 20 surrounding segments in total.")
    if before and offset:
        raise ApplicationError(422, "transcript_segment_offset_requires_no_before",
                               "For segment continuation use before=0 with the returned offset.")
    ordinal = _ordinal(database, record, segment_id, expected_revision)
    scope = _scope(record, record.normalized_revision)
    maximum = database.scalar(select(func.max(SEGMENTS.c.ordinal)).where(scope))
    assert maximum is not None
    window = SegmentWindow(anchor_segment_id=segment_id, anchor_ordinal=ordinal,
                           first_ordinal=max(0, ordinal - before),
                           last_ordinal=min(ordinal + after, maximum))
    rendered, total, continuation = _window(database, scope, window, offset, limit)
    return TranscriptText(transcript_id=record.id, project_id=project_id,
        text="\n\n".join(value.text for value in rendered), total_chars=total,
        offset=offset, limit=limit, next_offset=None, status=cast(TranscriptStatus, record.status),
        truncated=record.normalization_incomplete, text_sha256=record.text_sha256,
        normalized_revision=record.normalized_revision, segments=rendered,
        segment_window=window, **continuation)
