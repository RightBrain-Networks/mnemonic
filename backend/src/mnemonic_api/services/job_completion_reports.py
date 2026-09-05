"""Immutable closeout reports and independent human review/provenance."""

import hashlib
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError, conflict, not_found
from mnemonic_api.models import (
    JobCompletionReport,
    JobCompletionReportFollowUp,
    JobCompletionReportReview,
    ProjectJobCompletionReportCount,
    ProjectSettings,
    WorkDuplicateMerge,
    WorkEvent,
    WorkItem,
)
from mnemonic_api.phase12_schemas import (
    DismissalFilter,
    HumanDismissalRead,
    JobCompletionReportCount,
    JobCompletionReportDetailEnvelope,
    JobCompletionReportDetailRead,
    JobCompletionReportEnvelope,
    JobCompletionReportFollowUpRead,
    JobCompletionReportInput,
    JobCompletionReportPage,
    JobCompletionReportRead,
    ReportFollowUpPage,
    SourceWorkState,
    WorkReportFollowUpPage,
)
from mnemonic_api.schemas import (
    JobCompletionReportDismissalResult,
    JobCompletionReportFollowUpRequest,
    JobCompletionReportFollowUpResult,
    MutationActor,
    WorkItemCreate,
    WorkItemRead,
)
from mnemonic_api.services.activity_cursors import cursor_base, decode_cursor, encode_cursor
from mnemonic_api.services.project_activity import activity_head
from mnemonic_api.services.work_context import checkpoint_read
from mnemonic_api.services.work_events import database_now
from mnemonic_api.services.work_items import create_work_records


def require_report(
    database: Session,
    project_id: UUID,
    report_id: UUID,
) -> JobCompletionReport:
    report = database.scalar(
        select(JobCompletionReport).where(
            JobCompletionReport.project_id == project_id,
            JobCompletionReport.id == report_id,
        )
    )
    if report is None:
        activity_head(database, project_id)
        raise not_found("job_completion_report_not_found", "Report not found in this project.")
    return report


def report_read(report: JobCompletionReport, *, detail: bool = False) -> JobCompletionReportRead:
    data = {field: getattr(report, field) for field in JobCompletionReportRead.model_fields}
    data["closeout_event_id"] = str(report.closeout_event_id)
    data["prompt_revision"] = str(report.prompt_revision)
    if detail:
        return JobCompletionReportDetailRead(**data, authoring_prompt=report.prompt_text)
    return JobCompletionReportRead(**data)


def prepare_closeout_report(
    database: Session,
    work_item: WorkItem,
    payload: JobCompletionReportInput | None,
) -> ProjectSettings:
    if payload is None:
        raise ApplicationError(
            422, "job_completion_report_required", "A closeout report is required."
        )
    if not database.info.get("client_operation_keyed"):
        raise ApplicationError(
            422, "client_operation_id_required", "A client operation ID is required."
        )
    settings = database.get(ProjectSettings, work_item.project_id)
    if settings is None:
        raise ApplicationError(
            503, "job_completion_report_unavailable", "Report settings are unavailable."
        )
    if int(payload.prompt_revision) != settings.revision:
        raise conflict(
            "job_report_prompt_changed", "Project settings changed. Review the current prompt."
        )
    return settings


def seal_closeout_report(
    database: Session,
    work_item: WorkItem,
    event: WorkEvent,
    report_id: UUID,
    payload: JobCompletionReportInput,
    settings: ProjectSettings,
    actor: MutationActor,
    checkpoint_id: UUID | None = None,
) -> JobCompletionReport:
    report = JobCompletionReport(
        id=report_id,
        project_id=work_item.project_id,
        work_item_id=work_item.id,
        closeout_event_id=event.id,
        closeout_work_version=work_item.version,
        closeout_status=work_item.status,
        completion_checkpoint_id=checkpoint_id,
        work_title_at_closeout=work_item.title,
        summary=payload.summary,
        fyi_items=payload.fyi_items,
        prompt_revision=settings.revision,
        prompt_text=settings.job_completion_report_prompt,
        prompt_sha256=hashlib.sha256(settings.job_completion_report_prompt.encode()).hexdigest(),
        **actor.model_dump(),
    )
    database.add(report)
    database.flush()
    return report


def closeout_report(database: Session, work_item: WorkItem) -> JobCompletionReportRead | None:
    report = database.scalar(
        select(JobCompletionReport).where(
            JobCompletionReport.project_id == work_item.project_id,
            JobCompletionReport.work_item_id == work_item.id,
            JobCompletionReport.closeout_work_version == work_item.version,
        )
    )
    return report_read(report) if report is not None else None


