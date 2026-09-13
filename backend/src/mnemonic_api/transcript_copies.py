"""Stream stable source bytes into private immutable, crash-recoverable snapshots."""

import errno
import fcntl
import hashlib
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from mnemonic_api.artifact_storage import (
    ArtifactContentUnavailable,
    ArtifactStorage,
    ArtifactTooLarge,
    StagedArtifact,
)
from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.transcript_storage import _open_source

_CHUNK_BYTES = 1024 * 1024
_TRANSIENT_ERRNOS = {errno.EAGAIN, errno.EINTR, errno.EIO, errno.ESTALE, errno.ETIMEDOUT,
                     errno.ENOENT, errno.ENOSPC, errno.EDQUOT, errno.EMFILE, errno.ENFILE}


@dataclass(frozen=True)
class TranscriptCopy:
    storage_key: str
    sha256: str
    size_bytes: int


def _transient(error: BaseException) -> bool:
    return ((isinstance(error, OSError) and error.errno in _TRANSIENT_ERRNOS)
            or (isinstance(error, ExtractionError) and error.retryable))


def _source_chunks(source: str, roots: list[Path], maximum: int) -> Iterator[bytes]:
    descriptor = _open_source(source, roots)
    with os.fdopen(descriptor, "rb") as content:
        before = os.fstat(content.fileno())
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


class TranscriptStorage(ArtifactStorage):
    """Reuse private no-follow storage, but never replace a completed snapshot.

    The snapshot UUID is persisted before copying and survives retries/rebuilds.
    A worker that dies after the rename but before its DB commit is recovered by
    inspecting this exact file, even when the original source has disappeared.
    A new enrollment chooses a new snapshot UUID. Final files are immutable;
    stale workers can only leave an unreferenced file, never overwrite a new one.
    """

    @staticmethod
    def key(transcript_id: UUID, snapshot_id: UUID) -> str:
        return f"{transcript_id}/{snapshot_id}/transcript.jsonl"

    def describe(self, storage_key: str) -> TranscriptCopy:
        digest = hashlib.sha256()
        size = 0
        with self.open(storage_key) as content:
            while chunk := content.read(_CHUNK_BYTES):
                size += len(chunk)
                if size > self.max_bytes:
                    raise ExtractionError("transcript_too_large")
                digest.update(chunk)
        return TranscriptCopy(storage_key, digest.hexdigest(), size)

    def _publish_once(self, staged: StagedArtifact) -> TranscriptCopy:
        identity, snapshot, filename = staged.relative_path.split("/")
        temporary = staged.temporary_path.rsplit("/", 1)[1]
        with self._directory(identity, snapshot) as directory:
            # Serialize competing claims of this same persisted snapshot. No DB
            # lock spans IO. rename is atomic; flock is released after a crash.
            fcntl.flock(directory, fcntl.LOCK_EX)
            try:
                try:
                    retained = self.describe(staged.relative_path)
                except FileNotFoundError:
                    os.rename(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                    return TranscriptCopy(staged.relative_path, staged.sha256, staged.size_bytes)
                os.fsync(directory)
                return retained
            finally:
                fcntl.flock(directory, fcntl.LOCK_UN)

    def _capture_once(self, transcript_id: UUID, snapshot_id: UUID,
                      source: str, roots: list[Path]) -> TranscriptCopy:
        key = self.key(transcript_id, snapshot_id)
        try:
            retained = self.describe(key)
        except FileNotFoundError:
            pass
        else:
            with self._directory(str(transcript_id), str(snapshot_id)) as directory:
                os.fsync(directory)
            return retained
        staged = self.stage(transcript_id, snapshot_id, "transcript.jsonl",
                            _source_chunks(source, roots, self.max_bytes))
        try:
            return self._publish_once(staged)
        finally:
            self.discard(staged)

    def capture(self, transcript_id: UUID, snapshot_id: UUID,
                source: str, roots: list[Path]) -> TranscriptCopy:
        try:
            for attempt in Retrying(stop=stop_after_attempt(3),
                                    wait=wait_random_exponential(multiplier=0.1, max=1),
                                    retry=retry_if_exception(_transient), reraise=True):
                with attempt:
                    return self._capture_once(transcript_id, snapshot_id, source, roots)
        except ArtifactTooLarge as error:
            raise ExtractionError("transcript_too_large") from error
        except ArtifactContentUnavailable as error:
            raise ExtractionError("transcript_copy_unavailable") from error
        except OSError as error:
            retryable = _transient(error) or error.errno in {errno.EACCES, errno.EPERM}
            raise ExtractionError("transcript_io_error", retryable=retryable) from error
        raise AssertionError("Copy retry policy completed without a disposition")

    def read_copy(self, copy: TranscriptCopy) -> bytes:
        try:
            with self.open(copy.storage_key) as content:
                data = content.read(self.max_bytes + 1)
        except (OSError, ValueError) as error:
            raise ExtractionError("transcript_copy_unavailable", retryable=True) from error
        if len(data) > self.max_bytes:
            raise ExtractionError("transcript_too_large")
        if len(data) != copy.size_bytes or hashlib.sha256(data).hexdigest() != copy.sha256:
            raise ExtractionError("transcript_copy_integrity_failed")
        return data
