"""Operator bounds for the transcript content corpus are independent of source files."""

import json

import pytest
from pydantic import ValidationError

from mnemonic_api.config import Settings

SOURCE_SETTINGS = (
    ("MNEMONIC_TRANSCRIPT_SOURCE_DIR", "transcript_source_dir"),
    ("MNEMONIC_CODEX_TRANSCRIPT_SOURCE_DIR", "codex_transcript_source_dir"),
    ("MNEMONIC_CODEX_ARCHIVED_TRANSCRIPT_SOURCE_DIR", "codex_archived_transcript_source_dir"),
)


@pytest.fixture(autouse=True)
def clear_source_environment(monkeypatch):
    for name, _ in SOURCE_SETTINGS:
        monkeypatch.delenv(name, raising=False)


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


@pytest.mark.parametrize("name,field", SOURCE_SETTINGS)
@pytest.mark.parametrize("roots", [None, "[]"])
def test_configured_source_supplies_default_allowlist(monkeypatch, tmp_path, roots, name, field):
    source = tmp_path / 'private "quoted" home' / "projects"
    monkeypatch.setenv(name, str(source))
    monkeypatch.delenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", raising=False)
    if roots is not None:
        monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", roots)
    configured = settings()
    assert getattr(configured, field) == source
    assert configured.transcript_source_dirs == [source]
    assert configured.transcript_allowed_roots == [source]


@pytest.mark.parametrize("name,field", SOURCE_SETTINGS)
@pytest.mark.parametrize("source", [None, ""])
def test_unconfigured_source_keeps_filesystem_access_disabled(monkeypatch, source, name, field):
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", "[]")
    if source is not None:
        monkeypatch.setenv(name, source)
    configured = settings()
    assert getattr(configured, field) is None
    assert configured.transcript_source_dirs == []
    assert configured.transcript_allowed_roots == []


def test_explicit_allowlist_is_preserved_with_or_without_source(monkeypatch, tmp_path):
    roots = [tmp_path / "other", tmp_path / "source"]
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", json.dumps(list(map(str, roots))))
    monkeypatch.delenv("MNEMONIC_TRANSCRIPT_SOURCE_DIR", raising=False)
    assert settings().transcript_allowed_roots == roots
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_SOURCE_DIR", str(roots[1]))
    assert settings().transcript_allowed_roots == roots


@pytest.mark.parametrize("name,_field", SOURCE_SETTINGS)
def test_configured_source_cannot_expand_nonempty_explicit_allowlist(monkeypatch, tmp_path, name,
                                                                    _field):
    monkeypatch.setenv(name, str(tmp_path / "source"))
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", json.dumps([str(tmp_path / "other")]))
    with pytest.raises(ValidationError, match="MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS must include"):
        settings()


@pytest.mark.parametrize("name,_field", SOURCE_SETTINGS)
@pytest.mark.parametrize("value", ["/", "relative/source", "/private/../source", "//host/source"])
def test_transcript_source_rejects_ambiguous_roots(monkeypatch, value, name, _field):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=name):
        settings()


def test_all_sources_supply_default_allowlist_and_preserve_explicit_order(monkeypatch, tmp_path):
    sources = [tmp_path / field for _, field in SOURCE_SETTINGS]
    for (name, _), source in zip(SOURCE_SETTINGS, sources, strict=True):
        monkeypatch.setenv(name, str(source))
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", "[]")
    assert settings().transcript_allowed_roots == sources
    explicit = [tmp_path / "other", *reversed(sources)]
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", json.dumps(list(map(str, explicit))))
    configured = settings()
    assert configured.transcript_source_dirs == sources
    assert configured.transcript_allowed_roots == explicit
    incomplete = json.dumps(list(map(str, sources[:-1])))
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", incomplete)
    with pytest.raises(ValidationError, match="must include every configured transcript"):
        settings()


def test_shared_sources_are_deduplicated(monkeypatch, tmp_path):
    for name, _ in SOURCE_SETTINGS:
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setenv("MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", "[]")
    configured = settings()
    assert configured.transcript_source_dirs == [tmp_path]
    assert configured.transcript_allowed_roots == [tmp_path]


def test_transcript_index_directory_rejects_nul():
    with pytest.raises(ValidationError, match="absolute dedicated paths"):
        Settings(database_url="postgresql+psycopg://test:test@localhost/test",
                 api_key="synthetic-test-key" * 3, transcript_index_dir="/private/invalid\x00path")
