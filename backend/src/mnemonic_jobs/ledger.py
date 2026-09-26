"""Transactional enqueue, expiring ownership, and durable retry decisions."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Event
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mnemonic_jobs.models import BackgroundJob

KINDS = frozenset({
    "transcript_copy", "transcript_index", "backup_create", "artifact_embed", "duplicate_embed",
})


class PermanentJobError(Exception):
    def __init__(self, code: str):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
            raise ValueError("Job error codes must be safe identifiers")
        self.code = code
        super().__init__(code)


class RetryJob(PermanentJobError):
    def __init__(self, code: str, delay_seconds: float = 30, *, consume_attempt: bool = True):
        super().__init__(code)
        self.delay_seconds = min(3600.0, max(1.0, delay_seconds))
        self.consume_attempt = consume_attempt


class LostLease(Exception):
    """Another delivery may now own the job; this attempt cannot publish."""


@dataclass(frozen=True)
class JobContext:
    job_id: UUID
    kind: str
    payload: dict[str, Any]
    lease_token: UUID
    attempts: int
    lost: Event = field(default_factory=Event, compare=False)

    def assert_owned(self, database: Session) -> None:
        """Lock the ledger row in the transaction which publishes domain results."""
        if self.lost.is_set() or database.scalar(
            select(BackgroundJob.id).where(*_owned(self)).with_for_update()
        ) is None:
            raise LostLease()


def _validate_payload(kind: str, payload: dict[str, Any]) -> None:
    identifier_key = {"backup_create": "project_id", "artifact_embed": "passage_index_id",
                      "duplicate_embed": "refresh_id"}.get(
        kind, "transcript_id",
    )
    counter = "offset" if kind in {"artifact_embed", "duplicate_embed"} else "generation"
    allowed = {identifier_key} if kind == "backup_create" else {identifier_key, counter}
    if kind not in KINDS or set(payload) != allowed:
        raise ValueError("Job payload must contain only the required identifiers")
    identifier = payload[identifier_key]
    if not isinstance(identifier, str) or str(UUID(identifier)) != identifier:
        raise ValueError("Job identifiers must be canonical UUID strings")
    if counter in payload and (
        type(payload[counter]) is not int or payload[counter] < (0 if counter == "offset" else 1)
        or (counter == "offset" and payload[counter] > 8_000_000)
    ):
        raise ValueError("Job cursor or generation is invalid")


def enqueue_job(database: Session, kind: str, dedupe_key: str, payload: dict[str, Any], *,
                due_at: datetime | None = None, max_attempts: int = 20) -> UUID:
    """Insert into the caller's transaction, retaining completed dedupe receipts."""
    _validate_payload(kind, payload)
    if not dedupe_key or len(dedupe_key) > 200 or not 1 <= max_attempts <= 100:
        raise ValueError("Invalid job dedupe key or attempt limit")
    values: dict[str, Any] = {
        "id": uuid4(), "kind": kind, "dedupe_key": dedupe_key, "payload": payload,
        "max_attempts": max_attempts,
    }
    if due_at is not None:
        values["due_at"] = due_at
    identifier = database.scalar(insert(BackgroundJob).values(**values).on_conflict_do_nothing(
        index_elements=[BackgroundJob.dedupe_key]
    ).returning(BackgroundJob.id))
    if identifier is not None:
        return identifier
    existing = database.scalars(select(BackgroundJob).where(
        BackgroundJob.dedupe_key == dedupe_key
    )).one()
    if existing.kind != kind or existing.payload != payload:
        raise ValueError("Job dedupe key already describes another operation")
    return existing.id


def _claimable():
    return or_(BackgroundJob.status == "pending", and_(
        BackgroundJob.status == "running", BackgroundJob.lease_expires_at <= func.clock_timestamp()
    ))


def reserve_dispatch(database: Session, *, limit: int = 50,
                     redelivery_seconds: int = 60) -> list[UUID]:
    """Reserve a bounded publish batch; expired reservations recover broker loss.

    The reservation commits before publishing. A crash or negative confirmation
    merely delays another publication; no broker state can erase the outbox.
    """
    rows = list(database.scalars(select(BackgroundJob).where(
        _claimable(), BackgroundJob.due_at <= func.clock_timestamp(),
        BackgroundJob.publish_after <= func.clock_timestamp(),
    ).order_by(BackgroundJob.publish_after, BackgroundJob.id).limit(limit)
      .with_for_update(skip_locked=True)))
    now = database.execute(select(func.clock_timestamp())).scalar_one()
    for row in rows:
        row.publish_after = now + timedelta(seconds=redelivery_seconds)
    return [row.id for row in rows]


def claim_job(database: Session, job_id: UUID, *, lease_seconds: int = 120) -> JobContext | None:
    row = database.scalar(select(BackgroundJob).where(
        BackgroundJob.id == job_id, _claimable(),
        BackgroundJob.due_at <= func.clock_timestamp(),
    ).with_for_update(skip_locked=True))
    if row is None:
        return None
    now = database.execute(select(func.clock_timestamp())).scalar_one()
    if row.attempts >= row.max_attempts:
        row.status, row.error_code, row.completed_at = "failed", "attempts_exhausted", now
        row.lease_token = row.lease_expires_at = None
        return None
    token = uuid4()
    row.status, row.lease_token = "running", token
    row.attempts += 1
    row.updated_at, row.lease_expires_at = now, now + timedelta(seconds=lease_seconds)
    return JobContext(row.id, row.kind, dict(row.payload), token, row.attempts)


def _owned(context: JobContext) -> tuple:
    return (BackgroundJob.id == context.job_id, BackgroundJob.status == "running",
            BackgroundJob.lease_token == context.lease_token,
            BackgroundJob.lease_expires_at > func.clock_timestamp())


def heartbeat_job(database: Session, context: JobContext, *, lease_seconds: int = 120) -> bool:
    return database.scalar(update(BackgroundJob).where(*_owned(context)).values(
        lease_expires_at=func.clock_timestamp() + timedelta(seconds=lease_seconds),
        updated_at=func.clock_timestamp(),
    ).returning(BackgroundJob.id)) is not None


def finish_job(database: Session, context: JobContext, *, result: dict | None = None,
               error: PermanentJobError | None = None) -> bool:
    retry = isinstance(error, RetryJob)
    values: dict[str, Any] = {
        "status": "failed" if error else "succeeded", "result": result,
        "error_code": error.code if error else None, "lease_token": None,
        "lease_expires_at": None, "updated_at": func.clock_timestamp(),
        "completed_at": func.clock_timestamp(),
    }
    if retry:
        values.update(status="pending", completed_at=None,
                      due_at=func.clock_timestamp() + timedelta(seconds=error.delay_seconds),
                      publish_after=func.clock_timestamp())
        if not error.consume_attempt:
            values["attempts"] = BackgroundJob.attempts - 1
    return database.scalar(update(BackgroundJob).where(*_owned(context)).values(**values)
                           .returning(BackgroundJob.id)) is not None
