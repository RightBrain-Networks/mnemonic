"""Receipt response validation binds every batch item to its original request."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mnemonic_api.errors import ApplicationError
from mnemonic_api.impact_schemas import (
    ImpactDispositionBatchCreate,
    ImpactDispositionBatchRead,
    ImpactDispositionCreate,
)
from mnemonic_api.services.client_operations import prepare_client_operation
from mnemonic_api.services.impact_receipts import disposition_batch_matches


def body():
    return {"client_operation_id": uuid4(), "expected_version": 4,
            "actor": {"actor_client": "codex", "actor_session_id": "native"},
            "items": [{"candidate_reference": f"ADR-{number:05d}-0", "disposition": "still-valid",
                       "reason": "Read the exact candidate and implementation."}
                      for number in (1, 2)]}


def test_new_optional_token_preserves_legacy_singleton_canonical_request():
    request = body()
    item = request.pop("items")[0]
    payload = ImpactDispositionCreate(**request, **item)
    project, assessment = uuid4(), uuid4()
    prepared = prepare_client_operation("assess_decision_impact", project,
                                        {"assessment_id": assessment}, payload)
    canonical = json.loads(prepared.canonical_bytes)
    assert "lease_token" not in canonical["request"]
    assert canonical["request"] == {
        **item, "remediation_work_item_id": None, "expected_version": 4,
        "actor": {"actor_client": "codex", "actor_session_id": "native", "actor_model": None},
    }
    explicit = ImpactDispositionCreate(**request, **item, lease_token=None)
    assert prepare_client_operation("assess_decision_impact", project,
        {"assessment_id": assessment}, explicit).canonical_bytes == prepared.canonical_bytes


@pytest.mark.parametrize("change", ["reason", "candidate_reference", "actor", "version",
                                    "assessment_id", "project_id", "id", "missing", "order"])
def test_batch_result_must_match_every_ordered_request_item(change):
    payload = ImpactDispositionBatchCreate(**body())
    project, assessment = uuid4(), uuid4()
    result = {"project_id": project, "assessment_id": assessment, "version": 6, "items": [
        {**item.model_dump(), "id": uuid4(), "project_id": project, "assessment_id": assessment,
         "version": 5 + offset, "actor": payload.actor, "created_at": datetime.now(UTC)}
        for offset, item in enumerate(payload.items)
    ]}
    if change == "missing":
        result["items"].pop()
    elif change == "order":
        result["items"].reverse()
    else:
        replacement = {"reason": "Altered", "candidate_reference": "ADR-99999-0",
                       "actor": {"actor_client": "codex", "actor_session_id": "other"},
                       "version": 7, "assessment_id": uuid4(), "project_id": uuid4(),
                       "id": result["items"][0]["id"]}[change]
        result["items"][1][change] = replacement
    assert not disposition_batch_matches(project, {"assessment_id": str(assessment)}, payload,
                                          ImpactDispositionBatchRead(**result))


def test_batch_capability_substrings_are_rejected_without_secret_error_content():
    values = body()
    secret = "private-capability-value"
    values["lease_token"] = secret
    values["items"][1]["reason"] = "Observed " + secret
    with pytest.raises(ApplicationError) as captured:
        prepare_client_operation("assess_decision_impact_batch", uuid4(),
            {"assessment_id": uuid4()}, ImpactDispositionBatchCreate(**values))
    assert captured.value.detail["code"] == "client_operation_secret_echo"
    assert secret not in str(captured.value.detail)
