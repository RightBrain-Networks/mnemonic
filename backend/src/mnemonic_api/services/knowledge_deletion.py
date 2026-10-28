"""Delete active knowledge graph edges while retaining identity and audit tombstones."""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import ColumnElement, Table, delete, func, or_, select, text
from sqlalchemy.orm import Session

from mnemonic_api.errors import conflict
from mnemonic_api.knowledge_management_schemas import (
    DecisionDeletionRequest,
    DomainDeletionRequest,
    KnowledgeDeletionPreview,
    KnowledgeDeletionRead,
    KnowledgeDeletionRequest,
)
from mnemonic_api.models import (
    DecisionArtifactLink,
    DecisionCommitMapping,
    DecisionDependency,
    DecisionDocument,
    DecisionRelationship,
    DecisionRevision,
    DecisionScope,
    DecisionSupersessionDetail,
    DecisionSupersessionReuse,
    DecisionWorkLink,
    KnowledgeDeletedEdge,
    KnowledgeDomain,
    ProductDecisionRequest,
    ProjectProductDecisionDomain,
)
from mnemonic_api.schemas import APIModel
from mnemonic_api.services import knowledge

type EdgeModel = type[DecisionCommitMapping | DecisionScope | DecisionRelationship
                      | DecisionDependency | DecisionSupersessionDetail | DecisionSupersessionReuse
                      | DecisionWorkLink | DecisionArtifactLink]


def _edge_selections(document_ids: list[UUID]) -> list[tuple[str, EdgeModel, ColumnElement[bool]]]:
    revisions = select(DecisionRevision.id).where(DecisionRevision.document_id.in_(document_ids))
    scopes = select(DecisionScope.id).where(DecisionScope.document_id.in_(document_ids))
    return [
        ("commit_mappings", DecisionCommitMapping,
         DecisionCommitMapping.scope_id.in_(scopes)),
        ("commit_citations", DecisionScope, DecisionScope.document_id.in_(document_ids)),
        ("decision_links", DecisionRelationship, or_(
            DecisionRelationship.revision_id.in_(revisions),
            DecisionRelationship.target_document_id.in_(document_ids))),
        ("dependencies", DecisionDependency, or_(
            DecisionDependency.revision_id.in_(revisions),
            DecisionDependency.target_revision_id.in_(revisions))),
        ("supersession_details", DecisionSupersessionDetail, or_(
            DecisionSupersessionDetail.revision_id.in_(revisions),
            DecisionSupersessionDetail.target_revision_id.in_(revisions))),
        ("supersession_reuses", DecisionSupersessionReuse, or_(
            DecisionSupersessionReuse.revision_id.in_(revisions),
            DecisionSupersessionReuse.target_revision_id.in_(revisions),
            DecisionSupersessionReuse.governing_revision_id.in_(revisions))),
        ("work_items", DecisionWorkLink, DecisionWorkLink.revision_id.in_(revisions)),
        ("artifacts", DecisionArtifactLink, DecisionArtifactLink.revision_id.in_(revisions)),
    ]


def _protected_reason(database: Session, domain: KnowledgeDomain,
                      document: DecisionDocument | None) -> str | None:
    binding = database.get(ProjectProductDecisionDomain, domain.project_id)
    if document is None and binding is not None and binding.domain_id == domain.id:
        return "The built-in Product Decision Records domain is required by Needs Attention."
    if document is not None and database.scalar(select(ProductDecisionRequest.gate_id).where(
        ProductDecisionRequest.document_id == document.id,
    )) is not None:
        return "This document records a human question and answer managed by Needs Attention."
    return None


