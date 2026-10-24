"""One explicit change owns a bounded review; lineage cannot mint recursive roots."""

from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mnemonic_api.decision_change_schemas import DecisionChangeRead, DecisionRevisionChangeRead
from mnemonic_api.errors import conflict, not_found
from mnemonic_api.models import (
    DecisionChange,
    DecisionChangeAssessment,
    DecisionChangeWork,
    DecisionImpactAssessment,
    DecisionRevision,
    DecisionRevisionChange,
    WorkDuplicateMerge,
    WorkItem,
)
from mnemonic_api.review_scope_schemas import ReviewScopeChanged


def require_change(database: Session, project_id: UUID, change_id: UUID) -> DecisionChange:
    change = database.get(DecisionChange, change_id)
    if change is None or change.project_id != project_id:
        raise not_found("decision_change_not_found", "Decision change not found in this project.")
    return change


def lock_change(database: Session, change: DecisionChange) -> DecisionChange:
    # Match work closeout's work -> change lock order, including moved drivers.
    if change.review_work_item_id is not None:
        database.scalar(select(WorkItem).where(
            WorkItem.id == change.review_work_item_id,
        ).with_for_update().execution_options(populate_existing=True))
    return database.scalars(select(DecisionChange).where(
        DecisionChange.id == change.id,
    ).with_for_update().execution_options(populate_existing=True)).one()


def closed(database: Session, change: DecisionChange) -> bool:
    if change.review_work_item_id is None:
        return False
    work = database.get(WorkItem, change.review_work_item_id)
    assert work is not None
    merged = database.scalar(select(WorkDuplicateMerge.id).where(
        WorkDuplicateMerge.source_work_item_id == work.id,
    ))
    return work.deleted_at is not None or work.status in {"done", "wont-do", "promoted"} or (
        merged is not None
    )


def require_open(database: Session, change: DecisionChange) -> None:
    if closed(database, change):
        raise conflict(
            "decision_change_closed",
            "This review cycle is closed. Ask the owner to admit a successor change before "
            "adding new review obligations; completed work is never reopened automatically.",
            context={"change_id": str(change.id)},
        )


def read_change(database: Session, change: DecisionChange) -> DecisionChangeRead:
    from mnemonic_api.services.decision_impact import _revision_reference
    from mnemonic_api.services.impact_findings import change_findings

    revisions = select(DecisionRevisionChange.revision_id).where(
        DecisionRevisionChange.change_id == change.id,
    ).union(select(DecisionImpactAssessment.replacement_revision_id).join(
        DecisionChangeAssessment,
        DecisionChangeAssessment.assessment_id == DecisionImpactAssessment.id,
    ).where(DecisionChangeAssessment.change_id == change.id))
    count = database.scalar(select(func.count()).select_from(revisions.subquery())) or 0
    driver = (database.get(WorkItem, change.review_work_item_id)
              if change.review_work_item_id else None)
    successor = database.scalar(select(DecisionChange.id).where(
        DecisionChange.parent_change_id == change.id,
    ))
    return DecisionChangeRead(
        id=change.id, project_id=change.project_id,
        origin_reference=(_revision_reference(database, change.origin_revision_id)
                          if change.origin_revision_id else None),
        review_work_item_id=change.review_work_item_id, parent_change_id=change.parent_change_id,
        review_work_project_id=driver.project_id if driver is not None else None,
        review_closed=closed(database, change), successor_change_id=successor,
        scope_version=change.scope_version,
        acknowledged_scope_version=change.acknowledged_scope_version,
        assessment_count=change.scope_version, revision_count=count,
        finding_summary=change_findings(database, change.id),
        pending_revision_count=len(pending_revisions(database, change.id)),
        created_at=change.created_at, updated_at=change.updated_at,
    )


def revision_change_read(database: Session, revision_id: UUID) -> DecisionRevisionChangeRead | None:
    from mnemonic_api.services.decision_impact import _revision_reference
    from mnemonic_api.services.decision_supersession_declarations import read_declarations

    row = database.get(DecisionRevisionChange, revision_id)
    if row is None:
        return None
    return DecisionRevisionChangeRead(
        requires_admission=row.requires_admission and row.revision_id in {
            item.revision_id for item in pending_revisions(database, row.change_id)
        },
        change_id=row.change_id,
        kind=cast(Literal["substantive", "record-existing", "maintenance"], row.kind),
        reason=row.reason,
        source_work_item_id=row.source_work_item_id,
        governing_reference=(_revision_reference(database, row.governing_revision_id)
                             if row.governing_revision_id else None),
        unchanged_supersessions=read_declarations(database, revision_id),
    )


