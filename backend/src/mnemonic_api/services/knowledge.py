"""Project-scoped decisions, append-only revisions, graph edges and commit citations."""

import hashlib
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, object_session

from mnemonic_api.errors import ApplicationError, conflict, not_found
from mnemonic_api.knowledge_schemas import (
    DecisionListQuery,
    DecisionPutRequest,
    DecisionPutResult,
    DecisionRead,
    DecisionScopeRead,
    DecisionScopeRequest,
    DecisionScopeResult,
    DecisionTextQuery,
    DecisionTextRead,
    DomainCreateRequest,
    DomainRenameRequest,
    KnowledgeDomainRead,
    KnowledgePage,
    KnowledgePagination,
)
from mnemonic_api.models import (
    Artifact,
    DecisionArtifactLink,
    DecisionCommitMapping,
    DecisionDocument,
    DecisionImpactDisposition,
    DecisionRelationship,
    DecisionRevision,
    DecisionScope,
    DecisionWorkLink,
    JobCompletionReport,
    KnowledgeDomain,
    ProductDecisionRequest,
    ProjectProductDecisionDomain,
    WorkGate,
    WorkItem,
)
from mnemonic_api.services.decision_dependencies import (
    add_pinned_links,
    pinned_links,
    same_annotations,
    superseded_ids,
)
from mnemonic_api.services.decision_scope_citations import completion_citations
from mnemonic_api.services.work_items import require_project, require_work_item


def decision_identifier(domain: KnowledgeDomain, document: DecisionDocument) -> str:
    return f"{domain.prefix}-{document.ordinal:05d}"


def require_domain(database: Session, project_id: UUID, domain_id: UUID) -> KnowledgeDomain:
    domain = database.scalar(
        select(KnowledgeDomain).where(
            KnowledgeDomain.id == domain_id,
            KnowledgeDomain.project_id == project_id,
        )
    )
    if domain is None:
        raise not_found("knowledge_domain_not_found", "Knowledge domain not found in this project.")
    return domain


def domain_read(database: Session, domain: KnowledgeDomain) -> KnowledgeDomainRead:
    count = (
        database.scalar(
            select(func.count())
            .select_from(DecisionDocument)
            .where(
                DecisionDocument.domain_id == domain.id,
            )
        )
        or 0
    )
    binding = database.get(ProjectProductDecisionDomain, domain.project_id)
    return KnowledgeDomainRead(
        is_product_decision_domain=binding is not None and binding.domain_id == domain.id,
        id=domain.id,
        project_id=domain.project_id,
        name=domain.name,
        prefix=domain.prefix,
        version=domain.version,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
        decision_count=count,
    )


def _check_domain_name(
    database: Session,
    project_id: UUID,
    name: str,
    domain_id: UUID | None = None,
) -> None:
    existing = database.scalar(
        select(KnowledgeDomain.id).where(
            KnowledgeDomain.project_id == project_id,
            func.lower(KnowledgeDomain.name) == func.lower(name),
        )
    )
    if existing is not None and existing != domain_id:
        raise conflict("knowledge_domain_name_conflict", "Domain names must be unique per project.")


def create_domain(
    database: Session,
    project_id: UUID,
    payload: DomainCreateRequest,
) -> KnowledgeDomainRead:
    _check_domain_name(database, project_id, payload.name)
    if (
        database.scalar(
            select(KnowledgeDomain.id).where(
                KnowledgeDomain.project_id == project_id,
                KnowledgeDomain.prefix == payload.prefix,
            )
        )
        is not None
    ):
        raise conflict(
            "knowledge_domain_prefix_conflict", "Domain prefixes must be unique per project."
        )
    domain = KnowledgeDomain(project_id=project_id, name=payload.name, prefix=payload.prefix)
    database.add(domain)
    database.flush()
    return domain_read(database, domain)


def rename_domain(
    database: Session,
    project_id: UUID,
    domain_id: UUID,
    payload: DomainRenameRequest,
) -> KnowledgeDomainRead:
    domain = require_domain(database, project_id, domain_id)
    if domain.version != payload.expected_version:
        raise conflict(
            "knowledge_domain_version_conflict", "Read the current domain before renaming."
        )
    _check_domain_name(database, project_id, payload.name, domain_id)
    if domain.name != payload.name:
        domain.name = payload.name
        domain.version += 1
        domain.updated_at = datetime.now(UTC)
        database.flush()
    return domain_read(database, domain)


