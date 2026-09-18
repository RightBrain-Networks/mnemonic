"""Stream stable source bytes into private immutable, crash-recoverable snapshots."""

import errno
import fcntl
import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from mnemonic_api.artifact_storage import (
    ArtifactContentUnavailable,
    ArtifactStorage,
    ArtifactTooLarge,
    StagedArtifact,
    _parse_path,
)
from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.transcript_access import TranscriptAccessError, access_error
from mnemonic_api.transcript_copy_references import (
    CopyReference,
    legacy_reference,
    object_reference,
    read_reference,
    source_head,
    write_reference,
)
from mnemonic_api.transcript_objects import (
    NativeFilePin,
    NativeSnapshotFile,
    TranscriptObjects,
    object_key,
)
from mnemonic_api.transcript_relocation import resolve_source
from mnemonic_api.transcript_source_identity import require_identity
from mnemonic_api.transcript_storage import _open_source

_CHUNK_BYTES = 1024 * 1024
_TRANSIENT_ERRNOS = {errno.EAGAIN, errno.EINTR, errno.EIO, errno.ESTALE, errno.ETIMEDOUT,
                     errno.EMFILE, errno.ENFILE}


@dataclass(frozen=True)
class TranscriptCopy:
    storage_key: str
    sha256: str
    size_bytes: int
    source_path: str | None = field(default=None, compare=False)
    source_modified_at: datetime | None = field(default=None, compare=False)


@dataclass(frozen=True)
class TranscriptCopyPin:
    sha256: str
    size_bytes: int

    def require(self, sha256: str, size_bytes: int) -> None:
        if (sha256, size_bytes) != (self.sha256, self.size_bytes):
            raise ExtractionError("transcript_recovery_content_changed")


@dataclass(frozen=True)
class TranscriptStage(StagedArtifact):
    source_path: str
    source_modified_at: datetime | None
    head_key: str
    base_sha256: str | None


def _transient(error: BaseException) -> bool:
    return ((isinstance(error, OSError) and error.errno in _TRANSIENT_ERRNOS)
            or (isinstance(error, ExtractionError) and error.retryable))


def _source_chunks(source: str, roots: list[Path], maximum: int,
                   identity: dict | None = None,
                   observation: dict | None = None) -> Iterator[bytes]:
    try:
        yield from _read_source_chunks(source, roots, maximum, identity, observation)
    except OSError as error:
        raise access_error(error, source) from None


def _read_source_chunks(source: str, roots: list[Path], maximum: int,
                        identity: dict | None = None,
                        observation: dict | None = None) -> Iterator[bytes]:
    descriptor = _open_source(source, roots)
    with os.fdopen(descriptor, "rb") as content:
        before = os.fstat(content.fileno())
        require_identity(content.fileno(), source, identity)
        if not stat.S_ISREG(before.st_mode):
            raise ExtractionError("transcript_not_regular_file")
        if before.st_size > maximum:
            raise ExtractionError("transcript_too_large")
        copied = 0
        while chunk := content.read(min(_CHUNK_BYTES, maximum + 1 - copied)):
            copied += len(chunk)
            if copied > maximum:
                raise ExtractionError("transcript_too_large")
            yield chunk
        after = os.fstat(content.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ) or copied != before.st_size:
            raise ExtractionError("transcript_content_changed", retryable=True)
        if observation is not None:
            observation["source_modified_at"] = datetime.fromtimestamp(before.st_mtime, UTC)


