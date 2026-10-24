"""Read causal candidates and retain explicit human/agent reassessment episodes."""

import difflib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError, conflict, not_found
from mnemonic_api.impact_schemas import (
    ImpactAssessmentDetail,
    ImpactAssessmentQuery,
    ImpactAssessmentRead,
    ImpactCandidate,
    ImpactCoverage,
    ImpactDiff,
    ImpactDispositionBatchRead,
    ImpactDispositionBatchRequest,
    ImpactDispositionRead,
    ImpactDispositionRequest,
    ImpactPageQuery,
    ImpactProjection,
    ImpactQuery,
    ImpactRead,
    is_code_impact,
)
from mnemonic_api.knowledge_schemas import DecisionPutRequest, KnowledgePage, KnowledgePagination
from mnemonic_api.models import (
    DecisionChange,
    DecisionChangeAssessment,
    DecisionDocument,
    DecisionImpactAssessment,
    DecisionImpactDisposition,
    DecisionRevision,
    DecisionRevisionChange,
    DecisionSupersessionReuse,
    KnowledgeDomain,
)
from mnemonic_api.review_scope_schemas import ExtendedDecisionReview
from mnemonic_api.services.assessment_progress import renew_assessment_progress
from mnemonic_api.services.decision_citation_excerpts import hydrate_citation_excerpts
from mnemonic_api.services.decision_dependencies import superseded_ids
from mnemonic_api.services.decision_impact_graph import (
    candidate,
    no_linked_commits_warning,
    reference,
    traverse,
)
from mnemonic_api.services.decision_scope_notices import extended_review, pending_scope_notice
from mnemonic_api.services.decision_supersession_reuse import reusable_supersession, same_evidence
from mnemonic_api.services.impact_read_projections import (
    author_read,
    candidate_read,
    diff_read,
    disposition_projection,
    projection,
)
from mnemonic_api.services.revision_authors import revision_author_context
from mnemonic_api.services.work_items import require_project, require_work_item


def read_impact(
    database: Session, project_id: UUID, identifier: str, filters: ImpactQuery
) -> ImpactRead:
    from mnemonic_api.services import knowledge

    document, domain, pinned = knowledge.require_document(database, project_id, identifier)
    if pinned is not None and filters.revision is not None and pinned != filters.revision:
        raise ApplicationError(422, "decision_revision_mismatch", "Revision assertions must agree.")
    revision = knowledge.require_revision(
        database, document, pinned if pinned is not None else filters.revision
    )
    nodes, coverage = traverse(database, document, revision, domain, filters.max_depth)
    candidate_count = sum(
        is_code_impact(node.relationship, node.related_direction) for node in nodes
    )
    context_count = len(nodes) - candidate_count
    nodes = [node for node in nodes
             if is_code_impact(node.relationship, node.related_direction)
             == (filters.kind == "code-impact")]
    items = [
        candidate_read(candidate(database, node), filters)
        for node in nodes[filters.offset : filters.offset + filters.limit]
    ]
    if filters.include_citation_excerpts:
        items = hydrate_citation_excerpts(database, project_id, items)
    end = filters.offset + len(items)
    return ImpactRead(
        project_id=project_id,
        origin_reference=reference(document, revision, domain),
        origin_author=author_read(revision_author_context(database, revision), filters),
        items=items,
        projection=projection(filters),
        kind=filters.kind,
        candidate_count=candidate_count,
        context_count=context_count,
        total=len(nodes),
        limit=filters.limit,
        offset=filters.offset,
        next_offset=end if end < len(nodes) else None,
        coverage=coverage,
    )


