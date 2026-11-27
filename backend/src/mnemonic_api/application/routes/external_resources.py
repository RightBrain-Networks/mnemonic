"""Public resource status, agent reservation capabilities, and dashboard-only controls."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from mnemonic_api.application.guards import reject_lease_operation_query
from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.database import Database
from mnemonic_api.external_resource_schemas import (
    ExternalLeaseClaim,
    ExternalLeaseClaimReceipt,
    ExternalLeaseRead,
    ExternalLeaseRelease,
    ExternalLeaseReleased,
    ExternalLeaseRenew,
    ExternalLeaseRevoke,
    ExternalResourceList,
    ExternalResourceRead,
    ExternalResourceWrite,
)
from mnemonic_api.services import external_resources as resources
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.work_items import require_project

BASE = "/projects/{project_id}/external-resources"
RESOURCE = BASE + "/{resource_id}"
LEASE = RESOURCE + "/leases/{lease_id}"
router = APIRouter(dependencies=[Depends(reject_lease_operation_query)])
browser_mutations = APIRouter()


@router.get(BASE, response_model=ExternalResourceList)
def list_external_resources(project_id: UUID, database: Database) -> ExternalResourceList:
    require_project(database, project_id)
    return resources.list_resources(database, project_id)


@router.get(RESOURCE, response_model=ExternalResourceRead)
def get_external_resource(
    project_id: UUID, resource_id: UUID, database: Database
) -> ExternalResourceRead:
    return resources.get_resource(database, project_id, resource_id)


@browser_mutations.post(BASE, response_model=ExternalResourceRead, status_code=201)
def create_external_resource(
    project_id: UUID, payload: ExternalResourceWrite, request: Request, database: Database
) -> JSONResponse:
    return run_registered_mutation(
        "create_external_resource",
        request=request,
        database=database,
        project_id=project_id,
        target={},
        payload=payload,
        execute=lambda body: resources.save_resource(database, project_id, None, body),
    )


@browser_mutations.put(RESOURCE, response_model=ExternalResourceRead)
def update_external_resource(
    project_id: UUID,
    resource_id: UUID,
    payload: ExternalResourceWrite,
    request: Request,
    database: Database,
) -> JSONResponse:
    return run_registered_mutation(
        "update_external_resource",
        request=request,
        database=database,
        project_id=project_id,
        target={"resource_id": resource_id},
        payload=payload,
        execute=lambda body: resources.save_resource(database, project_id, resource_id, body),
    )


@router.post(RESOURCE + "/claim", response_model=ExternalLeaseClaimReceipt)
def claim_external_lease(
    project_id: UUID, resource_id: UUID, payload: ExternalLeaseClaim, database: Database
) -> ExternalLeaseClaimReceipt:
    with project_mutation(database, project_id):
        result = resources.claim(database, project_id, resource_id, payload)
        database.commit()
    return result


@router.post(LEASE + "/renew", response_model=ExternalLeaseRead)
def renew_external_lease(
    project_id: UUID,
    resource_id: UUID,
    lease_id: UUID,
    payload: ExternalLeaseRenew,
    request: Request,
    database: Database,
) -> JSONResponse:
    return run_registered_mutation(
        "renew_external_lease",
        request=request,
        database=database,
        project_id=project_id,
        target={"resource_id": resource_id, "lease_id": lease_id},
        payload=payload,
        execute=lambda body: resources.renew(database, project_id, resource_id, lease_id, body),
    )


@router.post(LEASE + "/release", response_model=ExternalLeaseReleased)
def release_external_lease(
    project_id: UUID,
    resource_id: UUID,
    lease_id: UUID,
    payload: ExternalLeaseRelease,
    request: Request,
    database: Database,
) -> JSONResponse:
    return run_registered_mutation(
        "release_external_lease",
        request=request,
        database=database,
        project_id=project_id,
        target={"resource_id": resource_id, "lease_id": lease_id},
        payload=payload,
        execute=lambda body: resources.release(
            database, project_id, resource_id, lease_id, body.lease_token
        ),
    )


@browser_mutations.post(LEASE + "/revoke", response_model=ExternalLeaseReleased)
def revoke_external_lease(
    project_id: UUID,
    resource_id: UUID,
    lease_id: UUID,
    payload: ExternalLeaseRevoke,
    request: Request,
    database: Database,
) -> JSONResponse:
    return run_registered_mutation(
        "revoke_external_lease",
        request=request,
        database=database,
        project_id=project_id,
        target={"resource_id": resource_id, "lease_id": lease_id},
        payload=payload,
        execute=lambda _: resources.release(database, project_id, resource_id, lease_id, None),
    )
