"""Coherent, paginated dashboard task discovery without lifecycle overlays."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Integer, and_, case, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from mnemonic_api.models import (
    CodeReview,
    WorkCompletionReviewPolicy,
    WorkDuplicateMerge,
    WorkItem,
    WorkLease,
)
from mnemonic_api.schemas import LeasePublic
from mnemonic_api.search_query import parse_query
from mnemonic_api.services.work_context import work_summaries
from mnemonic_api.services.work_events import database_now
from mnemonic_api.services.work_items import require_project
from mnemonic_api.services.work_matching import lexical_match
from mnemonic_api.task_schemas import TaskCounts, TaskListQuery, TaskPage, TaskSummary
from mnemonic_api.work_search_fields import WORK_FIELDS


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

    def columns(identity, kind, status, active, review_state=None):
        return (
            identity.label("id"), literal(kind).label("kind"), WorkItem.project_id,
            WorkItem.id.label("work_item_id"), WorkItem.version.label("work_version"),
            WorkItem.title, WorkItem.summary,
            WorkItem.tags,
            (func.coalesce(WorkCompletionReviewPolicy.priority_at_closeout,
                           CodeReview.manual_request["priority"].astext.cast(Integer))
             if kind == "code_review" else WorkItem.priority).label("priority"),
            WorkItem.remediation_depth,
            (CodeReview.created_at if kind == "code_review" else WorkItem.created_at)
            .label("created_at"),
            (WorkItem.decision_review_revision_id if kind == "work_item" else literal(None))
            .label("decision_review_revision_id"),
            (WorkItem.decision_review_change_id if kind == "work_item" else literal(None))
            .label("decision_review_change_id"),
            (WorkItem.decision_review_project_id if kind == "work_item" else literal(None))
            .label("decision_review_project_id"),
            WorkItem.decision_change_ids.label("decision_change_ids"),
            status.label("status"), WorkItem.updated_at,
            (literal(None) if review_state is None else review_state).label("review_state"),
            case((active, WorkLease.work_item_id)).label("lease_work_id"),
        )

    work = select(*columns(
        WorkItem.id, "work_item", work_status,
        and_(implementation_lease, WorkLease.expires_at > now),
    )).outerjoin(WorkLease, WorkLease.work_item_id == WorkItem.id).where(*visible)
    reviews = select(*columns(
        CodeReview.id, "code_review", review_status, review_lease, CodeReview.state,
    )).select_from(CodeReview).join(WorkItem, WorkItem.id == CodeReview.work_item_id).outerjoin(
        WorkCompletionReviewPolicy, WorkCompletionReviewPolicy.id == CodeReview.policy_decision_id,
    ).outerjoin(
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
    conditions = task_conditions(tasks, filters)
    total = database.scalar(select(func.count()).select_from(tasks).where(*conditions)) or 0
    sort = {"updated": tasks.c.updated_at, "created": tasks.c.created_at,
            "priority": tasks.c.priority}[filters.sort]
    ordering = [sort.desc()]
    if filters.sort == "priority":
        ordering.append(tasks.c.updated_at.desc())
    ordering.append(tasks.c.id.desc())
    rows = database.execute(
        select(tasks).where(*conditions).order_by(*ordering)
        .limit(filters.limit).offset(filters.offset),
    ).mappings().all()
    items = task_summaries(database, rows, filters.include_summary)
    return TaskPage(
        project_id=project_id,
        work_items=TaskCounts(active=counts.get(("work_item", "active"), 0),
                              pending=counts.get(("work_item", "pending"), 0)),
        code_reviews=TaskCounts(active=counts.get(("code_review", "active"), 0),
                                pending=counts.get(("code_review", "pending"), 0)),
        next_lease_expires_at=next_expiry,
        items=items, total=total, limit=filters.limit, offset=filters.offset,
    )


def task_conditions(tasks, filters: TaskListQuery):
    conditions = []
    if filters.task_id is not None:
        conditions.append(tasks.c.id == filters.task_id)
    if filters.kind is not None:
        conditions.append(tasks.c.kind == filters.kind)
    if filters.status != "all":
        conditions.append(tasks.c.status == filters.status)
    if filters.q and filters.q.strip():
        matching = select(WorkItem.id).where(
            lexical_match(parse_query(filters.q, "terms"), WORK_FIELDS).condition,
        )
        conditions.append(tasks.c.work_item_id.in_(matching))
    if filters.tags:
        combine = and_ if filters.tag_mode == "and" else or_
        conditions.append(combine(*(task_tag_condition(tasks, name) for name in filters.tags)))
    return conditions


def task_tag_condition(tasks, name: str):
    intrinsic = {
        "code-review": tasks.c.kind == "code_review",
        "remediation": and_(tasks.c.kind == "work_item", tasks.c.remediation_depth > 0),
        "dir": and_(tasks.c.kind == "work_item", or_(
            tasks.c.decision_review_change_id.is_not(None),
            tasks.c.decision_review_revision_id.is_not(None),
        )),
    }
    assigned = tasks.c.tags.contains([name])
    return or_(assigned, intrinsic[name]) if name in intrinsic else assigned


def task_summaries(database: Session, rows, include_summary: bool):
    lease_ids = [row["lease_work_id"] for row in rows if row["lease_work_id"]]
    leases = {lease.work_item_id: LeasePublic.model_validate(lease) for lease in database.scalars(
        select(WorkLease).where(WorkLease.work_item_id.in_(lease_ids)),
    )} if lease_ids else {}
    summaries = {}
    if include_summary:
        works = database.scalars(select(WorkItem).where(
            WorkItem.id.in_({row["work_item_id"] for row in rows}),
        )).all()
        summaries = {item.work_item.id: item for item in work_summaries(database, works)}
    items = []
    for row in rows:
        values = dict(row)
        values["lease"] = leases.get(values.pop("lease_work_id"))
        depth = values.pop("remediation_depth")
        values["system_tags"] = task_system_tags(values, depth)
        if include_summary:
            values["work_summary"] = summaries[values["work_item_id"]]
        items.append(TaskSummary.model_validate(values))
    return items


def task_system_tags(values, depth: int) -> list[str]:
    if values["kind"] == "code_review":
        return ["code-review"]
    tags = ["remediation"] if depth else []
    if values["decision_review_change_id"] or values["decision_review_revision_id"]:
        tags.append("dir")
    return tags