def _diff(database: Session, before: DecisionRevision, after: DecisionRevision) -> ImpactDiff:
    from mnemonic_api.services import knowledge

    document_before = database.get(DecisionDocument, before.document_id)
    document_after = database.get(DecisionDocument, after.document_id)
    assert document_before is not None and document_after is not None
    old = knowledge.decision_read(database, document_before, before)
    new = knowledge.decision_read(database, document_after, after)
    fields = [
        name
        for name in (
            "title",
            "state",
            "media_type",
            "sha256",
            "author_kind",
            "agent_session_id",
            "model_id",
            "related_decision_ids",
            "related_work_item_ids",
            "related_artifact_ids",
            "supersedes",
            "depends_on",
            "supersession_details",
        )
        if getattr(old, name) != getattr(new, name)
    ]
    # Bound both diff computation and persisted prose independently of source size.
    old_text, new_text = before.content[:32768], after.content[:32768]
    lines = difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile=old.reference,
        tofile=new.reference,
        lineterm="",
    )
    chunks, size, truncated = [], 0, len(before.content) > 32768 or len(after.content) > 32768
    for line in lines:
        size += len(line.encode("utf-8")) + 1
        if size > 16384:
            truncated = True
            break
        chunks.append(line)
    return ImpactDiff(
        changed_fields=fields,
        before_sha256=before.sha256,
        after_sha256=after.sha256,
        unified_diff="\n".join(chunks),
        truncated=truncated,
    )


def _episode(
    database: Session,
    before: DecisionRevision,
    after: DecisionRevision,
    trigger: str,
    extent: str | None = None,
    reason: str | None = None,
) -> DecisionImpactAssessment:
    from mnemonic_api.services import knowledge

    document = database.get(DecisionDocument, before.document_id)
    assert document is not None
    domain = knowledge.require_domain(database, before.project_id, document.domain_id)
    nodes, coverage = traverse(database, document, before, domain, 8)
    created_at = datetime.now(UTC)
    coverage.snapshot_at = created_at
    coverage.notes.append(
        "This immutable snapshot excludes later evidence; rerun live impact to refresh."
    )
    assessment = DecisionImpactAssessment(
        project_id=before.project_id,
        origin_revision_id=before.id,
        replacement_revision_id=after.id,
        trigger=trigger,
        extent=extent,
        reason=reason,
        diff=_diff(database, before, after).model_dump(mode="json"),
        candidates=[candidate(database, node).model_dump(mode="json") for node in nodes],
        coverage=coverage.model_dump(mode="json"),
        created_at=created_at,
    )
    return assessment


def create_assessments(
    database: Session,
    document: DecisionDocument,
    revision: DecisionRevision,
    payload: DecisionPutRequest,
    *, hold_closed: bool = False, admitted_change: DecisionChange | None = None,
) -> ExtendedDecisionReview | None:
    from mnemonic_api.services import knowledge
    from mnemonic_api.services.decision_change_classification import classify_revision
    from mnemonic_api.services.decision_changes import attach_assessments, require_open

    change, governing = (admitted_change, None) if admitted_change is not None else (
        classify_revision(database, revision, payload, hold_closed=hold_closed)
    )
    classification = database.get(DecisionRevisionChange, revision.id)
    if (admitted_change is None and classification is not None
            and classification.requires_admission):
        return
    if payload.change is not None and payload.change.kind == "maintenance":
        return
    assert change is not None
    if governing is None:
        require_open(database, change)
    replacement = governing or revision

    episodes = []
    if revision.revision:
        before = knowledge.require_revision(database, document, revision.revision - 1)
        episodes.append(_episode(database, before, revision, "revision"))
    details = {item.decision_id: item for item in payload.supersession_details}
    for identifier in superseded_ids(payload):
        target, _, _ = knowledge.require_document(database, document.project_id, identifier)
        detail = details.get(identifier)
        before = knowledge.require_revision(
            database, target, None if detail is None else detail.revision
        )
        # A governing revision can reuse its original supersession obligation only
        # when it actually declared that target. Revision transitions remain exact.
        after = replacement
        if governing is not None:
            governing_document = database.get(DecisionDocument, governing.document_id)
            assert governing_document is not None
            if identifier not in knowledge.decision_read(
                database, governing_document, governing,
            ).supersedes:
                after = revision
        episodes.append(_episode(
            database,
            before,
            after,
            "supersession",
            None if detail is None else detail.extent,
            None if detail is None else detail.reason,
        ))
    episodes = _reuse_identical_assessments(database, change.id, episodes, revision)
    database.flush()
    notice = attach_assessments(database, change, episodes, causing_revision=revision)
    from mnemonic_api.services.decision_reviews import create_decision_review
    create_decision_review(database, document, revision, episodes, change=change)
    return extended_review(database, notice) if notice is not None else None


