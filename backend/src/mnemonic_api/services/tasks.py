"""Coherent, paginated dashboard task discovery without lifecycle overlays."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, case, func, literal, select, union_all
from sqlalchemy.orm import Session

from mnemonic_api.models import CodeReview, WorkDuplicateMerge, WorkItem, WorkLease
from mnemonic_api.schemas import LeasePublic
from mnemonic_api.services.work_events import database_now
from mnemonic_api.services.work_items import require_project
from mnemonic_api.task_schemas import TaskCounts, TaskListQuery, TaskPage, TaskSummary


def task_rows(project_id: UUID, now: datetime):
    visible = (
        WorkItem.project_id == project_id,
        WorkItem.deleted_at.is_(None),
        WorkItem.id.not_in(select(WorkDuplicateMerge.source_work_item_id)),
    )
    implementation_lease = and_(
        WorkItem.status == "pending", WorkLease.purpose == "implementation",
    )
    work_status = case(
        (and_(implementation_lease, WorkLease.expires_at > now), "active"),
        (implementation_lease, "dropped"),
        else_=WorkItem.status,
    )
    disposition = func.coalesce(CodeReview.human_decisions[-1]["status"].astext, "to-review")
    review_lease = and_(
        CodeReview.state == "requested", disposition == "to-review",
        WorkLease.purpose == "code_review", WorkLease.code_review_id == CodeReview.id,
        WorkLease.expires_at > now,
    )
    review_status = case(
        (CodeReview.state == "superseded", "superseded"),
        (CodeReview.state == "completed", "done"),
        (disposition != "to-review", disposition),
        (review_lease, "active"),
        else_="pending",
    )

    def columns(identity, kind, status, active):
        return (
            identity.label("id"), literal(kind).label("kind"), WorkItem.project_id,
            WorkItem.id.label("work_item_id"), WorkItem.title, WorkItem.summary,
            status.label("status"), WorkItem.updated_at,
            case((active, WorkLease.work_item_id)).label("lease_work_id"),
        )

    work = select(*columns(
        WorkItem.id, "work_item", work_status,
        and_(implementation_lease, WorkLease.expires_at > now),
    )).outerjoin(WorkLease, WorkLease.work_item_id == WorkItem.id).where(*visible)
    reviews = select(*columns(
        CodeReview.id, "code_review", review_status, review_lease,
    )).select_from(CodeReview).join(WorkItem, WorkItem.id == CodeReview.work_item_id).outerjoin(
        WorkLease, WorkLease.work_item_id == WorkItem.id,
    ).where(*visible)
    return union_all(work, reviews).subquery()


def task_page(database: Session, project_id: UUID, filters: TaskListQuery) -> TaskPage:
    require_project(database, project_id)
    tasks = task_rows(project_id, database_now(database))
    counts = {(kind, status): count for kind, status, count in database.execute(
        select(tasks.c.kind, tasks.c.status, func.count()).group_by(tasks.c.kind, tasks.c.status),
    )}
    next_expiry = database.scalar(select(func.min(WorkLease.expires_at)).where(
        WorkLease.work_item_id.in_(select(tasks.c.lease_work_id)),
    ))
    conditions = []
    if filters.task_id is not None:
        conditions.append(tasks.c.id == filters.task_id)
    if filters.kind is not None:
        conditions.append(tasks.c.kind == filters.kind)
    if filters.status != "all":
        conditions.append(tasks.c.status == filters.status)
    total = database.scalar(select(func.count()).select_from(tasks).where(*conditions)) or 0
    rows = database.execute(
        select(tasks).where(*conditions).order_by(tasks.c.updated_at.desc(), tasks.c.id)
        .limit(filters.limit).offset(filters.offset),
    ).mappings().all()
    lease_ids = [row["lease_work_id"] for row in rows if row["lease_work_id"]]
    leases = {lease.work_item_id: LeasePublic.model_validate(lease) for lease in database.scalars(
        select(WorkLease).where(WorkLease.work_item_id.in_(lease_ids)),
    )} if lease_ids else {}
    items = []
    for row in rows:
        values = dict(row)
        values["lease"] = leases.get(values.pop("lease_work_id"))
        items.append(TaskSummary.model_validate(values))
    return TaskPage(
        project_id=project_id,
        work_items=TaskCounts(active=counts.get(("work_item", "active"), 0),
                              pending=counts.get(("work_item", "pending"), 0)),
        code_reviews=TaskCounts(active=counts.get(("code_review", "active"), 0),
                                pending=counts.get(("code_review", "pending"), 0)),
        next_lease_expires_at=next_expiry,
        items=items, total=total, limit=filters.limit, offset=filters.offset,
    )
