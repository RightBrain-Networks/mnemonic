"""Fields which belong to one source snapshot and must be replaced together."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def empty_transcript_snapshot() -> dict[str, Any]:
    return {
        "size_bytes": None,
        "sha256": None,
        "mime_type": None,
        "format": None,
        "extracted_metadata": {},
        "normalized_text": None,
        "text_sha256": None,
        "truncated": False,
    }


def new_transcript_copy() -> dict[str, Any]:
    """Enrollment starts a fresh source capture; reindexing does not call this."""
    return {
        "normalization_status": "pending", "normalization_error_code": None,
        "normalized_revision": None, "normalized_sha256": None, "normalized_size_bytes": None,
        "normalization_schema_version": None, "normalizer_version": None,
        "segment_count": 0, "normalization_incomplete": False,
        "snapshot_id": uuid4(), "copy_status": "pending", "storage_key": None,
        "copy_sha256": None, "copy_size_bytes": None, "copied_at": None,
        "copy_error_code": None, "copy_attempts": 0,
        "copy_next_attempt_at": datetime.now(UTC),
        "copy_lease_token": None, "copy_lease_expires_at": None,
    }
