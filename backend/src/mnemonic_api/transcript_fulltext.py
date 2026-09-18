"""One complete text projection, hashed in a stream and assembled inside PostgreSQL."""

import hashlib
from collections.abc import Iterator, Sequence

from sqlalchemy import case, func, literal, select, update
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.models import Transcript
from mnemonic_api.transcript_normalization import Segment, segment_text
from mnemonic_api.transcript_normalized_storage import SEGMENTS


def text_digest(segments: Sequence[Segment]) -> str:
    digest = hashlib.sha256()
    separator = b""
    size = 0
    for segment in segments:
        text = segment_text(segment)
        if text:
            data = text.encode("utf-8")
            size += len(data) + len(separator)
            if size >= 1_073_741_824:
                raise ExtractionError("transcript_text_too_large")
            digest.update(separator)
            digest.update(data)
            separator = b"\n\n"
    return digest.hexdigest()


TEXT_PROJECTION_KEY = "transcript:text_projection"
TEXT_PROJECTION_VERSION = "canonical-v1"


def _rendered_segment():
    data = SEGMENTS.c.segment_data
    label = case(
        (SEGMENTS.c.content_kind == "tool_call", "tool call"),
        (SEGMENTS.c.content_kind == "tool_result", "tool result"),
        (SEGMENTS.c.content_kind == "reasoning", "reasoning"),
        (SEGMENTS.c.content_kind == "summary", "summary"),
        else_=func.coalesce(func.nullif(data["role"].astext, ""), "system"))
    tool = func.nullif(data["tool_name"].astext, "")
    label = label + case((tool.is_not(None), literal(" ") + tool), else_="")
    return label + literal(": ") + SEGMENTS.c.text


def complete_text_expression(transcript_id, revision):
    return select(func.coalesce(func.string_agg(_rendered_segment(), aggregate_order_by(
        literal("\n\n"), SEGMENTS.c.ordinal)), "")).where(
            SEGMENTS.c.transcript_id == transcript_id, SEGMENTS.c.revision == revision,
            SEGMENTS.c.text != "").scalar_subquery()


def publish_complete_text(database: Session, record: Transcript) -> None:
    # The application never hydrates the whole text. Its integrity hash is computed
    # from the same ordered segments before the atomic publication transaction.
    database.flush()
    database.execute(update(Transcript).where(Transcript.id == record.id).values(
        normalized_text=complete_text_expression(record.id, record.normalized_revision))
        .execution_options(synchronize_session=False))
    database.expire(record, ["normalized_text"])


def download_text_chunks(database: Session, record: Transcript) -> Iterator[bytes]:
    """Stream the exact published projection with one bounded segment cursor.

    Older Tika projections can differ from canonical segments, so they retain the
    bounded legacy reader until rebuilt. Never silently change bytes under a hash.
    """
    canonical = (record.normalized_revision and not record.truncated
        and record.extracted_metadata.get(TEXT_PROJECTION_KEY) == [TEXT_PROJECTION_VERSION])
    if not canonical:
        total = database.scalar(select(func.length(Transcript.normalized_text))
                                .where(Transcript.id == record.id)) or 0
        for offset in range(0, total, 65_536):
            value = database.scalar(select(func.substr(Transcript.normalized_text,
                offset + 1, 65_536)).where(Transcript.id == record.id))
            yield (value or "").encode("utf-8")
        return
    rows = database.scalars(select(_rendered_segment()).where(
        SEGMENTS.c.transcript_id == record.id,
        SEGMENTS.c.revision == record.normalized_revision, SEGMENTS.c.text != "")
        .order_by(SEGMENTS.c.ordinal).execution_options(yield_per=1))
    pending = bytearray()
    separator = b""
    try:
        for value in rows:
            data = separator + value.encode("utf-8")
            separator = b"\n\n"
            for offset in range(0, len(data), 65_536):
                pending.extend(data[offset:offset + 65_536])
                if len(pending) >= 65_536:
                    yield bytes(pending[:65_536])
                    del pending[:65_536]
        if pending:
            yield bytes(pending)
    finally:
        rows.close()
