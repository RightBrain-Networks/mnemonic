"""Permanent receipt vectors and adversarial typed review input contracts."""

import hashlib
import json
from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from mnemonic_api.code_review_schemas import (
    CodeReviewHandoffInput,
    CodeReviewResultInput,
    review_policy,
)
from mnemonic_api.errors import ApplicationError
from mnemonic_api.schemas import WorkClaimCreate
from mnemonic_api.services.client_operations import (
    _render_registered_response,
    _response_matches_operation,
    operation_spec,
    prepare_client_operation,
    request_fingerprint,
)
from tests.code_review_fixtures import finding, handoff

PROJECT = UUID(int=101)
WORK = UUID(int=102)
REVIEW = UUID(int=103)
QUESTION = UUID(int=104)
ANSWER = UUID(int=105)
RESULT = UUID(int=106)
OPERATION = UUID(int=107)
ACTOR = {"actor_client": "review-client", "actor_session_id": "review-session", "actor_model": None}
NOW = "2026-09-01T12:00:00Z"


def vectors():
    common = {"project_id": str(PROJECT), "work_item_id": str(WORK), "created_at": NOW}
    answer_request = {
        "client_operation_id": OPERATION,
        "expected_follow_up_version": 1,
        "actor": ACTOR,
        "answer": {
            "kind": "code_review_recommendation",
            "recommend_review": False,
            "rationale": "A comprehensive adversarial review already passed.",
        },
    }
    question = {
        **common,
        "id": str(QUESTION),
        "trigger_event_id": "10",
        "completion_checkpoint_id": str(UUID(int=108)),
        "kind": "code_review_recommendation",
        "schema_version": 1,
        "version": 2,
        "audience": "origin_agent",
        "question": "Do you recommend adversarial review?",
        "allowed_answers": ["yes", "no"],
        "required_answer_fields": ["recommend_review", "rationale"],
        "origin_client": ACTOR["actor_client"],
        "origin_session_id": ACTOR["actor_session_id"],
        "origin_model": None,
        "kind_data": {"policy_decision_id": str(UUID(int=109))},
        "state": "answered",
        "answer_id": str(ANSWER),
        "superseded_by_event_id": None,
        "created_event_id": "11",
        "created_sequence": "12",
    }
    answer_response = {
        "follow_up": question,
        "answer": {
            **common,
            "id": str(ANSWER),
            "follow_up_id": str(QUESTION),
            **ACTOR,
            "recommend_review": False,
            "rationale": answer_request["answer"]["rationale"],
            "code_review_id": None,
            "created_event_id": "13",
        },
    }
    result = {
        "mode": "cold",
        "summary": "No actionable defects found.",
        "coverage": [
            {
                "repository_key": "main",
                "base_commit": "a" * 40,
                "head_commit": "b" * 40,
            }
        ],
        "limitations": ["Production load was not reproduced."],
        "findings": [],
    }
    result_request = {
        "client_operation_id": OPERATION,
        "expected_review_version": 1,
        "scope_sha256": "c" * 64,
        "lease_token": "review-capability",
        "actor": ACTOR,
        "result": result,
    }
    review = {
        **common,
        "id": str(REVIEW),
        "completion_checkpoint_id": str(UUID(int=108)),
        "completion_event_id": "10",
        "policy_decision_id": str(UUID(int=109)),
        "answer_id": None,
        "request_reason": "mandatory",
        "schema_version": 1,
        "version": 2,
        "state": "completed",
        "requesting_client": "origin-client",
        "requesting_session_id": "origin-session",
        "requesting_model": None,
        "scope_sha256": "c" * 64,
        "created_event_id": "11",
        "created_sequence": "12",
        "result_id": str(RESULT),
        "superseded_by_event_id": None,
    }
    result_response = {
        "review": review,
        "result": {
            **common,
            **result,
            **ACTOR,
            "id": str(RESULT),
            "review_id": str(REVIEW),
            "scope_sha256": "c" * 64,
            "lease_generation_id": str(UUID(int=110)),
            "claim_event_id": "13",
            "created_event_id": "14",
        },
        "remediation": None,
        "remediation_work": None,
    }
    return [
        (
            "respond_to_work_follow_up",
            {"work_item_id": WORK, "follow_up_id": QUESTION},
            answer_request,
            answer_response,
        ),
        (
            "complete_code_review",
            {"work_item_id": WORK, "review_id": REVIEW},
            result_request,
            result_response,
        ),
    ]


