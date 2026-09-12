"""Persisted, expiring transcript jobs; no database lock spans filesystem or Tika IO."""

import asyncio
import hashlib
import io
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import exists, false, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from mnemonic_api.artifact_extraction import Extractor
from mnemonic_api.artifact_tika import ExtractedArtifact, ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Transcript, TranscriptSettings, WorkItem, WorkLease
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.transcripts import transcript_project_id
from mnemonic_api.transcript_detection import detect_transcript_client
from mnemonic_api.transcript_parsers import TranscriptParserFactory
from mnemonic_api.transcript_snapshots import empty_transcript_snapshot
from mnemonic_api.transcript_storage import canonical_source_path, read_transcript

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptJob:
    transcript_id: UUID
    generation: int
    lease_token: UUID
    source_path: str
    client: str
    maximum_bytes: int
    imported: bool


@dataclass(frozen=True)
class TranscriptResult:
    extracted: ExtractedArtifact
    size_bytes: int
    sha256: str
    format: str
    mime_type: str


def _active_generation():
    return exists(select(WorkLease.work_item_id).where(
        WorkLease.work_item_id == Transcript.work_item_id,
        WorkLease.lease_generation_id == Transcript.lease_generation_id,
        WorkLease.expires_at > func.clock_timestamp(),
    ))


def _resolved_path_errors(settings: Settings):
    # Compare POSIX aliases without changing the exact agent assertion. The
    # normal reader still enforces regular files and refuses all symlinks.
    path = func.regexp_replace(Transcript.source_path, r"/\.(?=/|$)", "", "g")
    path = func.regexp_replace(path, "/+", "/", "g")
    # Path() also removes trailing separators; the root itself is not a file.
    path = func.rtrim(path, "/")
    allowed = or_(false(), *(path.startswith(
        canonical_source_path(str(root)).rstrip("/") + "/", autoescape=True,
    ) for root in settings.transcript_allowed_roots))
    return (Transcript.status == "failed") & (
        Transcript.error_code == "transcript_path_not_allowed"
    ) & allowed & ~path.regexp_match(r"(^|/)\.\.(/|$)")


def _claimable_transcripts(settings: Settings):
    return (select(Transcript, TranscriptSettings.max_file_size_bytes)
        .outerjoin(WorkItem, WorkItem.id == Transcript.work_item_id)
        .outerjoin(TranscriptSettings, TranscriptSettings.project_id == transcript_project_id())
        .where(func.coalesce(TranscriptSettings.enabled, True), ~_active_generation(), or_(
            Transcript.status.in_(["waiting", "pending"])
            & (Transcript.next_attempt_at <= func.clock_timestamp()),
            _resolved_path_errors(settings),
            (Transcript.status == "processing")
            & (Transcript.lease_expires_at <= func.clock_timestamp()),
        )))


def _lock_claim_candidate(database: Session, candidate, settings: Settings):
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
    return database.execute(_claimable_transcripts(settings).where(
        Transcript.id == transcript_id, transcript_project_id() == project_id)
                            .with_for_update(of=Transcript)).first()


def _start_claim(database: Session, row, settings: Settings) -> TranscriptJob:
    transcript, maximum = row
    now = database.execute(select(func.clock_timestamp())).scalar_one()
    for field, value in empty_transcript_snapshot().items():
        setattr(transcript, field, value)
    if transcript.status == "failed":
        # A changed allowlist starts a new attempt budget for this same source.
        transcript.attempts = 0
        transcript.indexing_started_at = None
    transcript.error_code = None
    transcript.status = "processing"
    transcript.indexing_started_at = transcript.indexing_started_at or now
    transcript.indexing_completed_at = None
    transcript.lease_token = uuid4()
    transcript.lease_expires_at = now + timedelta(
        seconds=settings.artifact_extraction_timeout_seconds * 2 + 300)
    transcript.attempts += 1
    return TranscriptJob(transcript.id, transcript.generation, transcript.lease_token,
                         transcript.source_path, transcript.client,
                         min(maximum or settings.transcript_max_bytes,
                             settings.transcript_max_bytes), transcript.kind == "imported")


def claim_transcript_job(
    factory: sessionmaker[Session], settings: Settings,
) -> TranscriptJob | None:
    with factory() as database:
        candidate = database.execute(_claimable_transcripts(settings).with_only_columns(
            Transcript.id, Transcript.work_item_id, transcript_project_id().label("project_id"))
            .order_by(Transcript.next_attempt_at, Transcript.id).limit(1)).first()
        if candidate is None:
            return None
        # Follow the same project -> work -> lease -> transcript lock order as
        # lifecycle mutations. No candidate lock is taken before the project.
        with project_mutation(database, candidate.project_id):
            row = _lock_claim_candidate(database, candidate, settings)
            if row is None:
                return None
            job = _start_claim(database, row, settings)
            database.commit()
            return job


