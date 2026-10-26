"""Conservative comparison of complete captured evidence for reviewed conclusions."""

from mnemonic_api.impact_schemas import ImpactCandidate, ImpactCoverage


def comparable_coverage(left: dict, right: dict) -> bool:
    required = set(ImpactCoverage.model_fields) - {"snapshot_at"}
    if not required.issubset(left) or not required.issubset(right):
        return False
    return (left.get("traversal_complete") is True and right.get("traversal_complete") is True
            and left.get("reasons") == right.get("reasons") == []
            and {key: value for key, value in left.items() if key != "snapshot_at"}
            == {key: value for key, value in right.items() if key != "snapshot_at"})


def same_candidate(left: dict, right: dict) -> bool:
    required = set(ImpactCandidate.model_fields) - {"related_direction"}
    if not required.issubset(left) or not required.issubset(right):
        return False
    return (left.get("evidence_truncated") is False and right.get("evidence_truncated") is False
            and not any(scope.get("completion_citations_truncated")
                        for item in (left, right) for scope in item["scopes"])
            and {key: value for key, value in left.items() if key != "is_current"}
            == {key: value for key, value in right.items() if key != "is_current"})
