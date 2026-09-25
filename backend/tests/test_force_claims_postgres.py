"""Lost-token recovery rotates ownership once, without weakening other claim guards."""

import io
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from mnemonic_backup.archive import restore_project

from .code_review_fixtures import claim_review, mandatory, result_payload, result_url
from .report_fixtures import reported
from .test_artifact_extraction_migration_postgres import migrate
from .test_human_gates_postgres import gate_request
from .test_leases_postgres import claim_payload, create_work, expire_lease, item_path
from .test_progress_lease_postgres import progress_request, retained
from .test_project_backup_archive import _export
from .test_relationships_postgres import add_relationship, relationship_payload

pytestmark = pytest.mark.postgres


def force_payload(request_id="recovered", **changes):
    return {**claim_payload(request_id), "force": True, "session_transcript": None, **changes}


def assert_error(response, code):
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == code


@pytest.mark.parametrize("operation", ["claim", "claim-and-recall"])
@pytest.mark.parametrize("session", ["lease-session", "another-session"])
def test_force_claim_rotates_active_capability_and_replays_once(
    api, project, work_payload, postgres_engine, operation, session,
):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    original = api.post(path + "/claim", json=claim_payload("original")).json()
    before = retained(postgres_engine, work["id"])
    payload = force_payload(holder_session_id=session, lease_minutes=45)
    response = api.post(f"{path}/{operation}", json=payload)
    assert response.status_code == 200, response.text
    receipt = response.json()["lease"] if operation == "claim-and-recall" else response.json()
    after = retained(postgres_engine, work["id"])
    assert receipt["lease_token"] != original["lease_token"]
    assert after["lease_generation_id"] != before["lease_generation_id"]
    assert after["holder_session_id"] == session
    assert (after["expires_at"] - after["acquired_at"]).total_seconds() == 45 * 60
    assert api.post(path + "/claim", json=payload).json() == receipt
    assert retained(postgres_engine, work["id"]) == after
    assert_error(api.post(path + "/claim", json=claim_payload("original")), "lease_held")
    events = api.get(path + "/events", params={"event_type": "work_claimed"}).json()
    assert events["total"] == 2
    assert original["lease_token"] not in str(events)
    assert receipt["lease_token"] not in str(events)
    assert api.get(path).json()["work_item"] == work
    for operation in ("renew-claim", "release-claim"):
        assert_error(api.post(path + "/" + operation, json={
            "lease_token": original["lease_token"],
        }), "lease_token_mismatch")
    route, progress = progress_request("events")
    assert_error(api.post(path + "/" + route, json={
        **progress, "lease_token": original["lease_token"], "client_operation_id": str(uuid4()),
    }), "lease_token_mismatch")
    close = reported({
        "expected_version": work["version"], "lease_token": original["lease_token"],
        "checkpoint": {"prompt": "Finished recovery testing.", "source_client": "codex",
                       "source_session_id": session},
    })
    assert_error(api.post(path + "/complete", json=close), "lease_token_mismatch")
    close.update(lease_token=receipt["lease_token"], client_operation_id=str(uuid4()))
    completed = api.post(path + "/complete", json=close)
    assert completed.status_code == 200, completed.text


@pytest.mark.parametrize("change", [
    {"force": False}, {"lease_minutes": 15}, {"holder_session_id": "changed"},
    {"session_transcript": {"client": "codex", "path": "/missing/rollout.jsonl"}},
])
def test_force_retry_rejects_changed_arguments(api, project, work_payload, postgres_engine, change):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    payload = force_payload()
    assert api.post(path + "/claim", json=payload).status_code == 200
    before = retained(postgres_engine, work["id"])
    assert_error(api.post(path + "/claim", json={**payload, **change}), "claim_request_mismatch")
    assert retained(postgres_engine, work["id"]) == before


def test_force_requires_a_new_request_for_an_ordinary_active_claim(api, project, work_payload):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    payload = claim_payload("original")
    original = api.post(path + "/claim", json=payload).json()
    assert_error(api.post(path + "/claim", json={**payload, "force": True}),
                 "claim_request_mismatch")
    assert api.post(path + "/claim", json=payload).json() == original


@pytest.mark.parametrize("end", ["expiry", "release", "replacement"])
def test_old_force_request_never_reacquires_after_its_generation_ends(
    api, project, work_payload, postgres_engine, end,
):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    payload = force_payload()
    original = api.post(path + "/claim", json=payload).json()
    if end == "expiry":
        expire_lease(postgres_engine, work["id"])
    elif end == "release":
        assert api.post(path + "/release-claim", json={
            "lease_token": original["lease_token"],
        }).status_code == 200
    else:
        assert api.post(path + "/claim", json=force_payload("replacement")).status_code == 200
    before = api.get(path, params={"status_only": True}).json()
    assert_error(api.post(path + "/claim", json=payload), "claim_request_expired")
    assert api.get(path, params={"status_only": True}).json() == before


