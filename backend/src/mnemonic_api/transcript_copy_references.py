"""Small crash receipts and source hints; content identity always requires SHA-256."""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from mnemonic_api.artifact_storage import ArtifactStorage, _parse_path
from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.transcript_objects import (
    REFERENCE_MAGIC,
    NativeObject,
    digest_directories,
    document_bytes,
    read_document,
    require_digest,
    require_size,
)
from mnemonic_api.transcript_storage import canonical_source_path


@dataclass(frozen=True)
class CopyReference:
    sha256: str
    size_bytes: int
    source_path: str | None = None
    source_modified_at: datetime | None = None

    def document(self) -> bytes:
        return document_bytes({"sha256": self.sha256, "size_bytes": self.size_bytes,
            "source_path": self.source_path,
            "source_modified_at": (self.source_modified_at.isoformat()
                                   if self.source_modified_at is not None else None)},
                              REFERENCE_MAGIC)

    @classmethod
    def read(cls, source) -> CopyReference:
        value = read_document(source, REFERENCE_MAGIC)
        if (set(value) != {"sha256", "size_bytes", "source_path", "source_modified_at"}
                or source.read(1)):
            raise ExtractionError("transcript_copy_integrity_failed")
        path, modified = value["source_path"], value["source_modified_at"]
        if path is not None and (not isinstance(path, str) or not path.startswith("/")):
            raise ExtractionError("transcript_copy_integrity_failed")
        try:
            timestamp = None if modified is None else datetime.fromisoformat(modified)
        except (TypeError, ValueError):
            raise ExtractionError("transcript_copy_integrity_failed") from None
        if timestamp is not None and timestamp.tzinfo is None:
            raise ExtractionError("transcript_copy_integrity_failed")
        return cls(require_digest(value["sha256"]), require_size(value["size_bytes"]),
                   path, timestamp)


def read_reference(storage: ArtifactStorage, key: str) -> CopyReference | None:
    try:
        source = ArtifactStorage.open(storage, key)
    except FileNotFoundError:
        return None
    with source:
        return CopyReference.read(source)


def legacy_reference(storage: ArtifactStorage, key: str, source) -> CopyReference | None:
    """A magic-looking old payload is not a pointer without its matching crash receipt."""
    try:
        if source.read(len(REFERENCE_MAGIC)) != REFERENCE_MAGIC:
            return None
        source.seek(0)
        reference = CopyReference.read(source)
        first, second, _ = _parse_path(key)
        receipt = read_reference(storage, f'{first}/{second}/capture.json')
        if receipt is not None and (receipt.sha256, receipt.size_bytes) == (
            reference.sha256, reference.size_bytes
        ):
            return reference
        return None
    except ExtractionError:
        return None  # The caller still verifies these raw bytes against their pinned hash.
    finally:
        source.seek(0)


def write_reference(storage: ArtifactStorage, key: str, reference: CopyReference) -> None:
    first, second, filename = _parse_path(key)
    writer = ArtifactStorage(storage.root, 16384)
    staged = writer.stage(first, second, filename, [reference.document()])
    try:
        writer.publish(staged)
    finally:
        writer.discard(staged)


def source_head(source: str) -> str:
    digest = hashlib.sha256(("mnemonic-transcript-source-v1\0"
                             + canonical_source_path(source)).encode()).hexdigest()
    first, second = digest_directories(digest)
    return f"{first}/{second}/source-head.json"


def object_reference(item: NativeObject) -> CopyReference:
    return CopyReference(item.sha256, item.size_bytes)
