"""Pin reviewed public names and exercise the shared value-free location corpus."""

import json
from pathlib import Path
from typing import Any

import pytest

from mnemonic_api.application.validation import (
    PUBLIC_LOCATION_SEGMENTS,
    public_validation_errors,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VOCABULARY = json.loads(
    (REPOSITORY_ROOT / "docs/validation-vocabulary.json").read_text(encoding="utf-8")
)
CORPUS = json.loads(
    (REPOSITORY_ROOT / "tests/fixtures/validation-locations-v1.json").read_text(encoding="utf-8")
)


def test_backend_validation_fields_match_reviewed_surface_subset():
    common = VOCABULARY["common_fields"]
    surfaces = VOCABULARY["surface_fields"]
    assert set(surfaces) == {"backend", "mcp", "browser"}
    for fields in [common, *surfaces.values(), VOCABULARY["browser_location_roots"]]:
        assert fields == sorted(set(fields)), "Keep the reviewed catalog sorted and unique."
        assert all(isinstance(field, str) and field.isidentifier() for field in fields)
    for fields in surfaces.values():
        assert not set(common).intersection(fields)
    assert PUBLIC_LOCATION_SEGMENTS == frozenset(common + surfaces["backend"])


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["id"])
def test_backend_shared_validation_location_corpus(case: dict[str, Any]):
    error = {
        "type": case.get("type", "value_error"),
        "loc": case["location"],
        "msg": "PRIVATE_CALLER_VALUE",
        "input": {"PRIVATE_CALLER_KEY": "PRIVATE_CALLER_VALUE"},
        "ctx": {"error": "PRIVATE_CALLER_VALUE"},
        "url": "PRIVATE_CALLER_VALUE",
    }
    result = public_validation_errors([error])

    assert result == [{
        "type": case.get("backend_type", "value_error"),
        "loc": case["backend_location"],
        "msg": case.get("backend_message", "Value is invalid."),
    }]
    for marker in CORPUS["private_markers"]:
        assert marker not in json.dumps(result)
