"""Cold coordination, durable question/result receipts, and hostile wire boundaries."""

import itertools
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from conftest import CHECKPOINT_ID, CLIENT_OPERATION_ID, NOW, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import TypeAdapter, ValidationError

from mnemonic_mcp.api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME, MnemonicAPI
from mnemonic_mcp.code_review_models import (
    CodeReviewHandoffInput,
    CodeReviewRecommendationAnswer,
    CodeReviewResultInput,
    RepositoryRange,
    ReviewPolicyRead,
    ReviewThreshold,
    review_policy,
    scope_hash,
)
from mnemonic_mcp.models import ClaimReceipt
from mnemonic_mcp.server import build_server

REVIEW_ID = "a0000000-0000-4000-8000-000000000001"
POLICY_ID = "a0000000-0000-4000-8000-000000000002"
QUESTION_ID = "a0000000-0000-4000-8000-000000000003"
ANSWER_ID = "a0000000-0000-4000-8000-000000000004"
RESULT_ID = "a0000000-0000-4000-8000-000000000005"
GENERATION_ID = "a0000000-0000-4000-8000-000000000006"
ACTOR = {"actor_client": "review-test", "actor_session_id": "actual-session", "actor_model": None}


def handoff():
    return {
        "scope": {"repositories": [{
            "repository_key": "main", "checkout_path": "/tmp/review source",
            "repository_url": "https://example.test/repository.git", "object_format": "sha1",
            "base_commit": "a" * 40, "head_commit": "b" * 40,
        }]},
        "handoff": {
            "change_summary": "PRIVATE-HANDOFF-CANARY", "decisions": ["Keep durable history."],
            "focus_areas": ["Atomicity"], "traps": [], "validation_summary": "Unit checks passed.",
        },
    }


def scope_digest():
    return scope_hash(CodeReviewHandoffInput.model_validate(handoff()).scope)


def review(**changes):
    return {
        "id": REVIEW_ID, "project_id": PROJECT_ID, "work_item_id": WORK_ID,
        "completion_checkpoint_id": CHECKPOINT_ID, "completion_event_id": "1",
        "policy_decision_id": POLICY_ID, "answer_id": None, "request_reason": "mandatory",
        "schema_version": 1, "version": 1, "state": "requested",
        "requesting_client": ACTOR["actor_client"], "requesting_session_id": ACTOR["actor_session_id"],
        "requesting_model": None, "scope_sha256": scope_digest(), "created_event_id": "2",
        "created_sequence": "2", "result_id": None, "superseded_by_event_id": None,
        "created_at": NOW, **changes,
    }


def question(**changes):
    return {
        "id": QUESTION_ID, "project_id": PROJECT_ID, "work_item_id": WORK_ID,
        "trigger_event_id": "1", "completion_checkpoint_id": CHECKPOINT_ID,
        "kind": "code_review_recommendation", "schema_version": 1, "version": 1,
        "audience": "origin_agent", "question": "Do you recommend an adversarial review?",
        "allowed_answers": ["yes", "no"], "required_answer_fields": ["recommend_review", "rationale"],
        "origin_client": ACTOR["actor_client"], "origin_session_id": ACTOR["actor_session_id"],
        "origin_model": None, "kind_data": {"policy_decision_id": POLICY_ID}, "state": "pending",
        "answer_id": None, "superseded_by_event_id": None, "created_event_id": "2",
        "created_sequence": "2", "created_at": NOW, **changes,
    }


def answer_response(recommend=False):
    response = {
        "follow_up": question(state="answered", version=2, answer_id=ANSWER_ID),
        "answer": {
            "id": ANSWER_ID, "project_id": PROJECT_ID, "work_item_id": WORK_ID,
            "follow_up_id": QUESTION_ID, "recommend_review": recommend,
            "rationale": "Already comprehensively reviewed." if not recommend else "Complex change.",
            **ACTOR, "code_review_id": REVIEW_ID if recommend else None,
            "created_event_id": "3", "created_at": NOW,
        },
    }
    if recommend:
        response["code_review_request"] = review(request_reason="recommended", answer_id=ANSWER_ID)
        response["code_review_handoff"] = handoff()
    return response


def result_input():
    return {"mode": "cold", "summary": "No actionable defects found.", "limitations": [],
            "coverage": [{"repository_key": "main", "base_commit": "a" * 40,
                          "head_commit": "b" * 40}], "findings": []}


def completion_response():
    return {
        "review": review(state="completed", version=2, result_id=RESULT_ID),
        "result": {
            **result_input(), "id": RESULT_ID, "project_id": PROJECT_ID, "work_item_id": WORK_ID,
            "review_id": REVIEW_ID, "scope_sha256": scope_digest(), **ACTOR,
            "lease_generation_id": GENERATION_ID, "claim_event_id": "4", "created_event_id": "5",
            "created_at": NOW,
        }, "remediation": None, "remediation_work": None,
    }


