"""The browser's bounded unified-search request, including explicit facet opt-ins."""

import re
from datetime import datetime
from typing import Any

from mnemonic_api.application.browser_policy.values import (
    JS_WHITESPACE,
    JsonObject,
    allowed_keys,
    integer,
    optional,
    text,
    uuid,
)

FACETS = ("work_items", "artifacts", "transcripts")
DATE_FIELDS = ("created_after", "created_before", "updated_after", "updated_before")
WORK_FIELDS = ("title", "summary", "tags", "checkpoint", "identifiers", "provenance")
CONTENT_KINDS = (
    "human_text",
    "assistant_text",
    "tool_call",
    "tool_result",
    "system_text",
    "reasoning",
    "summary",
    "unsupported",
)
FILTER_KEYS = {
    "work_items": (
        "status_scope",
        "work_fields",
        "status",
        "tag",
        "tags",
        "tag_mode",
        "source_client",
        "source_session_id",
        "duplicate_scope",
        "canonical_work_item_id",
        "external_url",
        "semantic",
    ),
    "artifacts": (
        "tags",
        "tag_mode",
        "semantic",
        "work_item_id",
        "artifact_id",
        "include_deleted",
        "sensitive",
        "mime_type",
        "created_by_agent_session_id",
    ),
    "transcripts": (
        "work_item_id",
        "agent_session_id",
        "client",
        "kind",
        "status",
        "content_kinds",
    ),
}
ENUMS = {
    "tag_mode": ("or", "and"),
    "status_scope": ("effective", "work_item"),
    "duplicate_scope": ("canonical", "aliases", "all"),
    "kind": ("primary", "subagent", "imported"),
}
WORK_STATUSES = (
    "all",
    "pending",
    "active",
    "to-review",
    "dropped",
    "deferred",
    "done",
    "wont-do",
    "promoted",
)
TRANSCRIPT_STATUSES = ("waiting", "pending", "processing", "ready", "failed")
TEXT_BOUNDS = {
    "tag": 50,
    "client": 80,
    "source_client": 80,
    "external_url": 2_000,
    "mime_type": 255,
}


def unique_members(value: Any, choices: tuple[str, ...], minimum: int = 1) -> bool:
    return (
        isinstance(value, list)
        and minimum <= len(value) <= len(choices)
        and all(isinstance(item, str) and item in choices for item in value)
        and len(set(value)) == len(value)
    )


def search_sort(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and allowed_keys(value, ["by", "direction"])
        and optional(
            value, "by", lambda item: item in ("relevance", "created_at", "updated_at", "priority")
        )
        and optional(value, "direction", lambda item: item in ("asc", "desc"))
    )


def search_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})",
        value,
        re.ASCII,
    ):
        return None
    if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def date_bounds(value: JsonObject) -> bool:
    dates = {
        field: search_date(value[field]) for field in DATE_FIELDS if value.get(field) is not None
    }
    if any(date is None for date in dates.values()):
        return False
    for prefix in ("created", "updated"):
        after, before = dates.get(prefix + "_after"), dates.get(prefix + "_before")
        if after is not None and before is not None and after >= before:
            return False
    return True


def _filter_value(facet: str, key: str, value: Any) -> bool:
    if value is None:
        return key not in (
            "status_scope",
            "work_fields",
            "semantic",
            "include_deleted",
            "duplicate_scope",
        ) and not (facet == "work_items" and key == "status")
    if key == "tags":
        return isinstance(value, list) and 1 <= len(value) <= 20 and all(
            text(tag, 50) for tag in value) and len(set(value)) == len(value)
    if key in ENUMS:
        return value in ENUMS[key]
    if key == "work_fields":
        return (
            isinstance(value, list)
            and bool(value)
            and value == [field for field in WORK_FIELDS if field in value]
        )
    if key == "content_kinds":
        return unique_members(value, CONTENT_KINDS)
    if key in ("work_item_id", "artifact_id", "canonical_work_item_id"):
        return uuid(value)
    if key in ("semantic", "include_deleted", "sensitive"):
        return type(value) is bool
    if key == "status":
        return value in (WORK_STATUSES if facet == "work_items" else TRANSCRIPT_STATUSES)
    return text(value, TEXT_BOUNDS.get(key, 200))


