"""Readable immutable scope-growth evidence, separate from context-free notices."""

from uuid import UUID

from sqlalchemy.orm import Session

from mnemonic_api.models import DecisionChange, DecisionImpactAssessment, DecisionRevision
from mnemonic_api.review_scope_schemas import ReviewScopeChanged
from mnemonic_api.services.decision_scope_notices import captured_counts, scope_notice


def _count_text(counts: tuple[int, int, int]) -> str:
    return (f"{counts[0]} captured code-impact rows, {counts[1]} distinct revision references, "
            f"{counts[2]} distinct decision IDs")


def growth_checkpoint(
    database: Session, change: DecisionChange, additions: list[DecisionImpactAssessment],
    previous_ids: list[UUID], causing_revision: DecisionRevision | None,
) -> ReviewScopeChanged | None:
    if change.review_work_item_id is None:
        return None
    from mnemonic_api.services.decision_changes import scope_checkpoint
    from mnemonic_api.services.decision_impact import _revision_reference

    identities = [row.id for row in additions]
    notice = scope_notice(database, change, change.scope_version - len(additions), identities)
    before = captured_counts(database, previous_ids)
    after = captured_counts(database, previous_ids + identities)
    added = captured_counts(database, identities)
    lines = [f"Decision change `{change.id}` scope grew from version "
             f"{notice.old_scope_version} to {notice.new_scope_version}.", ""]
    revisions = ([causing_revision] if causing_revision is not None else [
        database.get(DecisionRevision, identity)
        for identity in dict.fromkeys(row.replacement_revision_id for row in additions)
    ])
    for revision in revisions:
        assert revision is not None
        reference = _revision_reference(database, revision.id)
        label = ("Causing revision" if causing_revision is not None
                 else "Attached replacement revision")
        lines.append(f"{label}: `{reference}`; recorded author: {revision.author_kind}; "
                     f"agent session: `{revision.agent_session_id}`; model: `{revision.model_id}`.")
    lines.extend([
        "", f"Previous scope: {notice.old_scope_version} episodes; {_count_text(before)}.",
        f"Current scope: {notice.new_scope_version} episodes; {_count_text(after)}.",
        f"Added scope: {notice.episodes_added} episodes; {_count_text(added)}.",
        "Distinct added identities are counted within added rows; they may already appear in "
        "earlier episodes. Counts describe captured evidence, not exhaustive real-world impact.",
        "", "Newly attached assessment episodes:",
    ])
    for row in additions:
        lines.append(f"- `{row.id}`: `{_revision_reference(database, row.origin_revision_id)}` → "
                     f"`{_revision_reference(database, row.replacement_revision_id)}` "
                     f"({row.trigger}); {_count_text(captured_counts(database, [row.id]))}.")
    lines.extend([
        "", "For every added episode, read both pinned revisions, classify its text/rule/scope/"
        "reference changes, page every code-impact candidate and inspect its captured evidence "
        'and coverage. Call decision_impact(kind="code-impact") on each episode\'s prior pinned '
        "reference, page every fresh result, and compare it with that episode's captured "
        'assessment ledger. Separately call decision_impact(kind="context") and read the relevant '
        "text to check whether the replacement contradicts prerequisites or related records. "
        "Record extra live candidates and their evidenced conclusions separately; preserve the "
        "episode's frozen scope and do not fabricate ledger entries. Record a disposition for "
        "each required episode/reference pair, even when "
        "that reference appears in another episode. Every change-required result needs "
        "tracked remediation. Context-only candidates do not require verdicts.",
        "Read decision_change_get and page decision_assessments with this change_id, then use "
        "decision_change_update(action=acknowledge) with the exact current expected_scope_version. "
        "Acknowledgement does not replace dispositions or grant implementation authority.",
    ])
    scope_checkpoint(database, change, "\n".join(lines), metadata={
        "review_scope_changed": notice.model_dump(mode="json"),
        "assessment_ids": [str(identity) for identity in identities],
        "causing_revision_id": str(causing_revision.id) if causing_revision is not None else None,
    })
    return notice
