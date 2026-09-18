"""Content-addressed native snapshots with immutable, verified prefix sharing.

An object stores a small header and only bytes after its base snapshot. The
logical snapshot is still the exact original file, identified by its full SHA-256.
Readers materialize a private temporary file so native adapters retain ordinary
seek/read semantics without keeping the conversation in memory.
"""

import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from io import BufferedRandom
from tempfile import TemporaryFile
from typing import BinaryIO
from uuid import UUID

from mnemonic_api.artifact_storage import ArtifactStorage, StagedArtifact, _parse_path, _read_file
from mnemonic_api.artifact_tika import ExtractionError

CHUNK_BYTES = 1024 * 1024
MAX_NATIVE_BYTES = 1024 * 1024 * 1024
OBJECT_MAGIC = b"\x00mnemonic-transcript-object-v1\n"
REFERENCE_MAGIC = b"\x00mnemonic-transcript-reference-v1\n"
MAX_HEADER_BYTES = 8192
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def require_digest(value: object) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ExtractionError("transcript_copy_integrity_failed")
    return value


def digest_directories(digest: str) -> tuple[str, str]:
    require_digest(digest)
    return str(UUID(digest[:32])), str(UUID(digest[32:]))


def object_key(digest: str) -> str:
    first, second = digest_directories(digest)
    return f"{first}/{second}/snapshot.bin"


def key_digest(key: str) -> str:
    try:
        first, second, filename = key.split("/")
        digest = UUID(first).hex + UUID(second).hex
    except (ValueError, AttributeError):
        raise ExtractionError("transcript_copy_integrity_failed") from None
    if filename != "snapshot.bin" or object_key(digest) != key:
        raise ExtractionError("transcript_copy_integrity_failed")
    return digest


