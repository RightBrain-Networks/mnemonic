"""Only supported explicit GitHub forms share identity; stored values stay raw."""

import json
from pathlib import Path

import pytest

from mnemonic_mcp.repository_identity import canonical_repository, repository_aliases

FIXTURE = Path(__file__).resolve().parents[2] / "backend/tests/fixtures/repository-identities.json"


@pytest.mark.parametrize(("value", "expected"), json.loads(FIXTURE.read_text()))
def test_repository_identity_is_conservative_and_idempotent(value, expected):
    assert canonical_repository(value) == expected
    assert canonical_repository(expected) == expected
    if value is not None:
        aliases = repository_aliases(value)
        assert all(canonical_repository(alias) == expected for alias in aliases)
        if expected == value and not value.startswith("https://github.com/team/repo"):
            assert aliases == {value}
