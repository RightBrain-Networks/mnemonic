"""Persisted copy claims keep migration text readable and fence stale publications."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.models import Transcript, TranscriptSettings, WorkItem
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.transcripts import transcript_project_id
from mnemonic_api.transcript_copies import TranscriptCopy, TranscriptCopyPin, TranscriptStorage
from mnemonic_api.transcript_indexing import (
    _active_generation,
    _lock_claim_candidate,
    _resolved_path_errors,
)
from mnemonic_api.transcript_recovery_sources import approved_copy_source
from mnemonic_api.transcript_snapshots import empty_transcript_snapshot
from mnemonic_jobs.ledger import JobContext

MAX_COPY_ATTEMPTS = 8


@dataclass(frozen=True)
class TranscriptCopyJob:
    transcript_id: UUID
    generation: int
    snapshot_id: UUID
    lease_token: UUID
    source_path: str
    maximum_bytes: int
    expected: TranscriptCopyPin | None = None


def claimable_copies(settings: Settings):
    return (select(Transcript, TranscriptSettings.max_file_size_bytes)
        .outerjoin(WorkItem, WorkItem.id == Transcript.work_item_id)
        .outerjoin(TranscriptSettings, TranscriptSettings.project_id == transcript_project_id())
        .where(func.coalesce(TranscriptSettings.enabled, True), ~_active_generation(), or_(
            (Transcript.copy_status == "pending")
            & (Transcript.copy_next_attempt_at <= func.clock_timestamp()),
            _resolved_path_errors(settings, copying=True),
            (Transcript.copy_status == "processing")
            & (Transcript.copy_lease_expires_at <= func.clock_timestamp()),
        )))


def _start_copy(database: Session, row, settings: Settings) -> TranscriptCopyJob | None:
    record, maximum = row
    now = database.execute(select(func.clock_timestamp())).scalar_one()
    if record.copy_status == "failed":
        record.copy_attempts = 0
    if record.copy_attempts >= MAX_COPY_ATTEMPTS:
        record.copy_lease_token = record.copy_lease_expires_at = None
        _copy_failure(record, ExtractionError("transcript_attempts_exhausted"), now)
        return None
    record.copy_status = "processing"
    record.copy_attempts += 1
    record.copy_error_code = None
    record.copy_lease_token = uuid4()
    record.copy_lease_expires_at = now + timedelta(minutes=10)
    try:
        source, expected = approved_copy_source(database, record)
    except ExtractionError as error:
        record.copy_lease_token = record.copy_lease_expires_at = None
        _copy_failure(record, error, now)
        return None
    return TranscriptCopyJob(record.id, record.generation, record.snapshot_id,
        record.copy_lease_token, source,
        min(maximum or settings.transcript_max_bytes, settings.transcript_max_bytes), expected)


def claim_transcript_copy(
    factory: sessionmaker[Session], settings: Settings,
    transcript_id: UUID | None = None, generation: int | None = None,
) -> TranscriptCopyJob | None:
    with factory() as database:
        statement = claimable_copies(settings)
        if transcript_id is not None:
            statement = statement.where(Transcript.id == transcript_id,
                                        Transcript.generation == generation)
        candidate = database.execute(statement.with_only_columns(
            Transcript.id, Transcript.work_item_id, transcript_project_id().label("project_id"))
            .order_by(Transcript.copy_next_attempt_at, Transcript.id).limit(1)).first()
        if candidate is None:
            return None
        with project_mutation(database, candidate.project_id):
            row = _lock_claim_candidate(database, candidate, settings, statement)
            if row is None:
                return None
            job = _start_copy(database, row, settings)
            database.commit()
            return job


def _copy_failure(record: Transcript, error: ExtractionError, now: datetime) -> None:
    retry = error.retryable and record.copy_attempts < MAX_COPY_ATTEMPTS
    record.copy_status = "pending" if retry else "failed"
    record.copy_error_code = error.code
    record.copy_next_attempt_at = now + timedelta(seconds=min(15 * 2**record.copy_attempts, 120))
    # A legacy ready snapshot is retained even if its source is no longer
    # recoverable. Copy metadata reports the migration gap independently.
    if record.status != "ready":
        record.status = "pending" if retry else "failed"
        record.error_code = error.code
        record.indexing_started_at = record.indexing_started_at or now
        record.indexing_completed_at = None if retry else now


def _copy_success(record: Transcript, copy: TranscriptCopy, now: datetime) -> None:
    record.copy_status = "ready"
    record.storage_key = copy.storage_key
    record.copy_sha256 = copy.sha256
    record.copy_size_bytes = copy.size_bytes
    record.copied_at = now
    record.copy_error_code = None
    if record.status == "ready":
        if record.sha256 != copy.sha256 or record.normalized_revision is None:
            record.reindex_status, record.reindex_error_code = "pending", None
            record.attempts, record.next_attempt_at = 0, now
        return
    # Changed legacy sources must have text re-extracted from the new copy;
    # parser/Tika failures keep the captured bytes available for later rebuilds.
    for name, value in empty_transcript_snapshot().items():
        setattr(record, name, value)
    record.status = "pending"
    record.error_code = None
    record.attempts = 0
    record.indexing_started_at = record.indexing_completed_at = None
    record.lease_token = record.lease_expires_at = None
    record.next_attempt_at = now


def complete_transcript_copy(factory: sessionmaker[Session], job: TranscriptCopyJob,
                             copy: TranscriptCopy | None, error: ExtractionError | None,
                             context: JobContext | None = None) -> None:
    with factory() as database:
        project_id = database.scalar(select(transcript_project_id()).select_from(Transcript)
            .outerjoin(WorkItem, WorkItem.id == Transcript.work_item_id)
            .where(Transcript.id == job.transcript_id))
        if project_id is None:
            return
        with project_mutation(database, project_id):
            if context is not None:
                context.assert_owned(database)
            record = database.scalar(select(Transcript).where(
                Transcript.id == job.transcript_id, Transcript.generation == job.generation,
                Transcript.snapshot_id == job.snapshot_id,
                Transcript.copy_status == "processing",
                Transcript.copy_lease_token == job.lease_token,
            ).with_for_update())
            if record is None:
                return
            now = database.execute(select(func.clock_timestamp())).scalar_one()
            record.copy_lease_token = record.copy_lease_expires_at = None
            if error is not None:
                _copy_failure(record, error, now)
            elif copy is not None:
                _copy_success(record, copy, now)
            else:
                raise ValueError("Transcript copy publication requires bytes or a safe error")
            database.commit()


def copy_next_transcript(factory: sessionmaker[Session], settings: Settings,
                         transcript_id: UUID | None = None, generation: int | None = None,
                         context: JobContext | None = None) -> bool:
    job = claim_transcript_copy(factory, settings, transcript_id, generation)
    if job is None:
        return False
    copy, error = None, None
    try:
        copy = TranscriptStorage(settings.transcript_root, job.maximum_bytes).capture(
            job.transcript_id, job.snapshot_id, job.source_path, settings.transcript_allowed_roots,
            expected=job.expected)
    except ExtractionError as failure:
        error = failure
    complete_transcript_copy(factory, job, copy, error, context)
    return True
