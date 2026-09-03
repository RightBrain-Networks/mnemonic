"""Authoritative duplicate merges and inert duplicate suggestions."""

import logging
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.application.state import api_key_of, embedder_of, settings_of
from mnemonic_api.application.suggestion_resources import (
    suggestion_inference_acquired,
    suggestion_request_deadline,
)
from mnemonic_api.database import Database
from mnemonic_api.errors import ApplicationError, duplicate_suggestion_unavailable
from mnemonic_api.schemas import (
    DuplicateSuggestionPage,
    DuplicateSuggestionRequest,
    WorkMergeCreate,
    WorkMergeRequest,
    WorkMergeResult,
)
from mnemonic_api.services.duplicate_suggestions import suggest_duplicate_work
from mnemonic_api.services.duplicates import merge_work_records, reject_merge_secret_echo

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/projects/{project_id}/duplicate-suggestions",
    response_model=DuplicateSuggestionPage,
    openapi_extra={"x-mnemonic-effect": "safe_read"},
)
def duplicate_suggestions(
    project_id: UUID,
    payload: DuplicateSuggestionRequest,
    request: Request,
    database: Database,
) -> DuplicateSuggestionPage:
    try:
        return suggest_duplicate_work(
            database,
            project_id,
            payload,
            settings=settings_of(request),
            embedder=embedder_of(request),
            inference_permitted=suggestion_inference_acquired(request.scope),
            deadline=suggestion_request_deadline(request.scope),
        )
    except ApplicationError:
        raise
    except Exception as exc:
        database.rollback()
        logger.error("Duplicate suggestion unavailable (%s)", type(exc).__name__)
        raise duplicate_suggestion_unavailable() from None


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
