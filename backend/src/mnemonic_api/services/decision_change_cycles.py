"""Advance closed review cycles without changing completed work or historical scope."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.models import DecisionChange
from mnemonic_api.services.decision_changes import bind_work, closed, lock_change


def open_cycle(
    database: Session, change: DecisionChange, *, require_successor: bool = False,
) -> DecisionChange:
    """Callers hold project/topology locks; parent row locking also fences creation.

    Existing successor links always win, including old empty admitted cycles.
    A held legacy answer requires a descendant even when its parent has no driver.
    """
    visited = set()
    while change.id not in visited:
        visited.add(change.id)
        change = lock_change(database, change)
        successor = database.scalar(select(DecisionChange).where(
            DecisionChange.parent_change_id == change.id,
        ))
        if successor is not None:
            change = successor
            require_successor = False
            continue
        if not require_successor and not closed(database, change):
            return change
        successor = DecisionChange(
            project_id=change.project_id, parent_change_id=change.id,
            scope_version=0, acknowledged_scope_version=0,
            admission_actor_client="mnemonic",
            admission_actor_session_id=f"decision-change:{change.id}",
        )
        database.add(successor)
        database.flush()
        if change.review_work_item_id is not None:
            bind_work(database, change.review_work_item_id, successor.id)
        return successor
    raise RuntimeError("Decision change successor lineage contains a cycle")
