"""Explicit, reversible read shaping without rewriting immutable impact evidence."""

from pydantic.experimental.missing_sentinel import MISSING

from mnemonic_api.impact_schemas import (
    ImpactAuthorRead,
    ImpactCandidate,
    ImpactCandidateRead,
    ImpactDiffRead,
    ImpactDispositionProjection,
    ImpactDispositionRead,
    ImpactProjection,
)
from mnemonic_api.revision_author_schemas import RevisionAuthorContext


def projection(options: ImpactProjection) -> ImpactProjection | MISSING:
    selected = ImpactProjection.model_validate(options.model_dump(include={
        "detail", "include_diff", "include_scopes",
    }))
    return MISSING if selected == ImpactProjection() else selected


def candidate_read(item: ImpactCandidate, options: ImpactProjection) -> ImpactCandidateRead:
    value = item.model_dump()
    if options.detail == "compact":
        for name in ("title", "rationale", "related_work_item_ids", "related_artifact_ids"):
            value.pop(name)
    if options.include_scopes != "full":
        scopes = value.pop("scopes")
        if options.include_scopes == "summary":
            value["scope_summary"] = {
                "captured_scope_count": len(scopes),
                "commit_hashes": sorted({scope["commit_hash"] for scope in scopes}),
                "evidence_truncated": item.evidence_truncated,
            }
    return ImpactCandidateRead.model_validate(value)


def author_read(item: RevisionAuthorContext, options: ImpactProjection) -> ImpactAuthorRead:
    value = item.model_dump()
    if options.detail == "compact":
        value.pop("active_work_items")
        value["active_work_item_ids"] = [work.work_item_id for work in item.active_work_items]
    return ImpactAuthorRead.model_validate(value)


def diff_read(value: dict, options: ImpactProjection) -> ImpactDiffRead:
    result = dict(value)
    if not options.include_diff:
        result.pop("unified_diff")
    return ImpactDiffRead.model_validate(result)


def disposition_projection(
    item: ImpactDispositionRead, options: ImpactProjection,
) -> ImpactDispositionProjection:
    value = item.model_dump()
    if options.detail == "compact":
        for name in ("id", "project_id", "assessment_id", "reason", "actor", "created_at"):
            value.pop(name)
    return ImpactDispositionProjection.model_validate(value)
