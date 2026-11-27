"""External capacity reservations. All mutations hold the project lock through commit."""

import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError, conflict, not_found
from mnemonic_api.external_resource_schemas import (
    ExternalLeaseClaim,
    ExternalLeaseClaimReceipt,
    ExternalLeaseRead,
    ExternalLeaseReleased,
    ExternalLeaseRenew,
    ExternalResourceDefinition,
    ExternalResourceList,
    ExternalResourceRead,
    ExternalResourceWrite,
)
from mnemonic_api.models import ExternalResource, ExternalResourceLease, Project
from mnemonic_api.schemas import APIModel
from mnemonic_api.services.work_events import database_now


def _missing_resource() -> ApplicationError:
    return not_found("external_resource_not_found", "External resource not found.")


def _resource(database: Session, project_id: UUID, resource_id: UUID) -> ExternalResource:
    resource = database.get(ExternalResource, resource_id)
    if resource is None or resource.project_id != project_id:
        raise _missing_resource()
    return resource


def list_resources(
    database: Session,
    project_id: UUID,
    resource_id: UUID | None = None,
) -> ExternalResourceList:
    # One statement ties capacity, available units and active holders to the same snapshot.
    query = (
        select(ExternalResource, ExternalResourceLease)
        .outerjoin(
            ExternalResourceLease,
            and_(
                ExternalResource.id == ExternalResourceLease.resource_id,
                ExternalResourceLease.ended_at.is_(None),
                ExternalResourceLease.expires_at > func.statement_timestamp(),
            ),
        )
        .where(ExternalResource.project_id == project_id)
        .order_by(
            ExternalResource.name,
            ExternalResource.id,
            ExternalResourceLease.acquired_at,
            ExternalResourceLease.id,
        )
    )
    if resource_id is not None:
        query = query.where(ExternalResource.id == resource_id)
    items: dict[UUID, ExternalResourceRead] = {}
    for resource, lease in database.execute(query):
        if resource.id not in items:
            items[resource.id] = ExternalResourceRead(
                **ExternalResourceDefinition.model_validate(resource).model_dump(),
                id=resource.id,
                project_id=project_id,
                available=resource.capacity,
                leases=[],
            )
        if lease is not None:
            row = items[resource.id]
            row.leases.append(ExternalLeaseRead.model_validate(lease))
            row.available -= lease.quantity
    return ExternalResourceList(project_id=project_id, items=list(items.values()))


def get_resource(database: Session, project_id: UUID, resource_id: UUID) -> ExternalResourceRead:
    items = list_resources(database, project_id, resource_id).items
    if not items:
        raise _missing_resource()
    return items[0]


def _expire(database: Session, resource: ExternalResource, now: datetime) -> None:
    database.execute(
        update(ExternalResourceLease)
        .where(
            ExternalResourceLease.resource_id == resource.id,
            ExternalResourceLease.ended_at.is_(None),
            ExternalResourceLease.expires_at <= now,
        )
        .values(ended_reason="expired", ended_at=ExternalResourceLease.expires_at, lease_token=None)
    )


def expire_due_leases(database: Session) -> None:
    """Conclude a bounded batch under the same project lock all interactive writers acquire."""
    with Session(database.get_bind()) as sweep, sweep.begin():
        candidates = (
            select(ExternalResourceLease.id)
            .join(ExternalResource, ExternalResource.id == ExternalResourceLease.resource_id)
            .join(Project, Project.id == ExternalResource.project_id)
            .where(
                ExternalResourceLease.ended_at.is_(None),
                ExternalResourceLease.expires_at <= func.statement_timestamp(),
            )
            .order_by(ExternalResourceLease.expires_at, ExternalResourceLease.id)
            .limit(64)
            .with_for_update(of=Project, skip_locked=True)
        )
        ids = sweep.scalars(candidates).all()
        if ids:
            # A renewal may have committed between the candidate snapshot and project lock.
            sweep.execute(
                update(ExternalResourceLease)
                .where(
                    ExternalResourceLease.id.in_(ids),
                    ExternalResourceLease.ended_at.is_(None),
                    ExternalResourceLease.expires_at <= func.statement_timestamp(),
                )
                .values(
                    ended_reason="expired",
                    ended_at=ExternalResourceLease.expires_at,
                    lease_token=None,
                )
            )


def _used(database: Session, resource_id: UUID) -> int:
    # Caller has expired old reservations at this mutation's lock-protected timestamp.
    return int(
        database.execute(
            select(func.coalesce(func.sum(ExternalResourceLease.quantity), 0)).where(
                ExternalResourceLease.resource_id == resource_id,
                ExternalResourceLease.ended_at.is_(None),
            )
        ).scalar_one()
    )


def _check_capacity(used: int, capacity: int) -> None:
    if used > capacity:
        raise conflict("external_resource_capacity", "Reserved units would exceed capacity.")


def save_resource(
    database: Session,
    project_id: UUID,
    resource_id: UUID | None,
    payload: ExternalResourceWrite,
) -> ExternalResourceRead:
    name_match = database.scalar(
        select(ExternalResource.id).where(
            ExternalResource.project_id == project_id,
            ExternalResource.name == payload.name,
        )
    )
    if name_match is not None and name_match != resource_id:
        raise conflict("external_resource_name_taken", "A resource with this name already exists.")
    if resource_id is None:
        resource = ExternalResource(id=uuid4(), project_id=project_id)
        database.add(resource)
    else:
        resource = _resource(database, project_id, resource_id)
        _expire(database, resource, database_now(database))
        _check_capacity(_used(database, resource.id), payload.capacity)
    for name in ExternalResourceDefinition.model_fields:
        setattr(resource, name, getattr(payload, name))
    database.flush()
    return get_resource(database, project_id, resource.id)


