"""Raw snapshots are private, immutable, stable and independently recoverable."""

import errno
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from mnemonic_api import transcript_copies
from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.transcript_copies import TranscriptCopyPin, TranscriptStorage


def test_copy_survives_source_disappearance_and_reuses_published_file(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_bytes(b'{"content":"retained evidence"}\n')
    original = source.read_bytes()
    store = TranscriptStorage(tmp_path / "private", 1024)
    identity, snapshot = uuid4(), uuid4()
    copied = store.capture(identity, snapshot, str(source), [tmp_path])
    assert store.read_copy(copied) == original
    assert copied.sha256 == hashlib.sha256(original).hexdigest()
    assert (store.root / copied.storage_key).stat().st_mode & 0o777 == 0o600
    assert (store.root / str(identity)).stat().st_mode & 0o777 == 0o700
    source.unlink()
    assert store.capture(identity, snapshot, str(source), [tmp_path]) == copied
    assert not list(store.root.rglob(".pending-*"))


def test_concurrent_capture_never_replaces_a_published_snapshot(tmp_path):
    first, second = tmp_path / "one.jsonl", tmp_path / "two.jsonl"
    first.write_bytes(b"first source")
    second.write_bytes(b"second source")
    store = TranscriptStorage(tmp_path / "private", 1024)
    identity, snapshot = uuid4(), uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        copies = list(pool.map(lambda path: store.capture(
            identity, snapshot, str(path), [tmp_path]), [first, second]))
    assert copies[0] == copies[1]
    assert store.read_copy(copies[0]) in {first.read_bytes(), second.read_bytes()}
    assert not list(store.root.rglob(".pending-*"))


def test_changing_source_is_retried_and_partial_stages_are_removed(tmp_path, monkeypatch):
    source = tmp_path / "source.jsonl"
    source.write_bytes(b"first line\n")
    store = TranscriptStorage(tmp_path / "private", 1024)
    chunks = transcript_copies._source_chunks
    mutated = []

    def changing(path, roots, maximum):
        for chunk in chunks(path, roots, maximum):
            if not mutated:
                with source.open("ab") as content:
                    content.write(b"later line\n")
                mutated.append(True)
            yield chunk

    monkeypatch.setattr(transcript_copies, "_source_chunks", changing)
    copied = store.capture(uuid4(), uuid4(), str(source), [tmp_path])
    assert store.read_copy(copied) == source.read_bytes()
    assert not list(store.root.rglob(".pending-*"))


@pytest.mark.parametrize("kind", ["symlink", "fifo", "outside", "oversized"])
def test_unsafe_sources_never_produce_a_copy(tmp_path, kind):
    source = tmp_path / "source.jsonl"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"private")
        source.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(source)
    else:
        source.write_bytes(b"x" * (1025 if kind == "oversized" else 10))
    store = TranscriptStorage(tmp_path / "private", 1024)
    roots = [tmp_path / "other"] if kind == "outside" else [tmp_path]
    with pytest.raises(ExtractionError):
        store.capture(uuid4(), uuid4(), str(source), roots)
    assert not list(store.root.rglob("transcript.jsonl"))
    assert not list(store.root.rglob(".pending-*"))


def test_copy_read_detects_corruption_without_rereading_source(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_bytes(b"original")
    store = TranscriptStorage(tmp_path / "private", 1024)
    copied = store.capture(uuid4(), uuid4(), str(source), [tmp_path])
    (store.root / copied.storage_key).write_bytes(b"corrupted")
    with pytest.raises(ExtractionError, match="transcript_copy_integrity_failed"):
        store.read_copy(copied)


def test_permissions_report_environment_failure_without_immediate_retries(tmp_path, monkeypatch):
    attempts = []

    def denied(*_args):
        attempts.append(True)
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(TranscriptStorage, "_capture_once", denied)
    with pytest.raises(ExtractionError) as failure:
        TranscriptStorage(tmp_path, 1024).capture(uuid4(), uuid4(), "/source", [])
    assert not failure.value.retryable and len(attempts) == 1
    assert failure.value.code == "transcript_permission_denied"


@pytest.mark.parametrize("mismatch", ["hash", "size"])
def test_pinned_recovery_rejects_changed_stage_before_rename(tmp_path, mismatch):
    source = tmp_path / "approved.jsonl"
    source.write_bytes(b"operator-approved source")
    digest, size = hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size
    pin = TranscriptCopyPin("0" * 64 if mismatch == "hash" else digest,
                            size + 1 if mismatch == "size" else size)
    store = TranscriptStorage(tmp_path / "private", 1024)
    with pytest.raises(ExtractionError, match="transcript_recovery_content_changed") as failure:
        store.capture(uuid4(), uuid4(), str(source), [tmp_path], expected=pin)
    assert not failure.value.retryable
    assert not list(store.root.rglob("transcript.jsonl"))
    assert not list(store.root.rglob(".pending-*"))


def test_pinned_recovery_rechecks_competing_retained_file_at_publication(tmp_path):
    approved, unapproved = tmp_path / "approved.jsonl", tmp_path / "unapproved.jsonl"
    approved.write_bytes(b"approved source")
    unapproved.write_bytes(b"unexpected competitor")
    identity, snapshot = uuid4(), uuid4()
    root = tmp_path / "private"
    pin = TranscriptCopyPin(hashlib.sha256(approved.read_bytes()).hexdigest(),
                            approved.stat().st_size)

    class CompetingStorage(TranscriptStorage):
        def _publish_once(self, staged, expected=None):
            TranscriptStorage(root, 1024).capture(identity, snapshot, str(unapproved), [tmp_path])
            return super()._publish_once(staged, expected)

    with pytest.raises(ExtractionError, match="transcript_recovery_content_changed"):
        CompetingStorage(root, 1024).capture(identity, snapshot, str(approved), [tmp_path],
                                            expected=pin)
    store = TranscriptStorage(root, 1024)
    assert store.read_copy(store.describe(store.key(identity, snapshot))) == unapproved.read_bytes()
    assert not list(root.rglob(".pending-*"))
