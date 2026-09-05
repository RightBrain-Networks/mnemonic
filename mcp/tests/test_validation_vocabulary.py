"""Review MCP names independently while sharing cross-surface sanitizer cases."""

import json
from pathlib import Path
from typing import Any

import pytest

from mnemonic_mcp.validation import (
    VALIDATION_FIELDS,
    validation_details,
    validation_error_message,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VOCABULARY = json.loads(
    (REPOSITORY_ROOT / "docs/validation-vocabulary.json").read_text(encoding="utf-8")
)
CORPUS = json.loads(
    (REPOSITORY_ROOT / "tests/fixtures/validation-locations-v1.json").read_text(encoding="utf-8")
)


def test_mcp_validation_fields_match_reviewed_surface_subset():
    assert VALIDATION_FIELDS == frozenset(
        VOCABULARY["common_fields"] + VOCABULARY["surface_fields"]["mcp"]
    )


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["id"])
def test_mcp_shared_validation_location_corpus(case: dict[str, Any]):
    details = validation_details([(case["location"], case.get("type", "value_error"))])
    path = case["mcp_path"]
    kind = case.get("mcp_type", "value_error")
    kinds = {kind} if kind is not None else set()

    expected_details = ({path: kinds}, set()) if path else ({}, kinds)
    assert details == expected_details
    message = validation_error_message(*details)
    if path:
        suffix = f" ({kind})" if kind is not None else ""
        assert message == f"Mnemonic rejected the input. Check: {path}{suffix}."
    elif kind is not None:
        assert message == (
            f"Mnemonic rejected the input ({kind}). Check the field names and constraints."
        )
    else:
        assert message == "Mnemonic rejected the input. Check the field names and constraints."
    for marker in CORPUS["private_markers"]:
        assert marker not in message


def test_mcp_formatter_rechecks_paths_and_error_types_at_its_own_boundary():
    message = validation_error_message(
        {
            "body.title": ["value_error", "PRIVATE_ERROR_TYPE"],
            "body.PRIVATE_CALLER_KEY": ["value_error"],
            "PRIVATE_CALLER_KEY": ["value_error"],
        },
        ["PRIVATE_ERROR_TYPE"],
    )
    assert message == "Mnemonic rejected the input. Check: body.title (value_error)."