def _active_reviews(database: Session, project_id: UUID,
                    document_ids: list[UUID]) -> list[tuple[str, ...]]:
    """Retained review snapshots remain actionable, including cross-project moved drivers."""
    rows = database.execute(text("""
        WITH targets AS (
            SELECT document.id, domain.prefix || '-' ||
                lpad(document.ordinal::text, greatest(5,length(document.ordinal::text)), '0')
                AS reference
            FROM decision_documents document
            JOIN knowledge_domains domain ON domain.id=document.domain_id
            WHERE document.id=ANY(CAST(:documents AS uuid[]))
        ), revisions AS (
            SELECT id FROM decision_revisions WHERE document_id IN (SELECT id FROM targets)
        ), affected AS (
            SELECT change.id FROM decision_changes change
            WHERE change.project_id=:project_id AND (
                change.origin_revision_id IN (SELECT id FROM revisions)
                OR EXISTS (SELECT 1 FROM decision_revision_changes classified
                           WHERE classified.change_id=change.id
                             AND classified.revision_id IN (SELECT id FROM revisions))
                OR EXISTS (
                    SELECT 1 FROM decision_change_assessments membership
                    JOIN decision_impact_assessments assessment
                        ON assessment.id=membership.assessment_id
                    WHERE membership.change_id=change.id AND (
                        assessment.origin_revision_id IN (SELECT id FROM revisions)
                        OR assessment.replacement_revision_id IN (SELECT id FROM revisions)
                        OR EXISTS (SELECT 1 FROM jsonb_array_elements(assessment.candidates) item
                                   JOIN targets ON targets.reference=item->>'decision_id'))))
        )
        SELECT work.id, work.version, change.id, change.scope_version,
               change.acknowledged_scope_version
        FROM work_items work
        LEFT JOIN decision_changes change ON change.review_work_item_id=work.id
        WHERE work.deleted_at IS NULL AND work.status IN ('pending','deferred')
          AND (change.id IN (SELECT id FROM affected)
               OR work.decision_review_revision_id IN (SELECT id FROM revisions))
          AND NOT EXISTS (SELECT 1 FROM work_duplicate_merges merged
                          WHERE merged.source_work_item_id=work.id)
        ORDER BY work.id
    """), {"documents": document_ids, "project_id": project_id})
    return [tuple(map(str, row)) for row in rows]


def deletion_preview(database: Session, domain: KnowledgeDomain,
                     document: DecisionDocument | None = None) -> KnowledgeDeletionPreview:
    documents = [document] if document is not None else list(database.scalars(
        select(DecisionDocument).where(DecisionDocument.domain_id == domain.id,
                                       DecisionDocument.deleted_at.is_(None))
        .order_by(DecisionDocument.id),
    ))
    witnesses = []
    counts = {}
    for label, model, predicate in _edge_selections([item.id for item in documents]):
        # Declarative types ``__table__`` as FromClause; these tables have one.
        table = cast(Table, model.__table__)
        rows = database.execute(select(*table.primary_key.columns).where(predicate)).all()
        counts[label] = len(rows)
        witnesses.extend((label, *map(str, row)) for row in rows)
    removed_count = sum(counts.values())
    reviews = _active_reviews(database, domain.project_id, [item.id for item in documents])
    counts["active_reviews"] = len(reviews)
    data = {
        "domain": [str(domain.id), domain.version],
        "documents": sorted((str(item.id), item.current_revision) for item in documents),
        "relationships": sorted(witnesses),
        "active_reviews": reviews,
    }
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return KnowledgeDeletionPreview(
        project_id=domain.project_id, domain_id=domain.id,
        decision_id=None if document is None else knowledge.decision_identifier(domain, document),
        document_count=len(documents), relationship_count=sum(counts.values()),
        removed_relationship_count=removed_count, affected_review_count=len(reviews),
        relationships=counts, expected_version=domain.version if document is None else None,
        expected_revision=None if document is None else document.current_revision,
        preview_token=fingerprint, blocked_reason=_protected_reason(database, domain, document),
    )


def _check_preview(preview: KnowledgeDeletionPreview, payload: KnowledgeDeletionRequest) -> None:
    if preview.blocked_reason is not None:
        raise conflict("knowledge_deletion_protected", preview.blocked_reason)
    if preview.preview_token != payload.expected_preview:
        raise conflict("knowledge_deletion_preview_conflict",
                       "Documents or relationships changed. Review the deletion again.")
    needs_acknowledgement = preview.relationship_count > 0
    if needs_acknowledgement and not payload.acknowledge_relationships:
        raise conflict("knowledge_deletion_acknowledgement_required",
                       "Acknowledge document deletion and the effect on upstream decisions.")


