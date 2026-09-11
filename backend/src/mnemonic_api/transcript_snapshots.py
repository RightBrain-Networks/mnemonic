"""Fields which belong to one source snapshot and must be replaced together."""

from typing import Any


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
