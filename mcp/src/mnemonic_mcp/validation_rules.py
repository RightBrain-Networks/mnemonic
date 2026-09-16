"""Reviewed, value-free rules shared by validators and public error formatting."""

from typing import LiteralString

VALIDATION_RULES: dict[str, tuple[str | None, LiteralString]] = {
    "absolute_http_url_required": (None, "Include an absolute http:// or https:// URL."),
    "canonical_filter_requires_alias_scope": (
        "canonical_work_item_id",
        "canonical_work_item_id requires duplicate_scope=aliases or all.",
    ),
    "external_url_requires_full_view": ("view", "external_url requires view=full."),
    "roots_require_canonical_scope": (
        "duplicate_scope", "view=roots requires duplicate_scope=canonical.",
    ),
    "semantic_requires_query": ("q", "semantic=true requires a nonblank q."),
    "view_requires_blank_query": (
        "view", "view=roots requires blank q; use view=full for text search.",
    ),
}