def _reuse_identical_assessments(
    database: Session, change_id: UUID, episodes: list[DecisionImpactAssessment],
    current_revision: DecisionRevision,
) -> list[DecisionImpactAssessment]:
    """Reuse only the exact transition and evidence; never transplant verdicts."""
    with database.no_autoflush:
        retained = list(database.scalars(select(DecisionImpactAssessment).join(
            DecisionChangeAssessment,
            DecisionChangeAssessment.assessment_id == DecisionImpactAssessment.id,
        ).where(DecisionChangeAssessment.change_id == change_id)))
    result = []
    for episode in episodes:
        previous = (
            reusable_supersession(database, current_revision, episode)
            if episode.trigger == "supersession" and database.get(
                DecisionSupersessionReuse, (current_revision.id, episode.origin_revision_id),
            ) is not None
            else next((old for old in retained if _same_obligation(old, episode)), None)
        )
        if previous is not None:
            result.append(previous)
        else:
            if episode.replacement_revision_id != current_revision.id:
                # The governing revision is sealed. Changed evidence or intent
                # needs a new truthful snapshot against this annotation revision.
                before = database.get(DecisionRevision, episode.origin_revision_id)
                assert before is not None
                episode = _episode(database, before, current_revision, episode.trigger,
                                   episode.extent, episode.reason)
            database.add(episode)
            result.append(episode)
    return result


def _same_obligation(left: DecisionImpactAssessment, right: DecisionImpactAssessment) -> bool:
    fields = ("origin_revision_id", "replacement_revision_id", "trigger", "extent", "reason",
              "diff")
    return all(getattr(left, key) == getattr(right, key) for key in fields) and same_evidence(
        left, right,
    )


def require_assessment(
    database: Session, project_id: UUID, assessment_id: UUID, *, lock: bool = False,
) -> DecisionImpactAssessment:
    query = select(DecisionImpactAssessment).where(
        DecisionImpactAssessment.id == assessment_id,
        DecisionImpactAssessment.project_id == project_id,
    )
    row = database.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise not_found("decision_assessment_not_found", "Decision impact assessment not found.")
    return row


def _revision_reference(database: Session, revision_id: UUID) -> str:
    row = database.execute(
        select(DecisionDocument, DecisionRevision, KnowledgeDomain)
        .join(DecisionRevision, DecisionRevision.document_id == DecisionDocument.id)
        .join(KnowledgeDomain, KnowledgeDomain.id == DecisionDocument.domain_id)
        .where(DecisionRevision.id == revision_id)
    ).one()
    return reference(*row)


def _version(database: Session, assessment_id: UUID) -> int:
    return (
        database.scalar(
            select(func.max(DecisionImpactDisposition.version)).where(
                DecisionImpactDisposition.assessment_id == assessment_id
            )
        )
        or 0
    )