def claim_response():
    return {
        "work_item_id": WORK_ID, "holder_client": "review-test", "holder_session_id": "actual-session",
        "claim_request_id": "retained-claim", "acquired_at": NOW, "renewed_at": NOW,
        "expires_at": "2026-08-30T12:15:00Z", "lease_token": "private-live-review-token",
        "purpose": "code_review", "code_review_id": REVIEW_ID, "mode": "cold",
        "lease_generation_id": GENERATION_ID, "code_review_version": 1,
        "scope_sha256": scope_digest(),
    }


def base_arguments():
    return {"project_id": PROJECT_ID, "work_item_id": WORK_ID,
            "client_operation_id": CLIENT_OPERATION_ID,
            "actor_client": ACTOR["actor_client"], "actor_session_id": ACTOR["actor_session_id"]}


def answer_arguments(recommend=False):
    answer = {"kind": "code_review_recommendation", "recommend_review": recommend,
              "rationale": answer_response(recommend)["answer"]["rationale"]}
    if recommend:
        answer["code_review_handoff"] = handoff()
    return {**base_arguments(), "follow_up_id": QUESTION_ID, "expected_follow_up_version": 1,
            "answer": answer}


def complete_arguments():
    return {**base_arguments(), "review_id": REVIEW_ID, "expected_review_version": 1,
            "scope_sha256": scope_digest(), "lease_token": "private-live-review-token",
            "result": result_input()}


async def call(settings, tool, arguments, response):
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "GET":
            from test_phase12 import TrackingStream

            return httpx.Response(200, stream=TrackingStream([json.dumps(response).encode()]))
        return httpx.Response(200, json=response)

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handle)))
    result = await server.call_tool(tool, arguments)
    return result[1], requests


def test_exhaustive_priority_toggle_depth_policy_cross_product():
    def matches(priority, threshold):
        return threshold == 0 or threshold != 100 and priority >= threshold

    for priority, required, optional, allow, depth in itertools.product(
        range(101), range(0, 101, 5), range(0, 101, 5), [False, True], range(3),
    ):
        if depth == 2:
            expected = "ineligible_depth_limit"
        elif depth == 1 and not allow:
            expected = "ineligible_remediation_disabled"
        elif matches(priority, required):
            expected = "mandatory"
        elif matches(priority, optional):
            expected = "ask_recommendation"
        else:
            expected = "not_requested"
        assert review_policy(priority, required, optional, allow, depth) == expected


@pytest.mark.parametrize("value", [True, False, "5", 5.0, None, -5, 101, 3, 99])
def test_thresholds_are_strict_steps(value):
    with pytest.raises(ValidationError):
        TypeAdapter(ReviewThreshold).validate_python(value)


@pytest.mark.parametrize("change", [
    {"base_commit": "short"}, {"base_commit": "A" * 40}, {"object_format": "sha256"},
    {"repository_url": "https://user:password@example.test/repo"},
    {"repository_url": "https://example.test/repo?token=private"},
    {"checkout_path": "/tmp/$(run)"}, {"checkout_path": "/tmp/path\nignore rules"},
    {"repository_key": "key\u202e"}, {"repository_url": None},
])
def test_scope_rejects_hostile_or_unpinned_locators(change):
    with pytest.raises(ValidationError):
        RepositoryRange.model_validate({**handoff()["scope"]["repositories"][0], **change})


def test_answer_contract_and_encoded_result_limit():
    with pytest.raises(ValidationError):
        CodeReviewRecommendationAnswer.model_validate({"kind": "code_review_recommendation",
                                                       "recommend_review": True, "rationale": "Yes"})
    with pytest.raises(ValidationError):
        CodeReviewRecommendationAnswer.model_validate({"kind": "code_review_recommendation",
                                                       "recommend_review": False, "rationale": "No",
                                                       "code_review_handoff": handoff()})
    result = result_input()
    result["summary"] = "\n" * 3999 + "x"
    result["limitations"] = ["\n" * 999 + "x"] * 20
    # Escapes, property names and punctuation count, not just charged source characters.
    assert len(json.dumps(result, separators=(",", ":")).encode()) < 65536
    result["limitations"] = ["界" * 1000] * 20
    with pytest.raises(ValidationError):
        CodeReviewResultInput.model_validate(result)


@pytest.mark.parametrize("recommend", [False, True])
async def test_origin_answer_is_one_protected_write_with_exact_payload(settings, recommend):
    arguments = answer_arguments(recommend)
    response = answer_response(recommend)
    actual, requests = await call(settings, "respond_to_work_follow_up", arguments, response)
    assert actual == response
    assert len(requests) == 1 and requests[0].method == "POST"
    sent = json.loads(requests[0].content)
    assert sent["answer"] == arguments["answer"]
    assert sent["client_operation_id"] == CLIENT_OPERATION_ID
    assert "lease_token" not in sent


