"""Clients and independent agent sessions coordinate through shared claims and provenance."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.code_review_fixtures import (
    close,
    configure,
    create,
    finding,
    mandatory,
    result_payload,
    result_url,
)

pytestmark = pytest.mark.postgres


def _base(project, work):
    return f"/api/v1/projects/{project['id']}/work-items/{work['id']}"


def _claim(client, session="shared-native-session"):
    return {
        "holder_client": client,
        "holder_session_id": session,
        "claim_request_id": "shared-request-id",
    }


def test_mixed_clients_claim_independent_work_and_identify_exact_contenders(
    api, project, work_payload,
):
    clients = ("claude-code", "codex")
    work = [create(api, project, {**work_payload, "title": f"Implementation by {client}"})
            for client in clients]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(
            lambda pair: api.post(_base(project, pair[1]) + "/claim", json=_claim(pair[0])),
            zip(clients, work, strict=True),
        ))
    assert [response.status_code for response in claimed] == [200, 200]
    for client, item, response in zip(clients, work, claimed, strict=True):
        receipt = response.json()
        base = _base(project, item)
        assert api.post(base + "/claim", json=_claim(client)).json() == receipt
        contenders = [_claim("opencode"), _claim(client, "independent-subagent")]
        for contender in contenders:
            rejected = api.post(base + "/claim", json=contender)
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()["detail"] == {
                "code": "lease_held", "message": "This work item has an active lease.",
                "context": {
                    "holder_client": client, "holder_session_id": "shared-native-session",
                    "purpose": "implementation", "expires_at": receipt["expires_at"],
                },
            }
            assert receipt["lease_token"] not in rejected.text
            assert receipt["claim_request_id"] not in rejected.text
    page = api.get(f"/api/v1/projects/{project['id']}/work-items", params={"status": "active"})
    holders = {entry["summary"]["readiness"]["active_lease"]["holder_client"]
               for entry in page.json()["items"]}
    assert holders == set(clients)


def test_opencode_review_of_claude_work_keeps_client_session_and_capability_separate(
    api, project, work_payload, checkpoint_fields,
):
    checkpoint = {**checkpoint_fields, "source_session_id": "shared-native-session"}
    completion, _ = mandatory(api, project, work_payload, checkpoint)
    implementation = create(api, project, {
        **work_payload, "title": "Independent Codex implementation",
        "initial_checkpoint": {**checkpoint, "source_client": "codex"},
    })
    implementation_url = _base(project, implementation) + "/claim"
    implementing = api.post(implementation_url, json=_claim("codex"))
    assert implementing.status_code == 200, implementing.text
    review = completion["code_review_request"]
    assert review["requesting_client"] == "claude-code"
    base = _base(project, completion["work_item"])
    payload = {
        **_claim("opencode"), "purpose": "code_review",
        "code_review_id": review["id"], "mode": "cold",
    }
    response = api.post(base + "/claim", json=payload)
    assert response.status_code == 200, response.text
    lease = response.json()
    assert "context" not in lease and "handoff" not in lease
    for client in ("claude-code", "codex"):
        rejected = api.post(base + "/claim", json={**payload, "holder_client": client})
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["context"] == {
            "holder_client": "opencode", "holder_session_id": "shared-native-session",
            "purpose": "code_review", "code_review_id": review["id"], "mode": "cold",
            "expires_at": lease["expires_at"],
        }
        assert lease["lease_token"] not in rejected.text
        assert lease["claim_request_id"] not in rejected.text
    result = result_payload(completion, lease, findings=[finding()])
    result["actor"] = {"actor_client": "codex", "actor_session_id": "shared-native-session"}
    rejected = api.post(result_url(project, completion), json=result)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "lease_purpose_mismatch"
    result["client_operation_id"] = str(uuid4())
    result["actor"] = {
        "actor_client": "opencode", "actor_session_id": "shared-native-session",
        "actor_model": "review-model",
    }
    accepted = api.post(result_url(project, completion), json=result)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["result"]["actor_client"] == "opencode"
    checkpoint = accepted.json()["remediation_work"]["initial_checkpoint"]
    assert checkpoint["source_client"] == "opencode"
    assert checkpoint["source_session_id"] == "shared-native-session"
    assert checkpoint["source_model"] == "review-model"
    assert api.post(result_url(project, completion), json=result).json() == accepted.json()
    assert api.post(implementation_url, json=_claim("codex")).json() == implementing.json()


def test_follow_up_is_owned_by_client_session_pair_even_when_model_changes(
    api, project, work_payload, checkpoint_fields,
):
    configure(api, project, code_review_optional_min_priority=0)
    work = create(api, project, work_payload)
    completed, _ = close(api, project, work, checkpoint_fields)
    assert completed.status_code == 200, completed.text
    follow_up = completed.json()["agent_follow_ups"][0]
    url = _base(project, work) + f"/agent-follow-ups/{follow_up['id']}/answer"
    payload = {
        "client_operation_id": str(uuid4()), "expected_follow_up_version": 1,
        "actor": {
            "actor_client": "claude-code",
            "actor_session_id": checkpoint_fields["source_session_id"],
            "actor_model": "new-model-in-same-session",
        },
        "answer": {
            "kind": "code_review_recommendation",
            "recommend_review": False, "rationale": "Existing regression covers the change.",
        },
    }
    for change in ({"actor_client": "codex"}, {"actor_session_id": "independent-subagent"}):
        rejected = api.post(url, json={**payload, "actor": {**payload["actor"], **change}})
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["code"] == "work_follow_up_origin_mismatch"
    accepted = api.post(url, json=payload)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["answer"]["actor_model"] == "new-model-in-same-session"
    assert api.post(url, json=payload).json() == accepted.json()
