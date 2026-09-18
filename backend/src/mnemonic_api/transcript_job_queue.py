"""Reconcile durable transcript lifecycle rows into targeted RabbitMQ jobs."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.models import Transcript, WorkItem
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.transcripts import transcript_project_id
from mnemonic_api.transcript_copying import _copy_failure, claimable_copies, copy_next_transcript
from mnemonic_api.transcript_indexing import (
    _claimable_transcripts,
    _index_failure,
    index_next_transcript,
)
from mnemonic_jobs.ledger import JobContext, PermanentJobError, RetryJob, enqueue_job
from mnemonic_jobs.models import BackgroundJob


def _fail_exhausted(database: Session, identity: UUID, generation: int,
                    kind: str, dedupe_key: str, attempt: int, due) -> bool:
    exhausted = database.scalar(select(BackgroundJob.id).where(
        BackgroundJob.dedupe_key == dedupe_key, BackgroundJob.status == "failed",
        BackgroundJob.error_code == "attempts_exhausted",
    ))
    if exhausted is None:
        return False
    project_id = database.scalar(select(transcript_project_id()).select_from(Transcript)
        .outerjoin(WorkItem, WorkItem.id == Transcript.work_item_id)
        .where(Transcript.id == identity))
    if project_id is None:
        return True
    with project_mutation(database, project_id):
        attempts = Transcript.copy_attempts if kind == "transcript_copy" else Transcript.attempts
        due_at = (Transcript.copy_next_attempt_at if kind == "transcript_copy"
                  else Transcript.next_attempt_at)
        record = database.scalar(select(Transcript).where(
            Transcript.id == identity, Transcript.generation == generation,
            attempts == attempt, due_at == due).with_for_update())
        if record is None:
            return True
        now = database.execute(select(func.clock_timestamp())).scalar_one()
        if kind == "transcript_copy" and record.copy_status == "pending":
            _copy_failure(record, ExtractionError("transcript_job_exhausted"), now)
        elif kind == "transcript_index" and (
            record.status in {"waiting", "pending"} or record.reindex_status == "pending"
        ):
            _index_failure(record, ExtractionError("transcript_job_exhausted"), None, None)
    return True


def enqueue_transcript_jobs(database: Session, settings: Settings) -> int:
    """Domain state is the outbox, including legacy rows and lease expiry.

    No source paths or bytes are sent to RabbitMQ. The worker reads validated
    identifiers from the database and rechecks project pause/lease guards.
    Each domain attempt has one enqueue identity; a durable retry advances it.
    """
    count = 0
    for kind, statement, attempts, due_at in (
        ("transcript_copy", claimable_copies(settings), Transcript.copy_attempts,
         Transcript.copy_next_attempt_at),
        ("transcript_index", _claimable_transcripts(settings), Transcript.attempts,
         Transcript.next_attempt_at),
    ):
        rows = database.execute(statement.with_only_columns(
            Transcript.id, Transcript.generation, attempts, due_at,
        ).order_by(due_at, Transcript.id).limit(100)).all()
        for identity, generation, attempt, due in rows:
            # Recovery can reset its bounded attempt budget. The committed due
            # time distinguishes that new retry from a completed earlier attempt.
            dedupe_key = f"{kind}:{identity}:{generation}:{attempt}:{due.isoformat()}"
            if _fail_exhausted(database, identity, generation, kind, dedupe_key, attempt, due):
                continue
            enqueue_job(database, kind, dedupe_key,
                        {"transcript_id": str(identity), "generation": generation})
            count += 1
    return count


def _disposition(factory: sessionmaker[Session], context: JobContext,
                  processed: bool) -> dict:
    copying = context.kind == "transcript_copy"
    status = (Transcript.copy_status if copying else
              func.coalesce(Transcript.reindex_status, Transcript.status))
    error = (Transcript.copy_error_code if copying else
             func.coalesce(Transcript.reindex_error_code, Transcript.error_code))
    with factory() as database:
        row = database.execute(select(Transcript.generation, status, error).where(
            Transcript.id == UUID(context.payload["transcript_id"]))).first()
    if row is None or row.generation != context.payload["generation"]:
        return {"disposition": "obsolete"}
    if row[1] == "failed":
        raise PermanentJobError(row[2] or "transcript_job_failed")
    if processed:
        # A pending retry has its due time committed in the domain record. The
        # next dispatcher pass schedules that attempt under its next identity.
        return {"disposition": row[1]}
    if row[1] == "ready":
        return {"disposition": "ready"}
    raise RetryJob("transcript_job_deferred", 30, consume_attempt=False)


def handle_transcript_copy(factory: sessionmaker[Session], settings: Settings,
                            context: JobContext) -> dict:
    processed = copy_next_transcript(factory, settings,
        UUID(context.payload["transcript_id"]), context.payload["generation"], context)
    return _disposition(factory, context, processed)


def handle_transcript_index(factory: sessionmaker[Session], settings: Settings,
                            context: JobContext) -> dict:
    processed = index_next_transcript(factory, settings,
        UUID(context.payload["transcript_id"]), context.payload["generation"], context)
    return _disposition(factory, context, processed)