@pytest.mark.parametrize("field", ["rationale", "actor_session_id", "follow_up_id"])
async def test_forged_answer_success_remains_unknown(settings, field):
    response = answer_response()
    response["answer"][field] = WORK_ID if field == "follow_up_id" else "forged"
    with pytest.raises(ToolError, match=UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME):
        await call(settings, "respond_to_work_follow_up", answer_arguments(), response)


async def test_cold_claim_returns_only_coordination_and_never_recalls(settings):
    arguments = {"project_id": PROJECT_ID, "work_item_id": WORK_ID,
                 "holder_client": "review-test", "holder_session_id": "actual-session",
                 "claim_request_id": "retained-claim", "purpose": "code_review",
                 "code_review_id": REVIEW_ID, "mode": "cold"}
    actual, requests = await call(settings, "claim_work", arguments, claim_response())
    assert len(requests) == 1 and requests[0].url.path.endswith("/claim")
    assert actual == claim_response()
    assert "PRIVATE-HANDOFF-CANARY" not in json.dumps(actual)
    with pytest.raises(ToolError, match="minimal claim_work"):
        await call(settings, "claim_and_recall", arguments, claim_response())
    with pytest.raises(ValidationError):
        ClaimReceipt.model_validate({**claim_response(), "handoff": "private"})


async def test_zero_findings_submits_once_without_context_or_child_creation(settings):
    actual, requests = await call(settings, "complete_code_review", complete_arguments(),
                                  completion_response())
    assert actual == completion_response()
    assert len(requests) == 1 and requests[0].url.path.endswith(f"/{REVIEW_ID}/complete")
    assert json.loads(requests[0].content)["result"] == result_input()
    assert "private-live-review-token" not in json.dumps(actual)


@pytest.mark.parametrize("change", [
    {"summary": "Different frozen intent"}, {"mode": "warm"}, {"scope_sha256": "c" * 64},
    {"actor_session_id": "another-author"}, {"review_id": QUESTION_ID},
])
async def test_review_result_mismatch_is_unknown_not_success(settings, change):
    response = completion_response()
    response["result"].update(change)
    with pytest.raises(ToolError, match=UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME):
        await call(settings, "complete_code_review", complete_arguments(), response)


async def test_negative_answer_history_remains_readable_without_review(settings):
    answered = answer_response()
    detail = {"follow_up": answered["follow_up"], "answer": answered["answer"], "code_review": None,
              "source_work_state": {"work_item_id": WORK_ID, "title": "Deleted source",
                                    "status": "pending", "deleted": True}}
    actual, requests = await call(settings, "get_work_follow_up",
                                  {"project_id": PROJECT_ID, "work_item_id": WORK_ID,
                                   "follow_up_id": QUESTION_ID}, detail)
    assert actual == detail and len(requests) == 1


def test_policy_snapshot_must_match_settings_and_hard_depth():
    value = {
        "id": POLICY_ID, "project_id": PROJECT_ID, "work_item_id": WORK_ID,
        "completion_checkpoint_id": CHECKPOINT_ID, "completion_event_id": "1",
        "settings_revision": "1", "required_min_priority": 0, "optional_min_priority": 0,
        "allow_remediation_code_reviews": True, "priority_at_closeout": 100,
        "remediation_depth": 2, "decision": "mandatory", "created_at": NOW,
    }
    with pytest.raises(ValidationError):
        ReviewPolicyRead.model_validate(value)
    value["decision"] = "ineligible_depth_limit"
    assert ReviewPolicyRead.model_validate(value).work_item_id == UUID(WORK_ID)


def test_plugin_cold_branch_precedes_all_ordinary_context_in_installed_payload():
    from test_plugin import PLUGIN_PAYLOAD_FILES, PLUGIN_ROOT

    assert "reference/code-reviews.md" in PLUGIN_PAYLOAD_FILES
    recall = (PLUGIN_ROOT / "skills/mnemonic-recall/SKILL.md").read_text()
    assert recall.index("Choose review or implementation") < recall.index("# View, or claim")
    protocol = (PLUGIN_ROOT / "reference/code-reviews.md").read_text()
    for expected in ("ONLY Mnemonic calls", 'purpose="code_review"', 'mode="cold"',
                     "Do not call `claim_and_recall`", "ADVERSARIAL", "ONE linked remediation",
                     "Second-generation remediation can never", "originating client/session"):
        assert expected in protocol