def _dismissal(review: JobCompletionReportReview) -> HumanDismissalRead | None:
    if review.dismissal_id is None:
        return None
    assert review.dismissed_at is not None
    assert review.dismissal_actor_client is not None
    assert review.dismissal_actor_session_id is not None
    return HumanDismissalRead(
        id=review.dismissal_id,
        created_at=review.dismissed_at,
        actor_client=review.dismissal_actor_client,
        actor_session_id=review.dismissal_actor_session_id,
        actor_model=review.dismissal_actor_model,
    )


def _canonical_ids(database: Session, project_id: UUID, ids: set[UUID]) -> dict[UUID, UUID]:
    """Bounded indexed traversals for only the page's sources, including deleted aliases."""
    result = {item: item for item in ids}
    visited = {item: {item} for item in ids}
    for _ in range(51):
        edges = {
            source: destination
            for source, destination in database.execute(
                select(
                    WorkDuplicateMerge.source_work_item_id,
                    WorkDuplicateMerge.destination_work_item_id,
                ).where(
                    WorkDuplicateMerge.project_id == project_id,
                    WorkDuplicateMerge.source_work_item_id.in_(set(result.values())),
                )
            ).all()
        }
        if not edges:
            return result
        for original, current in result.items():
            destination = edges.get(current)
            if destination is not None:
                if destination in visited[original]:
                    raise ApplicationError(
                        503, "duplicate_graph_invalid", "Work identity is unavailable."
                    )
                visited[original].add(destination)
                result[original] = destination
    raise ApplicationError(503, "duplicate_graph_invalid", "Work identity is unavailable.")


def _envelopes(
    database: Session,
    project_id: UUID,
    rows: list[Any],
    *,
    detail: bool = False,
) -> list[JobCompletionReportEnvelope]:
    canonical = _canonical_ids(database, project_id, {report.work_item_id for report, _, _ in rows})
    model = JobCompletionReportDetailEnvelope if detail else JobCompletionReportEnvelope
    return [
        model(
            created_sequence=str(review.created_sequence),
            report=report_read(report, detail=detail),
            human_dismissed=review.dismissal_id is not None,
            human_dismissal=_dismissal(review),
            follow_up_count=str(review.follow_up_count),
            source_work_state=SourceWorkState(
                work_item_id=work.id,
                status=work.status,
                canonical_work_item_id=canonical[work.id],
                deleted=work.deleted_at is not None,
            ),
        )
        for report, review, work in rows
    ]


def _report_query(project_id: UUID):
    return (
        select(JobCompletionReport, JobCompletionReportReview, WorkItem)
        .join(
            JobCompletionReportReview,
            JobCompletionReportReview.report_id == JobCompletionReport.id,
        )
        .join(WorkItem, WorkItem.id == JobCompletionReport.work_item_id)
        .where(
            JobCompletionReportReview.project_id == project_id,
        )
    )


def report_detail(
    database: Session,
    project_id: UUID,
    report_id: UUID,
) -> JobCompletionReportDetailEnvelope:
    require_report(database, project_id, report_id)
    rows = database.execute(
        _report_query(project_id).where(JobCompletionReport.id == report_id)
    ).all()
    result = _envelopes(database, project_id, list(rows), detail=True)[0]
    assert isinstance(result, JobCompletionReportDetailEnvelope)
    return result


def report_page(
    database: Session,
    project_id: UUID,
    *,
    dismissal: DismissalFilter,
    work_item_id: UUID | None,
    limit: int,
    cursor: str | None,
) -> JobCompletionReportPage:
    head = activity_head(database, project_id)
    scope = {"dismissal": dismissal, "work_item_id": str(work_item_id) if work_item_id else None}
    upper, last = head.last_sequence, None
    if cursor is not None:
        decoded = decode_cursor(cursor, head, "reports", scope)
        upper, last = int(decoded["upper"]), int(decoded["last"])
    review = JobCompletionReportReview
    query = _report_query(project_id).where(review.created_sequence <= upper)
    if last is not None:
        query = query.where(review.created_sequence < last)
    if dismissal != "all":
        query = query.where(
            review.dismissal_id.is_(None)
            if dismissal == "undismissed"
            else review.dismissal_id.is_not(None)
        )
    if work_item_id is not None:
        query = query.where(review.work_item_id == work_item_id)
    rows = database.execute(query.order_by(review.created_sequence.desc()).limit(limit + 1)).all()
    more = len(rows) > limit
    items = _envelopes(database, project_id, list(rows[:limit]))
    token = (
        encode_cursor(
            {
                **cursor_base(head, "reports", **scope),
                "upper": str(upper),
                "last": items[-1].created_sequence,
            }
        )
        if more
        else None
    )
    return JobCompletionReportPage(
        project_id=project_id,
        stream_id=head.stream_id,
        **scope,
        as_of_sequence=str(upper),
        items=items,
        has_more=more,
        next_cursor=token,
    )


