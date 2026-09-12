"""Progress liveness is transactional and receipt replays never extend ownership."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from mnemonic_api.errors import ApplicationError

from .code_review_fixtures import claim_review, mandatory
from .test_leases_postgres import claim_payload, create_work, expire_lease, item_path

pytestmark = pytest.mark.postgres


def progress_request(kind):
    if kind == "events":
        return "events", {
            "event_type": "progress", "body": "Verified concurrent cache readers.",
            "actor": {"actor_client": "claude-code", "actor_session_id": "lease-session"},
        }
    return "checkpoints", {
        "kind": kind, "prompt": "Verified concurrent cache readers.",
        "source_client": "claude-code", "source_session_id": "lease-session",
    }


def retained(engine, work_id):
    with engine.connect() as connection:
        return dict(connection.execute(text(
            "SELECT * FROM work_leases WHERE work_item_id=:id"
        ), {"id": work_id}).mappings().one())


def renewals(engine, work_id):
    with engine.connect() as connection:
        return connection.scalar(text(
            "SELECT count(*) FROM project_activity WHERE work_item_id=:id AND kind='lease_renewed'"
        ), {"id": work_id})


@pytest.mark.parametrize("kind", ["events", "context", "progress"])
def test_progress_renews_once_with_configured_ttl_and_replays_after_takeover(
    api, project, work_payload, postgres_engine, kind,
):
    api.app.state.settings.lease_ttl_seconds = 123
    work = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work)
    claim = api.post(endpoint + "/claim", json=claim_payload("progress-liveness")).json()
    before = retained(postgres_engine, work["id"])
    route, body = progress_request(kind)
    url = f"{endpoint}/{route}"
    # Even the holder's asserted client/session alone must not renew ownership.
    assert api.post(url, json=body).status_code == 201
    assert retained(postgres_engine, work["id"]) == before
    body.update(lease_token=claim["lease_token"], client_operation_id=str(uuid4()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: api.post(url, json=body), range(2)))
    assert [r.status_code for r in responses] == [201, 201], [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    after = retained(postgres_engine, work["id"])
    assert after["renewed_at"] > before["renewed_at"]
    assert (after["expires_at"] - after["renewed_at"]).total_seconds() == 123
    assert after["expires_at"] > before["expires_at"]
    for key in before.keys() - {"renewed_at", "expires_at"}:
        assert after[key] == before[key]
    assert renewals(postgres_engine, work["id"]) == 1
    assert claim["lease_token"] not in responses[0].text
    current = api.get(endpoint + "/context").json()
    assert current["work_item"]["version"] == work["version"]
    assert datetime.fromisoformat(current["readiness"]["active_lease"]["expires_at"]) == (
        after["expires_at"]
    )
    assert api.post(url, json=body).json() == responses[0].json()
    assert retained(postgres_engine, work["id"]) == after
    expire_lease(postgres_engine, work["id"])
    expired = retained(postgres_engine, work["id"])
    assert api.post(url, json=body).json() == responses[0].json()
    assert retained(postgres_engine, work["id"]) == expired
    fresh = api.post(url, json={**body, "client_operation_id": str(uuid4())})
    assert fresh.status_code == 409 and fresh.json()["detail"]["code"] == "lease_expired"
    assert retained(postgres_engine, work["id"]) == expired
    replacement = api.post(endpoint + "/claim", json=claim_payload("takeover")).json()
    assert replacement["lease_token"] != claim["lease_token"]
    taken = retained(postgres_engine, work["id"])
    assert api.post(url, json=body).json() == responses[0].json()
    stale = api.post(url, json={**body, "client_operation_id": str(uuid4())})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "lease_token_mismatch"
    assert retained(postgres_engine, work["id"]) == taken


@pytest.mark.parametrize("kind", ["events", "progress"])
def test_progress_failure_rolls_back_lease_renewal(
    api, project, work_payload, postgres_engine, monkeypatch, kind,
):
    work = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work)
    claim = api.post(endpoint + "/claim", json=claim_payload("rollback")).json()
    route, body = progress_request(kind)
    before = retained(postgres_engine, work["id"])
    before_events = api.get(endpoint + "/events").json()
    before_checkpoints = api.get(endpoint + "/checkpoints").json()

    def fail(*args, **kwargs):
        raise ApplicationError(422, "test_progress_failure", "Injected failure after renewal.")

    target = ("work_events.work_event_read" if kind == "events"
              else "work_items.stage_checkpoint_added")
    with monkeypatch.context() as patch:
        patch.setattr("mnemonic_api.services." + target, fail)
        response = api.post(f"{endpoint}/{route}", json={
            **body, "lease_token": claim["lease_token"], "client_operation_id": str(uuid4()),
        })
    assert response.status_code == 422, response.text
    assert retained(postgres_engine, work["id"]) == before
    assert renewals(postgres_engine, work["id"]) == 0
    assert api.get(endpoint + "/events").json() == before_events
    assert api.get(endpoint + "/checkpoints").json() == before_checkpoints


@pytest.mark.parametrize("kind", ["events", "progress"])
def test_progress_cannot_renew_review_capability(
    api, project, work_payload, checkpoint_fields, postgres_engine, kind,
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    claim = claim_review(api, project, completion, checkpoint_fields)
    work = completion["work_item"]
    before = retained(postgres_engine, work["id"])
    route, body = progress_request(kind)
    response = api.post(f"{item_path(project, work)}/{route}", json={
        **body, "lease_token": claim["lease_token"], "client_operation_id": str(uuid4()),
    })
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "lease_purpose_mismatch"
    assert retained(postgres_engine, work["id"]) == before
    assert renewals(postgres_engine, work["id"]) == 0