@pytest.mark.parametrize("guard", ["gate", "blocker", "deferred"])
def test_force_does_not_bypass_fresh_claim_eligibility(
    api, project, work_payload, postgres_engine, guard,
):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    if guard == "deferred":
        deferred = api.post(path + "/defer", json={"expected_version": 1})
        assert deferred.status_code == 200, deferred.text
        code = "work_not_pending"
    else:
        assert api.post(path + "/claim", json=claim_payload("original")).status_code == 200
        before = retained(postgres_engine, work["id"])
        if guard == "gate":
            assert api.post(path + "/gates", json=gate_request()).status_code == 201
            code = "work_gated"
        else:
            blocker = create_work(api, project, work_payload, title="Blocker")["work_item"]
            add_relationship(api, project, relationship_payload(blocker, work, "blocks"))
            code = "work_blocked"
    assert_error(api.post(path + "/claim", json=force_payload()), code)
    if guard != "deferred":
        assert retained(postgres_engine, work["id"]) == before


@pytest.mark.parametrize("invalid", [{"lease_minutes": 121}, {
    "session_transcript": {"client": "codex", "path": "/missing/rollout.jsonl"},
}])
def test_failed_force_claim_preserves_the_original_lease_and_events(
    api, project, work_payload, postgres_engine, invalid,
):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    assert api.post(path + "/claim", json=claim_payload("original")).status_code == 200
    before = retained(postgres_engine, work["id"])
    events = api.get(path + "/events").json()
    response = api.post(path + "/claim", json=force_payload(**invalid))
    assert response.status_code in (409, 422), response.text
    assert retained(postgres_engine, work["id"]) == before
    assert api.get(path + "/events").json() == events
    # Failed attempts do not reserve the request ID.
    assert api.post(path + "/claim", json=force_payload()).status_code == 200


@pytest.mark.parametrize("identical", [True, False])
def test_concurrent_force_claims_serialize_and_old_retries_do_not_steal_back(
    api, project, work_payload, postgres_engine, identical,
):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    assert api.post(path + "/claim", json=claim_payload("original")).status_code == 200
    barrier = Barrier(2)
    payloads = [force_payload("same" if identical else f"force-{i}") for i in range(2)]

    def attempt(payload):
        barrier.wait(timeout=10)
        return api.post(path + "/claim", json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(attempt, payloads))
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    current = retained(postgres_engine, work["id"])
    for payload, response in zip(payloads, responses, strict=True):
        replay = api.post(path + "/claim", json=payload)
        if response.json()["lease_token"] == current["lease_token"]:
            assert replay.json() == response.json()
        else:
            assert_error(replay, "claim_request_expired")
    assert retained(postgres_engine, work["id"]) == current
    total = api.get(path + "/events", params={"event_type": "work_claimed"}).json()["total"]
    assert total == (2 if identical else 3)


@pytest.mark.parametrize("mode", ["cold", "warm"])
def test_force_review_preserves_scope_and_rejects_old_result(
    api, project, work_payload, checkpoint_fields, mode,
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    original = claim_review(api, project, completion, checkpoint_fields, mode=mode)
    path = item_path(project, completion["work_item"])
    payload = force_payload(
        purpose="code_review", code_review_id=original["code_review_id"], mode=mode,
        holder_client="review-client", holder_session_id="review-session",
    )
    if mode == "cold":
        assert_error(api.post(path + "/claim-and-recall", json=payload), "lease_purpose_mismatch")
    response = api.post(path + ("/claim" if mode == "cold" else "/claim-and-recall"), json=payload)
    assert response.status_code == 200, response.text
    receipt = response.json() if mode == "cold" else response.json()["lease"]
    assert receipt["scope_sha256"] == original["scope_sha256"]
    assert receipt["code_review_version"] == original["code_review_version"]
    assert receipt["lease_generation_id"] != original["lease_generation_id"]
    rejected = api.post(result_url(project, completion), json=result_payload(completion, original))
    assert_error(rejected, "lease_token_mismatch")
    accepted = api.post(result_url(project, completion), json=result_payload(completion, receipt))
    assert accepted.status_code == 200, accepted.text


def test_force_journal_survives_backup_and_prevents_unsafe_downgrade(
    api, project, work_payload, postgres_engine,
):
    migrate(postgres_engine, "0047_artifact_transfer", downgrade=True)
    migrate(postgres_engine, "head")
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    payload = force_payload()
    original = api.post(path + "/claim", json=payload).json()
    replacement = api.post(path + "/claim", json=force_payload("replacement")).json()
    content = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content))
    assert_error(api.post(path + "/claim", json=payload), "claim_request_expired")
    assert api.post(path + "/claim", json=force_payload("replacement")).json() == replacement
    assert original["lease_token"] != replacement["lease_token"]
    with pytest.raises(RuntimeError, match="Retained force claims"):
        migrate(postgres_engine, "0047_artifact_transfer", downgrade=True)
    with postgres_engine.connect() as connection:
        head = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert head == "0048_force_claims"
