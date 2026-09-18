"""Bounded session metadata, separate from processing timestamps and claim provenance."""

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from mnemonic_api.models import TranscriptSegment

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


def session_time(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40 or not _TIMESTAMP.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError, OverflowError:
        return None


def time_bounds(values: Iterable[object]) -> tuple[datetime | None, datetime | None]:
    first = last = None
    for value in values:
        stamp = session_time(value)
        if stamp is not None:
            first = min(first, stamp) if first is not None else stamp
            last = max(last, stamp) if last is not None else stamp
    return first, last


def retained_time_bounds(
    database: Session | Connection,
    transcript_id: UUID,
    revision: str | None,
) -> tuple[datetime | None, datetime | None]:
    # Scan timestamp scalars only, including segments beyond the searchable text budget.
    table = TranscriptSegment.__table__
    values = database.scalars(
        select(table.c.segment_data["timestamp"].astext)
        .where(
            table.c.transcript_id == transcript_id,
            table.c.revision == revision,
        )
        .distinct()
        .execution_options(yield_per=1000)
    )
    try:
        return time_bounds(values)
    finally:
        values.close()


def iso_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def bounded_metadata(
    properties: dict[str, list[str]],
    required: dict[str, list[str]],
) -> dict[str, list[str]]:
    result = dict(required)
    limited = False
    # Native session properties take precedence over generic extractor properties.
    for key in sorted(properties, key=lambda value: (not value.startswith("transcript:"), value)):
        if key in required:
            continue
        candidate = result | {key: properties[key]}
        if len(candidate) > 63 or len(json.dumps(candidate, ensure_ascii=True)) > 8128:
            limited = True
        else:
            result = candidate
    if limited:
        result["transcript:metadata_limited"] = ["true"]
    return result


def timeline_metadata(
    properties: dict[str, list[str]],
    *,
    started_at: datetime | None,
    updated_at: datetime | None,
    source_modified_at: datetime | None,
    indexed_at: datetime | None,
) -> dict[str, list[str]]:
    required = {}
    for key, stamp in (
        ("session_started_at", started_at),
        ("last_updated_at", updated_at),
        ("source_modified_at", source_modified_at),
        ("index_created_at", indexed_at),
    ):
        if stamp is not None:
            required["transcript:" + key] = [iso_time(stamp)]
    if updated_at is not None:
        required["transcript:last_updated_basis"] = [
            "session_event" if started_at is not None else "source_mtime"
        ]
    return bounded_metadata(properties, required)
