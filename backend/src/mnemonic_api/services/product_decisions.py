"""Real knowledge documents owned atomically by the human-question workflow."""

import hashlib
from datetime import datetime
from uuid import UUID

from pydantic.experimental.missing_sentinel import MISSING
from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.knowledge_schemas import DecisionPutRequest, DecisionRead
from mnemonic_api.models import (
    DecisionDocument,
    DecisionRevision,
    KnowledgeDomain,
    ProductDecisionRequest,
    ProjectProductDecisionDomain,
    WorkGate,
    WorkItem,
)
from mnemonic_api.product_decision_content import (
    HUMAN_CLIENTS,
    product_decision_author,
    product_decision_content,
    product_decision_title,
)
from mnemonic_api.product_decision_schemas import (
    DecisionHumanGateRead,
    HumanGateDecisionMetadata,
    HumanGateDecisionRead,
)
from mnemonic_api.schemas import HumanGateRequestCreate
from mnemonic_api.services import knowledge

_METADATA_FIELDS = set(HumanGateDecisionMetadata.model_fields)


def require_question_author(payload: HumanGateRequestCreate) -> None:
    """Fresh writes require known agent identity; receipt replay precedes this guard."""
    if payload.requested_by_client not in HUMAN_CLIENTS and payload.requested_by_model is None:
        raise ApplicationError(
            422, "decision_author_model_required",
            "Agent-authored product decisions require requested_by_model.",
        )


def _domain(database: Session, project_id: UUID) -> KnowledgeDomain:
    binding = database.get(ProjectProductDecisionDomain, project_id)
    if binding is None:
        raise ApplicationError(
            503, "product_decision_domain_unavailable", "The project PDR domain is unavailable."
        )
    return knowledge.require_domain(database, project_id, binding.domain_id)


def _document(database: Session, gate: WorkGate) -> DecisionDocument:
    binding = database.get(ProductDecisionRequest, gate.id)
    if binding is None:
        raise ApplicationError(503, "product_decision_unavailable", "The PDR is unavailable.")
    document = database.get(DecisionDocument, binding.document_id)
    if document is None:
        raise ApplicationError(503, "product_decision_unavailable", "The PDR is unavailable.")
    return document


def _new_document(database: Session, gate: WorkGate) -> DecisionDocument:
    domain = _domain(database, gate.project_id)
    if domain.next_ordinal >= 9_223_372_036_854_775_807:
        raise conflict("knowledge_domain_exhausted", "Domain decision ordinals are exhausted.")
    document = DecisionDocument(
        project_id=gate.project_id, domain_id=domain.id, ordinal=domain.next_ordinal,
        current_revision=0, created_at=gate.created_at, updated_at=gate.created_at,
    )
    domain.next_ordinal += 1
    database.add(document)
    database.flush()
    database.add(ProductDecisionRequest(
        gate_id=gate.id, project_id=gate.project_id, document_id=document.id,
    ))
    database.flush()
    return document


def metadata_snapshot(decision: DecisionRead) -> HumanGateDecisionMetadata:
    return HumanGateDecisionMetadata.model_validate(
        {name: getattr(decision, name) for name in _METADATA_FIELDS}
    )


def _metadata(
    database: Session, document: DecisionDocument, work: WorkItem,
    supplied: HumanGateDecisionMetadata | None,
) -> dict:
    current = database.scalar(select(DecisionRevision).where(
        DecisionRevision.document_id == document.id,
        DecisionRevision.revision == document.current_revision,
    ))
    values: dict = (
        metadata_snapshot(knowledge.decision_read(database, document, current)).model_dump()
        if current is not None else {"title": product_decision_title(work.title)}
    )
    if supplied is not None:
        values.update(supplied.model_dump(exclude_unset=True))
    values["related_work_item_ids"] = sorted(
        {*values.get("related_work_item_ids", []), work.id}, key=str
    )
    if len(values["related_work_item_ids"]) > 100:
        raise ApplicationError(
            422, "product_decision_work_links_limit",
            "At most 100 work links, including the originating work, are allowed.",
        )
    return values


