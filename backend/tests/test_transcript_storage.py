"""Filesystem boundaries use descriptor walks and never follow agent-supplied symlinks."""

import os

import pytest

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.transcript_storage import read_transcript


def test_regular_file_is_read_without_mutating_source(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_bytes(b"synthetic")
    assert read_transcript(str(path), [tmp_path], 20) == b"synthetic"
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
    with pytest.raises(ExtractionError, match="transcript_io_error"):
        read_transcript(str(chosen), [link if kind == "root" else tmp_path], 100)


@pytest.mark.parametrize("source", ["relative.jsonl", "/etc/passwd", "/tmp/../etc/passwd"])
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
