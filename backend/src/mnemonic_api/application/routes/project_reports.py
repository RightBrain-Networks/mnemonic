"""Durable project activity and human closeout-review resources."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field, model_validator

from mnemonic_api.application.guards import reject_read_body_and_duplicate_query
from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.phase12_schemas import (
    Cursor,
    DismissalFilter,
    JobCompletionReportCount,
    JobCompletionReportDetailEnvelope,
    JobCompletionReportPage,
    ProjectActivityPage,
    ReportFollowUpPage,
    WorkReportFollowUpPage,
)
from mnemonic_api.schemas import (
    APIModel,
    JobCompletionReportDismissalCreate,
    JobCompletionReportDismissalRequest,
    JobCompletionReportDismissalResult,
    JobCompletionReportFollowUpCreate,
    JobCompletionReportFollowUpRequest,
    JobCompletionReportFollowUpResult,
)
from mnemonic_api.services.job_completion_reports import (
    create_follow_up,
    dismiss_report,
    provenance_page,
    report_count,
    report_detail,
    report_page,
)
from mnemonic_api.services.project_activity import activity_page

router = APIRouter()


class ActivityQuery(APIModel):
    after: Cursor | None = None
    start: Literal["now"] | None = None
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def exclusive_position(self):
        if self.after is not None and self.start is not None:
            raise ValueError("Use after or start, not both")
        return self


class ReportQuery(APIModel):
    dismissal: DismissalFilter = "undismissed"
    work_item_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=50)
    cursor: Cursor | None = None


class ProvenanceQuery(APIModel):
    limit: int = Field(default=20, ge=1, le=50)
    cursor: Cursor | None = None


class WorkProvenanceQuery(ProvenanceQuery):
    direction: Literal["origin", "created"]


class EmptyQuery(APIModel):
    pass


@router.get(
    "/projects/{project_id}/activity",
    response_model=ProjectActivityPage,
    dependencies=[Depends(reject_read_body_and_duplicate_query)],
)
def get_activity(
    project_id: UUID, query: Annotated[ActivityQuery, Query()], database: Database
) -> ProjectActivityPage:
    begin_coherent_read(database)
    return activity_page(database, project_id, **query.model_dump())


@router.get(
    "/projects/{project_id}/job-completion-reports",
    response_model=JobCompletionReportPage,
    dependencies=[Depends(reject_read_body_and_duplicate_query)],
)
def list_reports(
    project_id: UUID, query: Annotated[ReportQuery, Query()], database: Database
) -> JobCompletionReportPage:
    begin_coherent_read(database)
    return report_page(database, project_id, **query.model_dump())


@router.get(
    "/projects/{project_id}/job-completion-reports/count",
    response_model=JobCompletionReportCount,
    dependencies=[Depends(reject_read_body_and_duplicate_query)],
)
def count_reports(
    project_id: UUID, query: Annotated[EmptyQuery, Query()], database: Database
) -> JobCompletionReportCount:
    begin_coherent_read(database)
    return report_count(database, project_id)


@router.get(
    "/projects/{project_id}/job-completion-reports/{report_id}",
    response_model=JobCompletionReportDetailEnvelope,
    dependencies=[Depends(reject_read_body_and_duplicate_query)],
)
def get_report(
    project_id: UUID, report_id: UUID, query: Annotated[EmptyQuery, Query()], database: Database
) -> JobCompletionReportDetailEnvelope:
    begin_coherent_read(database)
    return report_detail(database, project_id, report_id)


@router.get(
    "/projects/{project_id}/job-completion-reports/{report_id}/follow-ups",
    response_model=ReportFollowUpPage,
    dependencies=[Depends(reject_read_body_and_duplicate_query)],
)
def get_report_follow_ups(
    project_id: UUID,
    report_id: UUID,
    query: Annotated[ProvenanceQuery, Query()],
    database: Database,
):
    begin_coherent_read(database)
    return provenance_page(database, project_id, report_id=report_id, **query.model_dump())


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/report-follow-ups",
    response_model=WorkReportFollowUpPage,
    dependencies=[Depends(reject_read_body_and_duplicate_query)],
)
def get_work_report_follow_ups(
    project_id: UUID,
    work_item_id: UUID,
    query: Annotated[WorkProvenanceQuery, Query()],
    database: Database,
):
    begin_coherent_read(database)
    return provenance_page(database, project_id, work_item_id=work_item_id, **query.model_dump())


@router.post(
    "/projects/{project_id}/job-completion-reports/{report_id}/dismiss",
    response_model=JobCompletionReportDismissalResult,
)
def dismiss(
    project_id: UUID,
    report_id: UUID,
    payload: JobCompletionReportDismissalCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    def execute(domain: JobCompletionReportDismissalRequest) -> JobCompletionReportDismissalResult:
        return dismiss_report(database, project_id, report_id, domain.actor)

    return run_registered_mutation(
        "dismiss_job_completion_report",
        request=request,
        database=database,
        project_id=project_id,
        target={"report_id": report_id},
        payload=payload,
        execute=execute,
    )


@router.post(
    "/projects/{project_id}/job-completion-reports/{report_id}/follow-ups",
    response_model=JobCompletionReportFollowUpResult,
    status_code=201,
)
def follow_up(
    project_id: UUID,
    report_id: UUID,
    payload: JobCompletionReportFollowUpCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    def execute(domain: JobCompletionReportFollowUpRequest) -> JobCompletionReportFollowUpResult:
        return create_follow_up(database, project_id, report_id, domain)

    return run_registered_mutation(
        "create_job_completion_report_follow_up",
        request=request,
        database=database,
        project_id=project_id,
        target={"report_id": report_id},
        payload=payload,
        execute=execute,
    )
