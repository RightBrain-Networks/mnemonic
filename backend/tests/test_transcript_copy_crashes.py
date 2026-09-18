"""Actual child-process death preserves published snapshots and releases stage locks."""

import hashlib
import json
import multiprocessing
import os
import signal
import time
from contextlib import contextmanager
from uuid import UUID, uuid4

from mnemonic_api import transcript_copies
from mnemonic_api.transcript_copies import TranscriptStorage
from mnemonic_api.transcript_objects import object_key

_CONTENT = (b'{"role":"user","content":"synthetic first line"}\r\n'
            b'{"role":"assistant","content":"synthetic retained evidence"}\n')
_PUBLISHED_EXIT = 73


def _exit_after_publication(root, source, identity, snapshot):
    class AbruptStorage(TranscriptStorage):
        def _publish_once(self, staged, expected=None):
            super()._publish_once(staged, expected)
            # No Python finally blocks, staging cleanup, or result delivery run.
            # The rename and its containing directory fsync already completed.
            os._exit(_PUBLISHED_EXIT)

    AbruptStorage(root, 1024).capture(identity, snapshot, str(source), [source.parent])
    os._exit(74)


def _block_after_partial_write(root, source, identity, snapshot, sender):
    original_chunks = transcript_copies._source_chunks

    def blocked_chunks(*args):
        for chunk in original_chunks(*args):
            middle = max(1, len(chunk) // 2)
            yield chunk[:middle]
            # Resuming this generator proves the staging writer wrote the first
            # part and still owns its OS file lock. Parent kills this process.
            sender.send("partial-written")
            time.sleep(20)
            os._exit(75)
            yield chunk[middle:]

    transcript_copies._source_chunks = blocked_chunks
    TranscriptStorage(root, 1024).capture(identity, snapshot, str(source), [source.parent])


@contextmanager
def _child(target, *args):
    child = multiprocessing.get_context("spawn").Process(target=target, args=args)
    child.start()
    try:
        yield child
    finally:
        if child.is_alive():
            child.kill()
        child.join(timeout=5)
        assert not child.is_alive(), "Synthetic capture child did not exit"
        child.close()


def _persist_snapshot_ids(directory):
    identity, snapshot = uuid4(), uuid4()
    path = directory / "persisted-job.json"
    with path.open("w") as record:
        json.dump({"transcript_id": str(identity), "snapshot_id": str(snapshot)}, record)
        record.flush()
        os.fsync(record.fileno())
    return path, identity, snapshot


def test_abrupt_exit_after_atomic_publication_recovers_without_original_source(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_bytes(_CONTENT)
    saved, identity, snapshot = _persist_snapshot_ids(tmp_path)
    root = tmp_path / "managed"
    with _child(_exit_after_publication, root, source, identity, snapshot) as child:
        child.join(timeout=10)
        assert child.exitcode == _PUBLISHED_EXIT
    source.unlink()
    # Recreate the worker from only its durable identity and the managed mount.
    persisted = json.loads(saved.read_text())
    store = TranscriptStorage(root, 1024)
    recovered = store.capture(UUID(persisted["transcript_id"]), UUID(persisted["snapshot_id"]),
                              str(source), [tmp_path])
    assert recovered.storage_key == object_key(hashlib.sha256(_CONTENT).hexdigest())
    assert recovered.size_bytes == len(_CONTENT)
    assert recovered.sha256 == hashlib.sha256(_CONTENT).hexdigest()
    assert store.read_copy(recovered) == _CONTENT
    assert not list(root.rglob(".pending-*"))


def test_killed_partial_copy_releases_lock_and_only_old_stage_is_cleaned(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_bytes(_CONTENT)
    store = TranscriptStorage(tmp_path / "managed", 1024)
    retained = store.capture(uuid4(), uuid4(), str(source), [tmp_path])
    recent = store.stage(uuid4(), uuid4(), "transcript.jsonl", [b"another recent stage"])
    _, identity, snapshot = _persist_snapshot_ids(tmp_path)
    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        with _child(_block_after_partial_write, store.root, source,
                    identity, snapshot, sender) as child:
            sender.close()
            assert receiver.poll(10), "Synthetic child did not write its partial stage"
            assert receiver.recv() == "partial-written"
            directory = store.root / str(identity) / str(snapshot)
            [partial] = list(directory.glob(".pending-*"))
            assert partial.read_bytes() == _CONTENT[:len(_CONTENT) // 2]
            old = time.time() - 86401
            os.utime(partial, (old, old))
            assert store.cleanup_staging(set()) == 0
            assert partial.exists() and child.is_alive()
            child.kill()
            child.join(timeout=5)
            assert child.exitcode == -signal.SIGKILL
        assert store.cleanup_staging(set()) == 1
        assert not partial.exists()
        assert (store.root / recent.temporary_path).read_bytes() == b"another recent stage"
        assert store.read_copy(retained) == _CONTENT
        recovered = store.capture(identity, snapshot, str(source), [tmp_path])
        assert store.read_copy(recovered) == _CONTENT
        assert not list(directory.glob(".pending-*"))
    finally:
        receiver.close()
        sender.close()
