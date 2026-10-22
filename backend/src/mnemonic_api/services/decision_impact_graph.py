"""Bounded reverse dependency traversal; related records carry no causal claim."""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from mnemonic_api.impact_schemas import ImpactCandidate, ImpactCoverage, ImpactScopeEvidence
from mnemonic_api.models import (
    DecisionArtifactLink,
    DecisionCommitMapping,
    DecisionDependency,
    DecisionDocument,
    DecisionRelationship,
    DecisionRevision,
    DecisionScope,
    DecisionWorkLink,
    KnowledgeDomain,
)

MAX_CANDIDATES = 500
MAX_EDGES = 5000
_EVIDENCE_LIMIT = 20


def reference(
    document: DecisionDocument, revision: DecisionRevision, domain: KnowledgeDomain
) -> str:
    return f"{domain.prefix}-{document.ordinal:05d}-{revision.revision}"


@dataclass
class Node:
    document: DecisionDocument
    revision: DecisionRevision
    domain: KnowledgeDomain
    path: list[str]
    relationship: str
    rationale: str | None = None


@dataclass
class Traversal:
    nodes: dict[UUID, Node]
    reasons: set[str] = field(default_factory=set)


def _dependents(database: Session, frontier: list[UUID]):
    return database.execute(
        select(DecisionDependency, DecisionRevision, DecisionDocument, KnowledgeDomain)
        .join(DecisionRevision, DecisionRevision.id == DecisionDependency.revision_id)
        .join(DecisionDocument, DecisionDocument.id == DecisionRevision.document_id)
        .join(KnowledgeDomain, KnowledgeDomain.id == DecisionDocument.domain_id)
        .where(DecisionDependency.target_revision_id.in_(frontier))
        .order_by(
            KnowledgeDomain.prefix,
            DecisionDocument.ordinal,
            DecisionRevision.revision,
            DecisionDependency.target_revision_id,
        )
        .limit(MAX_EDGES + 1)
    ).all()


def _walk(database: Session, traversal: Traversal, max_depth: int) -> None:
    frontier = list(traversal.nodes)
    for _ in range(max_depth):
        rows = _dependents(database, frontier)
        if len(rows) > MAX_EDGES:
            traversal.reasons.add("dependency_edge_limit")
        proposed: dict[UUID, Node] = {}
        for edge, revision, document, domain in rows[:MAX_EDGES]:
            if revision.id in traversal.nodes:
                continue
            path = [
                *traversal.nodes[edge.target_revision_id].path,
                reference(document, revision, domain),
            ]
            prior = proposed.get(revision.id)
            if prior is None or path < prior.path:
                proposed[revision.id] = Node(
                    document, revision, domain, path, "depends-on", edge.rationale
                )
        frontier = []
        for key, node in sorted(proposed.items(), key=lambda item: item[1].path):
            if len(traversal.nodes) >= MAX_CANDIDATES:
                traversal.reasons.add("candidate_limit")
                return
            traversal.nodes[key] = node
            frontier.append(key)
        if not frontier:
            return
    _remaining_coverage(database, traversal, frontier)


def _remaining_coverage(database: Session, traversal: Traversal, frontier: list[UUID]) -> None:
    remaining = _dependents(database, frontier)
    if len(remaining) > MAX_EDGES:
        traversal.reasons.add("dependency_edge_limit")
    if any(row[1].id not in traversal.nodes for row in remaining):
        traversal.reasons.add("depth_limit")


def _related_rows(
    database: Session, document_ids: list[UUID], revision_ids: list[UUID], *, incoming: bool
):
    source = aliased(DecisionRevision)
    target = DecisionDocument.id if incoming else source.document_id
    condition = (
        DecisionRelationship.target_document_id.in_(document_ids)
        if incoming
        else source.id.in_(revision_ids)
    )
    selected = (
        source.id == DecisionRevision.id
        if incoming
        else (
            (DecisionRevision.document_id == DecisionDocument.id)
            & (DecisionRevision.revision == DecisionDocument.current_revision)
        )
    )
    linked = source.document_id if incoming else DecisionRelationship.target_document_id
    return database.execute(
        select(
            DecisionRevision,
            DecisionDocument,
            KnowledgeDomain,
            DecisionRelationship.target_document_id,
            source.id,
        )
        .select_from(DecisionRelationship)
        .join(source, source.id == DecisionRelationship.revision_id)
        .join(DecisionDocument, DecisionDocument.id == linked)
        .join(DecisionRevision, selected)
        .join(KnowledgeDomain, KnowledgeDomain.id == DecisionDocument.domain_id)
        .where(condition, DecisionRelationship.kind == "related", target.is_not(None))
        .order_by(KnowledgeDomain.prefix, DecisionDocument.ordinal, DecisionRevision.revision)
        .limit(MAX_CANDIDATES + 1)
    ).all()


