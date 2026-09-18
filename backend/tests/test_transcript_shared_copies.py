"""Repeated enrollments share immutable native bytes, including append-only history."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.transcript_copies import TranscriptCopyPin, TranscriptStorage
from mnemonic_api.transcript_objects import NativeObject, object_key


def test_nine_unchanged_enrollments_share_one_content_object(tmp_path):
    source = tmp_path / 'session.jsonl'
    data = b'{"role":"user","content":"native shared bytes"}\n' * 60000
    source.write_bytes(data)
    store = TranscriptStorage(tmp_path / 'private', len(data) + 100)
    with ThreadPoolExecutor(max_workers=4) as pool:
        copies = list(pool.map(lambda _: store.capture(uuid4(), uuid4(), str(source), [tmp_path]),
                               range(9)))
    assert len({copy.storage_key for copy in copies}) == 1
    assert len(list(store.root.rglob('snapshot.bin'))) == 1
    assert len(list(store.root.rglob('capture.json'))) == 9
    assert not list(store.root.rglob('.pending-*'))
    source.unlink()
    for copy in copies:
        assert store.read_copy(copy) == data
        assert copy.sha256 == hashlib.sha256(data).hexdigest()


def test_growing_session_shares_complete_prefix_and_preserves_every_snapshot(tmp_path):
    source = tmp_path / 'session.jsonl'
    store = TranscriptStorage(tmp_path / 'private', 8 * 1024 * 1024)
    data = b'first native bytes\n' * 100000
    snapshots = []
    for index in range(16):
        data += (f'append {index:02d}\n'.encode()) * 12000
        source.write_bytes(data)
        copied = store.capture(uuid4(), uuid4(), str(source), [tmp_path])
        snapshots.append((copied, data))
    payload_bytes = 0
    for path in store.root.rglob('snapshot.bin'):
        with path.open('rb') as content:
            digest = path.parent.parent.name.replace('-', '') + path.parent.name.replace('-', '')
            header = NativeObject.read(content, digest)
            payload_bytes += path.stat().st_size - content.tell()
            assert header.size_bytes <= len(data)
    assert payload_bytes == len(data)
    # Include receipts, source hints and filesystem block rounding in this bound.
    assert sum(path.stat().st_blocks * 512 for path in store.root.rglob('*')) < 2 * len(data)
    source.unlink()
    for copy, expected in snapshots:
        assert store.read_copy(copy) == expected
        assert copy.sha256 == hashlib.sha256(expected).hexdigest()


def test_content_deduplicates_across_source_paths_and_keeps_rewrites_separate(tmp_path):
    first, second = tmp_path / 'first.jsonl', tmp_path / 'second.jsonl'
    original = b'x' * 1048576 + b'original final bytes'
    first.write_bytes(original)
    second.write_bytes(original)
    store = TranscriptStorage(tmp_path / 'private', 3 * 1048576)
    a = store.capture(uuid4(), uuid4(), str(first), [tmp_path])
    b = store.capture(uuid4(), uuid4(), str(second), [tmp_path])
    assert a.storage_key == b.storage_key
    # Matching an initial sample is insufficient: compare the complete base.
    revised = b'x' * 1048576 + b'changed final bytes and append'
    first.write_bytes(revised)
    c = store.capture(uuid4(), uuid4(), str(first), [tmp_path])
    assert c.storage_key != a.storage_key
    with (store.root / c.storage_key).open('rb') as raw:
        assert NativeObject.read(raw, c.sha256).base_sha256 is None
    assert store.read_copy(a) == original
    assert store.read_copy(b) == original
    assert store.read_copy(c) == revised


def test_shared_base_corruption_invalidates_all_dependent_reads(tmp_path):
    source = tmp_path / 'source.jsonl'
    store = TranscriptStorage(tmp_path / 'private', 10000)
    source.write_bytes(b'original\n')
    first = store.capture(uuid4(), uuid4(), str(source), [tmp_path])
    source.write_bytes(b'original\nappended\n')
    second = store.capture(uuid4(), uuid4(), str(source), [tmp_path])
    with (store.root / first.storage_key).open('r+b') as raw:
        raw.seek(-1, 2)
        raw.write(b'x')
    for copy in (first, second):
        with pytest.raises(ExtractionError, match='transcript_copy_integrity_failed'):
            store.read_copy(copy)


def test_recovery_pin_is_checked_before_content_or_reference_publication(tmp_path):
    source = tmp_path / 'source.jsonl'
    source.write_bytes(b'actual')
    store = TranscriptStorage(tmp_path / 'private', 1024)
    with pytest.raises(ExtractionError, match='transcript_recovery_content_changed'):
        store.capture(uuid4(), uuid4(), str(source), [tmp_path],
                      expected=TranscriptCopyPin('0' * 64, 6))
    assert not list(store.root.rglob('snapshot.bin'))
    assert not list(store.root.rglob('capture.json'))
    assert not list(store.root.rglob('.pending-*'))


def test_receipt_adoption_keeps_shared_identity_after_source_disappears(tmp_path):
    source = tmp_path / 'source.jsonl'
    source.write_bytes(b'preserve this exact capture')
    store = TranscriptStorage(tmp_path / 'private', 1024)
    identity, snapshot = uuid4(), uuid4()
    copy = store.capture(identity, snapshot, str(source), [tmp_path])
    source.unlink()
    assert store.capture(identity, snapshot, str(source), [tmp_path]) == copy
    assert copy.storage_key == object_key(copy.sha256)
    assert store.retained(identity, snapshot) == copy


def test_parent_change_during_native_read_is_rejected_on_context_exit(tmp_path):
    source = tmp_path / 'source.jsonl'
    source.write_bytes(b'base native content\n')
    store = TranscriptStorage(tmp_path / 'private', 1024)
    first = store.capture(uuid4(), uuid4(), str(source), [tmp_path])
    source.write_bytes(b'base native content\nnew tail\n')
    second = store.capture(uuid4(), uuid4(), str(source), [tmp_path])
    with pytest.raises(ExtractionError, match='transcript_copy_integrity_failed'):
        with store.open_copy(second) as retained:
            assert retained.read() == source.read_bytes()
            with (store.root / first.storage_key).open('ab') as native:
                native.write(b'tampered after reconstruction')


def test_missing_base_does_not_overwrite_an_existing_content_object(tmp_path):
    source = tmp_path / 'source.jsonl'
    source.write_bytes(b'first content\n')
    store = TranscriptStorage(tmp_path / 'private', 1024)
    first = store.capture(uuid4(), uuid4(), str(source), [tmp_path])
    source.write_bytes(b'first content\nsecond content\n')
    second = store.capture(uuid4(), uuid4(), str(source), [tmp_path])
    final = store.root / second.storage_key
    before = final.read_bytes()
    (store.root / first.storage_key).unlink()
    with pytest.raises(ExtractionError):
        store.capture(uuid4(), uuid4(), str(source), [tmp_path])
    assert final.read_bytes() == before


def test_legacy_payload_that_looks_like_a_reference_is_still_exact_native_bytes(tmp_path):
    from mnemonic_api.artifact_storage import ArtifactStorage
    from mnemonic_api.transcript_copy_references import CopyReference

    store = TranscriptStorage(tmp_path / 'private', 1024)
    identity, snapshot = uuid4(), uuid4()
    original = CopyReference('a' * 64, 100).document()
    writer = ArtifactStorage(store.root, 1024)
    staged = writer.stage(identity, snapshot, 'transcript.jsonl', [original])
    writer.publish(staged)
    retained = store.retained(identity, snapshot)
    assert retained is not None
    assert store.read_copy(retained) == original
