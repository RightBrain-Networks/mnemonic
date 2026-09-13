"""Real PostgreSQL tests for outbox recovery, dedupe, retries and stale owners."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from mnemonic_jobs import LostLease, PermanentJobError, RetryJob, enqueue_job
from mnemonic_jobs.ledger import claim_job, finish_job, heartbeat_job, reserve_dispatch
from mnemonic_jobs.models import BackgroundJob

pytestmark = pytest.mark.postgres


@pytest.fixture
def jobs(pristine_postgres_engine):
    return sessionmaker(pristine_postgres_engine, expire_on_commit=False)


def enqueue(jobs, **kwargs):
    with jobs.begin() as database:
        return enqueue_job(database, "backup_create", str(uuid4()),
                           {"project_id": str(uuid4())}, **kwargs)


def expire(jobs, identifier):
    with jobs.begin() as database:
        database.execute(update(BackgroundJob).where(BackgroundJob.id == identifier).values(
            lease_expires_at=func.clock_timestamp() - timedelta(seconds=1),
            publish_after=func.clock_timestamp() - timedelta(seconds=1),
        ))


def test_enqueue_commits_with_domain_transaction_and_preserves_dedupe(jobs):
    key, payload = str(uuid4()), {"project_id": str(uuid4())}
    with jobs.begin() as database:
        identifier = enqueue_job(database, "backup_create", key, payload)
        assert enqueue_job(database, "backup_create", key, payload) == identifier
    with jobs.begin() as database:
        assert enqueue_job(database, "backup_create", key, payload) == identifier
        with pytest.raises(ValueError, match="another operation"):
            enqueue_job(database, "backup_create", key, {"project_id": str(uuid4())})
    with pytest.raises(RuntimeError), jobs.begin() as database:
        enqueue_job(database, "backup_create", "rolled-back", payload)
        raise RuntimeError("domain transaction failed")
    with jobs() as database:
        assert database.scalar(select(func.count()).select_from(BackgroundJob)) == 1


def test_duplicate_deliveries_cannot_claim_running_or_completed_jobs(jobs):
    identifier = enqueue(jobs)
    with jobs.begin() as first:
        context = claim_job(first, identifier)
        with jobs.begin() as second:
            assert claim_job(second, identifier) is None
    with jobs.begin() as database:
        assert claim_job(database, identifier) is None
        assert heartbeat_job(database, context)
        assert finish_job(database, context, result={"backup_id": "synthetic"})
    with jobs.begin() as database:
        assert claim_job(database, identifier) is None
        row = database.get(BackgroundJob, identifier)
        assert row.status == "succeeded" and row.attempts == 1
        assert row.result == {"backup_id": "synthetic"}


def test_expired_owner_cannot_complete_or_extend_replacement_lease(jobs):
    identifier = enqueue(jobs)
    with jobs.begin() as database:
        old = claim_job(database, identifier)
    expire(jobs, identifier)
    with jobs.begin() as database:
        current = claim_job(database, identifier)
    with jobs.begin() as database:
        assert not heartbeat_job(database, old)
        assert not finish_job(database, old)
        with pytest.raises(LostLease):
            old.assert_owned(database)
        current.assert_owned(database)
        assert finish_job(database, current)


def test_dispatch_republishes_lost_messages_without_republishing_live_claims(jobs):
    identifier = enqueue(jobs)
    future = enqueue(jobs, due_at=datetime.now(UTC) + timedelta(days=1))
    with jobs.begin() as database:
        assert reserve_dispatch(database) == [identifier]
    with jobs.begin() as database:
        assert reserve_dispatch(database) == []
        database.execute(update(BackgroundJob).where(BackgroundJob.id == identifier).values(
            publish_after=func.clock_timestamp() - timedelta(seconds=1),
        ))
    with jobs.begin() as database:
        assert reserve_dispatch(database) == [identifier]
        claim_job(database, identifier)
    with jobs.begin() as database:
        assert reserve_dispatch(database) == []
    expire(jobs, identifier)
    with jobs.begin() as database:
        assert reserve_dispatch(database) == [identifier]
        assert claim_job(database, future) is None


def test_retry_delay_and_attempt_limit_are_durable(jobs):
    identifier = enqueue(jobs, max_attempts=1)
    with jobs.begin() as database:
        context = claim_job(database, identifier)
        assert finish_job(database, context, error=RetryJob("file_busy", 30))
    with jobs.begin() as database:
        assert claim_job(database, identifier) is None
        assert reserve_dispatch(database) == []
        row = database.get(BackgroundJob, identifier)
        assert row.status == "pending" and row.error_code == "file_busy"
        row.due_at = datetime.now(UTC) - timedelta(seconds=1)
    with jobs.begin() as database:
        assert claim_job(database, identifier) is None
    with jobs() as database:
        row = database.get(BackgroundJob, identifier)
        assert row.status == "failed" and row.error_code == "attempts_exhausted"
        assert row.lease_token is None and row.completed_at is not None


def test_permanent_failure_retains_durable_error(jobs):
    identifier = enqueue(jobs)
    with jobs.begin() as database:
        context = claim_job(database, identifier)
        assert finish_job(database, context, error=PermanentJobError("invalid_source"))
    with jobs() as database:
        row = database.get(BackgroundJob, identifier)
        assert row.status == "failed" and row.error_code == "invalid_source"


def test_domain_deferral_preserves_execution_attempt_budget(jobs):
    identifier = enqueue(jobs, max_attempts=1)
    with jobs.begin() as database:
        context = claim_job(database, identifier)
        assert finish_job(database, context, error=RetryJob(
            "transcript_active", 30, consume_attempt=False,
        ))
    with jobs.begin() as database:
        row = database.get(BackgroundJob, identifier)
        assert row.status == "pending" and row.attempts == 0
        row.due_at = datetime.now(UTC) - timedelta(seconds=1)
    with jobs.begin() as database:
        context = claim_job(database, identifier)
        assert context is not None and context.attempts == 1
        assert finish_job(database, context)


@pytest.mark.parametrize("payload", [
    {"project_id": str(uuid4()), "path": "/private"}, {"project_id": "invalid"},
    {"transcript_id": str(uuid4()), "generation": True},
])
def test_enqueue_rejects_non_identifier_payload(jobs, payload):
    with jobs.begin() as database, pytest.raises(ValueError):
        enqueue_job(database, "backup_create", str(uuid4()), payload)