def _related(database: Session, traversal: Traversal) -> None:
    documents: dict[UUID, Node] = {}
    for node in sorted(traversal.nodes.values(), key=lambda item: (len(item.path), item.path)):
        documents.setdefault(node.document.id, node)
    proposed: dict[UUID, Node] = {}
    for incoming in (True, False):
        rows = _related_rows(database, list(documents), list(traversal.nodes), incoming=incoming)
        if len(rows) > MAX_CANDIDATES:
            traversal.reasons.add("related_edge_limit")
        for revision, document, domain, target, source in rows[:MAX_CANDIDATES]:
            if revision.id in traversal.nodes:
                continue
            parent = documents[target] if incoming else traversal.nodes[source]
            path = [*parent.path, reference(document, revision, domain)]
            prior = proposed.get(revision.id)
            if prior is None or (len(path), path) < (len(prior.path), prior.path):
                proposed[revision.id] = Node(document, revision, domain, path, "related-only")
    for key, node in sorted(proposed.items(), key=lambda item: (len(item[1].path), item[1].path)):
        if len(traversal.nodes) >= MAX_CANDIDATES:
            traversal.reasons.add("candidate_limit")
            return
        traversal.nodes[key] = node


def _evidence(database: Session, node: Node) -> dict:
    scopes = database.scalars(
        select(DecisionScope)
        .where(
            DecisionScope.document_id == node.document.id,
            DecisionScope.revision == node.revision.revision,
        )
        .order_by(DecisionScope.commit_hash, DecisionScope.id)
        .limit(11)
    ).all()
    result: dict = {"scopes": [], "evidence_truncated": len(scopes) > 10}
    for scope in scopes[:10]:
        merged = database.scalars(
            select(DecisionCommitMapping.merged_commit_hash)
            .where(DecisionCommitMapping.scope_id == scope.id)
            .order_by(DecisionCommitMapping.merged_commit_hash)
            .limit(11)
        ).all()
        result["evidence_truncated"] |= len(merged) > 10
        result["scopes"].append(
            ImpactScopeEvidence(
                id=scope.id,
                commit_hash=scope.commit_hash,
                revision=scope.revision,
                work_item_id=scope.work_item_id,
                agent_session_id=scope.agent_session_id,
                repository=scope.repository,
                completion_report_id=scope.completion_report_id,
                merged_commit_hashes=list(merged[:10]),
            )
        )
    for model, column, field_name in (
        (DecisionWorkLink, DecisionWorkLink.work_item_id, "related_work_item_ids"),
        (DecisionArtifactLink, DecisionArtifactLink.artifact_id, "related_artifact_ids"),
    ):
        values = database.scalars(
            select(column)
            .where(model.revision_id == node.revision.id)
            .order_by(column)
            .limit(_EVIDENCE_LIMIT + 1)
        ).all()
        result[field_name] = list(values[:_EVIDENCE_LIMIT])
        result["evidence_truncated"] |= len(values) > _EVIDENCE_LIMIT
    return result


def candidate(database: Session, node: Node) -> ImpactCandidate:
    value = ImpactCandidate.model_validate(
        {
            "decision_id": f"{node.domain.prefix}-{node.document.ordinal:05d}",
            "reference": reference(node.document, node.revision, node.domain),
            "revision": node.revision.revision,
            "title": node.revision.title,
            "state": node.revision.state,
            "is_current": node.revision.revision == node.document.current_revision,
            "relationship": node.relationship,
            "path": node.path,
            "rationale": node.rationale,
            **_evidence(database, node),
        }
    )
    # Keep one candidate representable, even with maximum-length Unicode author
    # metadata and repository names. Preserve its identity, path and rationale.
    for field_name in ("scopes", "related_work_item_ids", "related_artifact_ids"):
        values = getattr(value, field_name)
        while values and len(value.model_dump_json().encode("utf-8")) > 28000:
            values.pop()
            value.evidence_truncated = True
    return value


def traverse(
    database: Session,
    document: DecisionDocument,
    revision: DecisionRevision,
    domain: KnowledgeDomain,
    max_depth: int,
) -> tuple[list[Node], ImpactCoverage]:
    origin = reference(document, revision, domain)
    traversal = Traversal({revision.id: Node(document, revision, domain, [origin], "origin")})
    _walk(database, traversal, max_depth)
    visited = len(traversal.nodes)
    _related(database, traversal)
    nodes = sorted(
        traversal.nodes.values(),
        key=lambda item: (
            {"origin": 0, "depends-on": 1, "related-only": 2}[item.relationship],
            len(item.path),
            item.path,
        ),
    )
    return nodes, ImpactCoverage(
        traversal_complete=not traversal.reasons,
        max_depth=max_depth,
        candidate_limit=MAX_CANDIDATES,
        visited_revisions=visited,
        reasons=sorted(traversal.reasons),
    )
