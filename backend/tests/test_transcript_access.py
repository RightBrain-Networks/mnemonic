"""Filesystem regressions use real permissions and never expose transcript bodies."""

import errno
import os
from pathlib import Path

import pytest

from mnemonic_api.transcript_access import TranscriptAccessError, access_error
from mnemonic_api.transcript_health import probe_storage
from mnemonic_api.transcript_storage import _open_source, read_transcript


def test_execute_only_parent_does_not_require_directory_listing(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "sources"
    root.mkdir()
    file = root / "session.jsonl"
    file.write_bytes(b"fixture")
    parent.chmod(0o100)
    try:
        assert read_transcript(str(file), [root], 100) == b"fixture"
    finally:
        parent.chmod(0o700)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses DAC")
@pytest.mark.parametrize("blocked", ["file", "parent"])
def test_permission_failure_identifies_exact_blocking_component(tmp_path, blocked):
    parent = tmp_path / "sources"
    parent.mkdir()
    file = parent / "session.jsonl"
    file.write_bytes(b"secret fixture body never appears in errors")
    target = file if blocked == "file" else parent
    target.chmod(0)
    try:
        with pytest.raises(TranscriptAccessError) as failure:
            _open_source(str(file), [parent])
        error = failure.value
        assert error.code == "transcript_permission_denied"
        assert error.details["path"] == str(target)
        assert error.details["operation"] == ("read_file" if blocked == "file" else "traverse")
        assert error.details["uid"] == os.geteuid()
        assert "secret" not in str(error.details)
    finally:
        target.chmod(0o600 if blocked == "file" else 0o700)


def test_directory_is_classified_before_fdopen(tmp_path):
    directory = tmp_path / "workflow.jsonl"
    directory.mkdir()
    with pytest.raises(TranscriptAccessError, match="transcript_not_regular_file"):
        read_transcript(str(directory), [tmp_path], 100)


@pytest.mark.parametrize("number,code", [
    (errno.ENOENT, "transcript_source_missing"),
    (errno.EACCES, "transcript_permission_denied"),
    (errno.ENOSPC, "transcript_storage_full"),
    (errno.EDQUOT, "transcript_storage_full"),
    (errno.EROFS, "transcript_storage_read_only"),
    (errno.EIO, "transcript_io_error"),
])
def test_os_errors_have_stable_codes_without_raw_exception_text(number, code):
    error = access_error(OSError(number, "private OS text"), "/configured/path")
    assert error.code == code
    assert "private OS text" not in str(error.details)
    assert error.retryable is (number == errno.EIO)


def test_storage_probe_detects_full_disk_and_removes_probe(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)

    def full(*_args):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(os, "write", full)
    result = probe_storage(tmp_path)
    assert result["code"] == "transcript_storage_full"
    assert result["details"]["path"] == str(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_symlink_within_allowed_root_still_rejected(tmp_path):
    source = tmp_path / "real.jsonl"
    source.write_bytes(b"fixture")
    link = tmp_path / "session.jsonl"
    link.symlink_to(source)
    with pytest.raises(TranscriptAccessError) as failure:
        _open_source(str(link), [Path(tmp_path)])
    assert failure.value.code == "transcript_symlink_rejected"
    assert failure.value.details["path"] == str(link)
