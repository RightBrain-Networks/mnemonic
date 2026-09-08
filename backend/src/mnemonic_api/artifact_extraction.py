"""One bounded extraction worker, with PostgreSQL jobs and revision-safe publication.

No database transaction or project lock spans the parser call. Expiring claims
survive process loss; a changed revision or committed mutation invalidates the
claim and prevents both stale text and stale metadata from being published.
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import BinaryIO, Protocol
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from mnemonic_api.artifact_storage import ArtifactStorage, UnsafeArtifactPath
from mnemonic_api.artifact_tika import ExtractedArtifact, ExtractionError
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Artifact, ArtifactExtraction, ArtifactOperation
from mnemonic_api.services.project_mutations import project_mutation

logger = logging.getLogger(__name__)


class Extractor(Protocol):
    def extract(
        self, content: BinaryIO, *, filename: str, size_bytes: int
    ) -> ExtractedArtifact: ...


@dataclass(frozen=True)
class ExtractionJob:
    artifact_id: UUID
    project_id: UUID
    revision: int
    lease_token: UUID
    relative_path: str
    filename: str
    size_bytes: int
    sha256: str


def _pending_operation(artifact_id):
    return exists(select(ArtifactOperation.id).where(
        ArtifactOperation.artifact_id == artifact_id,
        ArtifactOperation.state == "pending",
    ))


def _claim_job(factory: sessionmaker[Session], timeout_seconds: int) -> ExtractionJob | None:
    now = datetime.now(UTC)
    with factory() as database:
        record = database.execute(
            select(ArtifactExtraction, Artifact)
            .join(Artifact, Artifact.id == ArtifactExtraction.artifact_id)
            .where(
                Artifact.revision == ArtifactExtraction.revision,
                Artifact.deleted_at.is_(None),
                ~_pending_operation(Artifact.id),
                or_(
                    (ArtifactExtraction.status == "pending")
                    & (ArtifactExtraction.next_attempt_at <= now),
                    (ArtifactExtraction.status == "processing")
                    & (ArtifactExtraction.lease_expires_at <= now),
                ),
            )
            .order_by(ArtifactExtraction.next_attempt_at, ArtifactExtraction.artifact_id)
            .with_for_update(of=ArtifactExtraction, skip_locked=True)
            .limit(1)
        ).first()
        if record is None:
            return None
        extraction, artifact = record
        token = uuid4()
        extraction.status = "processing"
        extraction.lease_token = token
        extraction.lease_expires_at = now + timedelta(seconds=timeout_seconds * 2 + 300)
        extraction.attempts += 1
        job = ExtractionJob(
            artifact.id, artifact.project_id, artifact.revision, token,
            artifact.relative_path, artifact.filename, artifact.size_bytes, artifact.sha256,
        )
        database.commit()
        return job


def _current_claim(database: Session, job: ExtractionJob) -> ArtifactExtraction | None:
    return database.scalar(
        select(ArtifactExtraction)
        .join(Artifact, Artifact.id == ArtifactExtraction.artifact_id)
        .where(
            ArtifactExtraction.artifact_id == job.artifact_id,
            ArtifactExtraction.revision == job.revision,
            ArtifactExtraction.status == "processing",
            ArtifactExtraction.lease_token == job.lease_token,
            Artifact.revision == job.revision,
            Artifact.deleted_at.is_(None),
            ~_pending_operation(Artifact.id),
        )
    )


def _open_job(
    factory: sessionmaker[Session], storage: ArtifactStorage, job: ExtractionJob
) -> BinaryIO | None:
    with factory() as database:
        with project_mutation(database, job.project_id):
            if _current_claim(database, job) is None:
                return None
            # The descriptor pins one inode; replacements may unlink it but never
            # redirect the parser to a different revision or an arbitrary path.
            content = storage.open(job.relative_path)
            try:
                database.commit()
                return content
            except BaseException:
                content.close()
                raise


def _verify_content(content: BinaryIO, job: ExtractionJob) -> None:
    digest = hashlib.sha256()
    size = 0
    while chunk := content.read(64 * 1024):
        size += len(chunk)
        if size > job.size_bytes:
            raise ExtractionError("artifact_content_mismatch")
        digest.update(chunk)
    if size != job.size_bytes or digest.hexdigest() != job.sha256:
        raise ExtractionError("artifact_content_mismatch")
    content.seek(0)


def _complete_job(
    factory: sessionmaker[Session], job: ExtractionJob,
    result: ExtractedArtifact | None, error: ExtractionError | None,
) -> None:
    with factory() as database:
        with project_mutation(database, job.project_id):
            extraction = _current_claim(database, job)
            if extraction is None:
                return
            extraction.lease_token = None
            extraction.lease_expires_at = None
            if error is not None:
                extraction.error_code = error.code
                extraction.status = "pending" if error.retryable else "failed"
                delay = min(3600, 30 * 2 ** min(extraction.attempts - 1, 7))
                extraction.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
            elif result is not None:
                extraction.status = "ready"
                extraction.normalized_text = result.text
                extraction.extracted_metadata = result.metadata
                extraction.truncated = result.truncated
                extraction.extracted_at = datetime.now(UTC)
                extraction.error_code = None
            else:
                raise ValueError("Extraction publication requires a result or safe error")
            database.commit()


def extract_next_artifact(
    factory: sessionmaker[Session], storage: ArtifactStorage, extractor: Extractor,
    timeout_seconds: int = 60,
) -> bool:
    """Process at most one due job; return whether a job was claimed, not its success."""
    job = _claim_job(factory, timeout_seconds)
    if job is None:
        return False
    result = None
    error = None
    try:
        content = _open_job(factory, storage, job)
        if content is None:
            return True
        with content:
            _verify_content(content, job)
            result = extractor.extract(content, filename=job.filename, size_bytes=job.size_bytes)
    except ExtractionError as failure:
        error = failure
    except (OSError, UnsafeArtifactPath):
        error = ExtractionError("artifact_content_unavailable")
    _complete_job(factory, job, result, error)
    return True


async def artifact_extraction_loop(
    factory: sessionmaker[Session], storage: ArtifactStorage, extractor: Extractor,
    timeout_seconds: int = 60,
) -> None:
    while True:
        try:
            processed = await run_in_threadpool(
                extract_next_artifact, factory, storage, extractor, timeout_seconds,
            )
        except (SQLAlchemyError, OSError, UnsafeArtifactPath, ApplicationError) as error:
            # Error types only: parser responses and artifact text can contain PII.
            logger.warning("Artifact extraction unavailable (%s)", type(error).__name__)
            processed = False
        await asyncio.sleep(0.1 if processed else 5)
