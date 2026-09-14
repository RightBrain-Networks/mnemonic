"""Operator-only, exact-replay recovery approvals; no filesystem IO in mutations."""

import hashlib
from dataclasses import dataclass
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.errors import ApplicationError, conflict, not_found
from mnemonic_api.models import (
    Transcript,
    TranscriptRecovery,
    TranscriptSettings,
    WorkItem,
    WorkLease,
)
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.transcripts import transcript_project_id
from mnemonic_api.transcript_copies import _source_chunks
from mnemonic_api.transcript_indexing import _active_generation
from mnemonic_api.transcript_locations import TranscriptLocation
from mnemonic_api.transcript_snapshots import new_transcript_copy
from mnemonic_api.transcript_storage import _relative_source
from mnemonic_jobs.ledger import enqueue_job

PathAssertion = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class TranscriptRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: UUID
    transcript_id: UUID
    project_id: UUID
    original_source_path: PathAssertion
    replacement_path: PathAssertion
    expected_generation: Annotated[int, Field(gt=0, lt=2147483647)]
    expected_snapshot_id: UUID
    expected_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    expected_size_bytes: Annotated[int, Field(ge=0, le=268435456)]
    reason: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    evidence: Annotated[str, StringConstraints(min_length=1, max_length=8000)]

    @model_validator(mode="after")
    def safe_assertions(self) -> Self:
        for path in (self.original_source_path, self.replacement_path):
            TranscriptLocation(client="operator-recovery", path=path)
        if not self.reason.strip() or not self.evidence.strip():
            raise ValueError("Recovery requires an explicit reason and verification evidence")
        if "\x00" in self.reason or "\x00" in self.evidence:
            raise ValueError("Recovery explanations cannot contain null characters")
        return self


class TranscriptRecoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: UUID
    transcript_id: UUID
    resulting_generation: int
    resulting_snapshot_id: UUID


@dataclass(frozen=True)
class TranscriptRecoveryTarget:
    transcript_id: UUID
    project_id: UUID
    original_source_path: str
    generation: int
    snapshot_id: UUID
    maximum_bytes: int


@dataclass(frozen=True)
class RecoverySourceFingerprint:
    sha256: str
    size_bytes: int


def _target_query(transcript_id: UUID):
    return (select(Transcript, transcript_project_id(), TranscriptSettings.enabled,
                   TranscriptSettings.max_file_size_bytes, _active_generation())
        .outerjoin(WorkItem, WorkItem.id == Transcript.work_item_id)
        .outerjoin(TranscriptSettings, TranscriptSettings.project_id == transcript_project_id())
        .where(Transcript.id == transcript_id))


def _eligible_target(row, settings: Settings, project_id: UUID) -> TranscriptRecoveryTarget:
    if row is None:
        raise not_found("transcript_not_found", "Transcript not found.")
    record, current_project, enabled, maximum, active = row
    if current_project != project_id:
        raise conflict("transcript_recovery_stale", "Recovery project identity has changed.")
    if active:
        raise conflict("transcript_recovery_active", "Recovery waits until this lease ends.")
    if enabled is False:
        raise conflict("transcript_recovery_paused", "Transcript processing is paused.")
    if record.copy_status == "ready":
        raise conflict("transcript_recovery_already_copied", "This transcript already has a copy.")
    return TranscriptRecoveryTarget(record.id, current_project, record.source_path,
        record.generation, record.snapshot_id,
        min(maximum or settings.transcript_max_bytes, settings.transcript_max_bytes))


def inspect_recovery_target(factory: sessionmaker[Session], settings: Settings,
                            transcript_id: UUID, project_id: UUID) -> TranscriptRecoveryTarget:
    """Read only metadata; callers must check eligibility before hashing any file."""
    with factory() as database:
        return _eligible_target(database.execute(_target_query(transcript_id)).first(),
                                settings, project_id)


def describe_recovery_source(settings: Settings, replacement_path: str,
                             maximum_bytes: int | None = None) -> RecoverySourceFingerprint:
    """Hash only an explicitly supplied, contained, stable regular file, without retaining bytes."""
    digest, size = hashlib.sha256(), 0
    maximum = min(maximum_bytes or settings.transcript_max_bytes, settings.transcript_max_bytes)
    try:
        for chunk in _source_chunks(replacement_path, settings.transcript_allowed_roots, maximum):
            digest.update(chunk)
            size += len(chunk)
    except ExtractionError as error:
        raise ApplicationError(409, error.code, "Recovery source verification failed.") from None
    except OSError:
        raise ApplicationError(409, "transcript_io_error",
                               "Recovery source is unavailable.") from None
    return RecoverySourceFingerprint(digest.hexdigest(), size)


