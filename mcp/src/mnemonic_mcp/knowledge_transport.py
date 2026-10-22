"""Bound large decision text without granting an oversized JSON object allowance."""

import json
import re
from collections.abc import Iterator

DECISION_CONTENT_MAX_BYTES = 1_048_576
# Every one-byte control can occupy six bytes as a JSON Unicode escape.
DECISION_LITERAL_MAX_BYTES = 6 * DECISION_CONTENT_MAX_BYTES
_CONTENT_START = re.compile(rb'"content"\s*:\s*"')


def _literal_end(raw: bytes, start: int) -> int | None:
    position = start
    while (quote := raw.find(b'"', position)) != -1:
        backslashes = 0
        previous = quote - 1
        while previous >= start and raw[previous] == 92:
            backslashes += 1
            previous -= 1
        if backslashes % 2 == 0:
            return quote
        position = quote + 1
    return None


def decision_literals(raw: bytes, metadata_limit: int) -> Iterator[tuple[int, int]]:
    """Locate candidate literal spans before decoding; ordinary JSON framing stays bounded."""
    if len(raw) > DECISION_LITERAL_MAX_BYTES + metadata_limit:
        return
    for match in _CONTENT_START.finditer(raw):
        start = match.end()
        end = _literal_end(raw, start)
        if end is None:
            continue
        length = end - start
        if length <= DECISION_LITERAL_MAX_BYTES and len(raw) - length <= metadata_limit:
            yield start, end


def large_decision_matches(raw: bytes, arguments: object, metadata_limit: int) -> bool:
    if not isinstance(arguments, dict) or not isinstance(content := arguments.get("content"), str):
        return False
    try:
        if len(content.encode("utf-8")) > DECISION_CONTENT_MAX_BYTES:
            return False
    except UnicodeEncodeError:
        return False
    for start, end in decision_literals(raw, metadata_limit):
        try:
            actual = json.loads(raw[start - 1:end + 1])
        except (ValueError, UnicodeDecodeError):
            continue
        if actual == content:
            return True
    return False
