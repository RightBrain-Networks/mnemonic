"""Leases: server-arbitrated claims with a capability token.

The token appears only in a claim receipt and in request bodies: never in a
query string (the package-wide ``reject_lease_token_query`` guard plus this
router's ``reject_lease_operation_query``), never in a log line, never in an
error. The TTL is the server's (``MNEMONIC_LEASE_TTL_SECONDS``). Claim,
claim-and-recall, and renew are not receipt-protected; a lost claim receipt is
recovered through ``claim_request_id`` instead. Release is receipt-protected
like the other state-changing writes, and releasing a lease that is no longer
held is a no-op the registry reports through ``released``.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from mnemonic_api.application.guards import reject_lease_operation_query
from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.application.state import settings_of
from mnemonic_api.database import Database
from mnemonic_api.schemas import (
    ClaimAndRecall,
    ClaimReceipt,
    LeaseReleaseCreate,
    LeaseTokenCreate,
    ReleaseResult,
    WorkClaimCreate,
)
from mnemonic_api.services.leases import (
    claim_lease_record,
    release_lease_record,
    renew_lease_record,
)
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.work_context import assemble_work_context
from mnemonic_api.services.work_items import require_work_item

router = APIRouter(dependencies=[Depends(reject_lease_operation_query)])


@router.post("/projects/{project_id}/work-items/{work_item_id}/claim", response_model=ClaimReceipt)
def claim_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkClaimCreate,
    request: Request,
    database: Database,
) -> ClaimReceipt:
    with project_mutation(database, project_id):
        work_item = require_work_item(database, project_id, work_item_id, lock=True)
        receipt = claim_lease_record(
            database, work_item, payload, settings_of(request).lease_ttl_seconds
        )
        database.commit()
        return receipt


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/claim-and-recall",
    response_model=ClaimAndRecall,
)
def claim_and_recall(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkClaimCreate,
    request: Request,
    database: Database,
) -> ClaimAndRecall:
    with project_mutation(database, project_id):
        work_item = require_work_item(database, project_id, work_item_id, lock=True)
        receipt = claim_lease_record(
            database, work_item, payload, settings_of(request).lease_ttl_seconds
        )
        context = assemble_work_context(
            database,
            project_id,
            work_item_id,
            recent_limit=5,
            coherent_read=False,
        )
        database.commit()
        return ClaimAndRecall(lease=receipt, context=context)


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/renew-claim",
    response_model=ClaimReceipt,
)
def renew_claim(
    project_id: UUID,
    work_item_id: UUID,
    payload: LeaseTokenCreate,
    request: Request,
    database: Database,
) -> ClaimReceipt:
    with project_mutation(database, project_id):
        work_item = require_work_item(database, project_id, work_item_id, lock=True)
        receipt = renew_lease_record(
            database, work_item, payload.lease_token, settings_of(request).lease_ttl_seconds
        )
        database.commit()
        return receipt


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/release-claim",
    response_model=ReleaseResult,
)
def release_claim(
    project_id: UUID,
    work_item_id: UUID,
    payload: LeaseReleaseCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    def execute(domain_payload: LeaseReleaseCreate) -> ReleaseResult:
        work_item = require_work_item(database, project_id, work_item_id, lock=True)
        return release_lease_record(
            database, work_item, domain_payload.lease_token, domain_payload.actor
        )

    return run_registered_mutation(
        "release_claim",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": work_item_id},
        payload=payload,
        execute=execute,
    )