def _replay(database: Session,
            request: TranscriptRecoveryRequest) -> TranscriptRecoveryResult | None:
    receipt = database.get(TranscriptRecovery, request.operation_id)
    if receipt is None:
        return None
    if any(getattr(receipt, name) != value for name, value in request.model_dump().items()):
        raise conflict("transcript_recovery_conflict",
                       "Retry recovery with its exact frozen request and operation UUID.")
    return TranscriptRecoveryResult(**{name: getattr(receipt, name)
                                      for name in TranscriptRecoveryResult.model_fields})


def _lock_target(database: Session, request: TranscriptRecoveryRequest, settings: Settings):
    work_id = database.scalar(select(Transcript.work_item_id).where(
        Transcript.id == request.transcript_id))
    if work_id is not None:
        work = database.scalar(select(WorkItem.id).where(
            WorkItem.id == work_id, WorkItem.project_id == request.project_id).with_for_update())
        if work is None:
            raise conflict("transcript_recovery_stale", "Recovery project identity has changed.")
        database.scalar(select(WorkLease.work_item_id).where(
            WorkLease.work_item_id == work_id).with_for_update())
    row = database.execute(_target_query(request.transcript_id)
                           .with_for_update(of=Transcript)).first()
    target = _eligible_target(row, settings, request.project_id)
    if (target.original_source_path, target.generation, target.snapshot_id) != (
        request.original_source_path, request.expected_generation, request.expected_snapshot_id
    ):
        raise conflict("transcript_recovery_stale", "Read current metadata before a new recovery.")
    if request.expected_size_bytes > target.maximum_bytes:
        raise conflict("transcript_too_large", "Recovery exceeds the current size limit.")
    try:
        _relative_source(request.replacement_path, settings.transcript_allowed_roots)
    except ExtractionError as error:
        raise ApplicationError(409, error.code, "Recovery source is not allowed.") from None
    assert row is not None
    return row[0]


def _record_recovery(database: Session, record: Transcript,
                      request: TranscriptRecoveryRequest) -> TranscriptRecoveryResult:
    now = database.execute(select(func.clock_timestamp())).scalar_one()
    for name, value in new_transcript_copy().items():
        setattr(record, name, value)
    record.generation += 1
    record.copy_next_attempt_at = record.next_attempt_at = now
    record.lease_token = record.lease_expires_at = None
    record.attempts = 0
    record.reindex_status = record.reindex_error_code = None
    if record.status != "ready":
        record.status, record.error_code = "pending", None
        record.indexing_started_at = record.indexing_completed_at = None
    result = TranscriptRecoveryResult(operation_id=request.operation_id,
        transcript_id=record.id, resulting_generation=record.generation,
        resulting_snapshot_id=record.snapshot_id)
    database.add(TranscriptRecovery(**request.model_dump(),
        resulting_generation=result.resulting_generation,
        resulting_snapshot_id=result.resulting_snapshot_id))
    record.recovery_operation_id = request.operation_id
    database.flush()
    # Match the domain scheduler's exact identity so its next reconciliation
    # sees this same outbox job rather than creating a second delivery.
    enqueue_job(database, "transcript_copy",
        f"transcript_copy:{record.id}:{record.generation}:0:{now.isoformat()}",
        {"transcript_id": str(record.id), "generation": record.generation}, due_at=now)
    return result


def apply_transcript_recovery(factory: sessionmaker[Session], settings: Settings,
                              request: TranscriptRecoveryRequest) -> TranscriptRecoveryResult:
    """Commit a receipt, fresh snapshot and targeted outbox atomically, or replay exactly."""
    request = TranscriptRecoveryRequest.model_validate(request.model_dump())
    with factory() as database:
        replay = _replay(database, request)
        if replay is not None:
            return replay
        # Global operation IDs serialize before project locks. Never wait while
        # holding an unrelated project's lock or expose an unfinished receipt.
        digest = hashlib.sha256(b"transcript-recovery:" + request.operation_id.bytes).digest()
        lock_id = int.from_bytes(digest[:8], signed=True)
        if not database.scalar(select(func.pg_try_advisory_xact_lock(lock_id))):
            raise ApplicationError(503, "transcript_recovery_busy",
                                   "Retry the exact recovery request after the active attempt.")
        replay = _replay(database, request)
        if replay is not None:
            return replay
        with project_mutation(database, request.project_id):
            record = _lock_target(database, request, settings)
            result = _record_recovery(database, record, request)
            database.commit()
            return result
