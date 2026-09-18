"""Persisted, expiring transcript jobs; no database lock spans filesystem IO."""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import exists, false, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from mnemonic_api.artifact_tika import ExtractedArtifact, ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Transcript, TranscriptSettings, WorkItem, WorkLease
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.transcripts import transcript_project_id
from mnemonic_api.transcript_access import RECHECK_SECONDS, access_error
from mnemonic_api.transcript_copies import TranscriptCopy, TranscriptStorage
from mnemonic_api.transcript_detection import detect_transcript_client
from mnemonic_api.transcript_fulltext import publish_complete_text, text_digest
from mnemonic_api.transcript_metadata import retained_time_bounds, time_bounds, timeline_metadata
from mnemonic_api.transcript_normalization import (
    NormalizedConversation,
    normalize_transcript,
)
from mnemonic_api.transcript_normalized_storage import load_normalization, publish_normalization
from mnemonic_api.transcript_recovery_sources import effective_copy_path
from mnemonic_api.transcript_snapshots import empty_transcript_snapshot
from mnemonic_api.transcript_spool import TranscriptSegments
from mnemonic_api.transcript_storage import canonical_source_path
from mnemonic_jobs.ledger import JobContext

logger = logging.getLogger(__name__)
RECOVERABLE_INDEX_ERRORS = (
    "transcript_copy_unavailable", "transcript_storage_full", "transcript_storage_read_only",
    "transcript_io_error", "transcript_permission_denied", "transcript_source_missing",
)


@dataclass(frozen=True)
class TranscriptJob:
    transcript_id: UUID
    generation: int
    lease_token: UUID
    source_path: str
    client: str
    maximum_bytes: int
    imported: bool
    copy: TranscriptCopy
    started_at: datetime
    snapshot_id: UUID


@dataclass(frozen=True)
class TranscriptResult:
    extracted: ExtractedArtifact
    size_bytes: int
    sha256: str
    format: str
    mime_type: str
    session_started_at: datetime | None = None
    last_updated_at: datetime | None = None
    complete_text_sha256: str | None = None


def _active_generation():
    return exists(select(WorkLease.work_item_id).where(
        WorkLease.work_item_id == Transcript.work_item_id,
        WorkLease.lease_generation_id == Transcript.lease_generation_id,
        WorkLease.expires_at > func.clock_timestamp(),
    ))


def _resolved_path_errors(settings: Settings, *, copying: bool = False):
    # Compare POSIX aliases without changing the exact agent assertion. The
    # normal reader still enforces regular files and refuses all symlinks.
    source_path = effective_copy_path() if copying else Transcript.source_path
    path = func.regexp_replace(source_path, r"/\.(?=/|$)", "", "g")
    path = func.regexp_replace(path, "/+", "/", "g")
    # Path() also removes trailing separators; the root itself is not a file.
    path = func.rtrim(path, "/")
    allowed = or_(false(), *(path.startswith(
        canonical_source_path(str(root)).rstrip("/") + "/", autoescape=True,
    ) for root in settings.transcript_allowed_roots))
    status = Transcript.copy_status if copying else Transcript.status
    code = Transcript.copy_error_code if copying else Transcript.error_code
    return (status == "failed") & (
        code == "transcript_path_not_allowed"
    ) & allowed & ~path.regexp_match(r"(^|/)\.\.(/|$)")


def _claimable_transcripts(settings: Settings):
    return (select(Transcript, TranscriptSettings.max_file_size_bytes)
        .outerjoin(WorkItem, WorkItem.id == Transcript.work_item_id)
        .outerjoin(TranscriptSettings, TranscriptSettings.project_id == transcript_project_id())
        .where(func.coalesce(TranscriptSettings.enabled, True), ~_active_generation(),
               Transcript.copy_status == "ready", or_(
            (Transcript.status.in_(["waiting", "pending"])
             | (Transcript.reindex_status == "pending"))
            & (Transcript.next_attempt_at <= func.clock_timestamp()),
            ((Transcript.status == "failed") | (Transcript.reindex_status == "failed"))
            & func.coalesce(Transcript.reindex_error_code, Transcript.error_code).in_(
                RECOVERABLE_INDEX_ERRORS)
            & (Transcript.next_attempt_at <= func.clock_timestamp()),
            ((Transcript.status == "processing") | (Transcript.reindex_status == "processing"))
            & (Transcript.lease_expires_at <= func.clock_timestamp()),
        )))


def _lock_claim_candidate(database: Session, candidate, settings: Settings, statement=None):
    transcript_id, work_id, project_id = candidate
    if work_id is not None:
        work = database.scalar(select(WorkItem).where(
            WorkItem.id == work_id, WorkItem.project_id == project_id).with_for_update())
        if work is None:
            return None
        database.scalar(select(WorkLease).where(
            WorkLease.work_item_id == work_id).with_for_update())
    # This query runs after the lease lock: an earlier renewal must now be
    # committed, so the fresh clock comparison cannot observe its old expiry.
    statement = _claimable_transcripts(settings) if statement is None else statement
    return database.execute(statement.where(
        Transcript.id == transcript_id, transcript_project_id() == project_id)
                            .with_for_update(of=Transcript)).first()


