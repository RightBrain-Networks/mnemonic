"""Find immutable completion declarations that cite an existing implementation scope."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.decision_scope_schemas import ScopeCompletionCitation
from mnemonic_api.models import JobCompletionReport


def completion_citations(
    database: Session, project_id: UUID, decision_id: str, revision: int,
    repository: str | None, commit_hash: str, *, limit: int = 50,
) -> tuple[list[ScopeCompletionCitation], bool]:
    # A legacy citation without a repository cannot establish a repository identity.
    if repository is None:
        return [], False
    rows = database.scalars(select(JobCompletionReport).where(
        JobCompletionReport.project_id == project_id,
        JobCompletionReport.decision_provenance.contains({
            "disposition": "cited",
            "citations": [{"decision_id": decision_id, "revision": revision}],
            "commits": [{"repository": repository, "commit_hash": commit_hash}],
        }),
    ).order_by(JobCompletionReport.created_at, JobCompletionReport.id).limit(limit + 1)).all()
    return [ScopeCompletionCitation(
        report_id=row.id, work_item_id=row.work_item_id, actor_client=row.actor_client,
        actor_session_id=row.actor_session_id, actor_model=row.actor_model,
        created_at=row.created_at,
    ) for row in rows[:limit]], len(rows) > limit
