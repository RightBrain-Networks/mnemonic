"""Retain new episodes while pinning inspected conclusions on unchanged evidence."""

from sqlalchemy.orm import Session

from mnemonic_api.impact_schemas import is_code_impact
from mnemonic_api.models import (
    DecisionCandidateReuse,
    DecisionImpactAssessment,
    DecisionRevision,
    DecisionSupersessionReuse,
)
from mnemonic_api.services.decision_supersession_reuse import (
    continuous_declaration,
    latest_supersession,
)
from mnemonic_api.services.effective_dispositions import effective_dispositions
from mnemonic_api.services.impact_evidence_comparison import comparable_coverage, same_candidate


def _previous(
    database: Session, revision: DecisionRevision, episode: DecisionImpactAssessment,
) -> DecisionImpactAssessment | None:
    declaration = database.get(DecisionSupersessionReuse, (revision.id, episode.origin_revision_id))
    origin = database.get(DecisionRevision, episode.origin_revision_id)
    if declaration is None or origin is None:
        return None
    prior = latest_supersession(database, revision, origin)
    if prior is None:
        return None
    previous, first_revision = prior
    if (previous.replacement_revision_id != declaration.governing_revision_id
            or any(getattr(previous, key) != getattr(episode, key)
                   for key in ("origin_revision_id", "extent", "reason"))
            or not continuous_declaration(database, revision, origin, first_revision, episode)
            or not comparable_coverage(previous.coverage, episode.coverage)):
        return None
    return previous


def retain_candidate_conclusions(
    database: Session, revision: DecisionRevision, episode: DecisionImpactAssessment,
) -> None:
    if episode.trigger != "supersession":
        return
    previous = _previous(database, revision, episode)
    if previous is None:
        return
    old = {item["reference"]: item for item in previous.candidates}
    latest = {item.row.candidate_reference: item.row
              for item in effective_dispositions(database, [previous.id])}
    reusable = [item for item in episode.candidates
                if is_code_impact(item["relationship"], item.get("related_direction"))
                and item["reference"] in latest and item["reference"] in old
                and same_candidate(old[item["reference"]], item)]
    # Flush the fresh episode so every reference is enrolled in the same revision transaction.
    database.flush()
    database.add_all([DecisionCandidateReuse(
        project_id=episode.project_id, assessment_id=episode.id,
        candidate_reference=item["reference"],
        source_disposition_id=latest[item["reference"]].id,
    ) for item in reusable])
