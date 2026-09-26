"""Demand-driven duplicate vectors, one durable native batch per worker delivery."""

import hashlib
import logging
from dataclasses import dataclass
from math import hypot
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import String, cast, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.database import begin_coherent_read
from mnemonic_api.models import (
    BackgroundJob,
    DuplicateEmbeddingRefresh,
    WorkItem,
    WorkItemEmbedding,
)
from mnemonic_api.pool_deadlines import pool_checkout_deadline
from mnemonic_api.search_timing import timed_phase
from mnemonic_api.semantic import EMBED_BATCH_SIZE, Embedder
from mnemonic_api.services import duplicate_suggestions as suggestions
from mnemonic_jobs.ledger import JobContext, RetryJob, enqueue_job

logger = logging.getLogger(__name__)
REFRESHES = DuplicateEmbeddingRefresh.__table__


def configuration() -> str:
    # Dimensions are verified on cache reads; zero binds all other configuration fields.
    return hashlib.sha256(suggestions._cache_version(0).encode()).hexdigest()


def request_refresh(database: Session, project_id: UUID,
                    candidates: dict[UUID, suggestions.VectorCandidate], *, deadline: float,
                    dimensions: int | None = None) -> str:
    """Publish a disposable demand marker after the response read snapshot has closed."""
    missing = sorted((item for item in candidates.values() if item.cached_vector is None
                      or dimensions is not None and len(item.cached_vector) != dimensions),
                     key=lambda item: item.work.id)
    if not missing:
        return "not_needed"
    fingerprint = hashlib.sha256("\n".join(
        f"{item.work.id}:{item.digest}"
        for item in sorted(candidates.values(), key=lambda item: item.work.id)
    ).encode()).hexdigest()
    with Session(bind=database.get_bind()) as writer:
        with pool_checkout_deadline(deadline):
            writer.connection()
        suggestions._set_transaction_deadline(writer, deadline,
                                               lock_timeout_milliseconds=50)
        values = dict(id=uuid4(), project_id=project_id, config=configuration(),
                      fingerprint=fingerprint, target_ids=[item.work.id for item in missing])
        statement = insert(REFRESHES).values(**values).on_conflict_do_nothing(
            index_elements=[REFRESHES.c.project_id])
        writer.execute(statement)
        current = writer.execute(select(REFRESHES).where(
            REFRESHES.c.project_id == project_id).with_for_update()).mappings().one()
        if current["config"] == values["config"] and current["status"] == "pending":
            state = "queued"
        elif (current["status"] == "failed" and current["config"] == values["config"]
              and current["fingerprint"] == fingerprint):
            state = "failed"  # Repeated reads cannot reset an exhausted retry budget.
        else:
            writer.execute(update(REFRESHES).where(REFRESHES.c.project_id == project_id)
                .values(**values, next_offset=0, status="pending", error_code=None,
                        updated_at=func.clock_timestamp()))
            state = "queued"
        writer.commit()
    logger.info("Duplicate cache refresh status=%s missing=%d", state, len(missing))
    return state


def enqueue_duplicate_embedding_jobs(database: Session) -> int:
    cursor_job = (
        BackgroundJob.kind == "duplicate_embed",
        BackgroundJob.payload["refresh_id"].astext == cast(REFRESHES.c.id, String),
        BackgroundJob.payload["offset"].as_integer() == REFRESHES.c.next_offset,
    )
    failed = exists(select(BackgroundJob.id).where(*cursor_job, BackgroundJob.status == "failed"))
    exhausted = select(REFRESHES.c.id).where(REFRESHES.c.status == "pending", failed).limit(100)
    database.execute(update(REFRESHES).where(REFRESHES.c.id.in_(exhausted)).values(
        status="failed", error_code="duplicate_embedding_failed",
        updated_at=func.clock_timestamp()))
    # The ledger already owns dispatch/retry for enqueued cursors. Exclude them
    # before LIMIT so old jobs in backoff cannot starve a new project's demand.
    enqueued = exists(select(BackgroundJob.id).where(*cursor_job))
    rows = database.execute(select(REFRESHES).where(
        REFRESHES.c.status == "pending", ~enqueued)
        .order_by(REFRESHES.c.updated_at, REFRESHES.c.id).limit(100)).mappings()
    count = 0
    for row in rows:
        key = f"duplicate_embed:{row['id']}:{row['next_offset']}"
        enqueue_job(database, "duplicate_embed", key,
                    {"refresh_id": str(row["id"]), "offset": row["next_offset"]})
        count += 1
    return count


@dataclass(frozen=True)
class DuplicateBatch:
    refresh_id: UUID
    project_id: UUID
    config: str
    offset: int
    end: int
    total: int
    candidates: tuple[suggestions.VectorCandidate, ...]


