"""Identify the same continuously declared supersession without moving its history."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from mnemonic_api.models import (
    DecisionImpactAssessment,
    DecisionRelationship,
    DecisionRevision,
    DecisionSupersessionDetail,
    DecisionSupersessionReuse,
)


def same_evidence(left: DecisionImpactAssessment, right: DecisionImpactAssessment) -> bool:
    return left.candidates == right.candidates and (
        {key: value for key, value in left.coverage.items() if key != "snapshot_at"}
        == {key: value for key, value in right.coverage.items() if key != "snapshot_at"}
    )


def continuous_declaration(
    database: Session, revision: DecisionRevision, origin: DecisionRevision,
    first_revision: int, episode: DecisionImpactAssessment,
) -> bool:
    declared = select(DecisionRelationship.revision_id).where(
        DecisionRelationship.revision_id == DecisionRevision.id,
        DecisionRelationship.target_document_id == origin.document_id,
        DecisionRelationship.kind == "supersedes",
    ).exists()
    target = aliased(DecisionRevision)
    details = select(DecisionSupersessionDetail.revision_id).join(
        target, target.id == DecisionSupersessionDetail.target_revision_id,
    ).where(
        DecisionSupersessionDetail.revision_id == DecisionRevision.id,
        target.document_id == origin.document_id,
    )
    matching = ~details.exists() if episode.extent is None else details.where(
        DecisionSupersessionDetail.target_revision_id == origin.id,
        DecisionSupersessionDetail.extent == episode.extent,
        DecisionSupersessionDetail.reason == episode.reason,
    ).exists()
    interrupted = select(DecisionRevision.id).where(
        DecisionRevision.document_id == revision.document_id,
        DecisionRevision.revision >= first_revision,
        DecisionRevision.revision <= revision.revision,
        or_(~declared, ~matching),
    ).limit(1)
    return database.scalar(interrupted) is None


def latest_supersession(
    database: Session, revision: DecisionRevision, origin: DecisionRevision,
):
    before, after = aliased(DecisionRevision), aliased(DecisionRevision)
    return database.execute(select(DecisionImpactAssessment, after.revision).join(
        before, before.id == DecisionImpactAssessment.origin_revision_id,
    ).join(after, after.id == DecisionImpactAssessment.replacement_revision_id).where(
        DecisionImpactAssessment.project_id == revision.project_id,
        DecisionImpactAssessment.trigger == "supersession",
        before.document_id == origin.document_id,
        after.document_id == revision.document_id,
        after.revision < revision.revision,
    ).order_by(after.revision.desc(), DecisionImpactAssessment.created_at.desc(),
               DecisionImpactAssessment.id.desc()).limit(1)).first()


def reusable_supersession(
    database: Session, revision: DecisionRevision, episode: DecisionImpactAssessment,
) -> DecisionImpactAssessment | None:
    """Validate the declared original transition against the latest captured evidence."""
    origin = database.get(DecisionRevision, episode.origin_revision_id)
    assert origin is not None
    declaration = database.get(DecisionSupersessionReuse, (revision.id, origin.id))
    assert declaration is not None
    prior = latest_supersession(database, revision, origin)
    if prior is None:
        return None
    previous, first_revision = prior
    same_target = all(getattr(previous, key) == getattr(episode, key) for key in (
        "origin_revision_id", "extent", "reason",
    ))
    if (previous.replacement_revision_id == declaration.governing_revision_id
            and same_target and same_evidence(previous, episode)
            and continuous_declaration(database, revision, origin, first_revision, episode)):
        return previous
    return None
