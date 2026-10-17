"""One interpretation of explicit closeout constraints for writes and reads."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mnemonic_api.errors import conflict
from mnemonic_api.models import WorkItem, WorkRelationship
from mnemonic_api.precedence_schemas import DependencyIssue, cycle_description
from mnemonic_api.services.graph_cycles import Edge, closing_cycle, cycle_issues, issue_for_cycle

TERMINAL = frozenset({"done", "wont-do", "promoted"})


@dataclass(frozen=True)
class RelationshipFact:
    edge: Edge
    requires_child_closeout: bool
    source_status: str
    target_status: str

    @property
    def enforced(self) -> bool:
        return self.edge.type == "blocks" or self.requires_child_closeout

    @property
    def unsatisfied(self) -> bool:
        if self.edge.type == "blocks":
            return self.source_status != "done" and self.target_status not in TERMINAL
        return self.target_status not in TERMINAL and self.source_status not in TERMINAL


def relationship_facts(database: Session, ids: Sequence[UUID]) -> list[RelationshipFact]:
    """Read the complete connected neighborhood across project boundaries."""
    if not ids:
        return []
    rows = database.execute(
        text("""
        WITH RECURSIVE members(id) AS (
            SELECT unnest(CAST(:ids AS uuid[]))
            UNION
            SELECT CASE WHEN edge.source_work_item_id=members.id
                THEN edge.target_work_item_id ELSE edge.source_work_item_id END
            FROM members JOIN work_relationships edge
              ON edge.source_work_item_id=members.id OR edge.target_work_item_id=members.id
            WHERE edge.relationship_type IN ('blocks','parent-child')
        )
        SELECT edge.id, edge.relationship_type, edge.source_work_item_id,
               edge.target_work_item_id, edge.requires_child_closeout,
               source.status AS source_status, target.status AS target_status
        FROM work_relationships edge
        JOIN members ON members.id=edge.source_work_item_id
        JOIN work_items source ON source.id=edge.source_work_item_id
        JOIN work_items target ON target.id=edge.target_work_item_id
        WHERE edge.relationship_type IN ('blocks','parent-child')
    """),
        {"ids": list(set(ids))},
    ).mappings()
    return [
        RelationshipFact(
            Edge(
                row["id"],
                row["relationship_type"],
                row["source_work_item_id"],
                row["target_work_item_id"],
            ),
            bool(row["requires_child_closeout"]),
            row["source_status"],
            row["target_status"],
        )
        for row in rows
    ]


def dependency_issues(database: Session, ids: Sequence[UUID]) -> dict[UUID, DependencyIssue]:
    facts = relationship_facts(database, ids)
    active = [fact for fact in facts if fact.unsatisfied]
    planning = cycle_issues((fact.edge for fact in active), planning=True)
    enforced = cycle_issues(fact.edge for fact in active if fact.enforced)
    requested = set(ids)
    return {
        work_id: issue for work_id, issue in (planning | enforced).items() if work_id in requested
    }


def project_dependency_issues(database: Session, project_id: UUID) -> dict[UUID, DependencyIssue]:
    ids = list(
        database.scalars(
            select(WorkItem.id).where(
                WorkItem.project_id == project_id,
                WorkItem.status == "pending",
                WorkItem.deleted_at.is_(None),
            )
        )
    )
    return dependency_issues(database, ids)


def require_acyclic_relationship(
    database: Session, proposed: Edge, requires_child_closeout: bool
) -> None:
    if proposed.type not in {"blocks", "parent-child"}:
        return
    facts = relationship_facts(database, [proposed.source, proposed.target])
    candidates: list[list[Edge]] = []
    if proposed.type == "parent-child":
        candidates.append([fact.edge for fact in facts if fact.edge.type == "parent-child"])
    if proposed.type == "blocks" or requires_child_closeout:
        candidates.append([fact.edge for fact in facts if fact.enforced])
    for edges in candidates:
        cycle = closing_cycle(edges, proposed)
        if cycle is not None:
            issue = issue_for_cycle(cycle)
            raise conflict(
                "relationship_cycle",
                f"This relationship closes {cycle_description(issue)}; "
                "revise a dependency or parent assignment before retrying.",
                context={"dependency_issue": issue.model_dump(mode="json")},
            )


def require_no_deadlock(database: Session, work_item_id: UUID) -> None:
    issue = dependency_issues(database, [work_item_id]).get(work_item_id)
    if issue is not None and issue.kind == "deadlock":
        raise conflict(
            "work_deadlocked",
            f"This work cannot finish because of {cycle_description(issue)}; "
            "ask the owner to repair the dependency graph.",
            context={"dependency_issue": issue.model_dump(mode="json")},
        )


def require_children_closed(database: Session, parent_id: UUID) -> None:
    child = database.scalar(
        select(WorkItem.id)
        .join(
            WorkRelationship,
            WorkRelationship.target_work_item_id == WorkItem.id,
        )
        .where(
            WorkRelationship.source_work_item_id == parent_id,
            WorkRelationship.relationship_type == "parent-child",
            WorkRelationship.requires_child_closeout.is_(True),
            WorkItem.status.not_in(TERMINAL),
        )
        .order_by(WorkItem.id)
        .limit(1)
        .with_for_update(of=WorkItem)
    )
    if child is not None:
        raise conflict(
            "work_children_open",
            f"Child {child} must close before this parent can close; "
            "finish the child or ask the owner to revise its closure requirement.",
            context={"child_work_item_id": str(child)},
        )


def require_valid_child_closeout(parent: WorkItem, child: WorkItem) -> None:
    if parent.status in TERMINAL and child.status not in TERMINAL:
        raise conflict(
            "parent_already_closed",
            "Reopen the parent before requiring an unfinished child to close first.",
        )