def _remove_edges(database: Session, document_ids: list[UUID], deleted_at: datetime) -> None:
    for _, model, predicate in _edge_selections(document_ids):
        # Declarative types ``__table__`` as FromClause; these tables have one.
        table = cast(Table, model.__table__)
        # Preserve PostgreSQL's exact JSON spelling for the immutable-evidence guard,
        # including timestamp fractions that Python's ISO encoder pads with zeros.
        rows = select(func.to_jsonb(table.table_valued())).where(predicate)
        for values in database.scalars(rows):
            key = {column.name: values[column.name] for column in table.primary_key.columns}
            database.add(KnowledgeDeletedEdge(
                receipt_id=database.info["client_operation_receipt_id"],
                project_id=UUID(values["project_id"]), table_name=table.name,
                edge_key=hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest(),
                row_data=values, deleted_at=deleted_at,
            ))
        database.flush()
        database.execute(delete(model).where(predicate))


def delete_domain(database: Session, project_id: UUID, domain_id: UUID,
                  payload: DomainDeletionRequest) -> KnowledgeDeletionRead:
    domain = knowledge.require_domain(database, project_id, domain_id)
    if domain.version != payload.expected_version:
        raise conflict("knowledge_domain_version_conflict", "Read the current domain first.")
    preview = deletion_preview(database, domain)
    _check_preview(preview, payload)
    now = datetime.now(UTC)
    documents = list(database.scalars(select(DecisionDocument).where(
        DecisionDocument.domain_id == domain.id, DecisionDocument.deleted_at.is_(None),
    )))
    for document in documents:
        document.deleted_at = now
        document.updated_at = now
    domain.deleted_at = now
    domain.updated_at = now
    domain.version += 1
    database.flush()
    _remove_edges(database, [document.id for document in documents], now)
    return KnowledgeDeletionRead(
        project_id=project_id, domain_id=domain.id, deleted_at=now,
        document_count=preview.document_count, relationship_count=preview.relationship_count,
        removed_relationship_count=preview.removed_relationship_count,
        affected_review_count=preview.affected_review_count,
        relationships=preview.relationships,
        preview_token=preview.preview_token,
    )


def delete_document(database: Session, project_id: UUID, document_id: UUID,
                    payload: DecisionDeletionRequest) -> KnowledgeDeletionRead:
    document = database.get(DecisionDocument, document_id)
    assert document is not None
    domain = knowledge.require_domain(database, project_id, document.domain_id)
    reference = knowledge.decision_identifier(domain, document)
    document, _, _ = knowledge.require_document(database, project_id, reference)
    if document.current_revision != payload.expected_revision:
        raise conflict("decision_revision_conflict", "Read the current decision revision first.")
    preview = deletion_preview(database, domain, document)
    _check_preview(preview, payload)
    now = datetime.now(UTC)
    document.deleted_at = now
    document.updated_at = now
    database.flush()
    _remove_edges(database, [document.id], now)
    return KnowledgeDeletionRead(
        project_id=project_id, domain_id=domain.id, document_id=document.id,
        decision_id=reference, deleted_at=now, document_count=1,
        relationship_count=preview.relationship_count, preview_token=preview.preview_token,
        removed_relationship_count=preview.removed_relationship_count,
        affected_review_count=preview.affected_review_count,
        relationships=preview.relationships,
    )


def deletion_matches(project_id: UUID, target: Mapping[str, str],
                     payload: APIModel, typed: APIModel) -> bool:
    request = cast(KnowledgeDeletionRequest, payload)
    result = cast(KnowledgeDeletionRead, typed)
    return (
        result.project_id == project_id and result.preview_token == request.expected_preview
        and (str(result.domain_id) == target.get("domain_id")
             if isinstance(payload, DomainDeletionRequest)
             else str(result.document_id) == target.get("document_id"))
    )
