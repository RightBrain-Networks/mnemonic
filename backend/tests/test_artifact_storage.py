"""Adversarial coverage for untrusted artifact filenames and filesystem content."""

import asyncio
import hashlib
import os
import stat
import threading
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from anyio import fail_after

from mnemonic_api import artifact_storage as storage_module
from mnemonic_api.artifact_storage import (
    ArtifactContentUnavailable,
    ArtifactStorage,
    ArtifactTooLarge,
    InvalidArtifactFilename,
    UnsafeArtifactPath,
    detect_mime_type,
    validate_filename,
)


@pytest.mark.parametrize(
    "filename",
    [
        "", ".", "..", "../secrets.txt", "a/b.txt", "a\\b.txt", "/etc/passwd",
        "C:secret.txt", "report\x00.pdf", "a\r\nContent-Type: text/html", "a\t.txt",
        "report\u202egnp.exe", "report\u200b.txt", "report\ud800.txt", "report\ue000.txt",
        " report.txt", "report.txt ", "report.txt.", "-delete", "CON", "nul.txt",
        "lPt1.log", "COM².txt", ".pending-012345", "a%2fb.txt", "a\u2215b.txt",
        "a|b.txt", 'a"b.txt', "a?b.txt", "a*b.txt", "a" * 241, "é" * 121,
    ],
)
def test_rejects_unsafe_filename_without_rewriting(filename):
    with pytest.raises(InvalidArtifactFilename):
        validate_filename(filename)


@pytest.mark.parametrize(
    "filename", ["report final (2).pdf", "患者一覧.csv", "résumé.txt", ".env", "a" * 240],
)
def test_preserves_safe_original_basename(filename):
    assert validate_filename(filename) == filename


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"%PDF-1.7\n", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b'{"sensitive": true}', "application/json"),
        (b"A text document", "text/plain"),
        (b"<!doctype html><html><script>evil()</script>", "text/html"),
        (b"PK\x03\x04\x00", "application/zip"),
        (b"\x00\x01\x02", None),
        (b"", None),
    ],
)
def test_mime_uses_content_signatures(content, expected):
    assert detect_mime_type(content) == expected


def test_private_storage_preserves_name_and_content_metadata(tmp_path):
    root = tmp_path / "artifacts"
    storage = ArtifactStorage(root, max_bytes=100)
    project, artifact = uuid4(), uuid4()
    staged = storage.stage(project, artifact, "original name.pdf", [b"%PDF-1.7\n", b"data"])
    assert staged.relative_path == f"{project}/{artifact}/original name.pdf"
    assert staged.size_bytes == 13
    assert staged.sha256 == hashlib.sha256(b"%PDF-1.7\ndata").hexdigest()
    assert staged.mime_type == "application/pdf"
    assert not (root / staged.relative_path).exists()
    storage.publish(staged)
    with storage.open(staged.relative_path) as content:
        assert content.read() == b"%PDF-1.7\ndata"
    assert list((root / str(project) / str(artifact)).iterdir()) == [root / staged.relative_path]
    assert stat.S_IMODE((root / staged.relative_path).stat().st_mode) == 0o600
    for directory in (root, root / str(project), root / str(project) / str(artifact)):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_upload_bound_removes_all_partial_content(tmp_path):
    storage = ArtifactStorage(tmp_path, max_bytes=5)
    with pytest.raises(ArtifactTooLarge):
        storage.stage(uuid4(), uuid4(), "large.bin", [b"1234", b"56"])
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_interrupted_upload_removes_partial_content(tmp_path):
    def chunks():
        yield b"private partial bytes"
        raise ConnectionError("client disconnected")

    storage = ArtifactStorage(tmp_path, max_bytes=100)
    with pytest.raises(ConnectionError):
        storage.stage(uuid4(), uuid4(), "private.txt", chunks())
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_async_stream_stages_and_enforces_actual_byte_count(tmp_path):
    async def chunks():
        yield b"123"
        yield b"45"

    storage = ArtifactStorage(tmp_path, max_bytes=5)
    staged = asyncio.run(storage.stage_async(uuid4(), uuid4(), "test.txt", chunks()))
    storage.publish(staged)
    with storage.open(staged.relative_path) as content:
        assert content.read() == b"12345"
    storage = ArtifactStorage(tmp_path, max_bytes=4)
    with pytest.raises(ArtifactTooLarge):
        asyncio.run(storage.stage_async(uuid4(), uuid4(), "too-large.txt", chunks()))


