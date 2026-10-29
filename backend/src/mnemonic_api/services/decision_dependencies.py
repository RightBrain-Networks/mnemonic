"""New dependency annotations are sealed with their source decision revision."""

from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError
from mnemonic_api.impact_schemas import DecisionDependency as DependencyRead
from mnemonic_api.impact_schemas import SupersessionDetail
from mnemonic_api.knowledge_schemas import DecisionPutRequest
from mnemonic_api.models import (
    DecisionDependency,
    DecisionDocument,
    DecisionRevision,
    DecisionSupersessionDetail,
    KnowledgeDomain,
)


def superseded_ids(payload: DecisionPutRequest) -> list[str]:
    return sorted(
        set(payload.supersedes) | {item.decision_id for item in payload.supersession_details}
    )


def pinned_links(database: Session, revision: DecisionRevision) -> dict:
    result: dict = {"depends_on": [], "supersession_details": []}
    for model, key in (
        (DecisionDependency, "depends_on"),
        (DecisionSupersessionDetail, "supersession_details"),
    ):
        rows = database.execute(
            select(model, DecisionRevision, DecisionDocument, KnowledgeDomain)
            .join(DecisionRevision, DecisionRevision.id == model.target_revision_id)
            .join(DecisionDocument, DecisionDocument.id == DecisionRevision.document_id)
            .join(KnowledgeDomain, KnowledgeDomain.id == DecisionDocument.domain_id)
            .where(model.revision_id == revision.id)
        ).all()
        for edge, target, document, domain in rows:
            fields = {
                "decision_id": f"{domain.prefix}-{document.ordinal:05d}",
                "revision": target.revision,
            }
            if isinstance(edge, DecisionDependency):
                result[key].append(DependencyRead(**fields, rationale=edge.rationale))
            else:
                result[key].append(SupersessionDetail(
                    **fields, extent=cast(Literal["full", "partial"], edge.extent),
                    reason=edge.reason,
                ))
        result[key].sort(key=lambda item: (item.decision_id, item.revision))
    return result


def same_annotations(current, payload: DecisionPutRequest) -> bool:
    def values(items):
        return sorted(item.model_dump_json() for item in items)

    return values(current.depends_on) == values(payload.depends_on) and values(
        current.supersession_details
    ) == values(payload.supersession_details)


def add_pinned_links(
    database: Session,
    document: DecisionDocument,
    revision: DecisionRevision,
    payload: DecisionPutRequest,
) -> None:
    from mnemonic_api.services import knowledge

    for model, entries in (
        (DecisionDependency, payload.depends_on),
        (DecisionSupersessionDetail, payload.supersession_details),
    ):
        seen = set()
        for item in entries:
            target, _, _ = knowledge.require_document(
                database, document.project_id, item.decision_id
            )
            target_revision = knowledge.require_revision(database, target, item.revision)
            key = (target.id, item.revision) if model is DecisionDependency else target.id
            if target.id == document.id or key in seen:
                raise ApplicationError(
                    422,
                    "decision_dependency_invalid",
                    "Pinned links must be unique and cannot reference this decision.",
                )
            seen.add(key)
            fields = {
                "project_id": document.project_id,
                "revision_id": revision.id,
                "target_revision_id": target_revision.id,
            }
            if isinstance(item, DependencyRead):
                database.add(DecisionDependency(**fields, rationale=item.rationale))
            else:
                database.add(
                    DecisionSupersessionDetail(**fields, extent=item.extent, reason=item.reason)
                )