def report_count(database: Session, project_id: UUID) -> JobCompletionReportCount:
    head = activity_head(database, project_id)
    count = database.get(ProjectJobCompletionReportCount, project_id)
    if count is None:
        raise ApplicationError(
            503, "job_completion_report_unavailable", "Report count is unavailable."
        )
    return JobCompletionReportCount(
        project_id=project_id,
        undismissed_count=str(count.undismissed_count),
        as_of_sequence=str(head.last_sequence),
    )


def dismiss_report(
    database: Session,
    project_id: UUID,
    report_id: UUID,
    actor: MutationActor,
) -> JobCompletionReportDismissalResult:
    require_report(database, project_id, report_id)
    review = database.scalar(
        select(JobCompletionReportReview)
        .where(
            JobCompletionReportReview.report_id == report_id,
        )
        .with_for_update()
    )
    assert review is not None
    applied = review.dismissal_id is None
    if applied:
        review.dismissal_id = uuid4()
        review.dismissed_at = database_now(database)
        review.dismissal_actor_client = actor.actor_client
        review.dismissal_actor_session_id = actor.actor_session_id
        review.dismissal_actor_model = actor.actor_model
        database.flush()
        database.refresh(review)
    dismissal = _dismissal(review)
    assert dismissal is not None
    return JobCompletionReportDismissalResult(
        project_id=project_id, report_id=report_id, dismissed=applied, human_dismissal=dismissal
    )


def follow_up_read(row: JobCompletionReportFollowUp) -> JobCompletionReportFollowUpRead:
    data = {field: getattr(row, field) for field in JobCompletionReportFollowUpRead.model_fields}
    data["created_sequence"] = str(row.created_sequence)
    return JobCompletionReportFollowUpRead(**data)


def create_follow_up(
    database: Session,
    project_id: UUID,
    report_id: UUID,
    payload: JobCompletionReportFollowUpRequest,
) -> JobCompletionReportFollowUpResult:
    report = require_report(database, project_id, report_id)
    work, checkpoint, _ = create_work_records(
        database,
        project_id,
        WorkItemCreate(
            title=payload.title,
            summary=payload.summary,
            priority=payload.priority,
            initial_checkpoint=payload.initial_checkpoint,
        ),
    )
    association = JobCompletionReportFollowUp(
        id=uuid4(),
        project_id=project_id,
        report_id=report.id,
        source_work_item_id=report.work_item_id,
        follow_up_work_item_id=work.id,
        **payload.actor.model_dump(),
    )
    database.add(association)
    database.flush()
    database.refresh(association)
    return JobCompletionReportFollowUpResult(
        work_item=WorkItemRead.model_validate(work),
        initial_checkpoint=checkpoint_read(checkpoint),
        follow_up=follow_up_read(association),
    )


def provenance_page(
    database: Session,
    project_id: UUID,
    *,
    report_id: UUID | None = None,
    work_item_id: UUID | None = None,
    direction: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> ReportFollowUpPage | WorkReportFollowUpPage:
    head = activity_head(database, project_id)
    table = JobCompletionReportFollowUp
    query = select(table).where(table.project_id == project_id)
    if report_id is not None:
        require_report(database, project_id, report_id)
        kind, scope = "report_follow_ups", {"report_id": str(report_id)}
        query = query.where(table.report_id == report_id)
    else:
        work = database.get(WorkItem, work_item_id)
        if work is None or work.project_id != project_id:
            raise not_found("work_item_not_found", "Work item not found in this project.")
        kind, scope = (
            "work_report_follow_ups",
            {"work_item_id": str(work_item_id), "direction": direction},
        )
        column = (
            table.follow_up_work_item_id if direction == "origin" else table.source_work_item_id
        )
        query = query.where(column == work_item_id)
    upper, last = head.last_sequence, 0
    if cursor is not None:
        decoded = decode_cursor(cursor, head, kind, scope)
        upper, last = int(decoded["upper"]), int(decoded["last"])
    rows = list(
        database.scalars(
            query.where(table.created_sequence <= upper, table.created_sequence > last)
            .order_by(table.created_sequence)
            .limit(limit + 1)
        )
    )
    more = len(rows) > limit
    items = [follow_up_read(row) for row in rows[:limit]]
    token = (
        encode_cursor(
            {
                **cursor_base(head, kind, **scope),
                "upper": str(upper),
                "last": items[-1].created_sequence,
            }
        )
        if more
        else None
    )
    model = ReportFollowUpPage if report_id is not None else WorkReportFollowUpPage
    return model.model_validate(
        dict(
            project_id=project_id,
            **scope,
            items=items,
            as_of_sequence=str(upper),
            has_more=more,
            next_cursor=token,
        )
    )
