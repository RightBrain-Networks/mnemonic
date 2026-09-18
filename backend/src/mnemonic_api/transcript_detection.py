"""Bounded client detection shared by imported-source discovery and indexing."""

import json
from typing import BinaryIO

from mnemonic_api.artifact_tika import ExtractionError

MAX_SIGNATURE_BYTES = 8_388_608
MAX_SIGNATURE_RECORDS = 16


def is_task_journal(prefix: bytes) -> bool:
    """Recognize the task execution journal mistakenly supplied as a session."""
    try:
        row = json.loads(prefix.split(b"\n", 1)[0].decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return False
    return (isinstance(row, dict) and set(row) == {"agentId", "key", "type"}
            and row["type"] == "started" and isinstance(row["agentId"], str)
            and isinstance(row["key"], str))


def _record_client(line: bytes) -> str | None:
    try:
        row = json.loads(line.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
        return None
    return "codex" if row.get("type") in ("session_meta", "response_item", "turn_context",
                                         "event_msg", "compacted") else None


def detect_transcript_client(content: BinaryIO) -> str:
    remaining = MAX_SIGNATURE_BYTES
    for _ in range(MAX_SIGNATURE_RECORDS):
        line = content.readline(remaining + 1)
        if not line or len(line) > remaining:
            break
        remaining -= len(line)
        if is_task_journal(line):
            raise ExtractionError("transcript_not_native_session")
        client = _record_client(line)
        if client is not None:
            return client
    # Keep malformed, unsupported, or unreadable candidates visible. The normal
    # indexing worker reads the complete bounded source and records its failure.
    return "claude_code"


