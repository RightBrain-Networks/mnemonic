"""Human review decisions preserve the implementation episode and exact retry receipts."""

from uuid import uuid4

import pytest

from tests.code_review_fixtures import (
    assert_work_state,
    claim_review,
    close,
    configure,
    create,
    mandatory,
)

pytestmark = pytest.mark.postgres


def decide(api, project, work, resource, version, target_status, **overrides):
    decision = {
        "resource_id": resource["id"],
        "expected_decision_version": version,
        "status": target_status,
    }
    if target_status in {"done", "wont-do", "promoted"}:
        decision["job_completion_report"] = {
            "summary": "A person recorded this review decision outside the agent workflow.",
            "fyi_items": [],
            "prompt_revision": api.get(f"/api/v1/projects/{project['id']}/settings").json()[
                "revision"
            ],
        }
    payload = {
        "expected_version": work["version"],
        "client_operation_id": str(uuid4()),
        "actor": {"actor_client": "dashboard", "actor_session_id": "human-tab"},
        "review_decision": decision,
        **overrides,
    }
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    return api.patch(base, json=payload), payload


@pytest.mark.parametrize("recommendation", [False, True])
def test_human_review_decisions_round_trip_and_replay(
    api,
    project,
    work_payload,
    checkpoint_fields,
    recommendation,
):
    if recommendation:
        configure(api, project, code_review_optional_min_priority=0)
        work = create(api, project, work_payload)
        response, _ = close(api, project, work, checkpoint_fields)
        assert response.status_code == 200, response.text
        completion = response.json()
        resource = completion["agent_follow_ups"][0]
    else:
        completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
        resource = completion["code_review_request"]
        claim_review(api, project, completion, checkpoint_fields)
    work = completion["work_item"]
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    checkpoint_id = (
        completion["completion_checkpoint"]["id"] if "completion_checkpoint" in completion else None
    )
    first_payload = first_result = None
    for version, status in enumerate(
        [
            "deferred",
            "to-review",
            "wont-do",
            "to-review",
            "done",
            "to-review",
            "promoted",
            "to-review",
        ]
    ):
        response, payload = decide(api, project, work, resource, version, status)
        assert response.status_code == 200, response.text
        work = response.json()
        assert work["status"] == "done"
        assert_work_state(api, project, work, status)
        context = api.get(base + "/context").json()
        assert not context["readiness"]["has_active_lease"]
        key = "pending_follow_up" if recommendation else "current_review"
        decision = context["code_review_context"][key]["human_decision"]
        assert decision["version"] == version + 1
        assert decision["status"] == status
        assert (decision["job_completion_report"] is not None) == (
            status in {"done", "wont-do", "promoted"}
        )
        assert api.patch(base, json=payload).json() == work
        if version == 0:
            first_payload, first_result = payload, work
        if not recommendation and status != "to-review":
            claim = api.post(
                base + "/claim",
                json={
                    "holder_client": "review-client",
                    "holder_session_id": "new-review-session",
                    "claim_request_id": str(uuid4()),
                    "purpose": "code_review",
                    "code_review_id": resource["id"],
                    "mode": "cold",
                },
            )
            assert claim.status_code == 409, claim.text
    assert api.patch(base, json=first_payload).json() == first_result
    if checkpoint_id:
        assert completion["completion_checkpoint"]["id"] == checkpoint_id
    if not recommendation:
        lease = claim_review(api, project, completion, checkpoint_fields)
        assert lease["purpose"] == "code_review"


def test_review_decision_rejects_agents_stale_intents_and_mixed_implementation_changes(
    api,
    project,
    work_payload,
    checkpoint_fields,
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    work, resource = completion["work_item"], completion["code_review_request"]
    for overrides in [
        {"actor": {"actor_client": "claude-code", "actor_session_id": "agent"}},
        {"title": "Unexpected implementation rewrite"},
        {"status": "pending"},
        {"client_operation_id": None},
    ]:
        response, _ = decide(api, project, work, resource, 0, "deferred", **overrides)
        assert response.status_code == 422, response.text
    response, _ = decide(api, project, work, resource, 1, "deferred")
    assert response.status_code == 409, response.text


def test_review_closeout_requires_report_and_blocks_move_and_late_result(
    api, project, work_payload, checkpoint_fields,
):
    from tests.code_review_fixtures import result_payload, result_url

    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    lease = claim_review(api, project, completion, checkpoint_fields)
    work, resource = completion["work_item"], completion["code_review_request"]
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    response, payload = decide(api, project, work, resource, 0, "done")
    assert response.status_code == 200, response.text
    assert api.post(result_url(project, completion), json=result_payload(
        completion, lease, findings=[],
    )).status_code == 409
    target = api.post("/api/v1/projects", json={"name": "Review cannot move"}).json()
    moved = api.post(base+"/move", json={
        "expected_version": response.json()["version"], "target_project_id": target["id"],
        "actor": payload["actor"], "client_operation_id": str(uuid4()),
    })
    assert moved.status_code == 409, moved.text
    payload["client_operation_id"] = str(uuid4())
    payload["expected_version"] = response.json()["version"]
    payload["review_decision"].update(expected_decision_version=1, status="promoted")
    del payload["review_decision"]["job_completion_report"]
    missing_report = api.patch(base, json=payload)
    assert missing_report.status_code == 422, missing_report.text
    assert missing_report.json()["detail"]["code"] == "job_completion_report_required"


@pytest.mark.parametrize("recommendation", [False, True])
def test_review_decisions_cannot_be_rewritten_in_sql(
    api, project, work_payload, checkpoint_fields, postgres_engine, recommendation,
):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    if recommendation:
        configure(api, project, code_review_optional_min_priority=0)
        created = create(api, project, work_payload)
        completed, _ = close(api, project, created, checkpoint_fields)
        completion = completed.json()
        resource = completion["agent_follow_ups"][0]
        table = "work_agent_follow_ups"
    else:
        completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
        resource = completion["code_review_request"]
        table = "code_reviews"
    response, _ = decide(api, project, completion["work_item"], resource, 0, "deferred")
    assert response.status_code == 200, response.text
    for replacement in ["'[]'::jsonb", "jsonb_set(human_decisions,'{0,status}','\"done\"')"]:
        with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
            connection.execute(text(f"UPDATE {table} SET human_decisions={replacement} "
                                    "WHERE id=:id"), {"id": resource["id"]})


def test_review_audit_detects_tampered_human_decision(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    from sqlalchemy import text

    from tests.test_project_activity_audit_postgres import _audit

    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    resource = completion["code_review_request"]
    response, _ = decide(api, project, completion["work_item"], resource, 0, "deferred")
    assert response.status_code == 200, response.text
    assert _audit(postgres_engine)["result"] == "pass"
    with postgres_engine.begin() as connection:
        connection.execute(text("ALTER TABLE code_reviews DISABLE TRIGGER USER"))
        connection.execute(text("UPDATE code_reviews SET human_decisions=jsonb_set("
                                "human_decisions,'{0,status}','\"done\"') WHERE id=:id"),
                           {"id": resource["id"]})
        connection.execute(text("ALTER TABLE code_reviews ENABLE TRIGGER USER"))
    report = _audit(postgres_engine)
    assert report["blocking_findings"]["code_review_human_decision_event_mismatch"] == 1
