"""Only revision display status may change without requiring a fresh candidate review."""

from copy import deepcopy

import pytest

from mnemonic_api.impact_schemas import ImpactCandidate, ImpactCoverage
from mnemonic_api.services.impact_evidence_comparison import comparable_coverage, same_candidate


def candidate():
    return ImpactCandidate(
        decision_id="ADR-00001", reference="ADR-00001-0", revision=0, title="Rule",
        state="accepted", is_current=True, relationship="related-only",
        related_direction="incoming",
        path=["ADR-00002-0", "ADR-00001-0"], rationale=None, scopes=[],
        related_work_item_ids=[], related_artifact_ids=[], evidence_truncated=False,
    ).model_dump(mode="json")


@pytest.mark.parametrize("field,value", [
    ("revision", 1), ("reference", "ADR-00001-1"), ("relationship", "depends-on"),
    ("related_direction", "outgoing"), ("rationale", "Changed dependency reason"),
    ("scopes", [{"commit_hash": "a" * 40}]),
    ("related_work_item_ids", ["10000000-0000-0000-0000-000000000000"]),
    ("related_artifact_ids", ["20000000-0000-0000-0000-000000000000"]),
    ("evidence_truncated", True), ("unrecognized_future_evidence", "New evidence"),
])
def test_new_evidence_requires_fresh_review(field, value):
    old = candidate()
    assert not same_candidate(old, {**old, field: value})


def test_only_current_display_flag_is_ignored_and_unknown_shapes_fail_closed():
    old = candidate()
    assert same_candidate(old, {**old, "is_current": False})
    unknown = {key: value for key, value in old.items() if key != "evidence_truncated"}
    assert not same_candidate(unknown, unknown)
    scopes = [{"completion_citations": [], "completion_citations_truncated": False}]
    old["scopes"] = scopes
    new = deepcopy(old)
    new["scopes"][0]["completion_citations"] = [{"report_id": "new-completion"}]
    assert not same_candidate(old, new)
    old["scopes"][0]["completion_citations_truncated"] = True
    assert not same_candidate(old, old)


def test_coverage_requires_known_complete_equal_capture_except_time():
    old = ImpactCoverage(traversal_complete=True, max_depth=8, candidate_limit=500,
                         visited_revisions=1, reasons=[]).model_dump(mode="json")
    assert comparable_coverage(old, {**old, "snapshot_at": "2026-10-26T00:00:00Z"})
    assert not comparable_coverage(old, {**old, "visited_revisions": 2})
    incomplete = {**old, "traversal_complete": False, "reasons": ["candidate_limit"]}
    assert not comparable_coverage(incomplete, incomplete)
    old.pop("traversal_complete")
    assert not comparable_coverage(old, old)
