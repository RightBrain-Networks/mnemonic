"""Transcript assertions follow the exact lease generation and atomic closeout."""

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.models import Transcript

from .report_fixtures import reported
from .test_leases_postgres import claim_payload, create_work, expire_lease, item_path

pytestmark = pytest.mark.postgres
SOURCE = {"client": "claude_code", "path": "/synthetic/session.jsonl"}
CHILD = {"client": "claude_code", "path": "/synthetic/subagents/agent-child.jsonl"}


def transcripts(engine, work_id):
    with Session(engine) as database:
        return [(row.kind, row.source_path, row.lease_generation_id, row.status)
                for row in database.scalars(select(Transcript).where(
                    Transcript.work_item_id == UUID(work_id),
                ).order_by(Transcript.kind))]


def claim(api, path, request_id="transcript-claim", source=SOURCE):
    payload = {**claim_payload(request_id), "session_transcript": source}
    response = api.post(f"{path}/claim", json=payload)
    assert response.status_code == 200, response.text
    return response.json(), payload


def test_claim_records_once_and_replay_cannot_change_source(api, project, work_payload,
                                                          postgres_engine):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    receipt, payload = claim(api, path)
    original = transcripts(postgres_engine, work["id"])
    assert len(original) == 1
    assert original[0][:2] == ("primary", SOURCE["path"])
    assert original[0][3] == "waiting"
    assert api.post(f"{path}/claim", json=payload).json() == receipt
    assert transcripts(postgres_engine, work["id"]) == original
    for changed in (None, CHILD):
        response = api.post(f"{path}/claim", json={**payload, "session_transcript": changed})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "claim_transcript_conflict"
    assert transcripts(postgres_engine, work["id"]) == original


def test_null_claim_replay_cannot_add_source_and_reclaim_preserves_generation(
    api, project, work_payload, postgres_engine,
):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    _, payload = claim(api, path, source=None)
    assert transcripts(postgres_engine, work["id"]) == []
    changed = api.post(f"{path}/claim", json={**payload, "session_transcript": SOURCE})
    assert changed.status_code == 409
    expire_lease(postgres_engine, work["id"])
    claim(api, path, "second-claim")
    first = transcripts(postgres_engine, work["id"])[0]
    expire_lease(postgres_engine, work["id"])
    claim(api, path, "third-claim", CHILD)
    rows = transcripts(postgres_engine, work["id"])
    assert len(rows) == 2
    assert len({row[2] for row in rows}) == 2
    assert first in rows


def test_done_registers_children_atomically_and_receipt_retry_does_not_duplicate(
    api, project, work_payload, postgres_engine,
):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    receipt, _ = claim(api, path)
    payload = reported({
        "expected_version": work["version"],
        "checkpoint": {"prompt": "Implemented and validated transcript indexing.",
                       "source_client": "claude-code", "source_session_id": "transcript-test"},
        "lease_token": receipt["lease_token"], "subagent_transcripts": [CHILD],
    })
    rejected = api.post(f"{path}/complete", json={**payload, "expected_version": 999})
    assert rejected.status_code == 409
    assert len(transcripts(postgres_engine, work["id"])) == 1
    response = api.post(f"{path}/complete", json=payload)
    assert response.status_code == 200, response.text
    rows = transcripts(postgres_engine, work["id"])
    assert {row[:2] for row in rows} == {("primary", SOURCE["path"]), ("subagent", CHILD["path"])}
    assert len({row[2] for row in rows}) == 1
    assert api.post(f"{path}/complete", json=payload).json() == response.json()
    assert transcripts(postgres_engine, work["id"]) == rows
    changed = {**payload, "subagent_transcripts": [SOURCE]}
    assert api.post(f"{path}/complete", json=changed).status_code == 409


@pytest.mark.parametrize("status", ["wont-do", "promoted"])
def test_retirement_and_release_register_children(api, project, work_payload, postgres_engine,
                                                status):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    receipt, _ = claim(api, path)
    payload = reported({"expected_version": work["version"], "status": status,
                        "lease_token": receipt["lease_token"], "subagent_transcripts": [CHILD]},
                       retirement=True)
    response = api.patch(path, json=payload)
    assert response.status_code == 200, response.text
    assert len(transcripts(postgres_engine, work["id"])) == 2