def assessment_read(
    database: Session, row: DecisionImpactAssessment, options: ImpactProjection | None = None,
) -> ImpactAssessmentRead:
    options = options or ImpactProjection()
    before = database.get(DecisionRevision, row.origin_revision_id)
    after = database.get(DecisionRevision, row.replacement_revision_id)
    assert before is not None and after is not None
    latest = database.scalars(
        select(DecisionImpactDisposition)
        .where(DecisionImpactDisposition.assessment_id == row.id)
        .distinct(DecisionImpactDisposition.candidate_reference)
        .order_by(
            DecisionImpactDisposition.candidate_reference, DecisionImpactDisposition.version.desc()
        )
    ).all()
    required = {item["reference"] for item in row.candidates
                if is_code_impact(item["relationship"], item.get("related_direction"))}
    unassessed = len(required - {item.candidate_reference for item in latest})
    changes = sum(item.disposition == "change-required" for item in latest)
    return ImpactAssessmentRead.model_validate(
        {
            "id": row.id,
            "project_id": row.project_id,
            "trigger": row.trigger,
            "before_reference": _revision_reference(database, row.origin_revision_id),
            "after_reference": _revision_reference(database, row.replacement_revision_id),
            "before_author": author_read(revision_author_context(database, before), options),
            "after_author": author_read(revision_author_context(database, after), options),
            "extent": row.extent,
            "reason": row.reason,
            "diff": diff_read(row.diff, options),
            "projection": projection(options),
            "candidate_count": len(required),
            "context_count": len(row.candidates) - len(required),
            "coverage": _assessment_coverage(row),
            "created_at": row.created_at,
            "version": _version(database, row.id),
            "unassessed_count": unassessed,
            "change_required_count": changes,
            "status": "change-required" if changes else "pending" if unassessed else "resolved",
        }
    )


def _assessment_coverage(row: DecisionImpactAssessment) -> ImpactCoverage:
    coverage = ImpactCoverage.model_validate(row.coverage)
    if "warnings" not in row.coverage:
        origin = next((item for item in row.candidates if item["relationship"] == "origin"), None)
        if origin is not None and not origin["scopes"] and not origin["evidence_truncated"]:
            coverage.warnings.append(no_linked_commits_warning(origin["reference"]))
    return coverage


