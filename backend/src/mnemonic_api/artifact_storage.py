"""Bounded, private filesystem storage for untrusted artifact bytes.

The caller owns the database operation journal and per-artifact serialization.
Persist a staged descriptor before publishing it, then finalize its metadata.
Publishing and deletion are idempotent so that committed intents can be recovered
after a process failure without retaining previous versions of file content.
"""

import asyncio
import fcntl
import hashlib
import json
import os
import re
import stat
import time
import unicodedata
from collections.abc import AsyncIterable, Iterable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import BinaryIO
from uuid import UUID, uuid4

from anyio import CancelScope

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_STAGING_NAME = re.compile(r"\.pending-[0-9a-f]{32}\Z")
_RESERVED_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)
_FORBIDDEN_CHARACTERS = frozenset('<>:"/\\|?*%\u2044\u2215\uff0f\uff3c')
_SNIFF_BYTES = 16384


class InvalidArtifactFilename(ValueError):
    """The supplied name is not a safe, unambiguous portable basename."""


class ArtifactTooLarge(ValueError):
    """The configured maximum artifact size was exceeded."""


class UnsafeArtifactPath(ValueError):
    """An internal storage pointer is malformed or unsafe."""


class ArtifactContentUnavailable(OSError):
    """Artifact content is missing, unsafe, or fails its recorded integrity check."""


class ArtifactStorageOwnerMismatch(ArtifactContentUnavailable):
    """A storage directory or content file is not owned by the effective API user."""


def validate_filename(filename: str) -> str:
    """Preserve safe original names verbatim; reject unsafe names without rewriting."""
    if not isinstance(filename, str) or not filename or filename != filename.strip():
        raise InvalidArtifactFilename("Provide a filename without surrounding whitespace.")
    if filename in {".", ".."} or filename.endswith(".") or filename.startswith("-"):
        raise InvalidArtifactFilename("The filename has an unsafe leading or trailing character.")
    if filename.startswith(".pending-") or _RESERVED_NAME.match(filename):
        raise InvalidArtifactFilename("The filename is reserved.")
    if any(char in _FORBIDDEN_CHARACTERS or unicodedata.category(char)[0] == "C"
           for char in filename):
        raise InvalidArtifactFilename("The filename contains unsafe characters.")
    if len(filename.encode("utf-8")) > 240:
        raise InvalidArtifactFilename("The UTF-8 filename must not exceed 240 bytes.")
    return filename


def _text_mime(sample: bytes, *, complete: bool) -> str | None:
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text or any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        return None
    if complete:
        try:
            json.loads(text)
            return "application/json"
        except (ValueError, RecursionError):
            pass
    if text.lstrip().lower().startswith(("<!doctype html", "<html")):
        return "text/html"
    return "text/plain"


def detect_mime_type(sample: bytes, *, complete: bool = True) -> str | None:
    """Use recognizable bytes, never an untrusted upload header or file extension."""
    signatures = (
        (b"%PDF-", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"II\x2a\x00", "image/tiff"),
        (b"MM\x00\x2a", "image/tiff"),
        (b"PK\x03\x04", "application/zip"),
        (b"PK\x05\x06", "application/zip"),
        (b"\x1f\x8b\x08", "application/gzip"),
        (b"\x7fELF", "application/x-elf"),
        (b"fLaC", "audio/flac"),
        (b"OggS\x00", "application/ogg"),
    )
    for signature, mime_type in signatures:
        if sample.startswith(signature):
            return mime_type
    if sample.startswith(b"RIFF") and sample[8:12] == b"WEBP":
        return "image/webp"
    if sample.startswith(b"RIFF") and sample[8:12] == b"WAVE":
        return "audio/wav"
    return _text_mime(sample, complete=complete)


@dataclass(frozen=True)
class StagedArtifact:
    relative_path: str
    temporary_path: str
    sha256: str
    size_bytes: int
    mime_type: str | None


