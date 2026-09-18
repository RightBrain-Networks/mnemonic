"""Plan and resumably reclaim redundant native files without changing transcript history."""

import fcntl
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.artifact_storage import ArtifactStorage, StagedArtifact, _parse_path
from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.models import Transcript
from mnemonic_api.transcript_copies import TranscriptCopy, TranscriptStorage
from mnemonic_api.transcript_copy_references import (
    CopyReference,
    legacy_reference,
    object_reference,
    read_reference,
    source_head,
    write_reference,
)
from mnemonic_api.transcript_objects import (
    CHUNK_BYTES,
    NativeObject,
    TranscriptObjects,
    object_key,
)


@dataclass(frozen=True)
class ReclaimSnapshot:
    id: UUID
    snapshot_id: UUID
    storage_key: str
    sha256: str
    size_bytes: int
    source_path: str
    source_modified_at: datetime | None

    @property
    def legacy_key(self) -> str:
        return TranscriptStorage.key(self.id, self.snapshot_id)

    @property
    def copy(self) -> TranscriptCopy:
        return TranscriptCopy(self.storage_key, self.sha256, self.size_bytes)


def snapshots(factory: sessionmaker[Session]) -> list[ReclaimSnapshot]:
    with factory() as database:
        rows = database.execute(select(
            Transcript.id, Transcript.snapshot_id, Transcript.storage_key,
            Transcript.copy_sha256, Transcript.copy_size_bytes,
            func.coalesce(Transcript.copy_source_path, Transcript.source_path),
            Transcript.source_modified_at).where(Transcript.copy_status == 'ready')
            .order_by(Transcript.copy_size_bytes, Transcript.id))
        return [ReclaimSnapshot(*row) for row in rows]


def _raw_legacy_bytes(storage: TranscriptStorage, row: ReclaimSnapshot) -> int:
    try:
        source = ArtifactStorage.open(storage, row.legacy_key)
    except FileNotFoundError:
        return 0
    with source:
        reference = legacy_reference(storage, row.legacy_key, source)
        if reference is not None:
            _require_reference(row, reference)
            return 0
        return os.fstat(source.fileno()).st_size


def _is_prefix(storage: TranscriptStorage, base: ReclaimSnapshot, row: ReclaimSnapshot) -> bool:
    if not 0 < base.size_bytes < row.size_bytes:
        return False
    with storage.open_copy(base.copy) as previous, storage.open_copy(row.copy) as incoming:
        while chunk := previous.read(CHUNK_BYTES):
            if incoming.read(len(chunk)) != chunk:
                return False
    return True


def _require_reference(row: ReclaimSnapshot, reference: CopyReference) -> None:
    if (reference.sha256, reference.size_bytes) != (row.sha256, row.size_bytes):
        raise ExtractionError('transcript_copy_integrity_failed')


def _reference_growth(storage: TranscriptStorage, key: str, reference: CopyReference) -> int:
    try:
        with ArtifactStorage.open(storage, key) as source:
            previous = os.fstat(source.fileno()).st_size
    except FileNotFoundError:
        previous = 0
    return len(reference.document()) - previous


def _planned_receipts(storage: TranscriptStorage, rows: list[ReclaimSnapshot]) -> int:
    growth = 0
    heads: dict[str, CopyReference] = {}
    for row in rows:
        if not _raw_legacy_bytes(storage, row) and row.storage_key == object_key(row.sha256):
            continue
        receipt = CopyReference(row.sha256, row.size_bytes, row.source_path, row.source_modified_at)
        growth += _reference_growth(storage, storage.receipt_key(row.id, row.snapshot_id), receipt)
        heads[source_head(row.source_path)] = CopyReference(row.sha256, row.size_bytes)
    return growth + sum(_reference_growth(storage, key, value) for key, value in heads.items())


def plan_reclaim(storage: TranscriptStorage, rows: list[ReclaimSnapshot]) -> dict:
    """Read and verify every logical snapshot; never stage or publish content."""
    objects: set[str] = set()
    heads: dict[str, ReclaimSnapshot] = {}
    raw_bytes = new_bytes = aliases = identical = prefixes = 0
    targets = 0
    for row in rows:
        with storage.open_copy(row.copy):
            pass
        raw = _raw_legacy_bytes(storage, row)
        raw_bytes += raw
        target = raw > 0 or row.storage_key != object_key(row.sha256)
        targets += target
        if raw:
            aliases += len(CopyReference(row.sha256, row.size_bytes).document())
        head_key = source_head(row.source_path)
        base = heads.get(head_key)
        if row.sha256 in objects:
            identical += raw
        else:
            new, prefix = _planned_object(storage, row, base)
            new_bytes += new
            prefixes += prefix
            objects.add(row.sha256)
        heads[head_key] = row
    receipts = _planned_receipts(storage, rows)
    return {'verified_snapshots': len(rows), 'distinct_contents': len(objects),
        'rows_to_reclaim': targets, 'legacy_file_bytes': raw_bytes,
        'new_object_bytes': new_bytes, 'replacement_reference_bytes': aliases,
        'new_receipt_and_hint_bytes': receipts,
        'identical_payload_bytes': identical, 'shared_prefix_bytes': prefixes,
        'estimated_reclaimable_bytes': max(0, raw_bytes - new_bytes - aliases - receipts),
        'estimate_excludes_directory_allocation': True}