def _start_claim(database: Session, row, settings: Settings) -> TranscriptJob | None:
    transcript, maximum = row
    now = database.execute(select(func.clock_timestamp())).scalar_one()
    if (transcript.reindex_status or transcript.status) == "failed":
        transcript.attempts = 0
    if transcript.attempts >= 3:
        transcript.lease_token = transcript.lease_expires_at = None
        _index_failure(transcript, ExtractionError("transcript_attempts_exhausted"), None, None)
        return None
    if transcript.status == "ready":
        transcript.reindex_status, transcript.reindex_error_code = "processing", None
    else:
        for field, value in empty_transcript_snapshot().items():
            setattr(transcript, field, value)
        transcript.error_code = None
        transcript.status = "processing"
        transcript.indexing_started_at = transcript.indexing_started_at or now
        transcript.indexing_completed_at = None
    transcript.lease_token = uuid4()
    transcript.lease_expires_at = now + timedelta(
        seconds=settings.artifact_extraction_timeout_seconds * 2 + 300)
    transcript.attempts += 1
    assert transcript.storage_key is not None and transcript.copy_sha256 is not None
    assert transcript.copy_size_bytes is not None
    return TranscriptJob(transcript.id, transcript.generation, transcript.lease_token,
                         transcript.source_path, transcript.client,
                         min(maximum or settings.transcript_max_bytes,
                             settings.transcript_max_bytes), transcript.kind == "imported",
                         TranscriptCopy(transcript.storage_key, transcript.copy_sha256,
                                        transcript.copy_size_bytes,
                                        source_modified_at=transcript.source_modified_at),
                         now, transcript.snapshot_id)


def claim_transcript_job(
    factory: sessionmaker[Session], settings: Settings,
    transcript_id: UUID | None = None, generation: int | None = None,
) -> TranscriptJob | None:
    with factory() as database:
        statement = _claimable_transcripts(settings)
        if transcript_id is not None:
            statement = statement.where(Transcript.id == transcript_id,
                                        Transcript.generation == generation)
        candidate = database.execute(statement.with_only_columns(
            Transcript.id, Transcript.work_item_id, transcript_project_id().label("project_id"))
            .order_by(Transcript.next_attempt_at, Transcript.id).limit(1)).first()
        if candidate is None:
            return None
        # Follow the same project -> work -> lease -> transcript lock order as
        # lifecycle mutations. No candidate lock is taken before the project.
        with project_mutation(database, candidate.project_id):
            row = _lock_claim_candidate(database, candidate, settings, statement)
            if row is None:
                return None
            job = _start_claim(database, row, settings)
            database.commit()
            return job


def _save_result(record: Transcript, result: TranscriptResult) -> None:
    record.status = "ready"
    record.error_code = None
    record.reindex_status = record.reindex_error_code = None
    record.normalized_text = result.extracted.text
    record.text_sha256 = (result.complete_text_sha256
                          or hashlib.sha256(result.extracted.text.encode("utf-8")).hexdigest())
    record.extracted_metadata = result.extracted.metadata
    record.truncated = result.extracted.truncated
    record.size_bytes = result.size_bytes
    record.sha256 = result.sha256
    record.format = result.format
    record.mime_type = result.mime_type


def _index_failure(record: Transcript, error: ExtractionError,
                    source_size: int | None, source_details: dict | None) -> None:
    retry = error.retryable and record.attempts < 3
    state = "pending" if retry else "failed"
    delay = RECHECK_SECONDS if not retry and error.code in RECOVERABLE_INDEX_ERRORS else (
        30 * record.attempts)
    record.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
    if record.status == "ready":
        record.reindex_status, record.reindex_error_code = state, error.code
        return
    record.status, record.error_code = state, error.code
    snapshot = (empty_transcript_snapshot() | {"size_bytes": source_size}
                | (source_details or {}))
    for field, value in snapshot.items():
        setattr(record, field, value)
    record.indexing_completed_at = None if retry else datetime.now(UTC)


