"""Inherited conclusions retain source identity without borrowing the local write version."""

import pytest
from conftest import WORK_ID
from pydantic import ValidationError
from test_decision_candidate_tools import occurrence
from test_knowledge_impact_tools import ASSESSMENT_ID, assessment, candidate, disposition

from mnemonic_mcp.decision_candidate_models import DecisionCandidateOccurrence
from mnemonic_mcp.impact_models import ImpactAssessmentDetail, ImpactDispositionRead


def reused_detail():
    row = disposition(assessment_id=WORK_ID, version=7)
    row["reused_from"] = {
        "assessment_id": WORK_ID, "disposition_id": row["id"], "disposition_version": 7,
    }
    return assessment(
        trigger="supersession", kind="code-impact", total=1, candidates=[candidate()],
        dispositions=[row], disposition_history_total=0, version=0, unassessed_count=0,
        reused_candidate_count=1, change_required_count=1, status="change-required",
        limit=50, offset=0, next_offset=None,
    )


def test_inherited_source_version_does_not_advance_local_assessment():
    result = ImpactAssessmentDetail.model_validate(reused_detail())
    assert result.version == 0 and result.disposition_history_total == 0
    assert result.dispositions[0].version == 7
    assert result.dispositions[0].reused_from.assessment_id == result.dispositions[0].assessment_id


@pytest.mark.parametrize("damage", ["missing", "assessment", "id", "version", "self", "count"])
def test_inherited_conclusion_identity_cannot_be_forged(damage):
    value = reused_detail()
    row = value["dispositions"][0]
    if damage == "missing":
        row.pop("reused_from")
    elif damage == "count":
        value["reused_candidate_count"] = 0
    elif damage == "assessment":
        row["assessment_id"] = ASSESSMENT_ID
    elif damage == "self":
        row["reused_from"]["assessment_id"] = ASSESSMENT_ID
    elif damage == "id":
        row["reused_from"]["disposition_id"] = WORK_ID
    else:
        row["reused_from"]["disposition_version"] = 6
    with pytest.raises(ValidationError):
        ImpactAssessmentDetail.model_validate(value)


def test_grouped_occurrence_accepts_pinned_source_version():
    row = occurrence(0)
    row["latest_disposition"] = {
        key: value for key, value in reused_detail()["dispositions"][0].items()
        if key in {"id", "version", "disposition", "remediation_work_item_id", "reused_from"}
    }
    result = DecisionCandidateOccurrence.model_validate(row)
    assert result.assessment_version == 0 and result.latest_disposition.version == 7
    row["latest_disposition"]["reused_from"]["disposition_version"] = 6
    with pytest.raises(ValidationError):
        DecisionCandidateOccurrence.model_validate(row)


def test_mutation_receipts_never_accept_reused_conclusions():
    with pytest.raises(ValidationError):
        ImpactDispositionRead.model_validate(reused_detail()["dispositions"][0])