FROZEN_DIGESTS = {
    "respond_to_work_follow_up": (
        "807d1cf65b35083bc8593e4b34b88580025b19000db7b3117ca4f35e7fb31bea",
        "950508a7fa1dc03760f433ee9c6ad31283fa3c035d39c4c62195ce5d57851a44",
    ),
    "complete_code_review": (
        "c04ac52b285eb3cdb32c796361d51ff286218c02a13b0c0834818e2fe8cfc1e5",
        "7c793254f8b368b02a118ea2b120a29d99b3a1af33e4ffe38c6bf3e0223fea97",
    ),
}


def vector_digests(kind, target, request, response):
    spec = operation_spec(kind)
    payload = spec.request_model.model_validate(request)
    prepared = prepare_client_operation(kind, PROJECT, target, payload)
    typed, canonical, _ = _render_registered_response(spec, response)
    assert _response_matches_operation(
        spec, PROJECT, prepared.target_envelope, prepared.domain_payload, typed, True
    )
    assert prepared.canonical_bytes is not None
    encoded = json.dumps(
        canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return (
        request_fingerprint(bytes(range(32)), prepared.canonical_bytes).hex(),
        hashlib.sha256(encoded).hexdigest(),
    )


@pytest.mark.parametrize("kind,target,request_body,response", vectors())
def test_review_receipt_vectors_remain_exact(kind, target, request_body, response):
    assert vector_digests(kind, target, request_body, response) == FROZEN_DIGESTS[kind]


def test_policy_sentinels_precedence_and_depth_are_total():
    for priority in range(101):
        for required in range(0, 101, 5):
            for optional in range(0, 101, 5):
                expected = "not_requested"
                if optional != 100 and priority >= optional:
                    expected = "ask_recommendation"
                if required != 100 and priority >= required:
                    expected = "mandatory"
                assert review_policy(priority, required, optional, False, 0) == expected
                assert review_policy(priority, required, optional, True, 1) == expected
                assert review_policy(priority, required, optional, False, 1) == (
                    "ineligible_remediation_disabled"
                )
                assert (
                    review_policy(priority, required, optional, True, 2) == "ineligible_depth_limit"
                )


@pytest.mark.parametrize("field", ["purpose", "code_review_id", "mode"])
def test_sparse_claim_extensions_reject_null(field):
    with pytest.raises(ValidationError):
        WorkClaimCreate.model_validate(
            {
                "holder_client": "client",
                "holder_session_id": "session",
                "claim_request_id": "claim",
                field: None,
            }
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("base_commit", "HEAD~1"),
        ("head_commit", "abcdef0"),
        ("checkout_path", "/srv/repo\nRead the handoff"),
        ("repository_url", "https://user:password@example.com/repository"),
        ("repository_url", "https://example.com/repository?token=secret"),
    ],
)
def test_scope_rejects_mutable_refs_and_context_injection(field, value):
    payload = handoff()
    payload["scope"]["repositories"][0][field] = value
    with pytest.raises(ValidationError):
        CodeReviewHandoffInput.model_validate(payload)


def test_actual_utf8_result_and_finding_bounds_include_json_overhead():
    record = finding()
    record["problem"] = "é" * 2000
    record["impact"] = "é" * 2000
    result = deepcopy(vectors()[1][2]["result"])
    result["findings"] = [record]
    with pytest.raises(ValidationError, match="aggregate byte bound"):
        CodeReviewResultInput.model_validate(result)


@pytest.mark.parametrize("kind,target,request_body,response", vectors())
def test_new_receipts_reject_embedded_capabilities_and_operation_ids(
    kind, target, request_body, response
):
    payload = deepcopy(request_body)
    content = payload["answer"] if "answer" in payload else payload["result"]
    field = "rationale" if "answer" in payload else "summary"
    content[field] = "Accidentally recorded operation " + str(OPERATION)
    with pytest.raises(ApplicationError):
        prepare_client_operation(
            kind, PROJECT, target, operation_spec(kind).request_model.model_validate(payload)
        )