def list_assessments(
    database: Session, project_id: UUID, filters: ImpactAssessmentQuery
) -> KnowledgePage[ImpactAssessmentRead]:
    from mnemonic_api.services import knowledge

    require_project(database, project_id)
    query = select(DecisionImpactAssessment).where(
        DecisionImpactAssessment.project_id == project_id
    )
    if filters.change_id is not None:
        from mnemonic_api.services.decision_changes import require_change

        require_change(database, project_id, filters.change_id)
        query = query.join(DecisionChangeAssessment).where(
            DecisionChangeAssessment.change_id == filters.change_id,
        )
    if filters.decision_id is not None:
        document, _, _ = knowledge.require_document(database, project_id, filters.decision_id)
        revisions = select(DecisionRevision.id).where(DecisionRevision.document_id == document.id)
        query = query.where(
            or_(
                DecisionImpactAssessment.origin_revision_id.in_(revisions),
                DecisionImpactAssessment.replacement_revision_id.in_(revisions),
                DecisionImpactAssessment.candidates.contains(
                    [{"decision_id": filters.decision_id}]
                ),
            )
        )
    total = database.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = database.scalars(
        query.order_by(DecisionImpactAssessment.created_at.desc(), DecisionImpactAssessment.id)
        .offset(filters.offset)
        .limit(filters.limit)
    ).all()
    return KnowledgePage(
        items=[assessment_read(database, row, filters) for row in rows],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


def disposition_read(row: DecisionImpactDisposition) -> ImpactDispositionRead:
    return ImpactDispositionRead.model_validate(row, from_attributes=True)


def assessment_detail(
    database: Session, project_id: UUID, assessment_id: UUID, filters: ImpactPageQuery
) -> ImpactAssessmentDetail:
    row = require_assessment(database, project_id, assessment_id)
    value = assessment_read(database, row, filters)
    selected = [item for item in row.candidates
                if is_code_impact(item["relationship"], item.get("related_direction"))
                == (filters.kind == "code-impact")]
    candidates = [
        candidate_read(ImpactCandidate.model_validate(item), filters)
        for item in selected[filters.offset : filters.offset + filters.limit]
    ]
    if filters.include_citation_excerpts:
        candidates = hydrate_citation_excerpts(database, project_id, candidates)
    latest = database.scalars(
        select(DecisionImpactDisposition)
        .where(
            DecisionImpactDisposition.assessment_id == row.id,
            DecisionImpactDisposition.candidate_reference.in_(
                [item.reference for item in candidates]
            ),
        )
        .distinct(DecisionImpactDisposition.candidate_reference)
        .order_by(
            DecisionImpactDisposition.candidate_reference, DecisionImpactDisposition.version.desc()
        )
    ).all()
    end = filters.offset + len(candidates)
    return ImpactAssessmentDetail(
        **value.model_dump(),
        kind=filters.kind,
        total=len(selected),
        candidates=candidates,
        dispositions=[disposition_projection(disposition_read(item), filters) for item in latest],
        disposition_history_total=value.version,
        limit=filters.limit,
        offset=filters.offset,
        next_offset=end if end < len(selected) else None,
    )


def disposition_history(
    database: Session, project_id: UUID, assessment_id: UUID, filters: KnowledgePagination
) -> KnowledgePage[ImpactDispositionRead]:
    require_assessment(database, project_id, assessment_id)
    rows = database.scalars(
        select(DecisionImpactDisposition)
        .where(DecisionImpactDisposition.assessment_id == assessment_id)
        .order_by(DecisionImpactDisposition.version.desc())
        .offset(filters.offset)
        .limit(filters.limit)
    ).all()
    return KnowledgePage(
        items=[disposition_read(row) for row in rows],
        total=_version(database, assessment_id),
        limit=filters.limit,
        offset=filters.offset,
    )


def append_disposition(
    database: Session, project_id: UUID, assessment_id: UUID, payload: ImpactDispositionRequest
) -> ImpactDispositionRead:
    work_id = renew_assessment_progress(database, assessment_id, payload.lease_token)
    result = _append_disposition(database, project_id, assessment_id, payload)
    if work_id is not None:
        result.review_scope_changed = pending_scope_notice(database, work_id)
    return result


def append_disposition_batch(
    database: Session, project_id: UUID, assessment_id: UUID,
    payload: ImpactDispositionBatchRequest,
) -> ImpactDispositionBatchRead:
    work_id = renew_assessment_progress(database, assessment_id, payload.lease_token)
    items = [_append_disposition(database, project_id, assessment_id, ImpactDispositionRequest(
        **item.model_dump(), actor=payload.actor,
        expected_version=payload.expected_version + offset,
    )) for offset, item in enumerate(payload.items)]
    return ImpactDispositionBatchRead(
        project_id=project_id, assessment_id=assessment_id,
        version=payload.expected_version + len(items), items=items,
        review_scope_changed=(pending_scope_notice(database, work_id)
                              if work_id is not None else None),
    )


def _append_disposition(
    database: Session, project_id: UUID, assessment_id: UUID, payload: ImpactDispositionRequest,
) -> ImpactDispositionRead:
    assessment = require_assessment(database, project_id, assessment_id, lock=True)
    if payload.expected_version != _version(database, assessment_id):
        raise conflict(
            "decision_assessment_version_conflict", "Read the assessment before reassessing."
        )
    if payload.candidate_reference not in {item["reference"] for item in assessment.candidates}:
        raise ApplicationError(
            422,
            "decision_assessment_candidate_invalid",
            "Reassess an exact candidate from this immutable episode.",
        )
    if payload.remediation_work_item_id is not None:
        require_work_item(database, project_id, payload.remediation_work_item_id)
    row = DecisionImpactDisposition(
        project_id=project_id,
        assessment_id=assessment_id,
        version=payload.expected_version + 1,
        candidate_reference=payload.candidate_reference,
        disposition=payload.disposition,
        reason=payload.reason,
        remediation_work_item_id=payload.remediation_work_item_id,
        actor=payload.actor.model_dump(mode="json"),
    )
    database.add(row)
    database.flush()
    return disposition_read(row)
