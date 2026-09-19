"""A later external move is recoverable only with durable, unambiguous evidence."""

import errno
import hashlib
import os
from uuid import uuid4

import pytest

from mnemonic_api import transcript_copies, transcript_relocation
from mnemonic_api.transcript_access import TranscriptAccessError
from mnemonic_api.transcript_copies import TranscriptCopyPin, TranscriptStorage
from mnemonic_api.transcript_source_identity import PREFIX_BYTES
from mnemonic_api.transcript_storage import validate_transcript_assertion


@pytest.fixture
def enrolled(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    source = root / "session.jsonl"
    source.write_bytes(b'{"type":"user","message":{"content":"' + b"evidence " * 80 + b'"}}\n')
    identity = validate_transcript_assertion(str(source), [root])
    assert identity is not None
    return root, source, identity


def moved(source):
    destination = source.parent / "worktree" / source.name
    destination.parent.mkdir()
    source.rename(destination)
    return destination


def test_move_and_append_survive_restart_without_rewriting_assertion(enrolled, tmp_path):
    root, source, identity = enrolled
    target = moved(source)
    with target.open("ab") as output:
        output.write(b'{"type":"assistant","message":{"content":"later answer"}}\n')
    store = TranscriptStorage(tmp_path / "private", 100_000)
    transcript, snapshot = uuid4(), uuid4()
    copy = store.capture(transcript, snapshot, str(source), [root], source_identity=identity)
    assert copy.source_path == str(target)
    assert store.read_copy(copy) == target.read_bytes()
    target.unlink()
    # Already published immutable bytes recover without re-discovery or a live source.
    restarted = TranscriptStorage(tmp_path / "private", 100_000)
    recovered = restarted.capture(transcript, snapshot, str(source), [], source_identity=identity)
    assert recovered == copy
    # The durable capture receipt also preserves verified relocation provenance.
    assert recovered.source_path == str(target)
    assert recovered.source_modified_at == copy.source_modified_at


def test_fingerprint_is_bounded_and_short_assertions_do_not_gain_inferred_evidence(tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_bytes(b"x" * (PREFIX_BYTES + 1000))
    identity = validate_transcript_assertion(str(source), [tmp_path])
    assert identity == {"version": 1, "filename": source.name, "prefix_size": PREFIX_BYTES,
                        "prefix_sha256": hashlib.sha256(b"x" * PREFIX_BYTES).hexdigest()}
    source.write_bytes(b"short")
    assert validate_transcript_assertion(str(source), [tmp_path]) is None


@pytest.mark.parametrize("kind", ["missing", "changed", "ambiguous", "legacy", "outside",
                                   "symlink", "symlink_parent", "manual_pin"])
def test_unproven_or_ambiguous_moves_never_capture_bytes(enrolled, tmp_path, kind):
    root, source, identity = enrolled
    target = moved(source)
    expected = None
    error = "transcript_source_missing"
    if kind == "missing":
        target.unlink()
    elif kind == "changed":
        source.write_bytes(b"unrelated replacement" * 30)
        target.unlink()
        error = "transcript_source_identity_changed"
    elif kind == "ambiguous":
        duplicate = root / "another"
        duplicate.mkdir()
        (duplicate / target.name).write_bytes(target.read_bytes())
        error = "transcript_relocation_ambiguous"
    elif kind == "legacy":
        identity = None
    elif kind == "outside":
        target.rename(tmp_path / target.name)
    elif kind == "symlink":
        outside = tmp_path / target.name
        target.rename(outside)
        target.symlink_to(outside)
    elif kind == "symlink_parent":
        target.parent.rename(tmp_path / "outside")
        target.parent.symlink_to(tmp_path / "outside", target_is_directory=True)
    elif kind == "manual_pin":
        expected = TranscriptCopyPin(hashlib.sha256(target.read_bytes()).hexdigest(),
                                     target.stat().st_size)
    store = TranscriptStorage(tmp_path / "private", 100_000)
    with pytest.raises(TranscriptAccessError, match=error):
        store.capture(uuid4(), uuid4(), str(source), [root], expected=expected,
                       source_identity=identity)
    assert not list(store.root.rglob("transcript.jsonl"))
    assert not list(store.root.rglob(".pending-*"))


def test_current_approved_roots_and_overlapping_roots_are_honored(enrolled, tmp_path):
    root, source, identity = enrolled
    new_root = tmp_path / "new-approved-root"
    new_root.mkdir()
    target = new_root / source.name
    source.rename(target)
    assert transcript_relocation.resolve_source(str(source), [new_root], identity) == str(target)
    assert transcript_relocation.resolve_source(str(source), [new_root, tmp_path, root], identity
                                                ) == str(target)


@pytest.mark.parametrize("budget", ["MAX_ENTRIES", "MAX_DIRECTORIES", "MAX_DEPTH", "MAX_SECONDS"])
def test_incomplete_scan_cannot_select_even_one_matching_candidate(enrolled, monkeypatch, budget):
    root, source, identity = enrolled
    target = moved(source)
    monkeypatch.setattr(transcript_relocation, budget, 0)
    with pytest.raises(TranscriptAccessError, match="transcript_relocation_scan_limit"):
        transcript_relocation.resolve_source(str(source), [root], identity)
    assert target.exists()


def test_inaccessible_coverage_prevents_selection_and_names_blocker(enrolled, monkeypatch):
    root, source, identity = enrolled
    moved(source)
    blocked = root / "blocked"
    blocked.mkdir()
    original = transcript_relocation._open_source

    def open_source(path, roots, **options):
        if path == str(blocked):
            raise PermissionError(errno.EACCES, "denied")
        return original(path, roots, **options)

    monkeypatch.setattr(transcript_relocation, "_open_source", open_source)
    with pytest.raises(TranscriptAccessError, match="transcript_permission_denied") as failure:
        transcript_relocation.resolve_source(str(source), [root], identity)
    assert failure.value.details["path"] == str(blocked)
    assert failure.value.details["operation"] == "list_directory"


def test_substitution_between_discovery_and_capture_is_rejected(enrolled, tmp_path, monkeypatch):
    root, source, identity = enrolled
    target = moved(source)
    resolve = transcript_copies.resolve_source

    def substituted(*args):
        result = resolve(*args)
        target.write_bytes(b"unrelated source" * 100)
        return result

    monkeypatch.setattr(transcript_copies, "resolve_source", substituted)
    store = TranscriptStorage(tmp_path / "private", 100_000)
    with pytest.raises(TranscriptAccessError, match="transcript_source_identity_changed"):
        store.capture(uuid4(), uuid4(), str(source), [root], source_identity=identity)
    assert not list(store.root.rglob("transcript.jsonl"))
    assert not list(store.root.rglob(".pending-*"))


def test_matching_original_does_not_require_directory_listing(enrolled, monkeypatch):
    root, source, identity = enrolled

    def unexpected_scan(*_args):
        pytest.fail("A valid original source requires no relocation scan")

    monkeypatch.setattr(transcript_relocation, "_candidates", unexpected_scan)
    assert transcript_relocation.resolve_source(str(source), [root], identity) == str(source)


def test_unrelated_files_are_not_read_during_discovery(enrolled, monkeypatch):
    root, source, identity = enrolled
    target = moved(source)
    (root / "another-session.jsonl").write_bytes(b"private unrelated content")
    verify = transcript_relocation.verify_identity
    observed = []

    def recording(path, roots, proof):
        observed.append(path)
        return verify(path, roots, proof)

    monkeypatch.setattr(transcript_relocation, "verify_identity", recording)
    assert transcript_relocation.resolve_source(str(source), [root], identity) == str(target)
    assert set(observed) == {str(source), str(target)}


@pytest.mark.skipif(os.geteuid() == 0, reason="Root bypasses POSIX permission bits")
def test_real_directory_permission_failure_is_actionable(enrolled):
    root, source, identity = enrolled
    moved(source)
    root.chmod(0o100)
    try:
        with pytest.raises(TranscriptAccessError, match="transcript_permission_denied") as failure:
            transcript_relocation.resolve_source(str(source), [root], identity)
        assert failure.value.details["path"] == str(root)
        assert failure.value.details["operation"] == "list_directory"
    finally:
        root.chmod(0o700)
