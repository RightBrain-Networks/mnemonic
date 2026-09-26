"""Authoritative duplicate merges and inert duplicate suggestions."""

import asyncio
import logging
from queue import SimpleQueue
from time import monotonic
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.application.state import api_key_of, embedder_of, settings_of
from mnemonic_api.application.suggestion_resources import (
    suggestion_owned_work,
    suggestion_request_deadline,
)
from mnemonic_api.database import Database
from mnemonic_api.errors import ApplicationError, duplicate_suggestion_unavailable
from mnemonic_api.inference import with_inference_deadline
from mnemonic_api.schemas import (
    DuplicateSuggestionPage,
    DuplicateSuggestionRequest,
    WorkMergeCreate,
    WorkMergeRequest,
    WorkMergeResult,
)
from mnemonic_api.services.duplicate_suggestions import (
    InternalSuggestionResult,
    capture_internal_suggestions,
)
from mnemonic_api.services.duplicates import merge_work_records, reject_merge_secret_echo
from mnemonic_api.services.external_duplicate_suggestions import extend_external_suggestions
from mnemonic_api.suggestion_deadlines import (
    SUGGESTION_INTERACTIVE_SECONDS,
    suggestion_work_deadline,
)
from mnemonic_api.summary_limits import require_work_summary_length

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/projects/{project_id}/duplicate-suggestions",
    response_model=DuplicateSuggestionPage,
    openapi_extra={"x-mnemonic-effect": "safe_read"},
)
async def duplicate_suggestions(
    project_id: UUID,
    payload: DuplicateSuggestionRequest,
    request: Request,
) -> DuplicateSuggestionPage:
    require_work_summary_length(payload.summary, settings_of(request).work_summary_max_chars)
    factory: sessionmaker[Session] = request.app.state.session_factory
    owner = suggestion_owned_work(request.scope)
    deadline = suggestion_request_deadline(request.scope)
    work_deadline = suggestion_work_deadline(deadline, monotonic())
    responses: SimpleQueue[InternalSuggestionResult] = SimpleQueue()
    ready = asyncio.Event()
    loop = asyncio.get_running_loop()

    def retain(result: InternalSuggestionResult) -> None:
        responses.put(result)
        loop.call_soon_threadsafe(ready.set)

    def internal() -> InternalSuggestionResult:
        with factory() as database:
            return capture_internal_suggestions(
                database, project_id, payload, settings=settings_of(request),
                embedder=with_inference_deadline(embedder_of(request), work_deadline),
                deadline=work_deadline, on_result=retain,
            )

    try:
        captured = await _await_internal(owner.start(internal), work_deadline, responses, ready)
        return await extend_external_suggestions(
            captured.page, payload, session_factory=factory, embedder=embedder_of(request),
            query_vector=captured.query_vector,
            request_deadline=deadline, owned_work=owner,
        )
    except ApplicationError:
        raise
    except Exception as exc:
        logger.error("Duplicate suggestion unavailable (%s)", type(exc).__name__)
        raise duplicate_suggestion_unavailable(
            "deadline_exceeded" if isinstance(exc, TimeoutError) else "model_failure") from None


async def _await_internal(
    task: asyncio.Task[InternalSuggestionResult],
    deadline: float,
    responses: SimpleQueue[InternalSuggestionResult],
    ready: asyncio.Event,
) -> InternalSuggestionResult:
    # Neither timeout nor cancellation cancels the owned thread. The middleware
    # retains its request permit, and native inference keeps its model permit.
    readiness = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait((task, readiness),
            timeout=max(0.0, deadline - monotonic()), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        if readiness in done:
            done, _ = await asyncio.wait((task,), timeout=max(0.0, min(
                SUGGESTION_INTERACTIVE_SECONDS, deadline - monotonic())))
            if done:
                return task.result()
    finally:
        readiness.cancel()
        await asyncio.gather(readiness, return_exceptions=True)
    latest = None
    # The retained lexical snapshot remains useful if model work outlives the
    # short interactive budget. Native/request permits still belong to the worker.
    while not responses.empty():
        latest = responses.get_nowait()
    if latest is None:
        raise TimeoutError
    return latest


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
