"""Authoritative duplicate merge operations."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.application.state import api_key_of
from mnemonic_api.database import Database
from mnemonic_api.schemas import WorkMergeCreate, WorkMergeRequest, WorkMergeResult
from mnemonic_api.services.duplicates import merge_work_records, reject_merge_secret_echo

router = APIRouter()


@router.post(
    "/projects/{project_id}/work-items/{source_work_item_id}/merge",
    response_model=WorkMergeResult,
    status_code=201,
    openapi_extra={"x-mnemonic-effect": "receipt_protected_write"},
)
def merge_work(
    project_id: UUID,
    source_work_item_id: UUID,
    payload: WorkMergeCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    reject_merge_secret_echo(
        payload,
        bearer_key=api_key_of(request),
        client_operation_id=payload.client_operation_id,
    )

    def execute(domain_payload: WorkMergeRequest) -> WorkMergeResult:
        return merge_work_records(
            database,
            project_id,
            source_work_item_id,
            domain_payload,
        )

    def enforce_deferred_constraints() -> None:
        database.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    return run_registered_mutation(
        "merge_work",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": source_work_item_id},
        payload=payload,
        execute=execute,
        before_commit=enforce_deferred_constraints,
    )