def search_filters(value: Any) -> bool:
    if not isinstance(value, dict) or not allowed_keys(value, FACETS):
        return False
    for facet, filters in value.items():
        if (
            not isinstance(filters, dict)
            or not allowed_keys(filters, [*FILTER_KEYS[facet], *DATE_FIELDS])
            or not date_bounds(filters)
        ):
            return False
        if not all(
            key in DATE_FIELDS or _filter_value(facet, key, item) for key, item in filters.items()
        ):
            return False
    return True


def tag_counts(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and allowed_keys(value, ["limit", "offset"])
        and optional(value, "limit", lambda item: integer(item, 1, 100))
        and optional(value, "offset", lambda item: integer(item, 0, 1_000_000))
    )


def _facet_order(body: JsonObject) -> bool:
    orders = body.get("facet_order", [])
    if not isinstance(orders, list) or len(orders) > 3:
        return False
    seen = set()
    for order in orders:
        if (
            not isinstance(order, dict)
            or not allowed_keys(order, ["facet", "sort"])
            or order.get("facet") not in FACETS
            or order["facet"] not in body.get("facets", FACETS)
            or not optional(order, "sort", lambda item: item is None or search_sort(item))
        ):
            return False
        if order["facet"] in seen:
            return False
        seen.add(order["facet"])
    return True


def _pinned(body: JsonObject) -> bool:
    values = body.get("pinned_work_item_ids", [])
    return (
        isinstance(values, list)
        and len(values) <= 100
        and all(uuid(value) for value in values)
        and (not values or body.get("facets") == ["work_items"])
    )


def _semantic_dependencies(body: JsonObject) -> bool:
    filters = body.get("filters", {})
    artifacts = filters.get("artifacts", {})
    if artifacts.get("semantic") is True:
        query = body.get("q")
        if (
            body.get("fulltext") is not True
            or not isinstance(query, str)
            or not query.strip(JS_WHITESPACE)
            or '"' in query
            or body.get("query_mode", "terms") != "terms"
            or "artifacts" not in body.get("facets", FACETS)
        ):
            return False
    return (
        filters.get("transcripts", {}).get("content_kinds") is None or body.get("fulltext") is True
    )


def _optional_fields(body: JsonObject) -> bool:
    predicates = {
        "query_mode": lambda item: item in ("terms", "phrase", "literal"),
        "q": lambda item: (
            isinstance(item, str) and len(item) <= 1_000 and re.search(r"[\x00-\x1f]", item) is None
        ),
        "facets": lambda item: unique_members(item, FACETS),
        "fulltext": lambda item: type(item) is bool,
        "detail": lambda item: item in ("compact", "full"),
        "filters": search_filters,
        "sort": search_sort,
        "limit": lambda item: integer(item, 1, 100),
        "offset": lambda item: integer(item, 0, 1_000_000),
        "diagnostics": lambda item: item in ("on_empty", "always", "off"),
        "tag_counts": lambda item: item is None or tag_counts(item),
    }
    return all(optional(body, key, predicate) for key, predicate in predicates.items())


def search_request(body: JsonObject) -> bool:
    return (
        allowed_keys(
            body,
            [
                "q",
                "query_mode",
                "facets",
                "fulltext",
                "detail",
                "filters",
                "sort",
                "facet_order",
                "limit",
                "offset",
                "diagnostics",
                "tag_counts",
                "pinned_work_item_ids",
            ],
        )
        and _pinned(body)
        and _optional_fields(body)
        and _semantic_dependencies(body)
        and (body.get("tag_counts") is None or "work_items" in body.get("facets", FACETS))
        and _facet_order(body)
    )