def _parse_path(relative_path: str, *, temporary: bool = False) -> tuple[str, str, str]:
    parts = relative_path.split("/")
    if len(parts) != 3:
        raise UnsafeArtifactPath("Artifact storage pointers require two UUID directories.")
    for part in parts[:2]:
        try:
            valid = str(UUID(part)) == part
        except ValueError:
            valid = False
        if not valid:
            raise UnsafeArtifactPath("Artifact storage directories must be canonical UUIDs.")
    if temporary:
        if not _STAGING_NAME.fullmatch(parts[2]):
            raise UnsafeArtifactPath("Invalid artifact staging pointer.")
    else:
        validate_filename(parts[2])
    return parts[0], parts[1], parts[2]


def _verify_regular(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ArtifactContentUnavailable("Artifact content is not a private regular file.")
    if info.st_uid != os.geteuid():
        raise ArtifactStorageOwnerMismatch("Artifact content has an unexpected owner.")


@contextmanager
def _read_file(directory: int, filename: str) -> Iterator[BinaryIO]:
    descriptor = os.open(filename, _READ_FLAGS, dir_fd=directory)
    try:
        _verify_regular(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as content:
            yield content
    finally:
        os.close(descriptor)


class _StagingWriter:
    def __init__(self, directory: int, filename: str, max_bytes: int) -> None:
        self.directory = directory
        self.filename = filename
        self.max_bytes = max_bytes
        self.size_bytes = 0
        self.digest = hashlib.sha256()
        self.sample = bytearray()
        self.descriptor = os.open(
            filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600, dir_fd=directory,
        )
        fcntl.flock(self.descriptor, fcntl.LOCK_EX)

    def write(self, chunk: bytes) -> None:
        if not isinstance(chunk, bytes):
            raise TypeError("Artifact streams must yield bytes.")
        if self.size_bytes + len(chunk) > self.max_bytes:
            raise ArtifactTooLarge("Artifact content exceeds the configured byte limit.")
        remaining = memoryview(chunk)
        while remaining:
            written = os.write(self.descriptor, remaining)
            if written <= 0:
                raise OSError("Artifact content could not be written.")
            remaining = remaining[written:]
        self.digest.update(chunk)
        self.size_bytes += len(chunk)
        self.sample.extend(chunk[:max(0, _SNIFF_BYTES - len(self.sample))])

    def finish(self, relative_path: str, temporary_path: str) -> StagedArtifact:
        os.fsync(self.descriptor)
        os.fsync(self.directory)
        return StagedArtifact(
            relative_path=relative_path, temporary_path=temporary_path,
            sha256=self.digest.hexdigest(), size_bytes=self.size_bytes,
            mime_type=detect_mime_type(
                bytes(self.sample), complete=self.size_bytes <= _SNIFF_BYTES,
            ),
        )


class _AsyncChunks:
    """Let one filesystem worker pull one async chunk at a time, without buffering ahead."""

    def __init__(self, chunks: AsyncIterable[bytes]) -> None:
        self.iterator = aiter(chunks)
        self.loop = asyncio.get_running_loop()
        self.lock = Lock()
        self.pending: Future[bytes] | None = None
        self.cancelled = False

    async def _next(self) -> bytes:
        return await anext(self.iterator)

    def __iter__(self) -> Iterator[bytes]:
        while True:
            with self.lock:
                if self.cancelled:
                    raise asyncio.CancelledError
                pending = asyncio.run_coroutine_threadsafe(self._next(), self.loop)
                self.pending = pending
            try:
                yield pending.result()
            except StopAsyncIteration:
                return

    def cancel(self) -> None:
        with self.lock:
            self.cancelled = True
            if self.pending is not None:
                self.pending.cancel()


async def _finish_cancelled_stage(
    storage: ArtifactStorage, worker: asyncio.Task[StagedArtifact],
) -> None:
    try:
        staged = await worker
    except (Exception, asyncio.CancelledError):
        # The worker's staging context already removed its partial content.
        return
    # Cancellation can race with successful finish, before its result reaches
    # the caller. No journal can own this descriptor, so discard that stage too.
    await asyncio.to_thread(storage.discard, staged)


class ArtifactStorage:
    """Store files only below a trusted configured root, through no-follow dir FDs."""

    def __init__(self, root: Path | str, max_bytes: int) -> None:
        if isinstance(max_bytes, bool) or max_bytes < 1:
            raise ValueError("The artifact byte limit must be positive.")
        self.root = Path(root)
        self.max_bytes = max_bytes

    @contextmanager
    def _directory(self, project: str, artifact: str, *, create: bool = False) -> Iterator[int]:
        if create:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptors = [os.open(self.root, _DIRECTORY_FLAGS)]
        try:
            self._secure_directory(descriptors[-1])
            for component in (project, artifact):
                if create:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=descriptors[-1])
                        os.fsync(descriptors[-1])
                    except FileExistsError:
                        pass
                descriptors.append(os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptors[-1]))
                self._secure_directory(descriptors[-1])
            yield descriptors[-1]
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @staticmethod
    def _secure_directory(descriptor: int) -> None:
        if os.fstat(descriptor).st_uid != os.geteuid():
            raise ArtifactStorageOwnerMismatch("Artifact storage has an unexpected owner.")
        os.fchmod(descriptor, 0o700)

    @contextmanager
    def _writer(
        self, project_id: UUID | str, artifact_id: UUID | str, filename: str,
    ) -> Iterator[tuple[_StagingWriter, str, str]]:
        validate_filename(filename)
        project, artifact = str(UUID(str(project_id))), str(UUID(str(artifact_id)))
        temporary_name = ".pending-" + uuid4().hex
        relative_path = f"{project}/{artifact}/{filename}"
        temporary_path = f"{project}/{artifact}/{temporary_name}"
        with self._directory(project, artifact, create=True) as directory:
            writer = _StagingWriter(directory, temporary_name, self.max_bytes)
            try:
                yield writer, relative_path, temporary_path
            except BaseException:
                os.unlink(temporary_name, dir_fd=directory)
                os.fsync(directory)
                raise
            finally:
                os.close(writer.descriptor)

    def stage(
        self, project_id: UUID | str, artifact_id: UUID | str, filename: str,
        chunks: Iterable[bytes],
    ) -> StagedArtifact:
        with self._writer(project_id, artifact_id, filename) as (writer, path, temporary):
            for chunk in chunks:
                writer.write(chunk)
            return writer.finish(path, temporary)

    async def stage_async(
        self, project_id: UUID | str, artifact_id: UUID | str, filename: str,
        chunks: AsyncIterable[bytes],
    ) -> StagedArtifact:
        stream = _AsyncChunks(chunks)
        worker = asyncio.create_task(asyncio.to_thread(
            self.stage, project_id, artifact_id, filename, stream,
        ))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            stream.cancel()
            cleanup = asyncio.create_task(_finish_cancelled_stage(self, worker))
            # Keep descriptors and the caller's upload slot owned until the
            # worker exits. Shield against both AnyIO scopes and repeated raw
            # asyncio cancellation; neither can interrupt an OS syscall safely.
            with CancelScope(shield=True):
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        pass
            cleanup.result()
            raise

    @staticmethod
    def _verify_content(directory: int, filename: str, staged: StagedArtifact) -> None:
        with _read_file(directory, filename) as content:
            size_bytes = os.fstat(content.fileno()).st_size
            if size_bytes != staged.size_bytes:
                raise ArtifactContentUnavailable("Artifact content failed its integrity check.")
            digest = hashlib.sha256()
            while chunk := content.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != staged.sha256:
            raise ArtifactContentUnavailable("Artifact content failed its integrity check.")

    def publish(self, staged: StagedArtifact) -> None:
        """Durably replace content; repeated recovery verifies already-published bytes."""
        project, artifact, filename = _parse_path(staged.relative_path)
        temporary_project, temporary_artifact, temporary = _parse_path(
            staged.temporary_path, temporary=True,
        )
        if (project, artifact) != (temporary_project, temporary_artifact):
            raise UnsafeArtifactPath("Staged content must belong to the target artifact.")
        with self._directory(project, artifact) as directory:
            try:
                self._verify_content(directory, temporary, staged)
            except FileNotFoundError:
                self._verify_content(directory, filename, staged)
            else:
                os.replace(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)

    def discard(self, staged: StagedArtifact) -> None:
        project, artifact, temporary = _parse_path(staged.temporary_path, temporary=True)
        self._unlink(project, artifact, temporary)

    def _unlink(self, project: str, artifact: str, filename: str) -> None:
        try:
            with self._directory(project, artifact) as directory:
                try:
                    os.unlink(filename, dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
        except FileNotFoundError:
            pass

    def delete(self, relative_path: str) -> None:
        """Unlink current content durably without moving it to a trash/history folder."""
        self._unlink(*_parse_path(relative_path))

    def open(self, relative_path: str) -> BinaryIO:
        """Return an owned open handle, stable across concurrent atomic replacement."""
        project, artifact, filename = _parse_path(relative_path)
        with self._directory(project, artifact) as directory:
            descriptor = os.open(filename, _READ_FLAGS, dir_fd=directory)
            try:
                _verify_regular(descriptor)
                return os.fdopen(descriptor, "rb")
            except BaseException:
                os.close(descriptor)
                raise

    def cleanup_staging(
        self, active_paths: set[str], minimum_age_seconds: float = 86400,
    ) -> int:
        """Remove abandoned staging bytes, retaining journaled or actively written files.

        Run after journal recovery and periodically thereafter. The age threshold
        must exceed the request timeout, including the stage-to-journal window.
        Final artifact content is never selected by this maintenance operation.
        """
        if minimum_age_seconds < 0:
            raise ValueError("Staging cleanup age cannot be negative.")
        cutoff = time.time() - minimum_age_seconds
        removed = 0
        try:
            root = os.open(self.root, _DIRECTORY_FLAGS)
        except FileNotFoundError:
            return 0
        try:
            for project in self._uuid_directories(root):
                project_descriptor = os.open(project, _DIRECTORY_FLAGS, dir_fd=root)
                try:
                    for artifact in self._uuid_directories(project_descriptor):
                        with self._directory(project, artifact) as directory:
                            prefix = f"{project}/{artifact}/"
                            removed += self._cleanup_directory(
                                directory, prefix, active_paths, cutoff,
                            )
                finally:
                    os.close(project_descriptor)
        finally:
            os.close(root)
        return removed

    @staticmethod
    def _uuid_directories(directory: int) -> Iterator[str]:
        for name in os.listdir(directory):
            try:
                canonical = str(UUID(name)) == name
            except ValueError:
                continue
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if canonical and stat.S_ISDIR(info.st_mode):
                yield name

    @classmethod
    def _cleanup_directory(
        cls, directory: int, prefix: str, active_paths: set[str], cutoff: float,
    ) -> int:
        removed = 0
        for filename in os.listdir(directory):
            if _STAGING_NAME.fullmatch(filename) and prefix + filename not in active_paths:
                removed += cls._cleanup_file(directory, filename, cutoff)
        if removed:
            os.fsync(directory)
        return removed

    @staticmethod
    def _cleanup_file(directory: int, filename: str, cutoff: float) -> int:
        try:
            with _read_file(directory, filename) as content:
                fcntl.flock(content.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                if os.fstat(content.fileno()).st_mtime >= cutoff:
                    return 0
                os.unlink(filename, dir_fd=directory)
                return 1
        except (OSError, ArtifactContentUnavailable):
            return 0
