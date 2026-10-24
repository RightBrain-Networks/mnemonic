"""Finding labels are optional evidence metadata, not a replacement for dispositions."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from mnemonic_api.impact_finding_schemas import ImpactFindingSummary
from mnemonic_api.impact_schemas import ImpactDispositionRequest
from mnemonic_api.models import DecisionImpactDisposition
from mnemonic_api.services.impact_findings import findings_for_latest


def request(**changes):
    return ImpactDispositionRequest.model_validate({
        "candidate_reference": "ADR-00001-0", "expected_version": 0,
        "disposition": "change-required", "reason": "Pinned text and code disagree",
        "remediation_work_item_id": str(uuid4()),
        "actor": {"actor_client": "codex", "actor_session_id": "native-thread"},
        **changes,
    })


def test_optional_metadata_keeps_historical_request_shape_unchanged():
    old = request()
    assert "finding_id" not in old.model_dump()
    assert "remediation_target" not in old.model_dump()
    labelled = request(finding_id="ladder-wording", remediation_target="decision-record")
    assert labelled.finding_id == "ladder-wording"
    assert labelled.remediation_target == "decision-record"


@pytest.mark.parametrize("changes", [
    {"finding_id": ""}, {"finding_id": "bad identity"}, {"finding_id": "x" * 101},
    {"finding_id": "label\n"}, {"remediation_target": "unknown"},
    {"disposition": "still-valid", "remediation_target": "code"},
])
def test_finding_identity_and_target_are_validated(changes):
    with pytest.raises(ValidationError):
        request(**changes)


def test_summary_separates_one_named_finding_from_unknown_unlabelled_rows():
    rows = [DecisionImpactDisposition(disposition="change-required", finding_id="one-cause")
            for _ in range(19)]
    rows.extend(DecisionImpactDisposition(disposition="change-required") for _ in range(2))
    rows.append(DecisionImpactDisposition(disposition="resolved", finding_id="earlier-cause"))
    assert findings_for_latest(rows).model_dump() == {
        "named_finding_count": 1, "unlabelled_change_required_count": 2,
        "change_required_row_count": 21,
    }


@pytest.mark.parametrize("counts", [(2, 0, 1), (0, 0, 1), (0, 2, 1), (1, 1, 1)])
def test_summary_cannot_invent_or_hide_labelled_rows(counts):
    with pytest.raises(ValidationError):
        ImpactFindingSummary.model_validate(dict(zip((
            "named_finding_count", "unlabelled_change_required_count", "change_required_row_count",
        ), counts, strict=True)))
