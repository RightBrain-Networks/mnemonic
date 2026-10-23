"""Turn committed decision revisions into ordinary, investigative work atomically."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import conflict
from mnemonic_api.impact_schemas import is_code_impact
from mnemonic_api.models import (
    DecisionDocument,
    DecisionImpactAssessment,
    DecisionImpactDisposition,
    DecisionRevision,
    ProjectSettings,
    WorkItem,
)
from mnemonic_api.schemas import InitialCheckpointCreate, WorkItemCreate
from mnemonic_api.services import knowledge
from mnemonic_api.services.decision_impact_graph import reference
from mnemonic_api.services.prompts import render_prompt, storage_for
from mnemonic_api.services.revision_authors import revision_author_note
from mnemonic_api.services.work_items import create_work_records
from mnemonic_api.summary_limits import DEFAULT_WORK_SUMMARY_MAX_CHARS


def _scope(
    database: Session, episodes: list[DecisionImpactAssessment], replacement: str,
) -> str:
    lines = ["## Pinned decision impact review scope", "",
             f"Source project: `{episodes[0].project_id}`.",
             f"Triggering decision revision: `{replacement}`.", "",
             "Review these exact changes and their downstream implementation evidence:"]
    for episode in episodes:
        before = database.get(DecisionRevision, episode.origin_revision_id)
        assert before is not None
        document = database.get(DecisionDocument, before.document_id)
        assert document is not None
        domain = knowledge.require_domain(database, before.project_id, document.domain_id)
        origin = reference(document, before, domain)
        lines.append(
            f"- `{origin}` → `{replacement}` ({episode.trigger}); "
            f"impact assessment `{episode.id}`."
        )
    lines.extend([
        "",
        "Use decision_get for the pinned revisions, decision_assessment_get to retrieve the "
        "listed immutable assessment episodes, and decision_impact on each old reference "
        "to refresh downstream evidence. Page all results and report incomplete coverage. "
        "A later revision does not replace this task's pinned scope; inspect it separately "
        "when assessing whether remediation is still necessary.",
        "Use the source project for decision and assessment reads and for remediation work "
        "linked in those assessments. If this work has moved, use its current project for "
        "claims, checkpoints, and closeout; discovered-from still cites this exact review work.",
    ])
    return "\n".join(lines)


def _prompt(
    database: Session, project_id: UUID, work_id: UUID, title: str, summary: str,
    priority: int, template: str, scope: str,
) -> str:
    return render_prompt(
        database, project_id, "decision-impact-review", template=template.rstrip() + "\n\n" + scope,
        extra={
            "$WORK_ITEM_ID": str(work_id), "$WORK_ITEM_TITLE": title,
            "$WORK_ITEM_SUMMARY": summary, "$WORK_ITEM_STATUS": "pending",
            "$WORK_ITEM_PRIORITY": str(priority), "$WORK_ITEM_VERSION": "1",
        },
    )


def create_decision_review(
    database: Session, document: DecisionDocument, revision: DecisionRevision,
    episodes: list[DecisionImpactAssessment],
) -> None:
    """The revision write/receipt transaction owns both the work and its unique trigger."""
    settings = database.get(ProjectSettings, document.project_id)
    if not episodes or settings is None or not settings.automatic_decision_reviews:
        return
    domain = knowledge.require_domain(database, document.project_id, document.domain_id)
    replacement = reference(document, revision, domain)
    work_id = uuid4()
    title = f"Decision impact review: {replacement} — {revision.title}"[:200]
    maximum = database.info.get("work_summary_max_chars", DEFAULT_WORK_SUMMARY_MAX_CHARS)
    summary = (
        f"Investigate downstream code affected by {replacement}. Compare pinned decision "
        "revisions and implementation evidence; create separate work for necessary "
        "remediation or further investigation. Do not change code in this task."
    )[:maximum]
    template = storage_for(database).read(document.project_id, "decision-impact-review")
    prompt = _prompt(
        database, document.project_id, work_id, title, summary, settings.decision_review_priority,
        template.content, _scope(database, episodes, replacement)
        + "\n\n" + revision_author_note(database, revision),
    )
    create_work_records(
        database, document.project_id,
        WorkItemCreate(
            title=title, summary=summary, priority=settings.decision_review_priority,
            discovered_from_work_item_id=None,
            initial_checkpoint=InitialCheckpointCreate(
                prompt=prompt, source_client="mnemonic",
                source_session_id=f"decision-revision:{revision.id}",
                tags=["decision-impact-review"],
                source_metadata={
                    "automatic_decision_review": True,
                    "decision_project_id": str(document.project_id),
                    "decision_revision_id": str(revision.id),
                    "decision_reference": replacement,
                    "assessment_ids": [str(episode.id) for episode in episodes],
                    "prompt_id": "decision-impact-review", "prompt_revision": template.revision,
                },
            ),
        ),
        decision_review_revision_id=revision.id, work_item_id=work_id,
    )


def require_decision_impact_review_complete(database: Session, work: WorkItem) -> None:
    """Done means the retained assessment ledger was investigated, with tracked follow-ups."""
    if work.decision_review_revision_id is None:
        return
    episodes = database.scalars(select(DecisionImpactAssessment).where(
        DecisionImpactAssessment.replacement_revision_id == work.decision_review_revision_id,
    ).order_by(DecisionImpactAssessment.id).with_for_update()).all()
    candidates = {(episode.id, candidate["reference"])
                  for episode in episodes for candidate in episode.candidates
                  if is_code_impact(candidate["relationship"], candidate.get("related_direction"))}
    latest = database.scalars(select(DecisionImpactDisposition).where(
        DecisionImpactDisposition.assessment_id.in_([episode.id for episode in episodes]),
    ).distinct(
        DecisionImpactDisposition.assessment_id, DecisionImpactDisposition.candidate_reference,
    ).order_by(
        DecisionImpactDisposition.assessment_id, DecisionImpactDisposition.candidate_reference,
        DecisionImpactDisposition.version.desc(),
    )).all()
    reviewed = {(row.assessment_id, row.candidate_reference): row for row in latest}
    missing = len(candidates - reviewed.keys())
    remediation_ids = {row.remediation_work_item_id for row in latest
                       if row.disposition == "change-required"}
    # Moved reviews hold another project's mutation lock. FOR SHARE also fences
    # non-key updates such as source-project soft deletion until closeout commits.
    tracked = set(database.scalars(select(WorkItem.id).where(
        WorkItem.id.in_(remediation_ids - {None, work.id}), WorkItem.deleted_at.is_(None),
    ).order_by(WorkItem.id).with_for_update(read=True)))
    untracked = sum(row.disposition == "change-required"
                    and row.remediation_work_item_id not in tracked for row in latest)
    if not candidates or missing or untracked:
        raise conflict(
            "decision_impact_review_incomplete",
            "Complete the decision impact assessment ledger before marking this review Done: "
            "assess every pinned code-impact candidate and link separate remediation work for "
            "change-required findings.",
            context={"missing_dispositions": missing, "untracked_changes": untracked},
        )
