"""Force-claim retries must never replace a subsequent lease generation."""

import hashlib
import json
from uuid import UUID

from sqlalchemy.orm import Session

from mnemonic_api.errors import conflict
from mnemonic_api.models import WorkForceClaim, WorkLease
from mnemonic_api.schemas import WorkClaimCreate


def _fingerprint(payload: WorkClaimCreate) -> str:
    encoded = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def check_force_claim_retry(
    database: Session, work_item_id: UUID, lease: WorkLease | None, payload: WorkClaimCreate,
) -> bool:
    retained = database.get(WorkForceClaim, (work_item_id, payload.claim_request_id))
    if retained is None:
        return False
    if retained.payload_sha256 != _fingerprint(payload):
        raise conflict("claim_request_mismatch", "Retry the exact original force-claim arguments.")
    if lease is None or lease.lease_generation_id != retained.lease_generation_id:
        raise conflict(
            "claim_request_expired",
            "This force claim was released or replaced; reconcile before using a new request ID.",
        )
    return True


def retain_force_claim(database: Session, lease: WorkLease, payload: WorkClaimCreate) -> None:
    if payload.force:
        database.add(WorkForceClaim(
            work_item_id=lease.work_item_id,
            claim_request_id=payload.claim_request_id,
            payload_sha256=_fingerprint(payload),
            lease_generation_id=lease.lease_generation_id,
        ))
        database.flush()
