"""Materialize retained owner answers after removing review-cycle admission."""

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.decision_change_schemas import DecisionChangeUpdateRequest
from mnemonic_api.models import DecisionChange, DecisionDocument, DecisionRevision
from mnemonic_api.services import decision_change_locks
from mnemonic_api.services.decision_change_cycles import open_cycle
from mnemonic_api.services.decision_changes import bind_work, pending_revisions
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.relationships import lock_relationship_graph


def materialize_held(database: Session, parent: DecisionChange) -> int:
    from mnemonic_api.knowledge_schemas import DecisionPutRequest
    from mnemonic_api.services import knowledge
    from mnemonic_api.services.decision_impact import create_assessments

    held_rows = pending_revisions(database, parent.id, limit=1)
    if not held_rows:
        return 0
    successor = open_cycle(database, parent, require_successor=True)
    for held in held_rows:
        revision = database.get(DecisionRevision, held.revision_id)
        assert revision is not None
        document = database.get(DecisionDocument, revision.document_id)
        assert document is not None
        current = knowledge.decision_read(database, document, revision)
        values = {key: getattr(current, key) for key in (
            "domain_id", "title", "media_type", "state", "related_decision_ids",
            "related_work_item_ids", "related_artifact_ids", "supersedes", "depends_on",
            "supersession_details", "author_kind", "agent_session_id", "model_id",
        )}
        payload = DecisionPutRequest(
            **values, content=revision.content, source_work_item_id=held.source_work_item_id,
        )
        if successor.origin_revision_id is None:
            successor.origin_revision_id = revision.id
        if held.source_work_item_id is not None:
            bind_work(database, held.source_work_item_id, successor.id)
        create_assessments(database, document, revision, payload, retained_change=successor)
    return len(held_rows)


def _recover_change(database: Session, project_id: UUID, change_id: UUID) -> int:
    intent = DecisionChangeUpdateRequest(
        action="acknowledge", change_id=change_id, expected_scope_version=0,
        actor_client="mnemonic", actor_session_id=f"decision-change:{change_id}",
    )
    scope = decision_change_locks.discover_scope(database, "update_decision_change", {}, intent)
    assert scope is not None
    with project_mutation(database, project_id, additional_project_ids=scope.project_ids):
        lock_relationship_graph(database)
        decision_change_locks.require_unchanged_scope(
            database, scope, "update_decision_change", {}, intent,
        )
        parent = database.get(DecisionChange, change_id)
        assert parent is not None
        count = materialize_held(database, parent)
        database.commit()
        return count


@dataclass
class RecoveryCursor:
    after: UUID | None = None


def recover_held_answers(database: Session, cursor: RecoveryCursor | None = None) -> int:
    """Bounded independent transactions; failure never blocks unrelated scheduling.

    Immutable assessment memberships are the recovery receipt. A crash rolls back
    one revision; committed rows disappear from pending_revisions. A worker-local
    cursor wraps across sweeps so failing old rows cannot starve later answers.
    """
    cursor = cursor or RecoveryCursor()
    with Session(bind=database.get_bind(), info=dict(database.info)) as maintenance:
        query = select(
            DecisionChange.project_id, DecisionChange.id,
        ).where(DecisionChange.id.in_(
            select(_pending_changes().c.change_id),
        )).order_by(DecisionChange.id).limit(10)
        candidates = list(maintenance.execute(query.where(DecisionChange.id > cursor.after)
                          if cursor.after is not None else query))
        if not candidates and cursor.after is not None:
            cursor.after = None
            candidates = list(maintenance.execute(query))
        maintenance.rollback()
        count = 0
        for project_id, change_id in candidates:
            cursor.after = change_id
            try:
                count += _recover_change(maintenance, project_id, change_id)
            except Exception:
                maintenance.rollback()
                logging.getLogger(__name__).exception(
                    "Retained decision answer recovery failed for change %s", change_id,
                )
        return count


def _pending_changes():
    from mnemonic_api.models import (
        DecisionChangeAssessment,
        DecisionImpactAssessment,
        DecisionRevisionChange,
    )

    materialized = select(DecisionImpactAssessment.id).join(
        DecisionChangeAssessment,
        DecisionChangeAssessment.assessment_id == DecisionImpactAssessment.id,
    ).where(DecisionImpactAssessment.replacement_revision_id == DecisionRevisionChange.revision_id)
    return select(DecisionRevisionChange.change_id).where(
        DecisionRevisionChange.requires_admission.is_(True), ~materialized.exists(),
    ).distinct().subquery()
