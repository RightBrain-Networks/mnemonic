"""Transactional publication and reads of immutable normalized conversation rows."""

from dataclasses import asdict
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from mnemonic_api.models import Transcript, TranscriptNormalization, TranscriptSegment
from mnemonic_api.transcript_normalization import (
    NormalizedConversation,
    Segment,
    canonical_json,
    revision_for,
)
from mnemonic_api.transcript_spool import TranscriptSegments

NORMALIZATIONS = TranscriptNormalization.__table__
SEGMENTS = TranscriptSegment.__table__


def load_normalization(database: Session, transcript_id: UUID, snapshot_id: UUID,
                       source_sha256: str) -> NormalizedConversation | None:
    revision = revision_for(snapshot_id, source_sha256)
    row = database.execute(select(NORMALIZATIONS).where(
        NORMALIZATIONS.c.transcript_id == transcript_id,
        NORMALIZATIONS.c.revision == revision)).mappings().first()
    if row is None:
        return None
    # Tool payloads are already durable. Complete indexing reads every block
    # through the private spool, without a character budget or body-sized list.
    statement = (select(SEGMENTS.c.segment_data.op("-")("payload")).where(
        SEGMENTS.c.transcript_id == transcript_id, SEGMENTS.c.revision == revision)
        .order_by(SEGMENTS.c.ordinal).execution_options(yield_per=1))
    segments = TranscriptSegments()
    rows = database.scalars(statement)
    try:
        for value in rows:
            segment = Segment(**value)
            segments.append(segment)
    except BaseException:
        segments.close()
        raise
    finally:
        rows.close()
    return NormalizedConversation(revision, row["sha256"], snapshot_id, source_sha256,
        row["format"], row["mime_type"], row["metadata"], segments, row["incomplete"],
        row["schema_version"], row["normalizer_version"], row["segment_count"], row["size_bytes"],
        len(segments) < row["segment_count"])


def persist_normalization(database: Session, transcript_id: UUID,
                          value: NormalizedConversation) -> None:
    """Commit the complete immutable manifest and segments before activation."""
    size_bytes = value.stored_size_bytes
    if size_bytes is None:
        size_bytes = sum(len(canonical_json(asdict(segment))) + 1 for segment in value.segments)
    created = database.scalar(pg_insert(NORMALIZATIONS).values(
        transcript_id=transcript_id, revision=value.revision, snapshot_id=value.snapshot_id,
        source_sha256=value.source_sha256, sha256=value.sha256,
        schema_version=value.schema_version, normalizer_version=value.normalizer_version,
        format=value.format, mime_type=value.mime_type, metadata=value.metadata,
        incomplete=value.incomplete, segment_count=len(value.segments), size_bytes=size_bytes)
        .on_conflict_do_nothing(index_elements=[NORMALIZATIONS.c.transcript_id,
                                               NORMALIZATIONS.c.revision])
        .returning(NORMALIZATIONS.c.revision))
    if created is None:
        digest = database.scalar(select(NORMALIZATIONS.c.sha256).where(
            NORMALIZATIONS.c.transcript_id == transcript_id,
            NORMALIZATIONS.c.revision == value.revision))
        if digest != value.sha256:
            raise ValueError("An immutable normalized revision has conflicting content")
        return
    batch, charged = [], 0
    for segment in value.segments:
        data = asdict(segment)
        size = len(canonical_json(data)) + len(segment.text.encode("utf-8"))
        # A row count alone can hydrate an entire large conversation. Keep fast
        # batches for ordinary messages, bounded by bytes plus one native record.
        if batch and (len(batch) >= 100 or charged + size > 8 * 1024 * 1024):
            database.execute(insert(SEGMENTS), batch)
            batch, charged = [], 0
        batch.append({
            "transcript_id": transcript_id, "revision": value.revision,
            "ordinal": segment.ordinal, "segment_id": segment.segment_id,
            "content_kind": segment.content_kind, "text": segment.text,
            "segment_data": data,
        })
        charged += size
    if batch:
        database.execute(insert(SEGMENTS), batch)


def publish_normalization(database: Session, record: Transcript,
                          value: NormalizedConversation, *, activate: bool = True) -> None:
    """Activate already committed canonical data; no bulk work holds the project lock."""
    if not activate:
        return
    size_bytes = database.scalar(select(NORMALIZATIONS.c.size_bytes).where(
        NORMALIZATIONS.c.transcript_id == record.id,
        NORMALIZATIONS.c.revision == value.revision))
    if size_bytes is None:
        raise ValueError("Normalized data must be durably staged before activation")
    record.normalization_status = "ready"
    record.normalization_error_code = None
    record.normalized_revision = value.revision
    record.normalized_sha256 = value.sha256
    record.normalized_size_bytes = size_bytes
    record.normalization_schema_version = value.schema_version
    record.normalizer_version = value.normalizer_version
    record.segment_count = value.stored_segment_count or len(value.segments)
    record.normalization_incomplete = value.incomplete
