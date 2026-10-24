"""Atomic completion citations and reversible discovery of recorded Git provenance."""

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError, not_found
from mnemonic_api.knowledge_schemas import (
    DecisionScopeRead,
    DecisionScopeRequest,
    KnowledgePage,
    KnowledgePagination,
)
from mnemonic_api.models import (
    DecisionCommitMapping,
    DecisionDocument,
    DecisionScope,
    JobCompletionReport,
    KnowledgeDomain,
)
from mnemonic_api.provenance_api import CommitReconcileRequest, CommitReconcileResult
from mnemonic_api.provenance_schemas import DecisionProvenance
from mnemonic_api.repository_identity import repository_aliases
from mnemonic_api.schemas import APIModel
from mnemonic_api.services import knowledge
from mnemonic_api.services.work_items import require_project


def seal_provenance(database: Session, report: JobCompletionReport) -> None:
    """Called in the closeout transaction: a missing or invalid declaration rolls it all back."""
    if report.decision_provenance is None:
        has_domains = database.scalar(
            select(KnowledgeDomain.id)
            .where(KnowledgeDomain.project_id == report.project_id).limit(1)
        )
        if report.closeout_status == "done" and has_domains:
            raise ApplicationError(
                422, "decision_provenance_required",
                "Declare decision citations, no-applicable-decisions, or not-code in the report.",
            )
        return
    declaration = DecisionProvenance.model_validate(report.decision_provenance)
    for citation in declaration.citations:
        document, domain, _ = knowledge.require_document(
            database, report.project_id, citation.decision_id
        )
        for commit in declaration.commits:
            # The report retains this work/session's full declaration even when
            # the implementation was registered at merge time by another session.
            existing = database.scalar(select(DecisionScope.id).where(
                DecisionScope.project_id == report.project_id,
                DecisionScope.document_id == document.id,
                DecisionScope.revision == citation.revision,
                DecisionScope.repository == commit.repository,
                DecisionScope.commit_hash == commit.commit_hash,
            ).limit(1))
            if existing is not None:
                continue
            knowledge.append_scope(
                database, report.project_id, document, domain,
                DecisionScopeRequest(
                    revision=citation.revision, commit_hash=commit.commit_hash,
                    repository=commit.repository, work_item_id=report.work_item_id,
                    agent_session_id=report.actor_session_id,
                ),
                completion_report_id=report.id,
            )


def _matching_scopes(project_id: UUID, repository: str, commit_hash: str):
    mapped = select(DecisionCommitMapping.scope_id).where(
        DecisionCommitMapping.project_id == project_id,
        DecisionCommitMapping.repository == repository,
        DecisionCommitMapping.merged_commit_hash == commit_hash,
    )
    return select(DecisionScope).where(
        DecisionScope.project_id == project_id, DecisionScope.repository == repository,
        or_(DecisionScope.commit_hash == commit_hash, DecisionScope.id.in_(mapped)),
    )


def lookup_commit(
    database: Session, project_id: UUID, repository: str, commit_hash: str,
    filters: KnowledgePagination,
) -> KnowledgePage[DecisionScopeRead]:
    require_project(database, project_id)
    aliases = repository_aliases(repository)
    if len(aliases) == 1:
        statement = _matching_scopes(project_id, repository, commit_hash)
    else:
        mapped = select(DecisionCommitMapping.scope_id).where(
            DecisionCommitMapping.project_id == project_id,
            func.lower(DecisionCommitMapping.repository).in_(aliases),
            DecisionCommitMapping.merged_commit_hash == commit_hash,
        )
        statement = select(DecisionScope).where(
            DecisionScope.project_id == project_id,
            func.lower(DecisionScope.repository).in_(aliases),
            or_(DecisionScope.commit_hash == commit_hash, DecisionScope.id.in_(mapped)),
        )
    total = database.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = database.scalars(statement.order_by(DecisionScope.created_at, DecisionScope.id)
                            .offset(filters.offset).limit(filters.limit)).all()
    items = []
    for scope in rows:
        document = database.get(DecisionDocument, scope.document_id)
        assert document is not None
        domain = knowledge.require_domain(database, project_id, document.domain_id)
        items.append(knowledge.scope_read(scope, document, domain))
    return KnowledgePage(items=items, total=total, limit=filters.limit, offset=filters.offset)


def reconcile_commit(
    database: Session, project_id: UUID, payload: CommitReconcileRequest,
) -> CommitReconcileResult:
    require_project(database, project_id)
    rows = database.scalars(_matching_scopes(
        project_id, payload.repository, payload.original_commit_hash
    ).order_by(DecisionScope.id).limit(501)).all()
    if not rows:
        raise not_found("decision_scope_not_found", "No citations match this repository/commit.")
    if len(rows) > 500:
        raise ApplicationError(422, "decision_scope_reconciliation_too_large",
                               "A reconciliation supports at most 500 citations.")
    created = False
    for scope in rows:
        exists = database.scalar(select(DecisionCommitMapping.id).where(
            DecisionCommitMapping.scope_id == scope.id,
            DecisionCommitMapping.merged_commit_hash == payload.merged_commit_hash,
        ))
        if exists is None and scope.commit_hash != payload.merged_commit_hash:
            database.add(DecisionCommitMapping(
                project_id=project_id, scope_id=scope.id, repository=payload.repository,
                original_commit_hash=scope.commit_hash,
                merged_commit_hash=payload.merged_commit_hash,
            ))
            created = True
    database.flush()
    return CommitReconcileResult(
        **payload.model_dump(), project_id=project_id, scope_ids=[row.id for row in rows],
        created=created,
    )


def reconciled_scope_matches(
    project_id: UUID, target: Mapping[str, str], payload: APIModel, typed: APIModel,
) -> bool:
    request = cast(CommitReconcileRequest, payload)
    result = cast(CommitReconcileResult, typed)
    return result.project_id == project_id and bool(result.scope_ids) and all(
        getattr(result, field) == getattr(request, field)
        for field in ("repository", "original_commit_hash", "merged_commit_hash")
    )
