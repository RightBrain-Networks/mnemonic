"""Pin newer Python normalization to the shipped PostgreSQL 17 Unicode database."""

import json
import re
from pathlib import Path
from unicodedata import normalize

import pytest

from mnemonic_api.external_duplicate_schemas import duplicate_title_key

FIXTURE = json.loads((Path(__file__).resolve().parents[2]
                      / "tests/fixtures/external-record-contract-v1.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["title_key_cases"])
def test_shared_postgres_15_1_title_keys(case):
    assert duplicate_title_key(case["value"]) == case["key"]


def test_python_16_additions_do_not_create_false_exact_matches_or_reorder_old_runs():
    assert normalize("NFKC", "\U0001ccd6") == "A"
    assert duplicate_title_key("\U0001ccd6") != duplicate_title_key("A")
    assert normalize("NFKC", "a\u0315\u0897\u0300") != "a\u0315\u0897\u0300"
    assert duplicate_title_key("a\u0315\u0897\u0300") == "a\u0315\u0897\u0300"


def test_packaged_python_and_browser_normalizers_share_the_entire_assigned_table():
    root = Path(__file__).resolve().parents[2]
    backend = (root / "backend/src/mnemonic_api/title_normalization.py").read_text()
    mcp = (root / "mcp/src/mnemonic_mcp/title_normalization.py").read_text()
    browser = (root / "frontend/lib/title-normalization.ts").read_text()
    assert backend == mcp
    assert re.findall(r"0x[0-9A-F]+", backend) == re.findall(r"0x[0-9A-F]+", browser)
