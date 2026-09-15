"""Humans can request review independently of priority and implementation state."""

from uuid import uuid4

import pytest

from tests.code_review_fixtures import (
    assert_work_state,
    close,
    configure,
    create,
    finding,
    handoff,
    result_payload,
    result_url,
)
from tests.test_phase6_migration_postgres import (
    empty_phase6_migration_engine as empty_phase6_migration_engine,
)

pytestmark = pytest.mark.postgres


def request_review(api, project, work, **overrides):
    body = {
        "expected_version": work["version"],
        "client_operation_id": str(uuid4()),
        "actor": {"actor_client": "dashboard", "actor_session_id": "human-tab"},
        "request_code_review": True,
        **overrides,
    }
    path = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    return api.patch(path, json=body), body


@pytest.mark.parametrize(
    "before_done,threshold",
    [
        (False, "never"),
        (True, "never"),
        (True, "optional"),
        (True, "mandatory"),
    ],
)
def test_manual_request_queues_done_with_human_provenance(
    api,
    project,
    work_payload,
    checkpoint_fields,
    before_done,
    threshold,
):
    work = create(api, project, work_payload)
    if before_done:
        response, intent = request_review(api, project, work)
        assert response.status_code == 200, response.text
        work = response.json()
        assert work["status"] == "pending"
        assert_work_state(api, project, work, "pending")
    if threshold != "never":
        key = "optional" if threshold == "optional" else "required"
        configure(api, project, **{f"code_review_{key}_min_priority": 0})
    response, _ = close(api, project, work, checkpoint_fields)
    assert response.status_code == 200, response.text
    completed = response.json()
    work = completed["work_item"]
    if not before_done:
        response, intent = request_review(api, project, work)
        assert response.status_code == 200, response.text
        work = response.json()
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    assert_work_state(api, project, work, "to-review")
    context = api.get(base + "/context").json()
    review = context["code_review_context"]["current_review"]
    assert review["request_reason"] == "manual"
    assert review["requesting_client"] == "dashboard"
    assert review["requesting_session_id"] == "human-tab"
    assert review["scope_sha256"] is None
    assert review["manual_request"] == work["manual_review_request"]
    assert api.patch(base, json=intent).status_code == 200
    duplicate, _ = request_review(api, project, work)
    assert duplicate.status_code == 409
    detail = api.get(base + f"/code-reviews/{review['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["scope"] is None
    claim = {
        "holder_client": "review-client",
        "holder_session_id": "review-session",
        "claim_request_id": str(uuid4()),
        "session_transcript": None,
        "purpose": "code_review",
        "code_review_id": review["id"],
        "mode": "warm",
    }
    assert api.post(base + "/claim", json=claim).status_code == 422
    claim["code_review_handoff"] = handoff()
    claimed = api.post(base + "/claim", json=claim)
    assert claimed.status_code == 200, claimed.text
    assert api.post(base + "/claim", json=claim).json() == claimed.json()
    assert (
        api.post(
            base + "/claim",
            json={key: value for key, value in claim.items() if key != "code_review_handoff"},
        ).status_code
        == 409
    )
    detail = api.get(base + f"/code-reviews/{review['id']}").json()
    review = detail["review"]
    assert detail["scope"] == handoff()["scope"]
    completed["code_review_request"] = review
    result = api.post(
        result_url(project, completed),
        json=result_payload(
            completed,
            claimed.json(),
            findings=[finding()],
        ),
    )
    assert result.status_code == 200, result.text
    assert result.json()["review"]["requesting_client"] == "dashboard"


def test_agent_cannot_create_human_review_request(api, project, work_payload):
    work = create(api, project, work_payload)
    response, _ = request_review(
        api,
        project,
        work,
        actor={
            "actor_client": "codex",
            "actor_session_id": "agent-session",
            "actor_model": "model",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "review_request_requires_human"


@pytest.mark.parametrize("status", ["deferred", "wont-do", "promoted", "active"])
def test_earmark_preserves_existing_lifecycle_and_lease(
    api,
    project,
    work_payload,
    checkpoint_fields,
    status,
):
    work = create(api, project, work_payload)
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    actor = {"actor_client": "dashboard", "actor_session_id": "human-tab"}
    if status == "active":
        response = api.post(
            base + "/claim",
            json={
                "holder_client": "codex",
                "holder_session_id": "working-agent",
                "claim_request_id": str(uuid4()),
                "session_transcript": None,
            },
        )
    elif status == "deferred":
        response = api.post(
            base + "/defer",
            json={
                "expected_version": work["version"],
                "actor": actor,
                "client_operation_id": str(uuid4()),
            },
        )
    else:
        settings = api.get(f"/api/v1/projects/{project['id']}/settings").json()
        response = api.patch(
            base,
            json={
                "expected_version": work["version"],
                "status": status,
                "actor": actor,
                "subagent_transcripts": None,
                "client_operation_id": str(uuid4()),
                "job_completion_report": {
                    "summary": "Explicit human closeout decision.",
                    "fyi_items": [],
                    "prompt_revision": settings["revision"],
                },
            },
        )
    assert response.status_code == 200, response.text
    context = api.get(base + "/context").json()
    work = context["work_item"]
    response, intent = request_review(api, project, work)
    assert response.status_code == 200, response.text
    marked = response.json()
    assert marked["status"] == work["status"]
    current = api.get(base + "/context").json()
    assert current["work_item"]["manual_review_request"] == marked["manual_review_request"]
    flat = api.get(base.rsplit("/", 1)[0], params={"view": "full", "status": "all"}).json()
    flat_work = next(
        row["summary"]["work_item"]
        for row in flat["items"]
        if row["summary"]["work_item"]["id"] == work["id"]
    )
    assert flat_work["manual_review_request"] == marked["manual_review_request"]
    assert current["readiness"] == context["readiness"]
    assert current["code_review_context"]["current_review"] is None
    assert api.patch(base, json=intent).json() == marked
    if status == "active":
        return
    resumed = api.patch(
        base,
        json={
            "expected_version": marked["version"],
            "status": "pending",
            "actor": actor,
            "client_operation_id": str(uuid4()),
        },
    )
    assert resumed.status_code == 200, resumed.text
    response, _ = close(
        api, project, resumed.json(), checkpoint_fields, code_review_handoff=handoff()
    )
    assert response.status_code == 200, response.text
    assert (
        response.json()["code_review_request"]["manual_request"] == marked["manual_review_request"]
    )
    assert response.json()["code_review_request"]["scope_sha256"] is not None


def test_manual_request_audits_and_authorship_are_immutable(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine,
):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from tests.test_project_activity_audit_postgres import _audit

    work = create(api, project, work_payload)
    response, _ = request_review(api, project, work)
    assert response.status_code == 200, response.text
    marked = response.json()
    assert _audit(postgres_engine)["result"] == "pass"
    response, _ = close(api, project, marked, checkpoint_fields)
    assert response.status_code == 200, response.text
    review = response.json()["code_review_request"]
    assert _audit(postgres_engine)["result"] == "pass"
    for statement, identity in [
        ("UPDATE work_items SET manual_review_request=NULL WHERE id=:id", work["id"]),
        ("UPDATE code_reviews SET requesting_session_id='forged' WHERE id=:id", review["id"]),
    ]:
        with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
            connection.execute(text(statement), {"id": identity})


def test_historical_done_can_request_and_prepare_review_without_inventing_a_policy(
    empty_phase6_migration_engine,
    tmp_path,
):
    import runpy

    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from mnemonic_api.config import Settings
    from mnemonic_api.main import create_app
    from tests.conftest import BACKEND_DIR, TEST_API_KEY

    engine = empty_phase6_migration_engine
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    seeder = runpy.run_path(str(BACKEND_DIR.parent / "scripts/seed_e2e_historical_completion.py"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0018_repository_freshness")
        seeded = seeder["seed_historical_completion"](connection, uuid4())
        command.upgrade(config, "head")
    settings = Settings(
        database_url=engine.url.render_as_string(hide_password=False),
        api_key=TEST_API_KEY,
        transcript_root=tmp_path / "copies",
    )
    project = {"id": seeded["projectId"]}
    work_id = seeded["historicalCompletion"]["workItemId"]
    base = f"/api/v1/projects/{project['id']}/work-items/{work_id}"
    with TestClient(create_app(settings, engine=engine)) as api:
        api.headers["Authorization"] = f"Bearer {TEST_API_KEY}"
        work = api.get(base + "/context").json()["work_item"]
        response, _ = request_review(api, project, work)
        assert response.status_code == 200, response.text
        assert_work_state(api, project, response.json(), "to-review")
        review = api.get(base + "/context").json()["code_review_context"]["current_review"]
        detail = api.get(base + f"/code-reviews/{review['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["policy_decision"] is None
        assert review["policy_decision_id"] is None
        claim = api.post(
            base + "/claim",
            json={
                "holder_client": "review-client",
                "holder_session_id": "review-session",
                "claim_request_id": str(uuid4()),
                "session_transcript": None,
                "purpose": "code_review",
                "code_review_id": review["id"],
                "mode": "warm",
                "code_review_handoff": handoff(),
            },
        )
        assert claim.status_code == 200, claim.text
        review = api.get(base + f"/code-reviews/{review['id']}").json()["review"]
        completion = {"code_review_request": review}
        result = api.post(
            result_url(project, completion),
            json=result_payload(
                completion,
                claim.json(),
                findings=[finding()],
            ),
        )
        assert result.status_code == 200, result.text
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM work_completion_review_policies")) == 0


def test_human_can_request_after_agent_declines_without_rewriting_the_answer(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine,
):
    from sqlalchemy import text

    from tests.code_review_fixtures import actor
    from tests.test_project_activity_audit_postgres import _audit

    configure(api, project, code_review_optional_min_priority=0)
    work = create(api, project, work_payload)
    response, _ = close(api, project, work, checkpoint_fields)
    assert response.status_code == 200, response.text
    closed = response.json()
    question = closed["agent_follow_ups"][0]
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    answer_body = {
        "client_operation_id": str(uuid4()),
        "expected_follow_up_version": 1,
        "actor": actor(checkpoint_fields),
        "answer": {
            "kind": "code_review_recommendation",
            "recommend_review": False,
            "rationale": "The agent assessed this as low risk.",
        },
    }
    answer_path = base + f"/agent-follow-ups/{question['id']}/answer"
    answered = api.post(answer_path, json=answer_body)
    assert answered.status_code == 200, answered.text
    response, _ = request_review(api, project, closed["work_item"])
    assert response.status_code == 200, response.text
    assert_work_state(api, project, response.json(), "to-review")
    assert api.post(answer_path, json=answer_body).json() == answered.json()
    assert _audit(postgres_engine)["result"] == "pass"
    with postgres_engine.connect() as connection:
        connection.execute(
            text("SELECT mnemonic_code_review_assert_question(:id)"), {"id": question["id"]}
        )


def test_pending_manual_request_cannot_move_its_audited_human_authorship(
    api,
    project,
    work_payload,
):
    work = create(api, project, work_payload)
    response, _ = request_review(api, project, work)
    assert response.status_code == 200, response.text
    destination = api.post("/api/v1/projects", json={"name": f"Other project {uuid4()}"}).json()
    moved = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move",
        json={
            "expected_version": response.json()["version"],
            "target_project_id": destination["id"],
            "actor": {"actor_client": "dashboard", "actor_session_id": "human-tab"},
            "client_operation_id": str(uuid4()),
        },
    )
    assert moved.status_code == 409, moved.text
    assert moved.json()["detail"]["code"] == "work_move_review_history_conflict"


def test_unprepared_manual_review_supports_human_dispositions_and_reopening(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine,
):
    from tests.test_project_activity_audit_postgres import _audit
    from tests.test_review_decisions_postgres import decide

    work = create(api, project, work_payload)
    marked, _ = request_review(api, project, work)
    response, _ = close(api, project, marked.json(), checkpoint_fields)
    assert response.status_code == 200, response.text
    closed = response.json()
    work, review = closed["work_item"], closed["code_review_request"]
    for version, target in enumerate(["deferred", "done", "to-review"]):
        response, intent = decide(api, project, work, review, version, target)
        assert response.status_code == 200, response.text
        work = response.json()
        assert work["status"] == "done"
        assert (
            api.patch(
                f"/api/v1/projects/{project['id']}/work-items/{work['id']}", json=intent
            ).json()
            == work
        )
        assert_work_state(api, project, work, target)
    assert _audit(postgres_engine)["result"] == "pass"
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    reopened = api.patch(
        base,
        json={
            "expected_version": work["version"],
            "status": "pending",
            "supersede_code_review_id": review["id"],
            "expected_code_review_version": review["version"],
            "client_operation_id": str(uuid4()),
            "actor": {"actor_client": "dashboard", "actor_session_id": "human-tab"},
        },
    )
    assert reopened.status_code == 200, reopened.text
    assert "manual_review_request" not in reopened.json()
    retained = api.get(base + f"/code-reviews/{review['id']}").json()["review"]
    assert retained["state"] == "superseded" and retained["scope_sha256"] is None
    assert retained["manual_request"] == marked.json()["manual_review_request"]
    assert _audit(postgres_engine)["result"] == "pass"


def test_completed_manual_review_history_survives_duplicate_merge(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine,
):
    from tests.code_review_fixtures import claim_review
    from tests.test_duplicate_handling_postgres import merge_work
    from tests.test_project_activity_audit_postgres import _audit

    work = create(api, project, work_payload)
    marked, _ = request_review(api, project, work)
    closed, _ = close(api, project, marked.json(), checkpoint_fields, code_review_handoff=handoff())
    assert closed.status_code == 200, closed.text
    completion = closed.json()
    lease = claim_review(api, project, completion, checkpoint_fields, mode="warm")
    result = api.post(result_url(project, completion), json=result_payload(completion, lease))
    assert result.status_code == 200, result.text
    destination = create(api, project, {**work_payload, "title": "Canonical duplicate destination"})
    merged, _ = merge_work(api, project, completion["work_item"], destination)
    assert (
        merged["source_work_item"]["manual_review_request"]
        == marked.json()["manual_review_request"]
    )
    assert _audit(postgres_engine)["result"] == "pass"