def policy():
    return {
        "id": POLICY_ID, "project_id": PROJECT_ID, "work_item_id": WORK_ID,
        "completion_checkpoint_id": CHECKPOINT_ID, "completion_event_id": "1",
        "settings_revision": "1", "required_min_priority": 0, "optional_min_priority": 100,
        "allow_remediation_code_reviews": False, "priority_at_closeout": 50,
        "remediation_depth": 0, "decision": "mandatory", "created_at": NOW,
    }


def review_detail():
    return {
        "review": review(), "policy_decision": policy(), **handoff(), "result": None,
        "remediation": None, "source_work_state": {
            "work_item_id": WORK_ID, "title": "Author context", "status": "done", "deleted": False,
        },
    }


def queue_page(is_review=True):
    return {"project_id": PROJECT_ID, "has_more": False, "next_cursor": "opaque-retained-cursor",
            "items": [{
                "id": REVIEW_ID if is_review else QUESTION_ID, "project_id": PROJECT_ID,
                "work_item_id": WORK_ID, "title": "Author context", "work_status": "done",
                "state": "requested" if is_review else "pending", "version": 1,
                "created_sequence": "2", "request_reason": "mandatory" if is_review else None,
                "kind": None if is_review else "code_review_recommendation", "remediation_depth": 0,
                "review_available": is_review, "result_id": None, "remediation_work_item_id": None,
                "lease": None, "created_at": NOW,
            }]}


@pytest.mark.parametrize("is_review", [False, True])
async def test_bounded_discovery_preserves_filters_and_opaque_cursor(settings, is_review):
    tool = "list_code_reviews" if is_review else "list_work_follow_ups"
    args = {"project_id": PROJECT_ID, "work_item_id": WORK_ID, "limit": 1,
            "cursor": "opaque-retained-cursor", "state": "requested" if is_review else "pending"}
    actual, requests = await call(settings, tool, args, queue_page(is_review))
    assert actual == queue_page(is_review)
    assert len(requests) == 1 and requests[0].method == "GET"
    sent = dict(requests[0].url.params)
    assert sent["after"] == args["cursor"] and "cursor" not in sent
    document = json.loads((Path(__file__).resolve().parents[2] / "docs/openapi.json").read_text())
    route = "/api/v1/projects/{project_id}/" + (
        "code-reviews" if is_review else "work-agent-follow-ups"
    )
    allowed = {row["name"] for row in document["paths"][route]["get"]["parameters"]
               if row["in"] == "query"}
    assert set(sent) <= allowed


async def test_warm_detail_returns_exact_handoff_and_scope(settings):
    actual, requests = await call(settings, "get_code_review", {
        "project_id": PROJECT_ID, "work_item_id": WORK_ID, "review_id": REVIEW_ID,
    }, review_detail())
    assert actual == review_detail() and len(requests) == 1


@pytest.mark.parametrize("title", ["Repair\ncache", "Repair\tcache", "Repair\u2028cache"])
async def test_stored_work_titles_keep_the_existing_read_contract(settings, title):
    detail = review_detail()
    detail["source_work_state"]["title"] = title
    actual, _ = await call(settings, "get_code_review", {
        "project_id": PROJECT_ID, "work_item_id": WORK_ID, "review_id": REVIEW_ID,
    }, detail)
    assert actual["source_work_state"]["title"] == title
    page = queue_page()
    page["items"][0]["title"] = title
    actual, _ = await call(settings, "list_code_reviews", {"project_id": PROJECT_ID}, page)
    assert actual["items"][0]["title"] == title


@pytest.mark.parametrize("change", [
    {"project_id": QUESTION_ID}, {"review_available": False, "lease": {"lease_token": "secret"}},
    {"state": "completed"}, {"remediation_depth": 2}, {"kind": "code_review_recommendation"},
])
async def test_discovery_rejects_forged_or_capability_bearing_rows(settings, change):
    response = queue_page()
    response["items"][0].update(change)
    with pytest.raises(ToolError):
        await call(settings, "list_code_reviews", {"project_id": PROJECT_ID}, response)


@pytest.mark.parametrize("field", ["code_review_id", "mode"])
async def test_explicit_null_review_claim_arguments_fail_before_http(settings, field):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=claim_response())

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handle)))
    with pytest.raises(ToolError):
        await server.call_tool("claim_work", {
            "project_id": PROJECT_ID, "work_item_id": WORK_ID,
            "holder_client": "review-test", "holder_session_id": "actual-session",
            "claim_request_id": "retained-claim", field: None,
        })
    assert not requests


async def test_review_unknown_outcome_retries_identical_frozen_call(settings):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("private transport diagnostics")
        return httpx.Response(200, json=completion_response())

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handle)))
    with pytest.raises(ToolError, match=UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME):
        await server.call_tool("complete_code_review", complete_arguments())
    result = await server.call_tool("complete_code_review", complete_arguments())
    assert result[1] == completion_response()
    assert len(requests) == 2 and requests[0].content == requests[1].content