def list_domains(
    database: Session,
    project_id: UUID,
    filters: KnowledgePagination,
) -> KnowledgePage[KnowledgeDomainRead]:
    require_project(database, project_id)
    statement = select(KnowledgeDomain).where(KnowledgeDomain.project_id == project_id)
    total = database.scalar(select(func.count()).select_from(statement.subquery())) or 0
    domains = database.scalars(
        statement.order_by(func.lower(KnowledgeDomain.name), KnowledgeDomain.id)
        .offset(filters.offset)
        .limit(filters.limit)
    ).all()
    return KnowledgePage(
        items=[domain_read(database, domain) for domain in domains],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


def require_document(
    database: Session,
    project_id: UUID,
    reference: str,
) -> tuple[DecisionDocument, KnowledgeDomain, int | None]:
    match = re.fullmatch(r"([A-Z]{1,5})-([0-9]{5,19})(?:-([0-9]{1,10}))?", reference)
    if match is None or int(match[2]) > 9_223_372_036_854_775_807:
        raise ApplicationError(
            422, "decision_reference_invalid", "Use a decision ID or revision reference."
        )
    row = database.execute(
        select(DecisionDocument, KnowledgeDomain)
        .join(
            KnowledgeDomain,
            KnowledgeDomain.id == DecisionDocument.domain_id,
        )
        .where(
            DecisionDocument.project_id == project_id,
            KnowledgeDomain.prefix == match[1],
            DecisionDocument.ordinal == int(match[2]),
        )
    ).first()
    if row is None:
        raise not_found("decision_not_found", "Decision document not found in this project.")
    document, domain = row
    if f"{int(match[2]):05d}" != match[2]:
        raise ApplicationError(422, "decision_reference_invalid", "Use the canonical decision ID.")
    return document, domain, None if match[3] is None else int(match[3])


def require_revision(
    database: Session,
    document: DecisionDocument,
    revision: int | None = None,
) -> DecisionRevision:
    number = document.current_revision if revision is None else revision
    row = database.scalar(
        select(DecisionRevision).where(
            DecisionRevision.document_id == document.id,
            DecisionRevision.revision == number,
        )
    )
    if row is None:
        raise not_found("decision_revision_not_found", "Decision revision not found.")
    return row


def _outgoing(database: Session, revision: DecisionRevision) -> dict[str, list[str]]:
    rows = database.execute(
        select(DecisionRelationship.kind, DecisionDocument, KnowledgeDomain)
        .join(DecisionDocument, DecisionDocument.id == DecisionRelationship.target_document_id)
        .join(KnowledgeDomain, KnowledgeDomain.id == DecisionDocument.domain_id)
        .where(DecisionRelationship.revision_id == revision.id)
    ).all()
    result: dict[str, list[str]] = {"related": [], "supersedes": []}
    for kind, target, domain in rows:
        result[kind].append(decision_identifier(domain, target))
    return {kind: sorted(values) for kind, values in result.items()}


def _incoming(database: Session, document: DecisionDocument) -> dict[str, list[str]]:
    rows = database.execute(
        select(DecisionRelationship.kind, DecisionRevision, DecisionDocument, KnowledgeDomain)
        .join(DecisionRevision, DecisionRevision.id == DecisionRelationship.revision_id)
        .join(DecisionDocument, DecisionDocument.id == DecisionRevision.document_id)
        .join(KnowledgeDomain, KnowledgeDomain.id == DecisionDocument.domain_id)
        .where(
            DecisionRelationship.target_document_id == document.id,
            DecisionRevision.revision == DecisionDocument.current_revision,
        )
    ).all()
    result: dict[str, list[str]] = {"related": [], "supersedes": []}
    for kind, revision, source, domain in rows:
        result[kind].append(f"{decision_identifier(domain, source)}-{revision.revision}")
    return {kind: sorted(values) for kind, values in result.items()}


def decision_read(
    database: Session,
    document: DecisionDocument,
    revision: DecisionRevision,
    domain: KnowledgeDomain | None = None,
) -> DecisionRead:
    domain = domain or require_domain(database, document.project_id, document.domain_id)
    outgoing, incoming = _outgoing(database, revision), _incoming(database, document)
    work_ids = database.scalars(
        select(DecisionWorkLink.work_item_id)
        .where(
            DecisionWorkLink.revision_id == revision.id,
        )
        .order_by(DecisionWorkLink.work_item_id)
    ).all()
    artifact_ids = database.scalars(
        select(DecisionArtifactLink.artifact_id)
        .where(
            DecisionArtifactLink.revision_id == revision.id,
        )
        .order_by(DecisionArtifactLink.artifact_id)
    ).all()
    scope_count = (
        database.scalar(
            select(func.count())
            .select_from(DecisionScope)
            .where(
                DecisionScope.document_id == document.id,
                DecisionScope.revision == revision.revision,
            )
        )
        or 0
    )
    from mnemonic_api.services.product_decisions import decision_gate_read

    return DecisionRead.model_validate(
        {
            "human_gate": decision_gate_read(database, document.id),
            "id": document.id,
            "project_id": document.project_id,
            "domain_id": domain.id,
            "domain_name": domain.name,
            "prefix": domain.prefix,
            "ordinal": document.ordinal,
            "decision_id": decision_identifier(domain, document),
            "reference": f"{decision_identifier(domain, document)}-{revision.revision}",
            "revision": revision.revision,
            "title": revision.title,
            "state": revision.state,
            "media_type": revision.media_type,
            "size_bytes": revision.size_bytes,
            "sha256": revision.sha256,
            "created_at": document.created_at,
            "updated_at": revision.created_at,
            "author_kind": revision.author_kind,
            "agent_session_id": revision.agent_session_id,
            "model_id": revision.model_id,
            "related_decision_ids": outgoing["related"],
            "referenced_by": incoming["related"],
            "supersedes": outgoing["supersedes"],
            "superseded_by": incoming["supersedes"],
            "related_work_item_ids": work_ids,
            "related_artifact_ids": artifact_ids,
            "scope_count": scope_count,
            **pinned_links(database, revision),
        }
    )


def list_decisions(
    database: Session,
    project_id: UUID,
    filters: DecisionListQuery,
) -> KnowledgePage[DecisionRead]:
    require_project(database, project_id)
    statement = (
        select(DecisionDocument, DecisionRevision, KnowledgeDomain)
        .join(
            DecisionRevision,
            (DecisionRevision.document_id == DecisionDocument.id)
            & (DecisionRevision.revision == DecisionDocument.current_revision),
        )
        .join(KnowledgeDomain, KnowledgeDomain.id == DecisionDocument.domain_id)
        .where(
            DecisionDocument.project_id == project_id,
        )
    )
    if filters.domain_id is not None:
        require_domain(database, project_id, filters.domain_id)
        statement = statement.where(DecisionDocument.domain_id == filters.domain_id)
    if filters.state is not None:
        statement = statement.where(DecisionRevision.state == filters.state)
    if filters.q.strip():
        needle = filters.q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(
            or_(
                DecisionRevision.title.ilike(f"%{needle}%", escape="\\"),
                DecisionRevision.content.ilike(f"%{needle}%", escape="\\"),
            )
        )
    total = database.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = database.execute(
        statement.order_by(DecisionDocument.updated_at.desc(), DecisionDocument.id)
        .offset(filters.offset)
        .limit(filters.limit)
    ).all()
    return KnowledgePage(
        items=[decision_read(database, doc, rev, domain) for doc, rev, domain in rows],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


def read_decision_text(
    database: Session,
    project_id: UUID,
    reference: str,
    filters: DecisionTextQuery,
) -> DecisionTextRead:
    document, domain, pinned = require_document(database, project_id, reference)
    if pinned is not None and filters.revision is not None and pinned != filters.revision:
        raise ApplicationError(422, "decision_revision_mismatch", "Revision assertions must agree.")
    revision = require_revision(
        database, document, pinned if pinned is not None else filters.revision
    )
    lines = revision.content.splitlines(keepends=True)
    start = filters.line_start or 1
    end = min(filters.line_end or len(lines), len(lines))
    if start > max(len(lines), 1):
        raise ApplicationError(
            416, "decision_line_range_invalid", "Line range starts beyond the document."
        )
    return DecisionTextRead(
        **decision_read(database, document, revision, domain).model_dump(),
        content="".join(lines[start - 1 : end]),
        total_lines=len(lines),
        line_start=start,
        line_end=end,
    )


def decision_history(
    database: Session,
    project_id: UUID,
    reference: str,
    filters: KnowledgePagination,
) -> KnowledgePage[DecisionRead]:
    document, domain, _ = require_document(database, project_id, reference)
    rows = database.scalars(
        select(DecisionRevision)
        .where(DecisionRevision.document_id == document.id)
        .order_by(DecisionRevision.revision.desc())
        .offset(filters.offset)
        .limit(filters.limit)
    ).all()
    return KnowledgePage(
        items=[decision_read(database, document, row, domain) for row in rows],
        total=document.current_revision + 1,
        limit=filters.limit,
        offset=filters.offset,
    )


def _metadata_matches(
    database: Session,
    document: DecisionDocument,
    revision: DecisionRevision,
    payload: DecisionPutRequest,
) -> bool:
    current = decision_read(database, document, revision)
    return (
        (revision.title, revision.state, revision.media_type)
        == (payload.title, payload.state, payload.media_type)
        and set(current.related_decision_ids) == set(payload.related_decision_ids)
        and set(current.related_work_item_ids) == set(payload.related_work_item_ids)
        and set(current.related_artifact_ids) == set(payload.related_artifact_ids)
        and set(current.supersedes) == set(superseded_ids(payload))
        and same_annotations(current, payload)
    )


def _new_document(
    database: Session,
    project_id: UUID,
    domain: KnowledgeDomain,
) -> DecisionDocument:
    if domain.next_ordinal >= 9_223_372_036_854_775_807:
        raise conflict("knowledge_domain_exhausted", "Domain decision ordinals are exhausted.")
    document = DecisionDocument(
        project_id=project_id, domain_id=domain.id, ordinal=domain.next_ordinal, current_revision=0
    )
    domain.next_ordinal += 1
    database.add(document)
    database.flush()
    return document


def _put_target(
    database: Session,
    project_id: UUID,
    domain: KnowledgeDomain,
    payload: DecisionPutRequest,
    digest: str,
) -> tuple[DecisionDocument, DecisionRevision | None]:
    if payload.decision_id is None:
        previous = database.execute(
            select(DecisionDocument, DecisionRevision)
            .join(
                DecisionRevision,
                DecisionRevision.document_id == DecisionDocument.id,
            )
            .where(DecisionDocument.domain_id == domain.id, DecisionRevision.sha256 == digest)
            .order_by(DecisionRevision.created_at, DecisionRevision.id)
            .limit(1)
        ).first()
        if previous is not None:
            from mnemonic_api.services.product_decisions import require_unmanaged_document
            require_unmanaged_document(database, previous[0])
            return previous[0], previous[1]
        return _new_document(database, project_id, domain), None
    document, _, _ = require_document(database, project_id, payload.decision_id)
    from mnemonic_api.services.product_decisions import require_unmanaged_document
    require_unmanaged_document(database, document)
    if document.domain_id != domain.id:
        raise ApplicationError(422, "decision_domain_mismatch", "A decision cannot change domains.")
    current = require_revision(database, document)
    if current.sha256 == digest and _metadata_matches(database, document, current, payload):
        return document, current
    if document.current_revision != payload.expected_revision:
        raise conflict("decision_revision_conflict", "Read the current revision before updating.")
    document.current_revision += 1
    document.updated_at = datetime.now(UTC)
    database.flush()
    return document, None


def _validate_supersession(
    database: Session,
    document: DecisionDocument,
    targets: list[DecisionDocument],
) -> None:
    if any(target.domain_id != document.domain_id for target in targets):
        raise ApplicationError(
            422, "decision_supersession_domain", "Supersession requires the same domain."
        )
    adjacency: dict[UUID, set[UUID]] = {}
    rows = database.execute(
        select(DecisionRevision.document_id, DecisionRelationship.target_document_id)
        .join(DecisionRelationship, DecisionRelationship.revision_id == DecisionRevision.id)
        .join(DecisionDocument, DecisionDocument.id == DecisionRevision.document_id)
        .where(
            DecisionDocument.domain_id == document.domain_id,
            DecisionRevision.revision == DecisionDocument.current_revision,
            DecisionRelationship.kind == "supersedes",
        )
    ).all()
    for source, target in rows:
        adjacency.setdefault(source, set()).add(target)
    pending = [target.id for target in targets]
    seen: set[UUID] = set()
    while pending:
        current = pending.pop()
        if current == document.id:
            raise conflict("decision_supersession_cycle", "Supersession must not create a cycle.")
        if current not in seen:
            seen.add(current)
            pending.extend(adjacency.get(current, ()))


def _add_decision_links(
    database: Session,
    document: DecisionDocument,
    revision: DecisionRevision,
    payload: DecisionPutRequest,
) -> None:
    for kind, references in (
        ("related", payload.related_decision_ids),
        ("supersedes", superseded_ids(payload)),
    ):
        targets = [
            require_document(database, document.project_id, reference)[0]
            for reference in sorted(set(references))
        ]
        if any(target.id == document.id for target in targets):
            raise ApplicationError(
                422, "decision_self_reference", "A decision cannot reference itself."
            )
        if kind == "supersedes":
            _validate_supersession(database, document, targets)
        database.add_all(
            [
                DecisionRelationship(
                    project_id=document.project_id,
                    revision_id=revision.id,
                    target_document_id=target.id,
                    kind=kind,
                )
                for target in targets
            ]
        )


def _add_resource_links(
    database: Session,
    document: DecisionDocument,
    revision: DecisionRevision,
    payload: DecisionPutRequest,
) -> None:
    for work_id in set(payload.related_work_item_ids):
        require_work_item(database, document.project_id, work_id)
        database.add(
            DecisionWorkLink(
                project_id=document.project_id, revision_id=revision.id, work_item_id=work_id
            )
        )
    for artifact_id in set(payload.related_artifact_ids):
        artifact = database.scalar(
            select(Artifact.id).where(
                Artifact.id == artifact_id,
                Artifact.project_id == document.project_id,
                Artifact.deleted_at.is_(None),
            )
        )
        if artifact is None:
            raise not_found("artifact_not_found", "Artifact not found in this project.")
        database.add(
            DecisionArtifactLink(
                project_id=document.project_id, revision_id=revision.id, artifact_id=artifact_id
            )
        )


def put_decision(
    database: Session,
    project_id: UUID,
    payload: DecisionPutRequest,
) -> DecisionPutResult:
    domain = require_domain(database, project_id, payload.domain_id)
    content_bytes = payload.content.encode("utf-8")
    digest = hashlib.sha256(content_bytes).hexdigest()
    document, previous = _put_target(database, project_id, domain, payload, digest)
    if previous is not None:
        return DecisionPutResult(
            decision=decision_read(database, document, previous, domain), created=False
        )
    revision = DecisionRevision(
        project_id=project_id,
        document_id=document.id,
        revision=document.current_revision,
        title=payload.title,
        content=payload.content,
        media_type=payload.media_type,
        state=payload.state,
        size_bytes=len(content_bytes),
        sha256=digest,
        author_kind=payload.author_kind,
        agent_session_id=payload.agent_session_id,
        model_id=payload.model_id,
    )
    database.add(revision)
    database.flush()
    _add_decision_links(database, document, revision, payload)
    _add_resource_links(database, document, revision, payload)
    database.flush()
    add_pinned_links(database, document, revision, payload)
    database.flush()
    from mnemonic_api.services.decision_impact import create_assessments
    create_assessments(database, document, revision, payload)
    return DecisionPutResult(
        decision=decision_read(database, document, revision, domain), created=True
    )


def scope_read(
    scope: DecisionScope,
    document: DecisionDocument,
    domain: KnowledgeDomain,
) -> DecisionScopeRead:
    database = object_session(scope)
    merged = [] if database is None else list(database.scalars(
        select(DecisionCommitMapping.merged_commit_hash)
        .where(DecisionCommitMapping.scope_id == scope.id)
        .order_by(DecisionCommitMapping.merged_commit_hash)
    ))
    citations, truncated = ([], False) if database is None else completion_citations(
        database, scope.project_id, decision_identifier(domain, document), scope.revision,
        scope.repository, scope.commit_hash,
    )
    return DecisionScopeRead(
        id=scope.id,
        project_id=scope.project_id,
        document_id=document.id,
        decision_id=decision_identifier(domain, document),
        revision=scope.revision,
        commit_hash=scope.commit_hash,
        repository=scope.repository,
        completion_report_id=scope.completion_report_id,
        merged_commit_hashes=merged,
        completion_citations=citations,
        completion_citations_truncated=truncated,
        work_item_id=scope.work_item_id,
        agent_session_id=scope.agent_session_id,
        created_at=scope.created_at,
    )


def append_scope(
    database: Session,
    project_id: UUID,
    document: DecisionDocument,
    domain: KnowledgeDomain,
    payload: DecisionScopeRequest,
    *, completion_report_id: UUID | None = None,
) -> DecisionScopeResult:
    require_revision(database, document, payload.revision)
    if payload.work_item_id is not None:
        require_work_item(database, project_id, payload.work_item_id)
    existing = database.scalar(
        select(DecisionScope).where(
            DecisionScope.document_id == document.id,
            DecisionScope.revision == payload.revision,
            DecisionScope.commit_hash == payload.commit_hash,
            DecisionScope.work_item_id == payload.work_item_id,
            DecisionScope.agent_session_id == payload.agent_session_id,
            DecisionScope.repository == payload.repository,
            DecisionScope.completion_report_id == completion_report_id,
        )
    )
    if existing is not None:
        return DecisionScopeResult(scope=scope_read(existing, document, domain), created=False)
    row = DecisionScope(
        project_id=project_id,
        document_id=document.id,
        revision=payload.revision,
        commit_hash=payload.commit_hash,
        repository=payload.repository,
        completion_report_id=completion_report_id,
        work_item_id=payload.work_item_id,
        agent_session_id=payload.agent_session_id,
    )
    database.add(row)
    database.flush()
    return DecisionScopeResult(scope=scope_read(row, document, domain), created=True)


def list_scope(
    database: Session,
    project_id: UUID,
    reference: str,
    filters: KnowledgePagination,
    revision: int | None = None,
) -> KnowledgePage[DecisionScopeRead]:
    document, domain, pinned = require_document(database, project_id, reference)
    if pinned is not None and revision is not None and pinned != revision:
        raise ApplicationError(422, "decision_revision_mismatch", "Revision assertions must agree.")
    number = pinned if pinned is not None else revision
    statement = select(DecisionScope).where(DecisionScope.document_id == document.id)
    if number is not None:
        require_revision(database, document, number)
        statement = statement.where(DecisionScope.revision == number)
    total = database.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = database.scalars(
        statement.order_by(DecisionScope.created_at, DecisionScope.id)
        .offset(filters.offset)
        .limit(filters.limit)
    ).all()
    return KnowledgePage(
        items=[scope_read(row, document, domain) for row in rows],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


def require_no_knowledge_links(database: Session, work_item: WorkItem) -> None:
    """A project move cannot rewrite historical citations or strand same-project links."""
    linked = database.scalar(
        select(DecisionWorkLink.work_item_id)
        .where(
            DecisionWorkLink.work_item_id == work_item.id,
        )
        .limit(1)
    ) or database.scalar(
        select(DecisionScope.work_item_id)
        .where(
            DecisionScope.work_item_id == work_item.id,
        )
        .limit(1)
    ) or database.scalar(
        select(DecisionImpactDisposition.remediation_work_item_id)
        .where(
            DecisionImpactDisposition.remediation_work_item_id == work_item.id,
        )
        .limit(1)
    )
    linked = linked or database.scalar(
        select(JobCompletionReport.id)
        .where(
            JobCompletionReport.work_item_id == work_item.id,
            JobCompletionReport.decision_provenance["disposition"].astext == "cited",
        ).limit(1)
    )
    linked = linked or database.scalar(
        select(ProductDecisionRequest.gate_id)
        .join(WorkGate, WorkGate.id == ProductDecisionRequest.gate_id)
        .where(WorkGate.work_item_id == work_item.id).limit(1)
    )
    if linked is not None:
        raise conflict(
            "work_has_knowledge_links",
            "Work with historical decision links cannot change projects.",
        )
