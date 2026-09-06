"""Code review policy, capabilities, replay and single-remediation lifecycle."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.code_review_fixtures import (
    actor,
    claim_review,
    close,
    configure,
    create,
    finding,
    handoff,
    mandatory,
    result_payload,
    result_url,
)

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("same_operation", [False, True])
def test_concurrent_review_results_create_only_one_remediation(
    api,
    project,
    work_payload,
    checkpoint_fields,
    same_operation,
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    lease = claim_review(api, project, completion, checkpoint_fields)
    payload = result_payload(completion, lease, findings=[finding(), finding("F002")])
    other = dict(payload)
    if not same_operation:
        other["client_operation_id"] = str(uuid4())
    url = result_url(project, completion)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda body: api.post(url, json=body), [payload, other]))
    assert sorted(response.status_code for response in responses) == (
        [200, 200] if same_operation else [200, 409]
    ), [response.text for response in responses]
    success = next(response.json() for response in responses if response.status_code == 200)
    assert success["remediation_work"]["work_item"]["status"] == "pending"
    page = api.get(f"/api/v1/projects/{project['id']}/work-items", params={"status": "all"}).json()
    assert page["total"] == 2


def test_mandatory_precondition_and_result_validation_are_atomic(
    api,
    project,
    work_payload,
    checkpoint_fields,
):
    configure(api, project, code_review_required_min_priority=0)
    work = create(api, project, work_payload)
    failed, close_payload = close(api, project, work, checkpoint_fields)
    assert failed.status_code == 422, failed.text
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    assert api.get(base).json()["work_item"]["status"] == "pending"
    close_payload["code_review_handoff"] = handoff()
    accepted = api.post(base + "/complete", json=close_payload)
    assert accepted.status_code == 200, accepted.text
    completion = accepted.json()
    lease = claim_review(api, project, completion, checkpoint_fields)
    url = result_url(project, completion)
    valid = result_payload(completion, lease, findings=[finding()])
    invalids = [
        {**valid, "expected_review_version": 2},
        {**valid, "scope_sha256": "d" * 64},
        {**valid, "lease_token": "another-token"},
        {**valid, "actor": {"actor_client": "other", "actor_session_id": "other"}},
        {**valid, "result": {**valid["result"], "mode": "warm"}},
    ]
    for payload in invalids:
        response = api.post(url, json=payload)
        assert response.status_code in {409, 422}, response.text
        detail = api.get(url.removesuffix("/complete"))
        assert detail.status_code == 200, detail.text
        assert detail.json()["review"]["state"] == "requested"
        assert detail.json()["result"] is None
    accepted = api.post(url, json=valid)
    assert accepted.status_code == 200, accepted.text
    fresh = api.post(url, json={**valid, "client_operation_id": str(uuid4())})
    assert fresh.status_code == 409, fresh.text


def test_review_claim_replay_renew_and_cold_recall_guard(
    api,
    project,
    work_payload,
    checkpoint_fields,
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    review = completion["code_review_request"]
    base = f"/api/v1/projects/{project['id']}/work-items/{review['work_item_id']}"
    payload = {
        "holder_client": "review-client",
        "holder_session_id": "review-session",
        "claim_request_id": str(uuid4()),
        "purpose": "code_review",
        "code_review_id": review["id"],
        "mode": "cold",
    }
    denied = api.post(base + "/claim-and-recall", json=payload)
    assert denied.status_code == 409, denied.text
    first = api.post(base + "/claim", json=payload)
    assert first.status_code == 200, first.text
    assert api.post(base + "/claim", json=payload).json() == first.json()
    conflicting = api.post(base + "/claim", json={**payload, "mode": "warm"})
    assert conflicting.status_code == 409, conflicting.text
    renewed = api.post(base + "/renew-claim", json={"lease_token": first.json()["lease_token"]})
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["lease_generation_id"] == first.json()["lease_generation_id"]
    assert renewed.json()["scope_sha256"] == review["scope_sha256"]


@pytest.mark.parametrize("findings", [[], [finding(), finding("F002")]])
def test_mandatory_review_atomic_result_and_exact_replay(
    api,
    project,
    work_payload,
    checkpoint_fields,
    findings,
):
    completion, close_payload = mandatory(api, project, work_payload, checkpoint_fields)
    assert completion["review_policy_decision"]["decision"] == "mandatory"
    assert completion["code_review_handoff"] == handoff()
    work = completion["work_item"]
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    lease = claim_review(api, project, completion, checkpoint_fields)
    assert "context" not in lease and lease["purpose"] == "code_review"
    payload = result_payload(completion, lease, findings=findings)
    url = result_url(project, completion)
    response = api.post(url, json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["review"]["state"] == "completed"
    assert len(result["result"]["findings"]) == len(findings)
    assert (result["remediation"] is not None) == bool(findings)
    assert api.post(url, json=payload).json() == result
    assert api.post(base + "/complete", json=close_payload).json() == completion
    current = api.get(base).json()
    assert current["work_item"]["status"] == "done"
    context = api.get(base + "/context")
    assert context.status_code == 200, context.text
    assert context.json()["readiness"]["active_lease"] is None
    if findings:
        child = result["remediation_work"]["work_item"]
        assert child["status"] == "pending"
        child_context = api.get(base.rsplit("/", 1)[0] + "/" + child["id"]).json()
        assert child_context["code_review_context"]["remediation_depth"] == 1
        assert len(result["remediation_work"]["initial_relationships"]) == 1
    detail = api.get(url.removesuffix("/complete"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["result"] == result["result"]


@pytest.mark.parametrize("recommend", [False, True])
def test_optional_question_original_session_and_answer_replay(
    api,
    project,
    work_payload,
    checkpoint_fields,
    recommend,
):
    configure(api, project, code_review_optional_min_priority=0)
    work = create(api, project, work_payload)
    response, close_payload = close(api, project, work, checkpoint_fields)
    assert response.status_code == 200, response.text
    completion = response.json()
    question = completion["agent_follow_ups"][0]
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    url = base + f"/agent-follow-ups/{question['id']}/answer"
    answer = {
        "kind": "code_review_recommendation",
        "recommend_review": recommend,
        "rationale": "Complex concurrent invalidation warrants review.",
    }
    if recommend:
        answer["code_review_handoff"] = handoff()
    payload = {
        "client_operation_id": str(uuid4()),
        "expected_follow_up_version": 1,
        "actor": actor(checkpoint_fields),
        "answer": answer,
    }
    rejected = api.post(
        url,
        json={
            **payload,
            "actor": {
                "actor_client": "another-client",
                "actor_session_id": "another-session",
            },
        },
    )
    assert rejected.status_code == 409, rejected.text
    response = api.post(url, json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["follow_up"]["state"] == "answered"
    assert ("code_review_request" in result) == recommend
    assert api.post(url, json=payload).json() == result
    assert api.post(base + "/complete", json=close_payload).json() == completion
    detail = api.get(url.removesuffix("/answer"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["answer"] == result["answer"]


@pytest.mark.parametrize("optional", [False, True])
def test_explicit_reopen_supersedes_obligation_and_preserves_history(
    api,
    project,
    work_payload,
    checkpoint_fields,
    optional,
):
    if optional:
        configure(api, project, code_review_optional_min_priority=0)
        work = create(api, project, work_payload)
        response, close_payload = close(api, project, work, checkpoint_fields)
        assert response.status_code == 200, response.text
        completion = response.json()
        resource = completion["agent_follow_ups"][0]
        fields = {"supersede_follow_up_id": resource["id"], "expected_follow_up_version": 1}
        detail_suffix = f"/agent-follow-ups/{resource['id']}"
        state_key = "follow_up"
    else:
        completion, close_payload = mandatory(api, project, work_payload, checkpoint_fields)
        resource = completion["code_review_request"]
        claim_review(api, project, completion, checkpoint_fields)
        fields = {"supersede_code_review_id": resource["id"], "expected_code_review_version": 1}
        detail_suffix = f"/code-reviews/{resource['id']}"
        state_key = "review"
    work = completion["work_item"]
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    payload = {
        "expected_version": work["version"],
        "status": "pending",
        "client_operation_id": str(uuid4()),
        "actor": actor(checkpoint_fields),
    }
    rejected = api.patch(base, json=payload)
    assert rejected.status_code == 409, rejected.text
    payload.update(fields)
    reopened = api.patch(base, json=payload)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "pending"
    assert api.patch(base, json=payload).json() == reopened.json()
    detail = api.get(base + detail_suffix)
    assert detail.status_code == 200, detail.text
    assert detail.json()[state_key]["state"] == "superseded"
    assert api.post(base + "/complete", json=close_payload).json() == completion
    context = api.get(base + "/context")
    assert context.status_code == 200, context.text
    assert context.json()["readiness"]["active_lease"] is None


def test_queue_filters_history_and_capability_isolation(
    api,
    project,
    work_payload,
    checkpoint_fields,
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    review = completion["code_review_request"]
    base = f"/api/v1/projects/{project['id']}"
    queue_url = base + "/code-reviews"
    initial = api.get(queue_url, params={"availability": "unclaimed"})
    assert initial.status_code == 200, initial.text
    assert [item["id"] for item in initial.json()["items"]] == [review["id"]]
    lease = claim_review(api, project, completion, checkpoint_fields)
    empty = api.get(queue_url, params={"availability": "unclaimed"})
    assert empty.status_code == 200 and not empty.json()["items"]
    active = api.get(queue_url).json()["items"][0]
    assert active["lease"]["code_review_id"] == review["id"]
    work_base = base + f"/work-items/{review['work_item_id']}"
    bad_edit = api.patch(
        work_base,
        json={
            "expected_version": completion["work_item"]["version"],
            "summary": "Changed summary",
            "lease_token": lease["lease_token"],
        },
    )
    assert bad_edit.status_code == 409, bad_edit.text
    completed = api.post(
        result_url(project, completion),
        json=result_payload(completion, lease, findings=[finding()]),
    )
    assert completed.status_code == 200, completed.text
    history = api.get(queue_url, params={"state": "all", "work_item_id": review["work_item_id"]})
    assert history.status_code == 200, history.text
    row = history.json()["items"][0]
    assert (
        row["remediation_work_item_id"]
        == completed.json()["remediation"]["remediation_work_item_id"]
    )
    assert row["lease"] is None and row["review_available"] is False
    filtered = api.get(queue_url, params={"state": "all", "availability": "unclaimed"})
    assert filtered.status_code == 200 and filtered.json()["items"] == []
    for query in ("limit=51", "limit=1&limit=2", "unused=x", "after="):
        assert api.get(queue_url + "?" + query).status_code == 422
    assert (
        api.get(
            queue_url, params={"after": initial.json()["next_cursor"], "availability": "all"}
        ).status_code
        == 422
    )
