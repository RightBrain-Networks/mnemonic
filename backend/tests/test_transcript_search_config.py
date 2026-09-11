"""Operator bounds for the transcript content corpus are independent of source files."""

import json

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


@pytest.mark.parametrize("roots", [None, "[]"])
def test_configured_source_supplies_default_allowlist(monkeypatch, tmp_path, roots):
    source = tmp_path / 'private "quoted" home' / "projects"
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_SOURCE_DIR", str(source))
    monkeypatch.delenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", raising=False)
    if roots is not None:
        monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", roots)
    configured = settings()
    assert configured.transcript_source_dir == source
    assert configured.transcript_allowed_roots == [source]


@pytest.mark.parametrize("source", [None, ""])
def test_unconfigured_source_keeps_filesystem_access_disabled(monkeypatch, source):
    monkeypatch.delenv("MNEMONIC_TRANSCRIPT_SOURCE_DIR", raising=False)
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", "[]")
    if source is not None:
        monkeypatch.setenv("MNEMONIC_TRANSCRIPT_SOURCE_DIR", source)
    configured = settings()
    assert configured.transcript_source_dir is None
    assert configured.transcript_allowed_roots == []


def test_explicit_allowlist_is_preserved_with_or_without_source(monkeypatch, tmp_path):
    roots = [tmp_path / "other", tmp_path / "source"]
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", json.dumps(list(map(str, roots))))
    monkeypatch.delenv("MNEMONIC_TRANSCRIPT_SOURCE_DIR", raising=False)
    assert settings().transcript_allowed_roots == roots
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_SOURCE_DIR", str(roots[1]))
    assert settings().transcript_allowed_roots == roots


def test_configured_source_cannot_expand_nonempty_explicit_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_SOURCE_DIR", str(tmp_path / "source"))
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", json.dumps([str(tmp_path / "other")]))
    with pytest.raises(ValidationError, match="MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS must include"):
        settings()


@pytest.mark.parametrize("value", ["/", "relative/source", "/private/../source", "//host/source"])
def test_transcript_source_rejects_ambiguous_roots(monkeypatch, value):
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_SOURCE_DIR", value)
    with pytest.raises(ValidationError, match="MNEMONIC_TRANSCRIPT_SOURCE_DIR"):
        settings()


def test_transcript_index_directory_rejects_nul():
    with pytest.raises(ValidationError, match="absolute dedicated paths"):
        Settings(database_url="postgresql+psycopg://test:test@localhost/test",
                 api_key="synthetic-test-key" * 3, transcript_index_dir="/private/invalid\x00path")
