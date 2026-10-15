"""Strict prompt response checks formerly performed by the Next JSON boundary."""

import re
from typing import Any

from mnemonic_api.application.browser_policy.projects import prompt_content
from mnemonic_api.application.browser_policy.routes import PROMPT_IDS
from mnemonic_api.application.browser_policy.values import (
    exact_keys,
    integer,
    text,
    timestamp,
    uuid,
)

_METADATA = ("id", "name", "description", "size_bytes", "created_at", "updated_at", "revision")


def _revision(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def _metadata(row: Any) -> bool:
    return (
        isinstance(row, dict) and exact_keys(row, _METADATA)
        and row["id"] in PROMPT_IDS
        and text(row["name"], 200) and text(row["description"], 4_000)
        and integer(row["size_bytes"], 1, 400_000) and _revision(row["revision"])
        and timestamp(row["created_at"]) and timestamp(row["updated_at"])
    )


def _detail(row: dict[str, Any], prompt_id: str) -> bool:
    if not exact_keys(row, (*_METADATA, "content")):
        return False
    metadata = {key: value for key, value in row.items() if key != "content"}
    return (
        _metadata(metadata) and row["id"] == prompt_id
        and prompt_content(prompt_id, row["content"])
        and len(row["content"].encode()) == row["size_bytes"]
    )


def _macro(row: Any) -> bool:
    return (
        isinstance(row, dict) and exact_keys(row, ("macro", "description"))
        and isinstance(row["macro"], str)
        and re.fullmatch(r"\$[A-Z][A-Z0-9_]{0,99}", row["macro"]) is not None
        and text(row["description"], 4_000)
    )


def _library(row: dict[str, Any]) -> bool:
    if not exact_keys(row, ("items", "macros")):
        return False
    items, macros = row["items"], row["macros"]
    return (
        isinstance(items, list) and len(items) == len(PROMPT_IDS)
        and all(_metadata(item) for item in items)
        and len({item["id"] for item in items}) == len(PROMPT_IDS)
        and isinstance(macros, list) and 0 < len(macros) <= 100
        and all(_macro(macro) for macro in macros)
        and len({macro["macro"] for macro in macros}) == len(macros)
    )


def _prompt_revision(row: dict[str, Any]) -> bool:
    revision = row["prompt_revision"]
    if row["prompt_id"] != "job-completion-report":
        return revision is None
    return (
        isinstance(revision, str) and re.fullmatch("[1-9][0-9]{0,18}", revision) is not None
        and int(revision) <= 2**63 - 1
    )


def _rendered(row: dict[str, Any]) -> bool:
    return (
        exact_keys(row, ("project_id", "prompt_id", "work_item_id", "code_review_id",
                         "content", "revision", "prompt_revision"))
        and uuid(row["project_id"]) and row["prompt_id"] in PROMPT_IDS
        and _revision(row["revision"])
        and (row["work_item_id"] is None or uuid(row["work_item_id"]))
        and (row["code_review_id"] is None or uuid(row["code_review_id"]))
        and _prompt_revision(row)
        and text(row["content"], 100_000) and len(row["content"].encode()) <= 400_000
    )


def validate_prompt_response(path: str, method: str, value: object) -> None:
    parts = path.split("/")
    if "prompts" not in parts:
        return
    valid = False
    if isinstance(value, dict):
        if parts[-1] == "render" and method == "POST":
            valid = _rendered(value)
        elif parts[-1] == "prompts":
            valid = _library(value)
        else:
            valid = _detail(value, parts[-1])
    if not valid:
        raise ValueError("Invalid prompt response")