def complete_transcript_job(
    factory: sessionmaker[Session], job: TranscriptJob,
    result: TranscriptResult | None, error: ExtractionError | None,
    source_size: int | None = None, source_details: dict | None = None,
    detected_client: str | None = None,
    context: JobContext | None = None,
    normalized: NormalizedConversation | None = None,
) -> None:
    with factory() as database:
        project_id = database.scalar(select(transcript_project_id()).select_from(Transcript)
            .outerjoin(WorkItem, Transcript.work_item_id == WorkItem.id)
            .where(Transcript.id == job.transcript_id))
        if project_id is None:
            return
        with project_mutation(database, project_id):
            if context is not None:
                context.assert_owned(database)
            record = database.scalar(select(Transcript).where(
                Transcript.id == job.transcript_id, Transcript.generation == job.generation,
                Transcript.snapshot_id == job.snapshot_id,
                Transcript.copy_sha256 == job.copy.sha256,
                (Transcript.status == "processing") | (Transcript.reindex_status == "processing"),
                Transcript.lease_token == job.lease_token,
            ).with_for_update())
            if record is None:
                return
            if (detected_client is not None and job.imported and record.kind == "imported"
                    and (result is not None or record.status != "ready")):
                record.client = detected_client
            record.lease_token = None
            record.lease_expires_at = None
            if normalized is not None:
                publish_normalization(database, record, normalized,
                                      activate=result is not None or record.status != "ready")
            elif error is not None:
                record.normalization_status = "pending" if error.retryable else "failed"
                record.normalization_error_code = error.code
            if error is not None:
                _index_failure(record, error, source_size, source_details)
            elif result is not None:
                _save_result(record, result)
                if result.complete_text_sha256 is not None:
                    publish_complete_text(database, record)
                record.indexing_started_at = job.started_at
                record.indexing_completed_at = datetime.now(UTC)
                record.last_updated_at = result.last_updated_at or record.source_modified_at
                record.extracted_metadata = timeline_metadata(record.extracted_metadata,
                    started_at=result.session_started_at, updated_at=record.last_updated_at,
                    source_modified_at=record.source_modified_at,
                    indexed_at=record.indexing_completed_at)
            else:
                raise ValueError("Transcript publication requires result or safe error")
            database.commit()


def index_next_transcript(
    factory: sessionmaker[Session], settings: Settings,
    transcript_id: UUID | None = None, generation: int | None = None,
    context: JobContext | None = None,
) -> bool:
    job = claim_transcript_job(factory, settings, transcript_id, generation)
    if job is None:
        return False
    result, error, size, detected_client = None, None, None, None
    source_details = {
        "last_updated_at": job.copy.source_modified_at,
        "extracted_metadata": timeline_metadata({}, started_at=None,
            updated_at=job.copy.source_modified_at, source_modified_at=job.copy.source_modified_at,
            indexed_at=None),
    }
    normalized = None
    try:
        with factory() as database:
            normalized = load_normalization(database, job.transcript_id,
                                             job.snapshot_id, job.copy.sha256)
            bounds = (retained_time_bounds(database, job.transcript_id, normalized.revision)
                      if normalized is not None else (None, None))
        if normalized is None:
            storage = TranscriptStorage(settings.transcript_root, job.maximum_bytes)
            with storage.open_copy(job.copy) as content:
                detected_client = detect_transcript_client(content) if job.imported else None
                content.seek(0)
                normalized = normalize_transcript(content, detected_client or job.client,
                                                  job.snapshot_id)
            bounds = time_bounds(segment.timestamp for segment in normalized.segments)
        size = job.copy.size_bytes
        source_details.update(sha256=job.copy.sha256, format=normalized.format,
            mime_type=normalized.mime_type,
            last_updated_at=bounds[1] or job.copy.source_modified_at,
            extracted_metadata=timeline_metadata(normalized.metadata, started_at=bounds[0],
                updated_at=bounds[1] or job.copy.source_modified_at,
                source_modified_at=job.copy.source_modified_at, indexed_at=None))
        # Native adapters already produce normalized text. Sending it through an
        # artifact extractor imposed its unrelated two-million-character limit and
        # made structured conversations depend on Tika availability.
        result = TranscriptResult(ExtractedArtifact("", normalized.metadata, False),
            size, job.copy.sha256, normalized.format, normalized.mime_type, *bounds,
            complete_text_sha256=text_digest(normalized.segments))
    except ExtractionError as failure:
        error = failure
        if failure.code == "transcript_copy_integrity_failed" and normalized is not None:
            if isinstance(normalized.segments, TranscriptSegments):
                normalized.segments.close()
            normalized = None
    except OSError as failure:
        error = access_error(failure, str(settings.transcript_root), operation="write_storage")
    try:
        complete_transcript_job(factory, job, result, error, size, source_details, detected_client,
                                context, normalized)
    finally:
        if normalized is not None and isinstance(normalized.segments, TranscriptSegments):
            normalized.segments.close()
    return True


async def transcript_indexing_loop(
    factory: sessionmaker[Session], settings: Settings,
) -> None:
    while True:
        try:
            processed = await run_in_threadpool(index_next_transcript, factory, settings)
        except (SQLAlchemyError, OSError, ApplicationError) as error:
            logger.warning("Transcript indexing unavailable (%s)", type(error).__name__)
            processed = False
        await asyncio.sleep(0.1 if processed else 5)
