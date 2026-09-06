"""Review discovery, exact retained history and protected lifecycle writes."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from mnemonic_api.application.guards import (
    reject_empty_read_request,
    reject_read_body_and_duplicate_query,
)
from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.code_review_schemas import CodeReviewDetail, ReviewQueuePage, WorkFollowUpDetail
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.schemas import (
    APIModel,
    CodeReviewCompletionCreate,
    CodeReviewCompletionRead,
    CodeReviewCompletionRequest,
    WorkFollowUpResponseCreate,
    WorkFollowUpResponseRequest,
    WorkFollowUpResponseResult,
)
from mnemonic_api.services.code_review_reads import follow_up_detail, queue_page, review_detail
from mnemonic_api.services.code_reviews import answer_follow_up, complete_review
from mnemonic_api.services.work_items import require_work_item

router = APIRouter()
ReadGuard = [Depends(reject_empty_read_request)]
ListGuard = [Depends(reject_read_body_and_duplicate_query)]


class CodeReviewListQuery(APIModel):
    state: Literal["requested", "completed", "superseded", "all"] = "requested"
    availability: Literal["all", "unclaimed"] = "all"
    work_item_id: UUID | None = None
    after: Annotated[str, Query(min_length=1, max_length=4096)] | None = None
    limit: Annotated[int, Query(ge=1, le=50)] = 20


class WorkFollowUpListQuery(APIModel):
    state: Literal["pending", "answered", "superseded", "all"] = "pending"
    work_item_id: UUID | None = None
    after: Annotated[str, Query(min_length=1, max_length=4096)] | None = None
    limit: Annotated[int, Query(ge=1, le=50)] = 20


@router.get(
    "/projects/{project_id}/code-reviews", response_model=ReviewQueuePage, dependencies=ListGuard
)
def list_code_reviews(
    project_id: UUID,
    database: Database,
    filters: Annotated[CodeReviewListQuery, Query()],
) -> ReviewQueuePage:
    begin_coherent_read(database)
    return queue_page(
        database,
        project_id,
        kind="code_reviews",
        **filters.model_dump(),
    )


@router.get(
    "/projects/{project_id}/work-agent-follow-ups",
    response_model=ReviewQueuePage,
    dependencies=ListGuard,
)
def list_work_follow_ups(
    project_id: UUID,
    database: Database,
    filters: Annotated[WorkFollowUpListQuery, Query()],
) -> ReviewQueuePage:
    begin_coherent_read(database)
    return queue_page(
        database,
        project_id,
        kind="work_follow_ups",
        availability="all",
        **filters.model_dump(),
    )


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/code-reviews/{review_id}",
    response_model=CodeReviewDetail,
    dependencies=ReadGuard,
)
def get_code_review(
    project_id: UUID, work_item_id: UUID, review_id: UUID, database: Database
) -> CodeReviewDetail:
    begin_coherent_read(database)
    return review_detail(database, project_id, work_item_id, review_id)


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/agent-follow-ups/{follow_up_id}",
    response_model=WorkFollowUpDetail,
    dependencies=ReadGuard,
)
def get_work_follow_up(
    project_id: UUID, work_item_id: UUID, follow_up_id: UUID, database: Database
) -> WorkFollowUpDetail:
    begin_coherent_read(database)
    return follow_up_detail(database, project_id, work_item_id, follow_up_id)


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/agent-follow-ups/{follow_up_id}/answer",
    response_model=WorkFollowUpResponseResult,
)
def respond_to_work_follow_up(
    project_id: UUID,
    work_item_id: UUID,
    follow_up_id: UUID,
    payload: WorkFollowUpResponseCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    def execute(domain: WorkFollowUpResponseRequest) -> WorkFollowUpResponseResult:
        work = require_work_item(database, project_id, work_item_id, lock=True)
        return answer_follow_up(database, work, follow_up_id, domain)

    return run_registered_mutation(
        "respond_to_work_follow_up",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": work_item_id, "follow_up_id": follow_up_id},
        payload=payload,
        execute=execute,
    )


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/code-reviews/{review_id}/complete",
    response_model=CodeReviewCompletionRead,
)
def complete_code_review(
    project_id: UUID,
    work_item_id: UUID,
    review_id: UUID,
    payload: CodeReviewCompletionCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    def execute(domain: CodeReviewCompletionRequest) -> CodeReviewCompletionRead:
        work = require_work_item(database, project_id, work_item_id, lock=True)
        return complete_review(database, work, review_id, domain)

    return run_registered_mutation(
        "complete_code_review",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": work_item_id, "review_id": review_id},
        payload=payload,
        execute=execute,
    )
