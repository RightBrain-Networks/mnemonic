"""Reuse the shared exact nested contracts without granting REST-only capabilities."""

import re
from typing import Any

from pydantic import TypeAdapter, ValidationError

from mnemonic_api.application.browser_policy.values import (
    JS_WHITESPACE,
    allowed_keys,
    browser_url,
    decimal,
    encoded_size,
    exact_keys,
    text,
    valid_model,
)
from mnemonic_api.external_duplicate_schemas import ExternalCandidates
from mnemonic_api.external_references import ExternalReferences
from mnemonic_api.phase12_schemas import BIDI_CONTROLS
from mnemonic_api.provenance_schemas import DecisionProvenance
from mnemonic_api.schemas import CompletionEvidenceInput

_REFERENCES = TypeAdapter(ExternalReferences)
_CANDIDATES = TypeAdapter(ExternalCandidates)


def external_references(value: Any) -> bool:
    try:
        _REFERENCES.validate_python(value)
        # The browser charges the submitted spelling, before UTC normalization.
        return encoded_size(value) <= 32_768
    except ValidationError, ValueError, TypeError, UnicodeError:
        return False


def external_candidates(value: Any) -> bool:
    try:
        _CANDIDATES.validate_python(value)
    except ValidationError, ValueError, TypeError:
        return False
    return True


def report_text(
    value: Any, characters: int, maximum_bytes: int, *, multiline: bool = False
) -> bool:
    if not text(value, characters) or len(value.encode("utf-8")) > maximum_bytes:
        return False
    return all(
        character not in BIDI_CONTROLS and _allowed_character(character, multiline)
        for character in value
    )


def _allowed_character(character: str, multiline: bool) -> bool:
    if multiline and character in "\r\n\t":
        return True
    point = ord(character)
    return (
        point >= 32 and not 127 <= point <= 159 and (multiline or character not in "\u2028\u2029")
    )


def report_prompt(value: Any) -> bool:
    return report_text(value, 8_000, 16_384, multiline=True)


def job_report(value: Any) -> bool:
    if not isinstance(value, dict) or not allowed_keys(
        value, ["summary", "fyi_items", "prompt_revision", "decision_provenance"]
    ):
        return False
    items = value.get("fyi_items")
    provenance = value.get("decision_provenance")
    if provenance is not None and not valid_model(DecisionProvenance, provenance):
        return False
    return (
        report_text(value.get("summary"), 2_000, 8_000)
        and isinstance(items, list)
        and len(items) <= 10
        and all(report_text(item, 600, 2_400) for item in items)
        and sum(len(item.encode()) for item in [value["summary"], *items]) <= 16_384
        and decimal(value.get("prompt_revision"))
    )


def _repository_locator(key: str, value: Any) -> bool:
    maximum = 2_000 if key == "repository_url" else 4_096
    if not report_text(value, maximum, 16_384) or any(
        character in value for character in "`$;|<>\\"
    ):
        return False
    if key == "checkout_path":
        return value.startswith("/")
    url = browser_url(value)
    return (
        url is not None
        and url.scheme == "https"
        and not (url.username or url.password or url.query or url.fragment)
        and not any(character in JS_WHITESPACE for character in value)
    )


def _repository_range(value: Any) -> bool:
    required = ["repository_key", "object_format", "base_commit", "head_commit"]
    if (
        not isinstance(value, dict)
        or not exact_keys(
            value, required + [key for key in ("repository_url", "checkout_path") if key in value]
        )
        or not isinstance(value.get("repository_key"), str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value["repository_key"]) is None
        or value.get("object_format") not in ("sha1", "sha256")
    ):
        return False
    width = 40 if value["object_format"] == "sha1" else 64
    return (
        all(
            isinstance(value[key], str)
            and re.fullmatch(rf"[a-f0-9]{{{width}}}", value[key]) is not None
            for key in ("base_commit", "head_commit")
        )
        and any(key in value for key in ("repository_url", "checkout_path"))
        and all(
            _repository_locator(key, value[key])
            for key in ("repository_url", "checkout_path")
            if key in value
        )
    )


def _review_scope(value: Any) -> bool:
    if not isinstance(value, dict) or not exact_keys(value, ["repositories"]):
        return False
    rows = value["repositories"]
    if (
        not isinstance(rows, list)
        or not 1 <= len(rows) <= 10
        or not all(_repository_range(row) for row in rows)
        or encoded_size(value) > 65_536
    ):
        return False
    identities = {
        (
            row.get("repository_url"),
            row.get("checkout_path"),
            row["base_commit"],
            row["head_commit"],
        )
        for row in rows
    }
    return len({row["repository_key"] for row in rows}) == len(rows) == len(identities)


def _review_notes(value: Any) -> bool:
    fields = ["change_summary", "validation_summary", "decisions", "focus_areas", "traps"]
    if not isinstance(value, dict) or not exact_keys(value, fields):
        return False
    return (
        all(
            report_text(value[key], 4_000, 16_000, multiline=True)
            for key in ("change_summary", "validation_summary")
        )
        and all(
            isinstance(value[key], list)
            and len(value[key]) <= 20
            and all(report_text(item, 2_000, 8_000, multiline=True) for item in value[key])
            for key in ("decisions", "focus_areas", "traps")
        )
        and encoded_size(value) <= 65_536
    )


def review_handoff(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and exact_keys(value, ["scope", "handoff"])
        and _review_scope(value["scope"])
        and _review_notes(value["handoff"])
    )


def follow_up_answer(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and allowed_keys(value, ["kind", "recommend_review", "rationale", "code_review_handoff"])
        and value.get("kind") == "code_review_recommendation"
        and type(value.get("recommend_review")) is bool
        and report_text(value.get("rationale"), 2_000, 8_000, multiline=True)
        and (
            review_handoff(value.get("code_review_handoff"))
            if value["recommend_review"]
            else "code_review_handoff" not in value
        )
    )


def completion_evidence(value: Any) -> bool:
    return valid_model(CompletionEvidenceInput, value)