@pytest.mark.parametrize("operation", ["__init__", "write", "finish"])
def test_async_staging_keeps_event_loop_responsive_and_stream_bounded(
    tmp_path, monkeypatch, operation,
):
    release = threading.Event()
    storage = ArtifactStorage(tmp_path, max_bytes=100)
    original = getattr(storage_module._StagingWriter, operation)
    pulled = []

    async def run():
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()

        def stalled(writer, *args):
            assert threading.get_ident() != loop_thread
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2), "Filesystem work blocked the event loop"
            return original(writer, *args)

        monkeypatch.setattr(storage_module._StagingWriter, operation, stalled)

        async def chunks():
            for chunk in (b"first", b"second"):
                pulled.append(chunk)
                yield chunk

        task = asyncio.create_task(storage.stage_async(uuid4(), uuid4(), "file", chunks()))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            await asyncio.sleep(0.01)  # An unrelated request/heartbeat can run during disk I/O.
            assert not task.done()
            assert len(pulled) == {"__init__": 0, "write": 1, "finish": 2}[operation]
        finally:
            release.set()
        staged = await task
        assert staged.sha256 == hashlib.sha256(b"firstsecond").hexdigest()

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["__init__", "write", "finish"])
@pytest.mark.parametrize("deadline", ["asyncio", "anyio", "repeated"])
def test_async_staging_deadline_retains_worker_ownership_until_cleanup(
    tmp_path, monkeypatch, operation, deadline,
):
    release = threading.Event()
    storage = ArtifactStorage(tmp_path, max_bytes=100)
    original = getattr(storage_module._StagingWriter, operation)
    descriptors = []
    pulled = []

    async def run():
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        deadline_scope = None

        def stalled(writer, *args):
            result = original(writer, *args) if operation == "__init__" else None
            descriptors.extend([writer.descriptor, writer.directory])
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2), "Cancellation failed to preserve event-loop progress"
            # No cancellation path can close an active worker's descriptors.
            os.fstat(writer.descriptor)
            os.fstat(writer.directory)
            return result if operation == "__init__" else original(writer, *args)

        monkeypatch.setattr(storage_module._StagingWriter, operation, stalled)

        async def chunks():
            for chunk in (b"first", b"second"):
                pulled.append(chunk)
                yield chunk

        async def upload():
            nonlocal deadline_scope
            if deadline in {"asyncio", "repeated"}:
                async with asyncio.timeout(None) as deadline_scope:
                    await storage.stage_async(uuid4(), uuid4(), "file", chunks())
            else:
                with fail_after(None) as deadline_scope:
                    await storage.stage_async(uuid4(), uuid4(), "file", chunks())

        task = asyncio.create_task(upload())
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            # Start the real cancellation deadline only once the selected worker
            # operation owns its descriptors. Thread scheduling and fsync under
            # parallel test load must not cancel staging before that operation.
            _arm_staging_deadline(deadline_scope, deadline)
            await asyncio.sleep(0.06)
            if deadline == "repeated":
                task.cancel()
                await asyncio.sleep(0.01)
            assert not task.done()  # The caller still owns its bounded upload slot.
            assert len(pulled) == {"__init__": 0, "write": 1, "finish": 2}[operation]
            for descriptor in descriptors:
                os.fstat(descriptor)
        finally:
            release.set()
        expected = asyncio.CancelledError if deadline == "repeated" else TimeoutError
        with pytest.raises(expected):
            await task
        assert len(pulled) == {"__init__": 0, "write": 1, "finish": 2}[operation]

    asyncio.run(run())
    assert not list(tmp_path.rglob(".pending-*"))
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def _arm_staging_deadline(scope, deadline):
    assert scope is not None
    expires = asyncio.get_running_loop().time() + 0.03
    if deadline in {"asyncio", "repeated"}:
        scope.reschedule(expires)
    else:
        scope.deadline = expires