def _expires_at(resource: ExternalResource, now: datetime, requested: int | None) -> datetime:
    minutes = resource.default_minutes if requested is None else requested
    if not resource.minimum_minutes <= minutes <= resource.maximum_minutes:
        raise ApplicationError(
            422,
            "external_lease_minutes_out_of_range",
            f"Request between {resource.minimum_minutes} and {resource.maximum_minutes} minutes.",
        )
    return now + timedelta(minutes=minutes)


def _check_replay(lease: ExternalResourceLease, payload: ExternalLeaseClaim) -> None:
    if (
        lease.holder_client,
        lease.holder_session_id,
        lease.claim_quantity,
        lease.claim_lease_minutes,
    ) != (
        payload.holder_client,
        payload.holder_session_id,
        payload.quantity,
        payload.lease_minutes,
    ):
        raise conflict(
            "external_lease_request_mismatch",
            "The claim request ID was used with different arguments.",
        )
    _require_active(lease)


def _require_active(lease: ExternalResourceLease) -> None:
    if lease.ended_at is not None:
        raise conflict("external_lease_inactive", "This reservation has ended.")


def claim(
    database: Session,
    project_id: UUID,
    resource_id: UUID,
    payload: ExternalLeaseClaim,
) -> ExternalLeaseClaimReceipt:
    resource = _resource(database, project_id, resource_id)
    now = database_now(database)
    _expire(database, resource, now)
    lease = database.scalar(
        select(ExternalResourceLease).where(
            ExternalResourceLease.resource_id == resource_id,
            ExternalResourceLease.claim_request_id == payload.claim_request_id,
        )
    )
    if lease is not None:
        _check_replay(lease, payload)
        return ExternalLeaseClaimReceipt.model_validate(lease)
    held = database.scalar(
        select(ExternalResourceLease.id).where(
            ExternalResourceLease.resource_id == resource_id,
            ExternalResourceLease.ended_at.is_(None),
            ExternalResourceLease.holder_client == payload.holder_client,
            ExternalResourceLease.holder_session_id == payload.holder_session_id,
        )
    )
    if held is not None:
        raise conflict(
            "external_lease_held", "This session already holds a lease on this resource."
        )
    _check_capacity(_used(database, resource_id) + payload.quantity, resource.capacity)
    lease = ExternalResourceLease(
        id=uuid4(),
        resource_id=resource_id,
        holder_client=payload.holder_client,
        holder_session_id=payload.holder_session_id,
        claim_request_id=payload.claim_request_id,
        claim_quantity=payload.quantity,
        quantity=payload.quantity,
        claim_lease_minutes=payload.lease_minutes,
        lease_token=secrets.token_urlsafe(32),
        acquired_at=now,
        renewed_at=now,
        expires_at=_expires_at(resource, now, payload.lease_minutes),
    )
    database.add(lease)
    database.flush()
    return ExternalLeaseClaimReceipt.model_validate(lease)


def _active_lease(
    database: Session,
    resource: ExternalResource,
    lease_id: UUID,
    token: str | None,
    now: datetime,
) -> ExternalResourceLease:
    _expire(database, resource, now)
    lease = database.get(ExternalResourceLease, lease_id)
    if lease is None or lease.resource_id != resource.id:
        raise not_found("external_lease_not_found", "External resource lease not found.")
    _require_active(lease)
    if token is not None and not secrets.compare_digest(
        token.encode(), (lease.lease_token or "").encode()
    ):
        raise conflict("external_lease_token_mismatch", "A matching lease token is required.")
    return lease


def renew(
    database: Session,
    project_id: UUID,
    resource_id: UUID,
    lease_id: UUID,
    payload: ExternalLeaseRenew,
) -> ExternalLeaseRead:
    resource = _resource(database, project_id, resource_id)
    now = database_now(database)
    lease = _active_lease(database, resource, lease_id, payload.lease_token, now)
    if payload.quantity is not None:
        _check_capacity(
            _used(database, resource.id) - lease.quantity + payload.quantity, resource.capacity
        )
        lease.quantity = payload.quantity
    lease.renewed_at = now
    lease.expires_at = _expires_at(resource, now, payload.lease_minutes)
    database.flush()
    return ExternalLeaseRead.model_validate(lease)


def release(
    database: Session,
    project_id: UUID,
    resource_id: UUID,
    lease_id: UUID,
    token: str | None,
) -> ExternalLeaseReleased:
    resource = _resource(database, project_id, resource_id)
    now = database_now(database)
    lease = _active_lease(database, resource, lease_id, token, now)
    lease.ended_reason = "revoked" if token is None else "released"
    lease.ended_at = now
    lease.lease_token = None
    database.flush()
    return ExternalLeaseReleased(
        project_id=project_id, resource_id=resource_id, lease_id=lease_id, released=True
    )


def response_matches(
    project_id: UUID,
    target: Mapping[str, str],
    payload: APIModel,
    result: APIModel,
) -> bool:
    if isinstance(result, ExternalResourceRead):
        return (
            result.project_id == project_id
            and str(result.id) == target.get("resource_id", str(result.id))
            and all(
                getattr(result, key) == getattr(payload, key)
                for key in ExternalResourceDefinition.model_fields
            )
        )
    if isinstance(result, ExternalLeaseRead) and isinstance(payload, ExternalLeaseRenew):
        return (
            str(result.resource_id) == target.get("resource_id")
            and str(result.id) == target.get("lease_id")
            and (payload.quantity is None or payload.quantity == result.quantity)
        )
    return (
        isinstance(result, ExternalLeaseReleased)
        and result.project_id == project_id
        and str(result.resource_id) == target.get("resource_id")
        and str(result.lease_id) == target.get("lease_id")
        and result.released
    )
