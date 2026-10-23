"""Project settings, prompt updates and the bounded advisory request."""

import re
from typing import Any

from mnemonic_api.application.browser_policy.contracts import external_candidates, report_prompt
from mnemonic_api.application.browser_policy.routes import PROMPT_DETAIL
from mnemonic_api.application.browser_policy.values import (
    JS_WHITESPACE,
    JsonObject,
    allowed_keys,
    browser_url,
    decimal,
    exact_keys,
    integer,
    nullable_text,
    optional,
    string_array,
    text,
    uuid,
    valid_unicode,
)

LEASE_FIELDS = ("lease_default_minutes", "lease_minimum_minutes", "lease_maximum_minutes")
THRESHOLD_FIELDS = ("code_review_required_min_priority", "code_review_optional_min_priority")
PROMPT_FIELDS = ("recall_pointer_template", "job_completion_report_prompt")
SETTING_FIELDS = (
    *LEASE_FIELDS,
    *THRESHOLD_FIELDS,
    *PROMPT_FIELDS,
    "allow_remediation_code_reviews",
    "automatic_decision_reviews",
    "decision_review_priority",
)


def project_url(value: Any) -> bool:
    if not text(value, 2_000) or any(character in JS_WHITESPACE for character in value):
        return False
    url = browser_url(value)
    return url is not None and not (url.username or url.password)


def project_patch(body: JsonObject) -> bool:
    return (
        bool(body)
        and allowed_keys(body, ["name", "slug", "description", "repository_url"])
        and optional(body, "name", lambda item: text(item, 120))
        and optional(
            body,
            "slug",
            lambda item: (
                isinstance(item, str)
                and len(item) <= 100
                and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item) is not None
            ),
        )
        and optional(
            body,
            "description",
            lambda item: (
                isinstance(item, str)
                and valid_unicode(item)
                and "\0" not in item
                and len(item) <= 4_000
            ),
        )
        and optional(body, "repository_url", lambda item: item is None or project_url(item))
    )


def settings_patch(body: JsonObject) -> bool:
    if any(field in body for field in PROMPT_FIELDS) and len(body) != 2:
        return False
    return (
        allowed_keys(body, ["expected_revision", *SETTING_FIELDS])
        and decimal(body.get("expected_revision"))
        and any(field in body for field in SETTING_FIELDS)
        and all(
            optional(body, field, lambda item: integer(item, 0, 100) and item % 5 == 0)
            for field in THRESHOLD_FIELDS
        )
        and all(
            optional(body, field, lambda item: integer(item, 1, 2**31 - 1))
            for field in LEASE_FIELDS
        )
        and optional(body, "allow_remediation_code_reviews", lambda item: type(item) is bool)
        and optional(body, "automatic_decision_reviews", lambda item: type(item) is bool)
        and optional(body, "decision_review_priority", lambda item: integer(item, 0, 100))
        and optional(body, "recall_pointer_template", lambda item: nullable_text(item, 100_000))
        and optional(
            body, "job_completion_report_prompt", lambda item: item is None or report_prompt(item)
        )
    )


def rubric_patch(body: JsonObject) -> bool:
    return (
        allowed_keys(body, ["content", "expected_revision"])
        and text(body.get("content"), 100_000)
        and decimal(body.get("expected_revision"))
    )


def prompt_content(prompt_id: str, value: Any) -> bool:
    if prompt_id == "job-completion-report":
        return report_prompt(value)
    if prompt_id == "review-recommendation":
        return report_prompt(value) and len(value) <= 4_000 and len(value.encode()) <= 8_192
    return text(value, 100_000) and len(value.encode()) <= 400_000


def prompt_update(path: str, body: JsonObject) -> bool:
    detail = PROMPT_DETAIL.fullmatch(path)
    revision = body.get("expected_revision")
    return (
        detail is not None
        and exact_keys(body, ["content", "expected_revision"])
        and isinstance(revision, str)
        and re.fullmatch(r"[a-f0-9]{64}", revision) is not None
        and prompt_content(detail.group(1), body.get("content"))
    )


def prompt_render(body: JsonObject) -> bool:
    return allowed_keys(body, ["work_item_id", "code_review_id"]) and all(
        uuid(value) for value in body.values()
    )


def suggestions(body: JsonObject) -> bool:
    return (
        allowed_keys(
            body,
            [
                "title",
                "summary",
                "initial_prompt",
                "tags",
                "exclude_work_item_id",
                "limit",
                "external_candidates",
            ],
        )
        and text(body.get("title"), 200)
        and text(body.get("summary"), float("inf"))
        and text(body.get("initial_prompt"), 100_000)
        and optional(body, "tags", lambda item: string_array(item, 20, 50))
        and optional(body, "exclude_work_item_id", lambda item: item is None or uuid(item))
        and optional(body, "limit", lambda item: integer(item, 1, 10))
        and optional(body, "external_candidates", external_candidates)
    )