def test_release_noop_cannot_register_unowned_transcripts(api, project, work_payload,
                                                        postgres_engine):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    receipt, _ = claim(api, path)
    actor = {"actor_client": "claude-code", "actor_session_id": "transcript-test"}
    response = api.post(f"{path}/release-claim", json={"lease_token": receipt["lease_token"],
                        "actor": actor, "subagent_transcripts": [CHILD]})
    assert response.status_code == 200, response.text
    rows = transcripts(postgres_engine, work["id"])
    assert len(rows) == 2
    response = api.post(f"{path}/release-claim", json={"lease_token": receipt["lease_token"],
                        "actor": actor, "subagent_transcripts": [SOURCE]})
    assert response.status_code == 200
    assert response.json()["released"] is False
    assert transcripts(postgres_engine, work["id"]) == rows


@pytest.mark.parametrize("closeout", ["complete", "delete", "wont-do", "promoted"])
def test_fresh_closeout_requires_assertion_but_sparse_receipt_replays(
    api, project, work_payload, postgres_engine, closeout,
):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    receipt, _ = claim(api, path)
    payload = reported({"expected_version": work["version"],
                        "lease_token": receipt["lease_token"]}, retirement=True)
    method = api.post
    endpoint = path + "/" + closeout
    if closeout == "complete":
        payload.pop("actor")
        payload["checkpoint"] = {"prompt": "Verified work closeout.",
                                 "source_client": "claude-code", "source_session_id": "test"}
    elif closeout == "delete":
        payload.pop("job_completion_report")
    else:
        payload["status"] = closeout
        method, endpoint = api.patch, path
    sparse = {key: value for key, value in payload.items() if key != "subagent_transcripts"}
    rejected = method(endpoint, json=sparse)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "subagent_transcripts_required"
    assert api.get(path).json()["work_item"]["version"] == work["version"]
    assert len(transcripts(postgres_engine, work["id"])) == 1
    renewed = api.post(path + "/renew-claim", json={"lease_token": receipt["lease_token"]})
    assert renewed.status_code == 200
    accepted = method(endpoint, json=payload)
    assert accepted.status_code == 200, accepted.text
    replay = method(endpoint, json=sparse)
    assert replay.status_code == 200, replay.text
    assert replay.json() == accepted.json()


@pytest.mark.parametrize("decision", ["done", "wont-do", "promoted", "deferred"])
def test_human_review_decision_releases_transcript_lease_and_preserves_done_work(
    api, project, work_payload, checkpoint_fields, postgres_engine, tmp_path, decision,
):
    from .code_review_fixtures import mandatory
    from .test_review_decisions_postgres import decide
    from .test_transcript_indexing_postgres import Parser, run

    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    work, review = completion["work_item"], completion["code_review_request"]
    path = item_path(project, work)
    source = tmp_path / "review.jsonl"
    source.write_text('{"role":"assistant","content":"Review transcript for human disposition"}\n')
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    claimed = api.post(path + "/claim", json={
        **claim_payload("human-review-transcript", client="review-client",
                        session="review-session"),
        "purpose": "code_review", "code_review_id": review["id"], "mode": "cold",
        "session_transcript": {"client": "claude-code", "path": str(source)},
    })
    assert claimed.status_code == 200, claimed.text
    assert not run(api, Parser())
    before = api.get(path + "/checkpoints").json()
    changed, payload = decide(api, project, work, review, 0, decision)
    assert changed.status_code == 200, changed.text
    assert changed.json()["status"] == "done"
    assert changed.json()["review_decision"]["status"] == decision
    assert api.get(path + "/checkpoints").json() == before
    assert api.patch(path, json=payload).json() == changed.json()
    assert run(api, Parser())
    records = transcripts(postgres_engine, work["id"])
    assert len(records) == 1
    assert records[0][0] == "primary" and records[0][3] == "ready"
    assert api.get(path).json()["work_item"]["status"] == "done"
    assert not api.get(path + "/context").json()["readiness"]["has_active_lease"]
