"""Freeze the two Phase 12 receipts alongside the thirteen earlier frozen kinds.

The request/response contracts here are those shipped at 513f543, before code
reviews. Review-policy fields must never leak into these historical snapshots.
"""

import hashlib
import json
from copy import deepcopy
from uuid import UUID

import pytest

from mnemonic_api.services.client_operations import (
    _render_registered_response,
    operation_spec,
    prepare_client_operation,
    request_fingerprint,
)
from tests.test_client_operations import (
    OPERATION_ID,
    OTHER_WORK_ID,
    PROJECT_ID,
    WORK_ID,
    response_vector_cases,
)

REPORT_ID = UUID("60000000-0000-0000-0000-000000000001")
FACT_ID = UUID("70000000-0000-0000-0000-000000000001")
ACTOR = {"actor_client": "pytest", "actor_session_id": "phase-6-response-v1",
         "actor_model": "test-model"}
NOW = "2026-09-01T12:00:00Z"


def report_receipt_vectors():
    source = deepcopy(next(value for kind, value, _ in response_vector_cases()
                           if kind == "create_work"))
    work, checkpoint = source["work_item"], source["initial_checkpoint"]
    work["version"] = 1
    checkpoint["kind"] = "context"
    request_checkpoint = {key: checkpoint[key] for key in (
        "prompt", "source_client", "source_session_id", "source_model", "source_session_url",
        "repository_branch", "verified_against", "tags", "source_metadata",
    )}
    request = {
        "actor": ACTOR, "client_operation_id": OPERATION_ID,
        "title": work["title"], "summary": work["summary"], "priority": work["priority"],
        "initial_checkpoint": request_checkpoint,
    }
    return [
        ("dismiss_job_completion_report", {"actor": ACTOR, "client_operation_id": OPERATION_ID}, {
            "project_id": str(PROJECT_ID), "report_id": str(REPORT_ID), "dismissed": True,
            "human_dismissal": {"id": str(FACT_ID), "created_at": NOW, **ACTOR},
        }),
        ("create_job_completion_report_follow_up", request, {
            "work_item": work, "initial_checkpoint": checkpoint,
            "follow_up": {
                "id": str(FACT_ID), "project_id": str(PROJECT_ID), "report_id": str(REPORT_ID),
                "source_work_item_id": str(OTHER_WORK_ID), "follow_up_work_item_id": str(WORK_ID),
                "created_sequence": "9", "created_at": NOW, **ACTOR,
            },
        }),
    ]


FROZEN_DIGESTS = {
    "dismiss_job_completion_report": (
        "5739b4b00d7c2d805f2da1a0a81ab1dd6971260073d5908a1e1550034e29095e",
        "7c7afac9680ae078754b6e264c57f26378e090b593cf132c74d9d90101b357e1",
    ),
    "create_job_completion_report_follow_up": (
        "14cd409c9110a8350aa091b38493b49ab77573fbf61d8f2e01d550c03276bca3",
        "bf4aa20a239fddd9d0de1fa02b86a022408714243b242f5ac6a7893194582130",
    ),
}


@pytest.mark.parametrize("kind,request_body,response", report_receipt_vectors())
def test_pre_review_report_receipts_preserve_request_and_response_bytes(
    kind, request_body, response,
):
    spec = operation_spec(kind)
    payload = spec.request_model.model_validate(request_body)
    prepared = prepare_client_operation(kind, PROJECT_ID, {"report_id": REPORT_ID}, payload)
    actual, canonical, http = _render_registered_response(spec, response)
    assert actual.model_dump(mode="json") == canonical == response
    assert json.loads(http.body) == response
    assert prepared.canonical_bytes is not None
    response_bytes = json.dumps(canonical, ensure_ascii=False, allow_nan=False,
                                sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert (
        request_fingerprint(bytes(range(32)), prepared.canonical_bytes).hex(),
        hashlib.sha256(response_bytes).hexdigest(),
    ) == FROZEN_DIGESTS[kind]
