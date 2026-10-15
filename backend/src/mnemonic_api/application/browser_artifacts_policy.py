"""The artifact dashboard's closed path, query and JSON allowlists."""

import re
from typing import Any
from urllib.parse import quote, unquote

from starlette.datastructures import QueryParams

from mnemonic_api.application.browser_policy.routes import UUID
from mnemonic_api.application.browser_policy.search import DATE_FIELDS, date_bounds
from mnemonic_api.application.browser_policy.values import (
    allowed_keys,
    integer,
    nullable_text,
    optional,
    text,
    uuid,
)

COLLECTION = re.compile(rf"projects/{UUID}/artifacts")
ITEM = re.compile(rf"projects/{UUID}/artifacts/{UUID}")
CONTENT = re.compile(rf"projects/{UUID}/artifacts/{UUID}/content")
TEXT = re.compile(rf"projects/{UUID}/artifacts/{UUID}/text")
HISTORY = re.compile(rf"projects/{UUID}/artifacts/{UUID}/history")
SEARCH = re.compile(rf"projects/{UUID}/artifacts/search-content")
DISABLED_MESSAGE = "The artifact library is disabled. Stored files and metadata are preserved."


def artifact_query_keys(path: str, method: str) -> tuple[str, ...] | None:
    routes = (
        (COLLECTION, {"GET": ("q", "sort", "order", "limit", "offset", "include_deleted",
                               "work_item_id"), "POST": ()}),
        (ITEM, {"GET": (), "DELETE": (), "PATCH": ()}),
        (CONTENT, {"GET": (), "PUT": ()}),
        (TEXT, {"GET": ("expected_revision", "expected_text_sha256", "offset", "limit")}),
        (HISTORY, {"GET": ("limit", "offset")}),
        (SEARCH, {"POST": ()}),
    )
    if path == "status" and method == "GET":
        return ()
    for pattern, methods in routes:
        if pattern.fullmatch(path):
            return methods.get(method)
    return None


def artifact_text_pins(query: QueryParams) -> bool:
    revision = query.get("expected_revision", "")
    digest = query.get("expected_text_sha256", "")
    if not re.fullmatch(r"[1-9][0-9]*", revision) or len(revision) > 10:
        return False
    if not integer(int(revision), 1, 2**31 - 1) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    for key, minimum, maximum in (("limit", 1, 20_000), ("offset", 0, 8_000_000)):
        value = query.get(key, "")
        if (not re.fullmatch(r"[0-9]+", value) or len(value) > 20
                or not integer(int(value), minimum, maximum)):
            return False
    return True


def _uuid_list(value: Any) -> bool:
    return (isinstance(value, list) and len(value) <= 50 and all(uuid(item) for item in value)
            and len({item.lower() for item in value}) == len(value))


def valid_artifact_update(value: Any, operation: str) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        allowed_keys(value, ("client_operation_id", "expected_revision", "sensitive",
                             "related_artifact_ids", "related_work_item_ids", "description",
                             "agent_session_id", "actor_client"))
        and uuid(value.get("client_operation_id"))
        and value["client_operation_id"].lower() == operation.lower()
        and integer(value.get("expected_revision"), 1, 2**31 - 1)
        and optional(value, "sensitive", lambda item: type(item) is bool)
        and optional(value, "description", lambda item: nullable_text(item, 4000))
        and optional(value, "agent_session_id", lambda item: text(item, 200))
        and optional(value, "actor_client", lambda item: item == "dashboard")
        and optional(value, "related_artifact_ids", _uuid_list)
        and optional(value, "related_work_item_ids", _uuid_list)
    )


def valid_artifact_search(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        allowed_keys(value, ("q", "query_mode", "semantic", "fulltext", "detail", "work_item_id",
                             "artifact_id", "include_deleted", "limit", "offset", "diagnostics",
                             *DATE_FIELDS))
        and text(value.get("q"), 200) and date_bounds(value)
        and optional(value, "query_mode", lambda item: item in ("terms", "phrase", "literal"))
        and optional(value, "diagnostics", lambda item: item in ("on_empty", "always", "off"))
        and optional(value, "detail", lambda item: item in ("compact", "full"))
        and _search_options(value)
    )


def _search_options(value: dict[str, Any]) -> bool:
    return (
        all(optional(value, key, lambda item: type(item) is bool)
            for key in ("semantic", "fulltext", "include_deleted"))
        and all(optional(value, key, uuid) for key in ("artifact_id", "work_item_id"))
        and optional(value, "limit", lambda item: integer(item, 1, 100))
        and optional(value, "offset", lambda item: integer(item, 0, 1_000_000))
        and (value.get("semantic") is not True
             or value.get("fulltext") is True and value.get("query_mode", "terms") == "terms"
             and '"' not in value["q"])
    )


def artifact_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return _rounded_size(size, 1024, "KB")
    return _rounded_size(size, 1024 * 1024, "MB")


def _rounded_size(size: int, divisor: int, unit: str) -> str:
    # Match toFixed(1)'s positive halfway rounding, including 1280 -> 1.3 KB.
    tenths = (size * 10 + divisor // 2) // divisor
    return f"{tenths // 10}.{tenths % 10} {unit}"


def safe_artifact_disposition(value: str | None) -> str:
    name = "artifact.bin"
    encoded = re.search(r"filename\*=UTF-8''([^;]+)", value or "", re.IGNORECASE)
    quoted = re.search(r'filename="([^"\\]+)"', value or "", re.IGNORECASE)
    try:
        candidate = unquote(encoded[1], errors="strict") if encoded else quoted[1] if quoted else ""
        # decodeURIComponent rejects incomplete percent escapes as well as bad UTF-8.
        malformed = encoded is not None and re.search(r"%(?![0-9a-fA-F]{2})", encoded[1])
        if (candidate and len(candidate.encode("utf-16-le")) // 2 <= 255 and not malformed
                and not re.search(r"[\x00-\x1f\x7f/\\]", candidate)):
            name = candidate
    except UnicodeError:
        pass
    return "attachment; filename=\"artifact.bin\"; filename*=UTF-8''" + quote(name, safe="")
