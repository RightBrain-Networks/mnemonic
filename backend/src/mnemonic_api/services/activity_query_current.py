"""Current facet predicates and bounded metadata hydration for change pages."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from mnemonic_api.activity_query_schemas import (
    ActivityCurrentState,
    ActivityFilters,
)
from mnemonic_api.models import (
    Artifact,
    DecisionDocument,
    DecisionRevision,
    KnowledgeDomain,
    ProjectChange,
    WorkItem,
    WorkLease,
)
from mnemonic_api.services.readiness import review_status_clause
from mnemonic_api.services.tags import tag_conditions
from mnemonic_api.services.work_search import status_conditions


def _work_predicate(project_id: UUID, filters, as_of: datetime):
    conditions = [WorkItem.id == ProjectChange.resource_id, WorkItem.project_id == project_id,
                  WorkItem.deleted_at.is_(None)]
    if filters.statuses:
        conditions.append(or_(*(and_(*status_conditions(status, as_of))
                                for status in filters.statuses)))
    if filters.tags:
        conditions.append(tag_conditions(filters.tags, filters.tag_mode))
    return select(WorkItem.id).where(*conditions).exists()


def _artifact_predicate(project_id: UUID, filters):
    conditions = [Artifact.id == ProjectChange.resource_id, Artifact.project_id == project_id,
                  Artifact.deleted_at.is_(None)]
    if filters.tags:
        method = Artifact.tags.contains if filters.tag_mode == "and" else Artifact.tags.overlap
        conditions.append(method(filters.tags))
    return select(Artifact.id).where(*conditions).exists()


def _decision_predicate(project_id: UUID, filters):
    conditions = [DecisionDocument.id == ProjectChange.resource_id,
                  DecisionDocument.project_id == project_id,
                  DecisionDocument.deleted_at.is_(None), KnowledgeDomain.deleted_at.is_(None)]
    if filters.domain_ids:
        conditions.append(DecisionDocument.domain_id.in_(filters.domain_ids))
    if filters.domain_prefixes:
        conditions.append(KnowledgeDomain.prefix.in_(filters.domain_prefixes))
    if filters.states:
        conditions.append(DecisionRevision.state.in_(filters.states))
    return select(DecisionDocument.id).join(KnowledgeDomain).join(
        DecisionRevision, and_(DecisionRevision.document_id == DecisionDocument.id,
                               DecisionRevision.revision == DecisionDocument.current_revision),
    ).where(*conditions).exists()


def current_predicates(project_id: UUID, filters: ActivityFilters, as_of: datetime):
    conditions = []
    for facet, predicate in (
        ("work_items", lambda value: _work_predicate(project_id, value, as_of)),
        ("artifacts", lambda value: _artifact_predicate(project_id, value)),
        ("decisions", lambda value: _decision_predicate(project_id, value)),
    ):
        selected = getattr(filters, facet)
        if selected is not None:
            conditions.append(or_(ProjectChange.resource_type != facet, predicate(selected)))
    return conditions


def _work_current(database: Session, project_id: UUID, ids: set[UUID], as_of: datetime):
    rows = database.execute(select(
        WorkItem.id, WorkItem.title, WorkItem.version, WorkItem.tags, WorkItem.deleted_at,
        func.coalesce(review_status_clause(WorkItem.id), WorkItem.status).label("status"),
        WorkLease.expires_at, WorkItem.remediation_depth, WorkItem.decision_review_revision_id,
        WorkItem.decision_review_change_id,
    ).outerjoin(WorkLease, WorkLease.work_item_id == WorkItem.id).where(
        WorkItem.project_id == project_id, WorkItem.id.in_(ids),
    ))
    result = {}
    for row in rows:
        status = row.status
        if status == "pending" and row.expires_at is not None:
            status = "active" if row.expires_at > as_of else "dropped"
        result[row.id] = ActivityCurrentState(
            exists=True, deleted=row.deleted_at is not None, title=row.title,
            revision=str(row.version), status=status, tags=row.tags, system_tags=_system_tags(row),
        )
    return result


def _system_tags(row) -> list[str]:
    tags = ["remediation"] if row.remediation_depth else []
    if row.decision_review_revision_id is not None or row.decision_review_change_id is not None:
        tags.append("dir")
    return tags


def _artifact_current(database: Session, project_id: UUID, ids: set[UUID]):
    rows = database.execute(select(
        Artifact.id, Artifact.filename, Artifact.revision, Artifact.tags, Artifact.deleted_at,
    ).where(Artifact.project_id == project_id, Artifact.id.in_(ids)))
    return {row.id: ActivityCurrentState(
        exists=True, deleted=row.deleted_at is not None, title=row.filename,
        revision=str(row.revision), tags=row.tags,
    ) for row in rows}


def _decision_current(database: Session, project_id: UUID, ids: set[UUID]):
    rows = database.execute(select(
        DecisionDocument.id, DecisionDocument.deleted_at, DecisionDocument.domain_id,
        DecisionDocument.current_revision, KnowledgeDomain.prefix,
        DecisionRevision.title, DecisionRevision.state,
    ).join(KnowledgeDomain).join(DecisionRevision, and_(
        DecisionRevision.document_id == DecisionDocument.id,
        DecisionRevision.revision == DecisionDocument.current_revision,
    )).where(DecisionDocument.project_id == project_id, DecisionDocument.id.in_(ids)))
    return {row.id: ActivityCurrentState(
        exists=True, deleted=row.deleted_at is not None, title=row.title,
        revision=str(row.current_revision), domain_id=row.domain_id, domain_prefix=row.prefix,
        state=row.state,
    ) for row in rows}


def current_states(
    database: Session, project_id: UUID, resources: list[tuple[str, UUID]], as_of: datetime,
) -> dict[tuple[str, UUID], ActivityCurrentState]:
    result = {}
    for facet, fetch in (
        ("work_items", lambda ids: _work_current(database, project_id, ids, as_of)),
        ("artifacts", lambda ids: _artifact_current(database, project_id, ids)),
        ("decisions", lambda ids: _decision_current(database, project_id, ids)),
    ):
        ids = {identity for kind, identity in resources if kind == facet}
        if not ids:
            continue
        found = fetch(ids)
        result.update({(facet, identity): found.get(identity, ActivityCurrentState(exists=False))
                       for identity in ids})
    return result
