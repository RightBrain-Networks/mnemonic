"""Closed transcript browser paths and values, matching the dashboard transport."""

import re
from typing import Any

from starlette.datastructures import QueryParams

from mnemonic_api.application.browser_policy.search import CONTENT_KINDS, DATE_FIELDS, date_bounds
from mnemonic_api.application.browser_policy.values import (
    JS_WHITESPACE,
    exact_keys,
    integer,
    text,
    uuid,
)

TRANSCRIPT_MAX_BYTES = 1_073_741_824
TRANSCRIPT_JSON_MAX_BYTES = 16 * 1024 * 1024
MUTATIONS = frozenset({"save", "rebuild", "import"})
_LIST_KEYS = frozenset({
    "query", "query_mode", "sort_by", "sort_direction", "content_kinds", "fulltext",
    "detail", "work_item_id", "limit", "offset", "diagnostics", *DATE_FIELDS,
})
_TEXT_KEYS = frozenset({
    "limit", "offset", "expected_sha256", "segment_id", "expected_normalized_revision",
    "before", "after",
})
_ENUMS = {
    "query_mode": ("terms", "phrase", "literal"),
    "sort_by": ("name", "size", "session", "indexing", "updated"),
    "sort_direction": ("asc", "desc"),
    "content_kinds": CONTENT_KINDS,
    "fulltext": ("true", "false"),
    "detail": ("compact", "full"),
    "diagnostics": ("on_empty", "always", "off"),
}


def transcript_route(path: str, method: str) -> str | None:
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "projects" or not uuid(parts[1]):
        return None
    if len(parts) == 3 and parts[2] == "transcript-settings":
        return {"GET": "settings", "PATCH": "save"}.get(method)
    if parts[2] != "transcripts":
        return None
    return _collection_route(parts, method)


def _collection_route(parts: list[str], method: str) -> str | None:
    if len(parts) == 3:
        return "list" if method == "GET" else None
    if len(parts) == 4 and (parts[3], method) in {
        ("health", "GET"), ("rebuild", "POST"), ("import", "POST"),
    }:
        return parts[3]
    if not uuid(parts[3]) or method != "GET":
        return None
    if len(parts) == 4:
        return "detail"
    if len(parts) == 5 and parts[4] in {"text", "content"}:
        return parts[4]
    return None


def _numeric_value(key: str, value: str, action: str) -> bool:
    minimum = 1 if key == "limit" else 0
    maximum = {"before": 20, "after": 20, "offset": TRANSCRIPT_MAX_BYTES}.get(
        key, 20_000 if action == "text" else 100)
    digits = value.lstrip("0") or "0"
    return (bool(re.fullmatch(r"[0-9]+", value)) and len(digits) <= len(str(maximum))
            and minimum <= int(digits) <= maximum)


def _query_value(key: str, value: str, action: str) -> bool:
    if key in _ENUMS:
        return value in _ENUMS[key]
    if key == "query":
        return len(value) <= 200 and bool(value.strip(JS_WHITESPACE))
    if key == "work_item_id":
        return uuid(value)
    if key in {"expected_sha256", "expected_normalized_revision", "segment_id"}:
        size = 24 if key == "segment_id" else 64
        return re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is not None
    if key in {"before", "after", "limit", "offset"}:
        return _numeric_value(key, value, action)
    return True  # Date values are checked together, including range ordering.


def valid_transcript_query(query: QueryParams, action: str) -> bool:
    allowed = {"list": _LIST_KEYS, "text": _TEXT_KEYS,
               "content": {"expected_sha256"}}.get(action, set())
    if any(key not in allowed or len(query.getlist(key)) != 1
           or not _query_value(key, value, action) for key, value in query.multi_items()):
        return False
    if action == "list" and not date_bounds(dict(query)):
        return False
    return action != "text" or _valid_segment_query(query)


def _valid_segment_query(query: QueryParams) -> bool:
    before = int(query.get("before", "0").lstrip("0") or "0")
    after = int(query.get("after", "0").lstrip("0") or "0")
    if "segment_id" in query:
        return ("expected_normalized_revision" in query and before + after <= 20
                and not (before and int(query.get("offset", "0").lstrip("0") or "0")))
    return "expected_normalized_revision" not in query and not before and not after


def valid_transcript_directory(value: Any) -> bool:
    return (text(value, 4096) and value.startswith("/") and ".." not in value.split("/")
            and not any(ord(character) < 32 for character in value))


def valid_transcript_body(value: Any, action: str) -> bool:
    if not isinstance(value, dict):
        return False
    if action == "save":
        return (exact_keys(value, ["enabled", "max_file_size_bytes", "expected_revision"])
                and type(value["enabled"]) is bool
                and integer(value["max_file_size_bytes"], 1, TRANSCRIPT_MAX_BYTES)
                and integer(value["expected_revision"], 1))
    if action == "import":
        return (exact_keys(value, ["client_operation_id", "directory"])
                and uuid(value["client_operation_id"])
                and valid_transcript_directory(value["directory"]))
    return exact_keys(value, ["client_operation_id"]) and uuid(value["client_operation_id"])
