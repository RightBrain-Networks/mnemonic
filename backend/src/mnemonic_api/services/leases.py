"""Atomic work-lease operations and capability-token enforcement.

Callers lock the visible project-scoped WorkItem first. These helpers then lock
the retained WorkLease row, preserving the global work-before-lease lock order.
No helper commits; the route owns the one outer transaction boundary.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.errors import conflict
from mnemonic_api.models import WorkItem, WorkLease
from mnemonic_api.schemas import (
    ClaimReceipt,
    DashboardWorkPendingCreate,
    MutationActor,
    ReleaseResult,
    WorkClaimCreate,
)
from mnemonic_api.services.readiness import require_fresh_claim_eligible
from mnemonic_api.services.work_events import (
    stage_work_claimed,
    stage_work_released,
)


def _database_now(database: Session) -> datetime:
    return database.execute(select(func.clock_timestamp())).scalar_one()


def _locked_lease(database: Session, work_item_id: UUID) -> WorkLease | None:
    return database.scalar(
        select(WorkLease).where(WorkLease.work_item_id == work_item_id).with_for_update()
    )


def _same_token(supplied: str, retained: str) -> bool:
    return secrets.compare_digest(supplied.encode("utf-8"), retained.encode("utf-8"))


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _token_mismatch() -> NoReturn:
    raise conflict(
        "lease_token_mismatch",
        "A matching lease token is required for this operation.",
    )


def _expired() -> NoReturn:
    raise conflict("lease_expired", "This work lease has expired.")


def claim_receipt(lease: WorkLease) -> ClaimReceipt:
    """Build the only response projection that may expose the raw token."""
    return ClaimReceipt(
        work_item_id=lease.work_item_id,
        holder_client=lease.holder_client,
        holder_session_id=lease.holder_session_id,
        claim_request_id=lease.claim_request_id,
        acquired_at=lease.acquired_at,
        renewed_at=lease.renewed_at,
        expires_at=lease.expires_at,
        lease_token=lease.lease_token,
    )


def claim_lease_record(
    database: Session,
    work_item: WorkItem,
    payload: WorkClaimCreate,
    ttl_seconds: int,
) -> ClaimReceipt:
    """Acquire, replay, or replace one lease while the work row is locked."""
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    lease = _locked_lease(database, work_item.id)
    if work_item.status != "pending":
        raise conflict("work_not_pending", "Only pending work can be claimed.")

    # Capture time only after both possible work/lease lock waits.
    database_now = _database_now(database)
    requested_identity = (
        payload.holder_client,
        payload.holder_session_id,
        payload.claim_request_id,
    )

    if lease is None:
        require_fresh_claim_eligible(database, work_item)
        lease = WorkLease(
            work_item_id=work_item.id,
            holder_client=payload.holder_client,
            holder_session_id=payload.holder_session_id,
            claim_request_id=payload.claim_request_id,
            lease_token=secrets.token_urlsafe(32),
            lease_generation_id=uuid4(),
            acquired_at=database_now,
            renewed_at=database_now,
            expires_at=database_now + timedelta(seconds=ttl_seconds),
        )
        database.add(lease)
        database.flush()
        stage_work_claimed(
            database,
            work_item,
            holder_client=lease.holder_client,
            holder_session_id=lease.holder_session_id,
            lease_generation_id=lease.lease_generation_id,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
        )
        database.flush()
        return claim_receipt(lease)

    retained_identity = (
        lease.holder_client,
        lease.holder_session_id,
        lease.claim_request_id,
    )
    if lease.expires_at > database_now:
        if retained_identity == requested_identity:
            return claim_receipt(lease)
        require_fresh_claim_eligible(database, work_item)
        raise conflict(
            "lease_held",
            "This work item has an active lease.",
            context={
                "holder_client": lease.holder_client,
                "expires_at": _utc(lease.expires_at),
            },
        )

    if lease.claim_request_id == payload.claim_request_id:
        raise conflict(
            "claim_request_expired",
            "This claim request belongs to an expired lease; use a new claim_request_id.",
            context={"expires_at": _utc(lease.expires_at)},
        )

    require_fresh_claim_eligible(database, work_item)

    lease.holder_client = payload.holder_client
    lease.holder_session_id = payload.holder_session_id
    lease.claim_request_id = payload.claim_request_id
    lease.lease_generation_id = uuid4()
    lease.pending_release_id = None

    lease.lease_token = secrets.token_urlsafe(32)
    lease.acquired_at = database_now
    lease.renewed_at = database_now
    lease.expires_at = database_now + timedelta(seconds=ttl_seconds)
    database.flush()
    stage_work_claimed(
        database,
        work_item,
        holder_client=lease.holder_client,
        holder_session_id=lease.holder_session_id,
        lease_generation_id=lease.lease_generation_id,
        acquired_at=lease.acquired_at,
        expires_at=lease.expires_at,
    )
    database.flush()
    return claim_receipt(lease)


def renew_lease_record(
    database: Session,
    work_item: WorkItem,
    lease_token: str,
    ttl_seconds: int,
) -> ClaimReceipt:
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    lease = _locked_lease(database, work_item.id)
    database_now = _database_now(database)
    if lease is None:
        _expired()
    if not _same_token(lease_token, lease.lease_token):
        _token_mismatch()
    if lease.expires_at <= database_now:
        _expired()
    lease.renewed_at = database_now
    lease.expires_at = database_now + timedelta(seconds=ttl_seconds)
    database.flush()
    return claim_receipt(lease)


def release_lease_record(
    database: Session,
    work_item: WorkItem,
    lease_token: str,
    actor: MutationActor | None = None,
) -> ReleaseResult:
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    lease = _locked_lease(database, work_item.id)
    database_now = _database_now(database)
    if lease is None:
        return ReleaseResult(work_item_id=work_item.id, released=False)
    if _same_token(lease_token, lease.lease_token):
        release_id = uuid4()
        lease.pending_release_id = release_id
        database.flush()
        stage_work_released(
            database,
            work_item,
            lease_generation_id=lease.lease_generation_id,
            lease_release_id=release_id,
            lease_holder_client=lease.holder_client,
            lease_holder_session_id=lease.holder_session_id,
            actor=actor,
            created_at=database_now,
        )
        database.flush()
        database.delete(lease)
        database.flush()
        return ReleaseResult(work_item_id=work_item.id, released=True)
    if lease.expires_at > database_now:
        _token_mismatch()
    return ReleaseResult(work_item_id=work_item.id, released=False)


def release_lease_for_human_decision(
    database: Session,
    work_item: WorkItem,
    payload: DashboardWorkPendingCreate,
) -> ReleaseResult:
    """Clear the exact lease state a person reviewed without exposing its token."""
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    lease = _locked_lease(database, work_item.id)
    if lease is None:
        return ReleaseResult(work_item_id=work_item.id, released=False)

    database_now = _database_now(database)
    lease_is_active = lease.expires_at > database_now
    if lease_is_active != (payload.expected_lease_state == "active"):
        raise conflict(
            "lease_state_changed",
            "This work item's lease changed. Reload it before choosing Pending.",
        )
    expected = payload.expected_active_lease
    if expected is not None and (
        lease.holder_client != expected.holder_client
        or lease.holder_session_id != expected.holder_session_id
        or lease.acquired_at != expected.acquired_at
        or lease.renewed_at != expected.renewed_at
        or lease.expires_at != expected.expires_at
    ):
        raise conflict(
            "lease_state_changed",
            "This work item's lease changed. Reload it before choosing Pending.",
        )

    release_id = uuid4()
    lease.pending_release_id = release_id
    database.flush()
    stage_work_released(
        database,
        work_item,
        lease_generation_id=lease.lease_generation_id,
        lease_release_id=release_id,
        lease_holder_client=lease.holder_client,
        lease_holder_session_id=lease.holder_session_id,
        actor=payload.actor,
        created_at=database_now,
    )
    database.flush()
    database.delete(lease)
    database.flush()
    return ReleaseResult(work_item_id=work_item.id, released=True)


def validate_optional_lease_token(
    database: Session,
    work_item_id: UUID,
    lease_token: str | None,
    *,
    lock: bool = False,
) -> None:
    """Validate a supplied token without requiring ownership for the operation."""
    if lease_token is None:
        return
    statement = select(WorkLease).where(WorkLease.work_item_id == work_item_id)
    if lock:
        statement = statement.with_for_update()
    lease = database.scalar(statement)
    database_now = _database_now(database)
    if lease is None or not _same_token(lease_token, lease.lease_token):
        _token_mismatch()
    if lease.expires_at <= database_now:
        _expired()


def require_no_active_lease(database: Session, work_item_id: UUID) -> None:
    """Reject active deferral and clear an expired retained lease before parking work."""
    lease = _locked_lease(database, work_item_id)
    if lease is None:
        return
    database_now = _database_now(database)
    if lease.expires_at > database_now:
        raise conflict(
            "lease_held",
            "Active work cannot be deferred until its lease is released or expires.",
            context={
                "holder_client": lease.holder_client,
                "expires_at": _utc(lease.expires_at),
            },
        )
    database.delete(lease)
    database.flush()


def require_no_active_lease_for_move(database: Session, work_item_id: UUID) -> None:
    """Reject an active holder while retaining an expired lease as Dropped history."""
    lease = _locked_lease(database, work_item_id)
    if lease is None:
        return
    database_now = _database_now(database)
    if lease.expires_at > database_now:
        raise conflict(
            "work_move_active_lease",
            "Active work cannot move until its lease is released or expires.",
            context={
                "holder_client": lease.holder_client,
                "expires_at": _utc(lease.expires_at),
            },
        )


def consume_lease_for_terminal_mutation(
    database: Session,
    work_item_id: UUID,
    lease_token: str | None,
) -> None:
    """Authorize a terminal mutation and remove any retained lease atomically."""
    lease = _locked_lease(database, work_item_id)
    database_now = _database_now(database)
    if lease is None:
        if lease_token is not None:
            _token_mismatch()
        return

    if lease_token is not None:
        if not _same_token(lease_token, lease.lease_token):
            _token_mismatch()
        if lease.expires_at <= database_now:
            _expired()
    elif lease.expires_at > database_now:
        _token_mismatch()

    # An expired retained row is not ownership. A tokenless authorized caller
    # may proceed and clears it; a caller explicitly presenting its stale token
    # receives lease_expired above instead.
    database.delete(lease)
    database.flush()