def _append(
    database: Session, document: DecisionDocument, payload: DecisionPutRequest,
    authored_at: datetime, *, advancing: bool,
) -> None:
    if advancing:
        if document.current_revision == 2_147_483_647:
            raise conflict("decision_revision_exhausted", "Decision revisions are exhausted.")
        document.current_revision += 1
        document.updated_at = authored_at
    encoded = payload.content.encode("utf-8")
    revision = DecisionRevision(
        project_id=document.project_id, document_id=document.id,
        revision=document.current_revision, title=payload.title, content=payload.content,
        state=payload.state, media_type=payload.media_type, size_bytes=len(encoded),
        sha256=hashlib.sha256(encoded).hexdigest(), author_kind=payload.author_kind,
        agent_session_id=payload.agent_session_id, model_id=payload.model_id,
        created_at=authored_at,
    )
    database.add(revision)
    database.flush()
    knowledge._add_decision_links(database, document, revision, payload)
    knowledge._add_resource_links(database, document, revision, payload)
    database.flush()
    knowledge.add_pinned_links(database, document, revision, payload)
    database.flush()
    from mnemonic_api.services.decision_impact import create_assessments
    create_assessments(database, document, revision, payload)


def write_question_decision(
    database: Session, work: WorkItem, gate: WorkGate, payload: HumanGateRequestCreate,
    authored_at: datetime, *, creating: bool,
) -> None:
    document = _new_document(database, gate) if creating else _document(database, gate)
    supplied = None if payload.decision_metadata is MISSING else payload.decision_metadata
    metadata = _metadata(database, document, work, supplied)
    author_kind, agent_session_id, model_id = product_decision_author(
        payload.requested_by_client, payload.requested_by_session_id, payload.requested_by_model,
    )
    decision = DecisionPutRequest.model_validate({
        "domain_id": document.domain_id, "content": product_decision_content(payload.question),
        "media_type": "text/markdown", "state": "proposed", "author_kind": author_kind,
        "agent_session_id": agent_session_id, "model_id": model_id, **metadata,
    })
    _append(database, document, decision, authored_at, advancing=not creating)


def write_answer_decision(database: Session, work: WorkItem, gate: WorkGate) -> None:
    document = _document(database, gate)
    question = gate.question_revisions[-1]["question"] if gate.question_revisions else gate.question
    decision = DecisionPutRequest(
        domain_id=document.domain_id,
        content=product_decision_content(question, gate.resolution),
        media_type="text/markdown", state="accepted", author_kind="human",
        **_metadata(database, document, work, None),
    )
    assert gate.resolved_at is not None
    _append(database, document, decision, gate.resolved_at, advancing=True)


def gate_decision_read(database: Session, gate_id: UUID) -> HumanGateDecisionRead:
    row = database.execute(
        select(DecisionDocument, KnowledgeDomain)
        .join(ProductDecisionRequest, ProductDecisionRequest.document_id == DecisionDocument.id)
        .join(KnowledgeDomain, KnowledgeDomain.id == DecisionDocument.domain_id)
        .where(ProductDecisionRequest.gate_id == gate_id)
    ).one_or_none()
    if row is None:
        raise ApplicationError(503, "product_decision_unavailable", "The PDR is unavailable.")
    document, domain = row
    revision = knowledge.require_revision(database, document)
    decision = knowledge.decision_read(database, document, revision, domain)
    assert decision.human_gate is not None
    work_project_id = decision.human_gate.work_project_id
    moved = work_project_id if work_project_id != document.project_id else None
    return HumanGateDecisionRead.model_validate({
        "originating_work_project_id": moved,
        "document_id": document.id, "domain_id": domain.id,
        "decision_id": decision.decision_id, "reference": decision.reference,
        "revision": revision.revision, "state": revision.state,
        "sha256": revision.sha256, "metadata": metadata_snapshot(decision),
    })


def decision_gate_read(database: Session, document_id: UUID) -> DecisionHumanGateRead | None:
    gate = database.scalar(
        select(WorkGate)
        .join(ProductDecisionRequest, ProductDecisionRequest.gate_id == WorkGate.id)
        .where(ProductDecisionRequest.document_id == document_id)
    )
    if gate is None:
        return None
    work_project_id = database.scalar(select(WorkItem.project_id).where(
        WorkItem.id == gate.work_item_id,
    ))
    assert work_project_id is not None
    return DecisionHumanGateRead(
        work_project_id=work_project_id, gate_id=gate.id, work_item_id=gate.work_item_id,
        status="resolved" if gate.resolved_at is not None else "unresolved",
        question_version=len(gate.question_revisions) + 1,
    )


def require_unmanaged_document(database: Session, document: DecisionDocument) -> None:
    if database.scalar(select(ProductDecisionRequest.gate_id).where(
        ProductDecisionRequest.document_id == document.id,
    )) is not None:
        raise conflict(
            "decision_managed_by_human_gate",
            "Revise this PDR through request_human_input; only a human can answer it.",
        )
