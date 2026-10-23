"""Report recorded authorship and active leases without inferring implementation use."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.models import (
    DecisionDocument,
    DecisionRevision,
    KnowledgeDomain,
    WorkItem,
    WorkLease,
)
from mnemonic_api.revision_author_schemas import RevisionAuthorContext, RevisionAuthorWork

_WORK_LIMIT = 10


def revision_author_context(
    database: Session, revision: DecisionRevision,
) -> RevisionAuthorContext:
    document = database.get(DecisionDocument, revision.document_id)
    assert document is not None
    domain = database.get(KnowledgeDomain, document.domain_id)
    assert domain is not None
    observed_at = database.scalar(select(func.now()))
    assert observed_at is not None
    author_kind = revision.author_kind
    assert author_kind in ("human", "agent")
    held = []
    if revision.author_kind == "agent" and revision.agent_session_id:
        held = database.execute(select(WorkItem, WorkLease).join(
            WorkLease, WorkLease.work_item_id == WorkItem.id,
        ).where(
            WorkItem.project_id == revision.project_id,
            WorkItem.deleted_at.is_(None),
            WorkLease.holder_session_id == revision.agent_session_id,
            WorkLease.expires_at > observed_at,
        ).order_by(WorkItem.id).limit(_WORK_LIMIT + 1)).all()
    return RevisionAuthorContext(
        reference=f"{domain.prefix}-{document.ordinal:05d}-{revision.revision}",
        author_kind=author_kind,
        agent_session_id=revision.agent_session_id,
        model_id=revision.model_id,
        authored_at=revision.created_at,
        work_observed_at=observed_at,
        active_work_items=[RevisionAuthorWork(
            work_item_id=work.id, title=work.title, holder_client=lease.holder_client,
            purpose=lease.purpose, expires_at=lease.expires_at,
        ) for work, lease in held[:_WORK_LIMIT]],
        active_work_items_truncated=len(held) > _WORK_LIMIT,
    )


def revision_author_note(database: Session, revision: DecisionRevision) -> str:
    author = revision_author_context(database, revision)
    lines = ["## Revision author", "", f"Revision: `{author.reference}`."]
    if author.author_kind == "human":
        lines.append("Recorded author: human.")
    else:
        lines.append(
            f"Recorded agent session: `{author.agent_session_id}`; model: `{author.model_id}`."
        )
    lines.append(f"Active work observed at {author.work_observed_at.isoformat()}:")
    lines.extend(
        f"- `{item.work_item_id}` — {item.title} ({item.purpose}; {item.holder_client}); "
        f"lease expires {item.expires_at.isoformat()}."
        for item in author.active_work_items
    )
    if not author.active_work_items:
        lines.append("No active work lease for this authoring session in the source project.")
    if author.active_work_items_truncated:
        lines.append("Additional active work is omitted from this bounded author context.")
    lines.append(
        "This is session identity and lease context, not proof that the author implemented "
        "the decision. Inspect commit provenance independently; use a fresh impact/assessment "
        "read for current holdings."
    )
    return "\n".join(lines)