def _capture(factory: sessionmaker[Session], context: JobContext) -> DuplicateBatch | None:
    with factory() as database:
        begin_coherent_read(database, read_only=False)
        suggestions._set_transaction_deadline(database, monotonic() + 5,
                                               lock_timeout_milliseconds=50)
        context.assert_owned(database)
        row = database.execute(select(REFRESHES).where(
            REFRESHES.c.id == UUID(context.payload["refresh_id"]),
            REFRESHES.c.next_offset == context.payload["offset"],
            REFRESHES.c.status == "pending")).mappings().first()
        if row is None or row["config"] != configuration():
            return None
        end = min(len(row["target_ids"]), row["next_offset"] + EMBED_BATCH_SIZE)
        ids = row["target_ids"][row["next_offset"]:end]
        work = list(database.scalars(select(WorkItem).where(
            WorkItem.id.in_(ids), WorkItem.project_id == row["project_id"],
            WorkItem.deleted_at.is_(None)).order_by(WorkItem.id)))
        snapshots = {item.id: suggestions._work_snapshot(item) for item in work}
        candidates = suggestions._capture_vectors(database, snapshots, snapshots)
        batch = DuplicateBatch(row["id"], row["project_id"], row["config"], row["next_offset"],
                               end, len(row["target_ids"]), tuple(candidates.values()))
        database.commit()
        return batch


def _updates(batch: DuplicateBatch, embedder: Embedder) -> list[suggestions.CacheUpdate]:
    # Frozen targets were missing in the requesting snapshot, including vectors
    # whose stored dimensions disagreed with query inference. Recompute each
    # targeted slice; prior committed slices are skipped by the durable cursor.
    missing = list(batch.candidates)
    if not missing:
        return []
    vectors = embedder.embed_documents([item.text for item in missing])
    dimensions = len(vectors[0]) if vectors else 0
    if (len(vectors) != len(missing) or not 1 <= dimensions <= 4096
            or any(not suggestions._valid_vector(vector, dimensions) for vector in vectors)):
        raise ValueError("Invalid duplicate embedding batch")
    vectors = [[value / hypot(*vector) for value in vector] for vector in vectors]
    return [suggestions.CacheUpdate(item.work.id, item.work.project_id, item.work.version,
                                   item.digest, tuple(float(value) for value in vector))
            for item, vector in zip(missing, vectors, strict=True)]


def _publish_vectors(database: Session, batch: DuplicateBatch,
                     updates: list[suggestions.CacheUpdate]) -> bool:
    if not updates:
        return True
    by_id = {item.work_item_id: item for item in updates}
    current = list(database.scalars(select(WorkItem).where(
        WorkItem.id.in_(by_id), WorkItem.project_id == batch.project_id,
        WorkItem.deleted_at.is_(None)).order_by(WorkItem.id).with_for_update(skip_locked=True)))
    compositions = suggestions._bounded_compositions(database, [item.id for item in current])
    rows = suggestions._current_cache_rows(current, compositions, by_id, len(updates[0].vector))
    if rows:
        statement = insert(WorkItemEmbedding).values(rows)
        database.execute(statement.on_conflict_do_update(
            index_elements=[WorkItemEmbedding.work_item_id, WorkItemEmbedding.purpose],
            set_={"model": statement.excluded.model, "digest": statement.excluded.digest,
                  "vector": statement.excluded.vector, "updated_at": func.clock_timestamp()}))
    remaining = set(database.scalars(select(WorkItem.id).where(
        WorkItem.id.in_(by_id), WorkItem.project_id == batch.project_id,
        WorkItem.deleted_at.is_(None)))) - {row["work_item_id"] for row in rows}
    return not remaining


def _publish(factory: sessionmaker[Session], context: JobContext, batch: DuplicateBatch,
             updates: list[suggestions.CacheUpdate]) -> str:
    with factory.begin() as database:
        suggestions._set_transaction_deadline(database, monotonic() + 5,
                                               lock_timeout_milliseconds=50)
        context.assert_owned(database)
        row = database.execute(select(REFRESHES.c.id).where(
            REFRESHES.c.id == batch.refresh_id, REFRESHES.c.config == batch.config,
            REFRESHES.c.next_offset == batch.offset, REFRESHES.c.status == "pending")
            .with_for_update()).first()
        if row is None or batch.config != configuration():
            return "obsolete"
        if not _publish_vectors(database, batch, updates):
            return "deferred"
        state = "completed" if batch.end == batch.total else "pending"
        database.execute(update(REFRESHES).where(REFRESHES.c.id == batch.refresh_id).values(
            next_offset=batch.end, status=state, error_code=None,
            updated_at=func.clock_timestamp()))
        return state


def handle_duplicate_embedding(factory: sessionmaker[Session], embedder: Embedder,
                               context: JobContext) -> dict:
    batch = _capture(factory, context)
    if batch is None:
        return {"disposition": "obsolete"}
    try:
        with timed_phase("duplicate_suggestions", "document_inference"):
            updates = _updates(batch, embedder)
    except Exception:
        raise RetryJob("duplicate_embedding_unavailable",
                       min(3600, 30 * 2 ** min(context.attempts, 7))) from None
    with timed_phase("duplicate_suggestions", "cache_refresh"):
        state = _publish(factory, context, batch, updates)
    if state == "deferred":
        raise RetryJob("duplicate_embedding_changed", 1)
    logger.info("Duplicate cache batch status=%s candidates=%d embedded=%d",
                state, len(batch.candidates), len(updates))
    return {"disposition": state, "embedded": len(updates)}