def require_size(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_NATIVE_BYTES:
        raise ExtractionError("transcript_copy_integrity_failed")
    return value


def read_document(content: BinaryIO, magic: bytes) -> dict:
    if content.read(len(magic)) != magic:
        raise ExtractionError("transcript_copy_integrity_failed")
    header = content.readline(MAX_HEADER_BYTES + 1)
    if len(header) > MAX_HEADER_BYTES or not header.endswith(b"\n"):
        raise ExtractionError("transcript_copy_integrity_failed")
    try:
        value = json.loads(header)
    except (ValueError, RecursionError):
        raise ExtractionError("transcript_copy_integrity_failed") from None
    if not isinstance(value, dict):
        raise ExtractionError("transcript_copy_integrity_failed")
    return value


def document_bytes(value: dict, magic: bytes) -> bytes:
    header = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if len(header) > MAX_HEADER_BYTES:
        raise ExtractionError("transcript_copy_integrity_failed")
    return magic + header


@dataclass(frozen=True)
class NativeObject:
    sha256: str
    size_bytes: int
    base_sha256: str | None = None
    base_size_bytes: int = 0

    @property
    def key(self) -> str:
        return object_key(self.sha256)

    def header(self) -> bytes:
        return document_bytes({"sha256": self.sha256, "size_bytes": self.size_bytes,
            "base_sha256": self.base_sha256, "base_size_bytes": self.base_size_bytes}, OBJECT_MAGIC)

    @classmethod
    def read(cls, source: BinaryIO, digest: str) -> NativeObject:
        value = read_document(source, OBJECT_MAGIC)
        if set(value) != {"sha256", "size_bytes", "base_sha256", "base_size_bytes"}:
            raise ExtractionError("transcript_copy_integrity_failed")
        result = cls(require_digest(value["sha256"]), require_size(value["size_bytes"]),
            None if value["base_sha256"] is None else require_digest(value["base_sha256"]),
            require_size(value["base_size_bytes"]))
        if (result.sha256 != digest
                or (result.base_sha256 is None and result.base_size_bytes != 0)
                or (result.base_sha256 is not None
                    and not 0 < result.base_size_bytes < result.size_bytes)):
            raise ExtractionError("transcript_copy_integrity_failed")
        return result


@dataclass(frozen=True)
class NativeFilePin:
    key: str
    identity: tuple[int, int, int, int, int]

    @classmethod
    def of(cls, key: str, descriptor: int) -> NativeFilePin:
        info = os.fstat(descriptor)
        return cls(key, (info.st_dev, info.st_ino, info.st_size,
                         info.st_mtime_ns, info.st_ctime_ns))


class NativeSnapshotFile(BufferedRandom):
    def __init__(self) -> None:
        temporary = TemporaryFile(mode='w+b', prefix='mnemonic-transcript-native-')
        super().__init__(temporary.detach())
        self.native_pins: tuple[NativeFilePin, ...] = ()


class TranscriptObjects:
    def __init__(self, storage: ArtifactStorage) -> None:
        self.storage = storage

    def _raw_open(self, key: str) -> BinaryIO:
        return ArtifactStorage.open(self.storage, key)

    @contextmanager
    def _open_stage(self, staged: StagedArtifact):
        first, second, filename = _parse_path(staged.temporary_path, temporary=True)
        with self.storage._directory(first, second) as directory:
            with _read_file(directory, filename) as source:
                yield source

    def _chain(self, digest: str) -> list[NativeObject]:
        chain: list[NativeObject] = []
        expected_size: int | None = None
        while True:
            with self._raw_open(object_key(digest)) as source:
                item = NativeObject.read(source, digest)
            if expected_size is not None and item.size_bytes != expected_size:
                raise ExtractionError("transcript_copy_integrity_failed")
            chain.append(item)
            if item.base_sha256 is None:
                return list(reversed(chain))
            # Sizes decrease strictly, so a cycle cannot be followed indefinitely.
            digest, expected_size = item.base_sha256, item.base_size_bytes

    def materialize(self, key: str) -> BinaryIO:
        result = NativeSnapshotFile()
        try:
            digest = hashlib.sha256()
            pins = []
            for item in self._chain(key_digest(key)):
                pins.append(self._append_verified(result, digest, item))
            result.native_pins = tuple(pins)
            self.verify_pins(result)
            result.seek(0)
            return result
        except BaseException:
            result.close()
            raise

    def _append_verified(self, target: BinaryIO, digest, item: NativeObject) -> NativeFilePin:
        if target.tell() != item.base_size_bytes:
            raise ExtractionError("transcript_copy_integrity_failed")
        with self._raw_open(item.key) as source:
            before = os.fstat(source.fileno())
            if NativeObject.read(source, item.sha256) != item:
                raise ExtractionError("transcript_copy_integrity_failed")
            expected = source.tell() + item.size_bytes - item.base_size_bytes
            if before.st_size != expected:
                raise ExtractionError("transcript_copy_integrity_failed")
            while chunk := source.read(CHUNK_BYTES):
                if target.tell() + len(chunk) > item.size_bytes:
                    raise ExtractionError("transcript_copy_integrity_failed")
                target.write(chunk)
                digest.update(chunk)
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ):
                raise ExtractionError("transcript_copy_integrity_failed")
            pin = NativeFilePin.of(item.key, source.fileno())
        if target.tell() != item.size_bytes or digest.hexdigest() != item.sha256:
            raise ExtractionError("transcript_copy_integrity_failed")
        return pin

    def verify_pins(self, content: BinaryIO) -> None:
        for pin in content.native_pins if isinstance(content, NativeSnapshotFile) else ():
            try:
                with self._raw_open(pin.key) as source:
                    if NativeFilePin.of(pin.key, source.fileno()) != pin:
                        raise ExtractionError('transcript_copy_integrity_failed')
            except OSError:
                raise ExtractionError('transcript_copy_integrity_failed') from None

    def describe(self, digest: str) -> NativeObject:
        with self.materialize(object_key(digest)) as source:
            return NativeObject(digest, os.fstat(source.fileno()).st_size)

    def contains(self, digest: str) -> bool:
        try:
            source = self._raw_open(object_key(digest))
        except FileNotFoundError:
            return False
        source.close()
        return True

    def prefix(self, staged: StagedArtifact, base_digest: str | None) -> NativeObject | None:
        if base_digest is None:
            return None
        with self.materialize(object_key(base_digest)) as base:
            size = os.fstat(base.fileno()).st_size
            if not 0 < size < staged.size_bytes:
                return None
            with self._open_stage(staged) as incoming:
                while chunk := base.read(CHUNK_BYTES):
                    if incoming.read(len(chunk)) != chunk:
                        return None
            self.verify_pins(base)
        return NativeObject(base_digest, size)

    def publish(self, staged: StagedArtifact, base_digest: str | None) -> NativeObject:
        first, second = digest_directories(staged.sha256)
        with self.storage._directory(first, second, create=True) as directory:
            import fcntl

            fcntl.flock(directory, fcntl.LOCK_EX)
            try:
                retained = self.describe(staged.sha256) if self.contains(staged.sha256) else None
                if retained is not None:
                    if retained.size_bytes != staged.size_bytes:
                        raise ExtractionError("transcript_copy_integrity_failed")
                    return retained
                base = self.prefix(staged, base_digest)
                result = NativeObject(staged.sha256, staged.size_bytes,
                    base.sha256 if base else None, base.size_bytes if base else 0)
                self._write_object(staged, result)
                self.describe(result.sha256)
                return result
            finally:
                fcntl.flock(directory, fcntl.LOCK_UN)

    def _write_object(self, staged: StagedArtifact, item: NativeObject) -> None:
        first, second = digest_directories(item.sha256)
        writer = ArtifactStorage(self.storage.root, MAX_NATIVE_BYTES + MAX_HEADER_BYTES)
        with self._open_stage(staged) as source:
            encoded = writer.stage(first, second, "snapshot.bin", self._encode(source, item))
        try:
            writer.publish(encoded)
        finally:
            writer.discard(encoded)

    @staticmethod
    def _encode(source: BinaryIO, item: NativeObject):
        yield item.header()
        digest, size = hashlib.sha256(), 0
        while chunk := source.read(CHUNK_BYTES):
            digest.update(chunk)
            skip = max(0, item.base_size_bytes - size)
            size += len(chunk)
            if size > item.size_bytes:
                raise ExtractionError("transcript_copy_integrity_failed")
            if skip < len(chunk):
                yield chunk[skip:]
        if size != item.size_bytes or digest.hexdigest() != item.sha256:
            raise ExtractionError("transcript_copy_integrity_failed")