def test_async_staging_cancels_a_pending_client_read_and_removes_partial_bytes(tmp_path):
    storage = ArtifactStorage(tmp_path, max_bytes=100)

    async def run():
        reading = asyncio.Event()
        cancelled = asyncio.Event()

        async def chunks():
            yield b"partial private content"
            reading.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(storage.stage_async(uuid4(), uuid4(), "file", chunks()))
        await asyncio.wait_for(reading.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert cancelled.is_set()

    asyncio.run(run())
    assert not list(tmp_path.rglob(".pending-*"))


def test_atomic_replace_removes_old_content_and_supports_recovery(tmp_path):
    storage = ArtifactStorage(tmp_path, max_bytes=100)
    project, artifact = uuid4(), uuid4()
    first = storage.stage(project, artifact, "private.txt", [b"original"])
    storage.publish(first)
    second = storage.stage(project, artifact, "private.txt", [b"replacement"])
    with storage.open(first.relative_path) as opened_before_replace:
        storage.publish(second)
        assert opened_before_replace.read() == b"original"
        with storage.open(second.relative_path) as content:
            assert content.read() == b"replacement"
    storage.publish(second)
    assert list((tmp_path / str(project) / str(artifact)).iterdir()) == [
        tmp_path / second.relative_path,
    ]
    with pytest.raises(ArtifactContentUnavailable):
        storage.publish(first)
    storage.delete(second.relative_path)
    storage.delete(second.relative_path)
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_publish_detects_corruption_and_keeps_existing_file(tmp_path):
    storage = ArtifactStorage(tmp_path, max_bytes=100)
    project, artifact = uuid4(), uuid4()
    first = storage.stage(project, artifact, "private.txt", [b"original"])
    storage.publish(first)
    second = storage.stage(project, artifact, "private.txt", [b"replacement"])
    with pytest.raises(ArtifactContentUnavailable):
        storage.publish(replace(second, sha256="0" * 64))
    with storage.open(first.relative_path) as content:
        assert content.read() == b"original"
    storage.discard(second)
    storage.discard(second)


@pytest.mark.parametrize("component", ["root", "project", "artifact"])
def test_upload_cannot_traverse_symlinked_directory(tmp_path, component):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    project, artifact = uuid4(), uuid4()
    target = {"root": root, "project": root / str(project),
              "artifact": root / str(project) / str(artifact)}[component]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    storage = ArtifactStorage(root, max_bytes=100)
    with pytest.raises(OSError):
        storage.stage(project, artifact, "private.txt", [b"secret"])
    assert not list(outside.iterdir())


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_download_rejects_non_private_regular_files(tmp_path, kind):
    storage = ArtifactStorage(tmp_path / "root", max_bytes=100)
    staged = storage.stage(uuid4(), uuid4(), "private.txt", [b"secret"])
    storage.publish(staged)
    destination = storage.root / staged.relative_path
    destination.unlink()
    external = tmp_path / "external-secret"
    external.touch()
    if kind == "symlink":
        destination.symlink_to(external)
    elif kind == "hardlink":
        os.link(external, destination)
    else:
        os.mkfifo(destination)
    with pytest.raises(OSError):
        storage.open(staged.relative_path)


@pytest.mark.parametrize(
    "pointer", [
        "/etc/passwd", "../../secret", "a/b/file.txt",
        "2c3dd671-4d70-4401-8e0a-5d2e81440f46/743621ae-6a29-4819-8d3a-35b8fc952d0b/../x",
    ],
)
def test_invalid_database_pointers_cannot_access_filesystem(tmp_path, pointer):
    storage = ArtifactStorage(tmp_path, max_bytes=100)
    with pytest.raises((UnsafeArtifactPath, InvalidArtifactFilename)):
        storage.open(pointer)
    with pytest.raises((UnsafeArtifactPath, InvalidArtifactFilename)):
        storage.delete(pointer)


def test_publish_cannot_adopt_another_artifacts_stage(tmp_path):
    storage = ArtifactStorage(tmp_path, max_bytes=100)
    first = storage.stage(uuid4(), uuid4(), "one.txt", [b"one"])
    second = storage.stage(uuid4(), uuid4(), "two.txt", [b"two"])
    with pytest.raises(UnsafeArtifactPath):
        storage.publish(replace(first, temporary_path=second.temporary_path))
    assert not (tmp_path / first.relative_path).exists()


def test_declared_size_is_verified_on_publish(tmp_path):
    storage = ArtifactStorage(tmp_path, max_bytes=100)
    staged = storage.stage(uuid4(), uuid4(), "private.txt", [b"secret"])
    with pytest.raises(ArtifactContentUnavailable):
        storage.publish(replace(staged, size_bytes=staged.size_bytes + 1))


def test_empty_file_is_supported_without_inventing_mime_type(tmp_path):
    storage = ArtifactStorage(tmp_path, max_bytes=100)
    staged = storage.stage(uuid4(), uuid4(), "empty.bin", [])
    assert staged.size_bytes == 0
    assert staged.mime_type is None
    storage.publish(staged)
    with storage.open(staged.relative_path) as content:
        assert content.read() == b""


def test_storage_root_accepts_any_configured_mount_path(tmp_path):
    storage = ArtifactStorage(str(tmp_path / "arbitrary" / "mount"), max_bytes=100)
    staged = storage.stage(uuid4(), uuid4(), "file.bin", [b"\x00"])
    storage.publish(staged)
    assert isinstance(storage.root, Path)


def test_stale_cleanup_removes_only_unjournaled_abandoned_staging(tmp_path):
    storage = ArtifactStorage(tmp_path, max_bytes=100)
    abandoned = storage.stage(uuid4(), uuid4(), "orphan.txt", [b"abandoned private bytes"])
    journaled = storage.stage(uuid4(), uuid4(), "pending.txt", [b"recoverable private bytes"])
    recent = storage.stage(uuid4(), uuid4(), "new.txt", [b"new upload"])
    published = storage.stage(uuid4(), uuid4(), "current.txt", [b"keep current"])
    storage.publish(published)
    old = time.time() - 86401
    for staged in (abandoned, journaled):
        os.utime(tmp_path / staged.temporary_path, (old, old))
    os.utime(tmp_path / published.relative_path, (old, old))
    assert storage.cleanup_staging({journaled.temporary_path}) == 1
    assert not (tmp_path / abandoned.temporary_path).exists()
    assert (tmp_path / journaled.temporary_path).exists()
    assert (tmp_path / recent.temporary_path).exists()
    assert (tmp_path / published.relative_path).exists()


def test_stale_cleanup_cannot_remove_a_live_upload(tmp_path):
    storage = ArtifactStorage(tmp_path, max_bytes=100)

    def chunks():
        yield b"in progress"
        pending = next(tmp_path.rglob(".pending-*"))
        old = time.time() - 86401
        os.utime(pending, (old, old))
        assert storage.cleanup_staging(set()) == 0
        yield b" complete"

    staged = storage.stage(uuid4(), uuid4(), "active.txt", chunks())
    assert (tmp_path / staged.temporary_path).exists()


def test_stale_cleanup_does_not_follow_external_directories(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / str(uuid4())).symlink_to(outside, target_is_directory=True)
    (outside / (".pending-" + uuid4().hex)).touch()
    assert ArtifactStorage(root, 100).cleanup_staging(set(), minimum_age_seconds=0) == 0
    assert len(list(outside.iterdir())) == 1


def test_cleanup_missing_root_is_a_noop(tmp_path):
    assert ArtifactStorage(tmp_path / "missing", 100).cleanup_staging(set()) == 0