def pending_revisions(database: Session, change_id: UUID | None) -> list[DecisionRevisionChange]:
    materialized = select(DecisionImpactAssessment.id).join(
        DecisionChangeAssessment,
        DecisionChangeAssessment.assessment_id == DecisionImpactAssessment.id,
    ).where(DecisionImpactAssessment.replacement_revision_id == DecisionRevisionChange.revision_id)
    return list(database.scalars(select(DecisionRevisionChange).where(
        DecisionRevisionChange.change_id == change_id,
        DecisionRevisionChange.requires_admission.is_(True),
        ~materialized.exists(),
    ).order_by(DecisionRevisionChange.created_at, DecisionRevisionChange.revision_id)))


def inherit_changes(database: Session, work_id: UUID, source_id: UUID | None) -> None:
    if source_id is None:
        return
    for change_id in database.scalars(select(DecisionChangeWork.change_id).where(
        DecisionChangeWork.work_item_id == source_id,
    )):
        bind_work(database, work_id, change_id)


def bind_work(database: Session, work_id: UUID, change_id: UUID) -> None:
    # Callers serialize relationship creation before binding. Existing descendants
    # must inherit too, including when an old combined investigation adopts scope.
    inserted = database.scalars(text("""
        WITH RECURSIVE descendants(id) AS (
            SELECT CAST(:work_id AS uuid)
            UNION
            SELECT edge.source_work_item_id FROM work_relationships edge
            JOIN descendants parent ON edge.target_work_item_id=parent.id
            WHERE edge.relationship_type='discovered-from'
        ) INSERT INTO decision_change_work(work_item_id,change_id)
          SELECT id,CAST(:change_id AS uuid) FROM descendants ORDER BY id
          ON CONFLICT DO NOTHING RETURNING work_item_id
    """), {"work_id": work_id, "change_id": change_id}).all()
    identities = {*inserted, work_id}
    for work in list(database.identity_map.values()):
        if isinstance(work, WorkItem) and work.id in identities:
            database.expire(work, ["decision_review_change_id", "decision_change_ids",
                                   "decision_review_project_id", "decision_change_projects"])


def scope_checkpoint(
    database: Session, change: DecisionChange, message: str,
    *, metadata: dict[str, JsonValue] | None = None,
) -> None:
    if change.review_work_item_id is None:
        return
    from mnemonic_api.schemas import CheckpointCreate
    from mnemonic_api.services.work_items import append_checkpoint_record

    work = database.get(WorkItem, change.review_work_item_id)
    assert work is not None
    append_checkpoint_record(database, work, CheckpointCreate(
        kind="progress", prompt=message,
        source_client="mnemonic", source_session_id=f"decision-change:{change.id}",
        tags=["decision-impact-review"],
        source_metadata={"decision_change_id": str(change.id),
                         "scope_version": change.scope_version, **(metadata or {})},
    ))


def attach_assessments(
    database: Session, change: DecisionChange, episodes: list[DecisionImpactAssessment],
    *, causing_revision: DecisionRevision | None = None,
) -> ReviewScopeChanged | None:
    previous_ids = list(database.scalars(select(DecisionChangeAssessment.assessment_id).where(
        DecisionChangeAssessment.change_id == change.id,
    )))
    additions = list({row.id: row for row in episodes if row.id not in set(previous_ids)}.values())
    if not additions:
        return None
    require_open(database, change)
    for index, row in enumerate(additions, start=change.scope_version + 1):
        database.add(DecisionChangeAssessment(
            change_id=change.id, assessment_id=row.id, attachment_scope_version=index,
        ))
    change.scope_version += len(additions)
    change.updated_at = datetime.now(UTC)
    database.flush()
    from mnemonic_api.services.decision_scope_checkpoints import growth_checkpoint

    return growth_checkpoint(database, change, additions, previous_ids, causing_revision)


def require_merge_scope_adopted(database: Session, source: WorkItem, destination: WorkItem) -> None:
    source_group = database.scalar(select(DecisionChange).where(
        DecisionChange.review_work_item_id == source.id,
    ))
    if source_group is None:
        return
    destination_group = database.scalar(select(DecisionChange).where(
        DecisionChange.review_work_item_id == destination.id,
    ))
    source_ids = set(database.scalars(select(DecisionChangeAssessment.assessment_id).where(
        DecisionChangeAssessment.change_id == source_group.id,
    )))
    destination_ids = set() if destination_group is None else set(database.scalars(
        select(DecisionChangeAssessment.assessment_id).where(
            DecisionChangeAssessment.change_id == destination_group.id,
        )
    ))
    if source_ids - destination_ids:
        raise conflict(
            "decision_change_adoption_conflict",
            "Explicitly adopt all source assessments into the destination before merging reviews.",
        )