def _planned_object(storage: TranscriptStorage, row: ReclaimSnapshot,
                    base: ReclaimSnapshot | None) -> tuple[int, int]:
    objects = TranscriptObjects(storage)
    if objects.contains(row.sha256):
        objects.describe(row.sha256)
        return 0, 0
    previous = base if base is not None and _is_prefix(storage, base, row) else None
    item = NativeObject(row.sha256, row.size_bytes, previous.sha256 if previous else None,
                        previous.size_bytes if previous else 0)
    return len(item.header()) + item.size_bytes - item.base_size_bytes, item.base_size_bytes


def _shared_object(storage: TranscriptStorage, row: ReclaimSnapshot) -> NativeObject:
    head_key = source_head(row.source_path)
    first, second, _ = _parse_path(head_key)
    with storage._directory(first, second, create=True) as directory:
        fcntl.flock(directory, fcntl.LOCK_EX)
        try:
            head = read_reference(storage, head_key)
            with storage.open_copy(row.copy) as source:
                staged = storage.stage(row.id, row.snapshot_id, 'transcript.jsonl',
                                       iter(lambda: source.read(CHUNK_BYTES), b''))
            try:
                item = _publish_verified(storage, row, staged, head)
                write_reference(storage, head_key, object_reference(item))
                return item
            finally:
                storage.discard(staged)
        finally:
            fcntl.flock(directory, fcntl.LOCK_UN)


def _publish_verified(storage: TranscriptStorage, row: ReclaimSnapshot, staged: StagedArtifact,
                      head: CopyReference | None) -> NativeObject:
    if (staged.sha256, staged.size_bytes) != (row.sha256, row.size_bytes):
        raise ExtractionError('transcript_copy_integrity_failed')
    item = TranscriptObjects(storage).publish(staged, head.sha256 if head else None)
    reference = CopyReference(item.sha256, item.size_bytes, row.source_path, row.source_modified_at)
    receipt_key = storage.receipt_key(row.id, row.snapshot_id)
    retained = read_reference(storage, receipt_key)
    if retained is not None and (retained.sha256, retained.size_bytes) != (
        reference.sha256, reference.size_bytes
    ):
        raise ExtractionError('transcript_copy_integrity_failed')
    write_reference(storage, receipt_key, reference)
    return item


def _repoint(factory: sessionmaker[Session], row: ReclaimSnapshot, item: NativeObject) -> None:
    with factory.begin() as database:
        updated = database.scalar(update(Transcript).where(
            Transcript.id == row.id, Transcript.snapshot_id == row.snapshot_id,
            Transcript.copy_status == 'ready', Transcript.copy_sha256 == row.sha256,
            Transcript.copy_size_bytes == row.size_bytes,
            Transcript.storage_key.in_([row.storage_key, item.key]),
        ).values(storage_key=item.key).returning(Transcript.id))
        if updated is None:
            raise ExtractionError('transcript_reclaim_conflict')


def _replace_legacy(storage: TranscriptStorage, row: ReclaimSnapshot) -> bool:
    try:
        source = ArtifactStorage.open(storage, row.legacy_key)
    except FileNotFoundError:
        return False
    with source:
        # A reader that already opened the old location finishes before its inode
        # is unlinked. Later readers can follow the small immutable-byte reference.
        fcntl.flock(source.fileno(), fcntl.LOCK_EX)
        reference = legacy_reference(storage, row.legacy_key, source)
        if reference is not None:
            _require_reference(row, reference)
            return False
        source.seek(0)
        digest, size = hashlib.sha256(), 0
        while chunk := source.read(CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
        if (digest.hexdigest(), size) != (row.sha256, row.size_bytes):
            raise ExtractionError('transcript_copy_integrity_failed')
        if size == 0:
            # An empty historical file has no redundant payload to reclaim.
            # Preserve its old pointer without adding a larger reference file.
            return False
        write_reference(storage, row.legacy_key, CopyReference(row.sha256, row.size_bytes))
    return True


def apply_reclaim(factory: sessionmaker[Session], storage: TranscriptStorage,
                   rows: list[ReclaimSnapshot]) -> dict:
    """Caller holds the exclusive maintenance lock through all commits and reclamation."""
    changed = reclaimed = 0
    for row in rows:
        raw = _raw_legacy_bytes(storage, row)
        if not raw and row.storage_key == object_key(row.sha256):
            continue
        item = _shared_object(storage, row)
        _repoint(factory, row, item)
        changed += row.storage_key != item.key
        with factory() as database:
            references = database.scalar(select(func.count()).select_from(Transcript)
                .where(Transcript.storage_key == row.legacy_key))
            if references:
                raise ExtractionError('transcript_reclaim_still_referenced')
        reclaimed += _replace_legacy(storage, row)
        with storage.open_copy(TranscriptCopy(item.key, row.sha256, row.size_bytes)):
            pass
    return {'rows_repointed': changed, 'legacy_files_reclaimed': reclaimed}
