"""Bounded format labels for diagnosing adapter drift without copying message bodies."""

import re
from collections import Counter

_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")
_MAX_TYPES = 32


def _label(value: object) -> str:
    return value if isinstance(value, str) and _LABEL.fullmatch(value) else "unknown"


def record_unsupported_type(row: dict, codex: bool, counts: Counter, unsupported: bool) -> None:
    """Count record envelopes, never arbitrary fields or authored content."""
    if not unsupported:
        return
    parts = ["codex" if codex else "claude", _label(row.get("type"))]
    container = row.get("payload") if codex else row.get("attachment")
    if isinstance(container, dict):
        parts.append(_label(container.get("type")))
        item = container.get("item")
        if isinstance(item, dict):
            parts.extend((_label(item.get("type")), _label(item.get("kind"))))
    key = ":".join(parts)
    if key not in counts and len(counts) >= _MAX_TYPES:
        key = "other_types"
    counts[key] += 1
