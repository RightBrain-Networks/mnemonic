"""Transactional publication and reads of immutable normalized conversation rows."""

from dataclasses import asdict
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from mnemonic_api.models import Transcript, TranscriptNormalization, TranscriptSegment
from mnemonic_api.transcript_normalization import (
    NormalizedConversation,
    Segment,
    canonical_json,
    revision_for,
    segment_text,
)

NORMALIZATIONS = TranscriptNormalization.__table__
SEGMENTS = TranscriptSegment.__table__


def load_normalization(database: Session, transcript_id: UUID, snapshot_id: UUID,
                       source_sha256: str,
                       maximum_chars: int = 8_000_000) -> NormalizedConversation | None:
    revision = revision_for(snapshot_id, source_sha256)
    row = database.execute(select(NORMALIZATIONS).where(
        NORMALIZATIONS.c.transcript_id == transcript_id,
        NORMALIZATIONS.c.revision == revision)).mappings().first()
    if row is None:
        return None
    # Tool payloads are already durable and irrelevant to text extraction. Stream
    # one source block at a time and stop after the derived-text budget is met.
    statement = (select(SEGMENTS.c.segment_data.op("-")("payload")).where(
        SEGMENTS.c.transcript_id == transcript_id, SEGMENTS.c.revision == revision)
        .order_by(SEGMENTS.c.ordinal).execution_options(yield_per=1))
    segments, size = [], 0
    rows = database.scalars(statement)
    try:
        for value in rows:
            segment = Segment(**value)
            segments.append(segment)
            text = segment_text(segment)
            size += len(text) + 2 if text else 0
            if size > maximum_chars:
                break
    finally:
        rows.close()
    return NormalizedConversation(revision, row["sha256"], snapshot_id, source_sha256,
        row["format"], row["mime_type"], row["metadata"], segments, row["incomplete"],
        row["schema_version"], row["normalizer_version"], row["segment_count"], row["size_bytes"],
        len(segments) < row["segment_count"])


def publish_normalization(database: Session, record: Transcript,
                          value: NormalizedConversation, *, activate: bool = True) -> None:
    size_bytes = value.stored_size_bytes
    if size_bytes is None:
        size_bytes = sum(len(canonical_json(asdict(segment))) + 1 for segment in value.segments)
    exists = database.scalar(select(NORMALIZATIONS.c.revision).where(
        NORMALIZATIONS.c.transcript_id == record.id, NORMALIZATIONS.c.revision == value.revision))
    if exists is None:
        database.execute(insert(NORMALIZATIONS).values(
            transcript_id=record.id, revision=value.revision, snapshot_id=value.snapshot_id,
            source_sha256=value.source_sha256, sha256=value.sha256,
            schema_version=value.schema_version, normalizer_version=value.normalizer_version,
            format=value.format, mime_type=value.mime_type, metadata=value.metadata,
            incomplete=value.incomplete, segment_count=len(value.segments), size_bytes=size_bytes))
        for offset in range(0, len(value.segments), 100):
            database.execute(insert(SEGMENTS), [{
                "transcript_id": record.id, "revision": value.revision,
                "ordinal": segment.ordinal, "segment_id": segment.segment_id,
                "content_kind": segment.content_kind, "text": segment.text,
                "segment_data": asdict(segment),
            } for segment in value.segments[offset:offset + 100]])
    if not activate:
        return
    record.normalization_status = "ready"
    record.normalization_error_code = None
    record.normalized_revision = value.revision
    record.normalized_sha256 = value.sha256
    record.normalized_size_bytes = size_bytes
    record.normalization_schema_version = value.schema_version
    record.normalizer_version = value.normalizer_version
    record.segment_count = value.stored_segment_count or len(value.segments)
    record.normalization_incomplete = value.incomplete