def _save_result(record: Transcript, result: TranscriptResult) -> None:
    record.status = "ready"
    record.error_code = None
    record.normalized_text = result.extracted.text
    record.text_sha256 = hashlib.sha256(result.extracted.text.encode("utf-8")).hexdigest()
    record.extracted_metadata = result.extracted.metadata
    record.truncated = result.extracted.truncated
    record.size_bytes = result.size_bytes
    record.sha256 = result.sha256
    record.format = result.format
    record.mime_type = result.mime_type


def complete_transcript_job(
    factory: sessionmaker[Session], job: TranscriptJob,
    result: TranscriptResult | None, error: ExtractionError | None,
    source_size: int | None = None, source_details: dict | None = None,
    detected_client: str | None = None,
) -> None:
    with factory() as database:
        project_id = database.scalar(select(transcript_project_id()).select_from(Transcript)
            .outerjoin(WorkItem, Transcript.work_item_id == WorkItem.id)
            .where(Transcript.id == job.transcript_id))
        if project_id is None:
            return
        with project_mutation(database, project_id):
            record = database.scalar(select(Transcript).where(
                Transcript.id == job.transcript_id, Transcript.generation == job.generation,
                Transcript.status == "processing", Transcript.lease_token == job.lease_token,
            ).with_for_update())
            if record is None:
                return
            if detected_client is not None and job.imported and record.kind == "imported":
                record.client = detected_client
            record.lease_token = None
            record.lease_expires_at = None
            record.indexing_completed_at = datetime.now(UTC)
            if error is not None:
                retry = error.retryable and record.attempts < 3
                record.status = "pending" if retry else "failed"
                record.error_code = error.code
                snapshot = (empty_transcript_snapshot() | {"size_bytes": source_size}
                            | (source_details or {}))
                for field, value in snapshot.items():
                    setattr(record, field, value)
                record.next_attempt_at = datetime.now(UTC) + timedelta(seconds=30 * record.attempts)
                if retry:
                    record.indexing_completed_at = None
            elif result is not None:
                _save_result(record, result)
            else:
                raise ValueError("Transcript publication requires result or safe error")
            database.commit()


def _combined_metadata(extracted: ExtractedArtifact, parsed_metadata: dict) -> tuple[dict, bool]:
    metadata = dict(parsed_metadata)
    truncated = False
    for key, value in extracted.metadata.items():
        candidate = metadata | {key: value}
        if len(candidate) > 64 or len(json.dumps(candidate, ensure_ascii=True).encode()) > 8192:
            truncated = True
        else:
            metadata = candidate
    return metadata, truncated


def index_next_transcript(
    factory: sessionmaker[Session], settings: Settings, extractor: Extractor,
) -> bool:
    job = claim_transcript_job(factory, settings)
    if job is None:
        return False
    result, error, size, detected_client = None, None, None, None
    source_details = {}
    try:
        parser = None if job.imported else TranscriptParserFactory.create(job.client)
        data = read_transcript(job.source_path, settings.transcript_allowed_roots,
                               job.maximum_bytes)
        size = len(data)
        source_details["sha256"] = hashlib.sha256(data).hexdigest()
        if parser is None:
            detected_client = detect_transcript_client(io.BytesIO(data))
            parser = TranscriptParserFactory.create(detected_client)
        parsed = parser.parse(data, settings.artifact_extraction_max_chars)
        source_details.update(format=parsed.format, mime_type=parsed.mime_type,
                              extracted_metadata=parsed.metadata, truncated=parsed.truncated)
        normalized = parsed.text.encode("utf-8")
        extracted = extractor.extract(io.BytesIO(normalized), filename="transcript.txt",
                                      size_bytes=len(normalized))
        metadata, metadata_truncated = _combined_metadata(extracted, parsed.metadata)
        result = TranscriptResult(ExtractedArtifact(
            extracted.text, metadata,
            extracted.truncated or parsed.truncated or metadata_truncated,
        ), len(data), hashlib.sha256(data).hexdigest(), parsed.format, parsed.mime_type)
    except ExtractionError as failure:
        error = failure
    complete_transcript_job(factory, job, result, error, size, source_details, detected_client)
    return True


async def transcript_indexing_loop(
    factory: sessionmaker[Session], settings: Settings, extractor: Extractor,
) -> None:
    while True:
        try:
            processed = await run_in_threadpool(index_next_transcript, factory, settings, extractor)
        except (SQLAlchemyError, OSError, ApplicationError) as error:
            logger.warning("Transcript indexing unavailable (%s)", type(error).__name__)
            processed = False
        await asyncio.sleep(0.1 if processed else 5)