class TranscriptStorage(ArtifactStorage):
    """Keep immutable logical snapshots and crash receipts while sharing their bytes."""

    @staticmethod
    def key(transcript_id: UUID, snapshot_id: UUID) -> str:
        return f"{transcript_id}/{snapshot_id}/transcript.jsonl"

    @staticmethod
    def receipt_key(transcript_id: UUID, snapshot_id: UUID) -> str:
        return f"{transcript_id}/{snapshot_id}/capture.json"

    @contextmanager
    def maintenance_lock(self, *, exclusive: bool = False):
        """Copy publication holds a shared lock through its database commit."""
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self._secure_directory(descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            os.close(descriptor)

    def open(self, relative_path: str) -> BinaryIO:
        if relative_path.endswith("/snapshot.bin"):
            return TranscriptObjects(self).materialize(relative_path)
        content = super().open(relative_path)
        try:
            pin = NativeFilePin.of(relative_path, content.fileno())
            reference = legacy_reference(self, relative_path, content)
            if reference is None:
                return content
            if NativeFilePin.of(relative_path, content.fileno()) != pin:
                raise ExtractionError('transcript_copy_integrity_failed')
        except BaseException:
            content.close()
            raise
        content.close()
        native = self._open_reference(reference)
        if isinstance(native, NativeSnapshotFile):
            native.native_pins = (*native.native_pins, pin)
        return native

    def _open_reference(self, reference: CopyReference) -> BinaryIO:
        content = TranscriptObjects(self).materialize(object_key(reference.sha256))
        if os.fstat(content.fileno()).st_size != reference.size_bytes:
            content.close()
            raise ExtractionError("transcript_copy_integrity_failed")
        return content

    def retained(self, transcript_id: UUID, snapshot_id: UUID) -> TranscriptCopy | None:
        reference = read_reference(self, self.receipt_key(transcript_id, snapshot_id))
        if reference is not None:
            with self._open_reference(reference):
                pass
            return TranscriptCopy(object_key(reference.sha256), reference.sha256,
                reference.size_bytes, reference.source_path, reference.source_modified_at)
        try:
            return self.describe(self.key(transcript_id, snapshot_id))
        except FileNotFoundError:
            return None

    def describe(self, storage_key: str) -> TranscriptCopy:
        digest = hashlib.sha256()
        size = 0
        with self.open(storage_key) as content:
            while chunk := content.read(_CHUNK_BYTES):
                size += len(chunk)
                digest.update(chunk)
        return TranscriptCopy(storage_key, digest.hexdigest(), size)

    def _publish_once(self, staged: TranscriptStage,
                      expected: TranscriptCopyPin | None = None) -> TranscriptCopy:
        if expected is not None:
            expected.require(staged.sha256, staged.size_bytes)
        identity, snapshot, _ = staged.relative_path.split("/")
        with self._directory(identity, snapshot) as directory:
            # Serialize competing claims of this same persisted snapshot. No DB
            # lock spans IO. rename is atomic; flock is released after a crash.
            fcntl.flock(directory, fcntl.LOCK_EX)
            try:
                retained = self.retained(UUID(identity), UUID(snapshot))
                if retained is None:
                    item = TranscriptObjects(self).publish(staged, staged.base_sha256)
                    write_reference(self, staged.head_key, object_reference(item))
                    reference = CopyReference(item.sha256, item.size_bytes, staged.source_path,
                                              staged.source_modified_at)
                    receipt = self.receipt_key(UUID(identity), UUID(snapshot))
                    write_reference(self, receipt, reference)
                    retained = TranscriptCopy(item.key, item.sha256, item.size_bytes,
                                              staged.source_path, staged.source_modified_at)
                elif expected is not None:
                    expected.require(retained.sha256, retained.size_bytes)
                os.fsync(directory)
                # Once the durable receipt exists, no recovery needs the full
                # staging file. Remove it before reporting publication success.
                self.discard(staged)
                return retained
            finally:
                fcntl.flock(directory, fcntl.LOCK_UN)

    def _capture_once(self, transcript_id: UUID, snapshot_id: UUID,
                      source: str, roots: list[Path],
                      expected: TranscriptCopyPin | None = None,
                      source_identity: dict | None = None) -> TranscriptCopy:
        retained = self.retained(transcript_id, snapshot_id)
        if retained is not None:
            if expected is not None:
                expected.require(retained.sha256, retained.size_bytes)
            with self._directory(str(transcript_id), str(snapshot_id)) as directory:
                os.fsync(directory)
            return retained
        source = resolve_source(source, roots, source_identity)
        head_key = source_head(source)
        first, second, _ = _parse_path(head_key)
        # Serialize one source before staging: N simultaneous enrollments must
        # not require N full-size temporary files or fork the shared prefix.
        with self._directory(first, second, create=True) as directory:
            fcntl.flock(directory, fcntl.LOCK_EX)
            try:
                return self._capture_source(transcript_id, snapshot_id, source, roots,
                                            expected, source_identity, head_key)
            finally:
                fcntl.flock(directory, fcntl.LOCK_UN)

    def _capture_source(self, transcript_id: UUID, snapshot_id: UUID, source: str,
                        roots: list[Path], expected: TranscriptCopyPin | None,
                        source_identity: dict | None, head_key: str) -> TranscriptCopy:
        head = read_reference(self, head_key)
        observation: dict = {}
        staged = self.stage(transcript_id, snapshot_id, "transcript.jsonl",
                            _source_chunks(source, roots, self.max_bytes, source_identity,
                                           observation))
        staged = TranscriptStage(staged.relative_path, staged.temporary_path, staged.sha256,
            staged.size_bytes, staged.mime_type, source, observation.get("source_modified_at"),
            head_key, head.sha256 if head else None)
        try:
            copied = self._publish_once(staged, expected)
            if (copied.sha256, copied.size_bytes) == (staged.sha256, staged.size_bytes):
                copied = replace(copied, source_path=source,
                                 source_modified_at=observation.get("source_modified_at"))
            return copied
        finally:
            self.discard(staged)

    def capture(self, transcript_id: UUID, snapshot_id: UUID,
                source: str, roots: list[Path], *,
                expected: TranscriptCopyPin | None = None,
                source_identity: dict | None = None) -> TranscriptCopy:
        if expected is not None:
            source_identity = None  # Audited full-file pins never permit automatic relocation.
        try:
            for attempt in Retrying(stop=stop_after_attempt(3),
                                    wait=wait_random_exponential(multiplier=0.1, max=1),
                                    retry=retry_if_exception(_transient), reraise=True):
                with attempt:
                    return self._capture_once(transcript_id, snapshot_id, source, roots, expected,
                                              source_identity)
        except ArtifactTooLarge as error:
            raise ExtractionError("transcript_too_large") from error
        except ArtifactContentUnavailable as error:
            raise TranscriptAccessError("transcript_copy_unavailable", str(self.root),
                                        operation="write_storage") from error
        except OSError as error:
            raise access_error(error, str(self.root), operation="write_storage") from None
        raise AssertionError("Copy retry policy completed without a disposition")

    @contextmanager
    def open_copy(self, copy: TranscriptCopy) -> Iterator[BinaryIO]:
        """Read verified retained bytes independently of the current capture limit."""
        try:
            content = self.open(copy.storage_key)
        except (OSError, ValueError) as error:
            raise ExtractionError("transcript_copy_unavailable", retryable=True) from error
        with content:
            fcntl.flock(content.fileno(), fcntl.LOCK_SH)
            before = os.fstat(content.fileno())
            if before.st_size != copy.size_bytes:
                raise ExtractionError("transcript_copy_integrity_failed")
            digest = hashlib.sha256()
            while chunk := content.read(_CHUNK_BYTES):
                digest.update(chunk)
            if digest.hexdigest() != copy.sha256:
                raise ExtractionError("transcript_copy_integrity_failed")
            content.seek(0)
            yield content
            after = os.fstat(content.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ):
                raise ExtractionError("transcript_copy_integrity_failed")
            TranscriptObjects(self).verify_pins(content)

    def read_copy(self, copy: TranscriptCopy) -> bytes:
        with self.open_copy(copy) as content:
            return content.read()
