"""Operator bounds for the transcript content corpus are independent of source files."""

import pytest
from pydantic import ValidationError

from mnemonic_api.config import Settings


def settings():
    return Settings(database_url="postgresql+psycopg://test:test@localhost/test",
                    api_key="synthetic-test-key" * 3)


def test_transcript_search_budget_default_and_environment_override(monkeypatch):
    monkeypatch.delenv("MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES", raising=False)
    assert settings().transcript_search_max_bytes == 536_870_912
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES", "1073741824")
    configured = settings()
    assert configured.transcript_search_max_bytes == 1_073_741_824
    assert configured.transcript_max_bytes == 67_108_864


@pytest.mark.parametrize("value", ["0", "-1", "2147483649", "invalid"])
def test_transcript_search_budget_rejects_invalid_limits(monkeypatch, value):
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES", value)
    with pytest.raises(ValidationError, match="MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES"):
        settings()


def test_transcript_index_directory_environment_and_memory_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MNEMONIC_TRANSCRIPT_INDEX_DIR", raising=False)
    assert settings().transcript_index_dir is None
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_INDEX_DIR", str(tmp_path / "private index"))
    assert settings().transcript_index_dir == tmp_path / "private index"
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_INDEX_DIR", "")
    assert settings().transcript_index_dir is None


@pytest.mark.parametrize("value", ["/", "relative/index", "/private/../source", "//host/index"])
def test_transcript_index_directory_rejects_ambiguous_roots(monkeypatch, value):
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_INDEX_DIR", value)
    with pytest.raises(ValidationError, match="MNEMONIC_TRANSCRIPT_INDEX_DIR"):
        settings()
