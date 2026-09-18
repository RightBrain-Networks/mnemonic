"""Filesystem boundaries use descriptor walks and never follow agent-supplied symlinks."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.main import create_app
from mnemonic_api.transcript_storage import check_transcript_source, read_transcript


@pytest.mark.parametrize("prefix", ["", "/", "//"])
@pytest.mark.parametrize("root_prefix", ["", "/"])
def test_regular_file_is_read_without_mutating_source(tmp_path, prefix, root_prefix):
    path = tmp_path / "session.jsonl"
    path.write_bytes(b"synthetic")
    roots = [Path(root_prefix + str(tmp_path))]
    assert read_transcript(prefix + str(path), roots, 20) == b"synthetic"
    assert path.read_bytes() == b"synthetic"


@pytest.mark.parametrize("kind", ["file", "parent", "root"])
def test_every_symlink_component_is_rejected(tmp_path, kind):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "session.jsonl"
    path.write_bytes(b"hidden")
    link = tmp_path / "link"
    link.symlink_to(path if kind == "file" else source)
    chosen = link if kind == "file" else link / "session.jsonl"
    with pytest.raises(ExtractionError, match="transcript_symlink_rejected"):
        read_transcript(str(chosen), [link if kind == "root" else tmp_path], 100)


@pytest.mark.parametrize("source", ["relative.jsonl", "/etc/passwd", "/tmp/../etc/passwd",
                                    "//etc/passwd", "//tmp/../etc/passwd"])
def test_unapproved_paths_rejected(tmp_path, source):
    with pytest.raises(ExtractionError, match="transcript_path_not_allowed"):
        read_transcript(source, [tmp_path], 100)


def test_fifo_never_blocks_and_large_file_is_rejected(tmp_path):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(ExtractionError, match="transcript_not_regular_file"):
        read_transcript(str(fifo), [tmp_path], 100)
    file = tmp_path / "large"
    file.write_bytes(b"x" * 101)
    with pytest.raises(ExtractionError, match="transcript_too_large"):
        read_transcript(str(file), [tmp_path], 100)


def test_configured_source_is_checked_without_reading_transcripts(tmp_path):
    check_transcript_source(None, [])
    check_transcript_source(tmp_path, [tmp_path])
    with pytest.raises(RuntimeError, match="Configured transcript source is unavailable"):
        check_transcript_source(tmp_path / "missing", [tmp_path])
    with pytest.raises(RuntimeError, match="Configured transcript source is unavailable"):
        check_transcript_source(tmp_path, [])
    link = tmp_path / "linked"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError, match="Configured transcript source is unavailable"):
        check_transcript_source(link, [tmp_path])


@pytest.mark.parametrize("missing_field", [
    "transcript_source_dir", "codex_transcript_source_dir", "codex_archived_transcript_source_dir",
])
def test_startup_preserves_dashboard_when_source_is_unavailable(tmp_path, missing_field):
    sources = {field: tmp_path / field for field in (
        "transcript_source_dir", "codex_transcript_source_dir",
        "codex_archived_transcript_source_dir",
    )}
    for field, source in sources.items():
        if field != missing_field:
            source.mkdir()
    config = Settings(database_url="postgresql+psycopg://test:test@localhost/test",
                      api_key="synthetic-test-key" * 3, artifact_max_bytes=0, **sources)
    engine = create_engine("sqlite://")
    try:
        with TestClient(create_app(settings=config, engine=engine)) as client:
            assert client.get("/healthz").status_code == 200
        from mnemonic_api.transcript_health import environment_report
        issues = environment_report(config, worker=False)["issues"]
        assert any(item["details"]["path"] == str(sources[missing_field]) for item in issues)
    finally:
        engine.dispose()


def test_index_outage_preserves_dashboard_and_reports_the_directory(tmp_path):
    from mnemonic_api.artifact_index import ArtifactSearchIndex
    from mnemonic_api.transcript_health import index_warnings

    missing = tmp_path / "missing-index"
    config = Settings(database_url="postgresql+psycopg://test:test@localhost/test",
                      api_key="synthetic-test-key" * 3, artifact_max_bytes=0,
                      transcript_index_dir=missing)
    engine = create_engine("sqlite://")
    try:
        with TestClient(create_app(settings=config, engine=engine)) as client:
            assert client.get("/healthz").status_code == 200
        warnings = index_warnings(None, config)
        assert len(warnings) == 1 and warnings[0].path == str(missing)
        assert "0700" in warnings[0].action
        missing.mkdir(mode=0o700)
        index = ArtifactSearchIndex(missing)
        try:
            assert index_warnings(index, config) == []
        finally:
            index.close()
    finally:
        engine.dispose()
