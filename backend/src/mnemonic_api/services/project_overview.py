"""Aggregate the whole project without loading work bodies or paginating work."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import CTE

from mnemonic_api.database import begin_coherent_read
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import (
    DecisionDocument,
    KnowledgeDomain,
    ProjectSettings,
    ProjectTag,
    WorkGate,
    WorkItem,
    WorkLease,
)
from mnemonic_api.project_overview_schemas import (
    ProjectActiveWorkItem,
    ProjectKnowledgeDomainSummary,
    ProjectNeedsAttentionSummary,
    ProjectOverviewRead,
    ProjectSettingsValues,
    ProjectStateCounts,
    ProjectWorkItemsSummary,
)
from mnemonic_api.services.precedence import dependency_issues
from mnemonic_api.services.readiness import eligibility_clauses, review_status_clause
from mnemonic_api.services.work_items import require_project


def _state_counts(counts: dict[str, int]) -> ProjectStateCounts:
    zeros = {field.alias or name: 0 for name, field in ProjectStateCounts.model_fields.items()}
    return ProjectStateCounts.model_validate(zeros | counts)


def _work_states(database: Session, project_id: UUID, as_of: datetime) -> CTE:
    work = WorkItem.__table__
    lease = WorkLease.__table__
    nonterminal_ids = list(database.scalars(select(work.c.id).where(
        work.c.project_id == project_id, work.c.deleted_at.is_(None),
        work.c.status.in_(("pending", "deferred")),
    )))
    deadlocked = [work_id for work_id, issue in dependency_issues(
        database, nonterminal_ids,
    ).items() if issue.kind == "deadlock"]
    facts = eligibility_clauses(
        work.c.id, work.c.project_id, work.c.status, func.transaction_timestamp(),
        correlate_from=work,
    )
    review = review_status_clause(work.c.id)
    state = case(
        (~facts.is_canonical, "duplicate"),
        ((work.c.status == "done") & review.is_not(None), review),
        (work.c.id.in_(deadlocked), "deadlocked"),
        (work.c.status != "pending", work.c.status),
        (facts.has_unresolved_gate, "waiting"),
        (facts.has_unresolved_blocker, "blocked"),
        (lease.c.expires_at > as_of, "active"),
        (lease.c.work_item_id.is_not(None), "dropped"),
        else_="pending",
    ).label("state")
    return select(
        work.c.id, work.c.title, work.c.created_at, work.c.updated_at,
        work.c.tags, lease.c.holder_session_id.label("session_id"), state,
    ).select_from(work.outerjoin(lease, lease.c.work_item_id == work.c.id)).where(
        work.c.project_id == project_id, work.c.deleted_at.is_(None),
    ).cte("project_work_states")


def _work_summary(database: Session, project_id: UUID, as_of: datetime) -> ProjectWorkItemsSummary:
    states = _work_states(database, project_id, as_of)
    counts = {state: count for state, count in database.execute(select(
        states.c.state, func.count(),
    ).group_by(states.c.state))}
    active = [ProjectActiveWorkItem.model_validate({
        **row, "tags": row["tags"] or [],
    }) for row in database.execute(select(
        states.c.id, states.c.title, states.c.created_at, states.c.updated_at,
        states.c.session_id, states.c.tags,
    ).where(states.c.state == "active").order_by(
        states.c.created_at, states.c.id,
    )).mappings()]
    assignments = select(
        states.c.state, func.unnest(states.c.tags).label("tag"),
    ).subquery()
    tags: dict[str, dict[str, int]] = {name: {} for name in database.scalars(select(
        ProjectTag.name,
    ).where(ProjectTag.project_id == project_id).order_by(ProjectTag.name))}
    for tag, state, count in database.execute(select(
        assignments.c.tag, assignments.c.state, func.count(),
    ).group_by(assignments.c.tag, assignments.c.state)):
        tags.setdefault(tag, {})[state] = count
    return ProjectWorkItemsSummary(
        total=sum(counts.values()), counts_by_state=_state_counts(counts),
        active=active,
        counts_by_tag={tag: _state_counts(values)
                       for tag, values in sorted(tags.items())},
    )


def _domains(database: Session, project_id: UUID) -> list[ProjectKnowledgeDomainSummary]:
    return [ProjectKnowledgeDomainSummary.model_validate(row) for row in database.execute(select(
        KnowledgeDomain.id, KnowledgeDomain.name, KnowledgeDomain.prefix,
        func.count(DecisionDocument.id).label("decision_document_count"),
    ).outerjoin(DecisionDocument, (
        (DecisionDocument.domain_id == KnowledgeDomain.id)
        & (DecisionDocument.project_id == project_id)
        & DecisionDocument.deleted_at.is_(None)
    )).where(
        KnowledgeDomain.project_id == project_id, KnowledgeDomain.deleted_at.is_(None),
    ).group_by(KnowledgeDomain.id).order_by(KnowledgeDomain.prefix, KnowledgeDomain.id)).mappings()]


def project_overview(database: Session, project_id: UUID) -> ProjectOverviewRead:
    begin_coherent_read(database)
    require_project(database, project_id)
    settings = database.get(ProjectSettings, project_id)
    if settings is None:
        raise ApplicationError(503, "project_settings_unavailable", "Settings are unavailable.")
    as_of = database.scalar(select(func.transaction_timestamp()))
    if as_of is None:
        raise RuntimeError("Database did not provide a transaction timestamp")
    scalar_settings = {name: getattr(settings, name) for name in ProjectSettingsValues.model_fields}
    scalar_settings["revision"] = str(settings.revision)
    open_count = database.scalar(select(func.count()).select_from(WorkGate).where(
        WorkGate.project_id == project_id, WorkGate.resolved_at.is_(None),
    )) or 0
    return ProjectOverviewRead(
        project_id=project_id,
        settings=ProjectSettingsValues.model_validate(scalar_settings),
        work_items_summary=_work_summary(database, project_id, as_of),
        needs_attention=ProjectNeedsAttentionSummary(open_count=open_count),
        knowledge_domains=_domains(database, project_id),
    )
