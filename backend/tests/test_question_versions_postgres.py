"""A rewritten question stays in place, retains its prose, and binds answers to a version."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .test_human_gates_postgres import (
    collection,
    create_work,
    gate_path,
    gate_request,
    resolution_payload,
)


def revise(api, path, gate, question, **overrides):
    payload = {
        **gate_request(operation_id=uuid4(), question=question),
        "gate_id": gate["id"],
        "expected_question_version": gate["question_version"],
        **overrides,
    }
    return api.post(path, json=payload), payload


@pytest.mark.postgres
def test_rewrite_keeps_one_queue_item_and_replays_exactly(api, project, work_payload):
    work = create_work(api, project, work_payload, title="Choose a deployment window")
    path = gate_path(project, work)
    original_payload = gate_request(operation_id=uuid4(), question="Deploy Monday or Tuesday?")
    original = api.post(path, json=original_payload).json()
    assert original["question_version"] == 1
    assert original["previous_questions"] == []
    second = api.post(path, json=gate_request(question="Separate decision")).json()
    rewritten, payload = revise(api, path, original, "Monday is unavailable. Deploy Tuesday?")
    assert rewritten.status_code == 201, rewritten.text
    current = rewritten.json()
    assert current["id"] == original["id"]
    assert current["created_at"] == original["created_at"]
    assert current["question_version"] == 2
    assert current["previous_questions"][0]["question"] == original["question"]
    assert current["previous_questions"][0]["context_revision"] == original[
        "requested_context_revision"
    ]
    latest, _ = revise(api, path, current, "Tuesday is confirmed available. Proceed Tuesday?")
    assert latest.status_code == 201, latest.text
    assert [v["question"] for v in latest.json()["previous_questions"]] == [
        original["question"], current["question"]
    ]
    queue = api.get(f"/api/v1/projects/{project['id']}/human-attention").json()
    assert queue["total"] == 2
    assert [item["gate"]["id"] for item in queue["items"]] == [original["id"], second["id"]]
    assert queue["items"][0]["gate"] == latest.json()
    assert api.post(path, json=payload).json() == current
    assert api.post(path, json=original_payload).json() == original
    context = api.get(f"{collection(project)}/{work['work_item']['id']}/context").json()
    assert next(g for g in context["unresolved_gates"] if g["id"] == original["id"])[
        "question_version"
    ] == 3


@pytest.mark.postgres
def test_rewrites_and_answers_reject_stale_question_versions(api, project, work_payload):
    work = create_work(api, project, work_payload, title="Question race")
    path = gate_path(project, work)
    original = api.post(path, json=gate_request()).json()
    rewritten, _ = revise(api, path, original, "A revised decision?")
    current = rewritten.json()
    stale, _ = revise(api, path, original, "A stale competing rewrite?")
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "gate_question_changed"
    answer = resolution_payload(revision=current["current_context_revision"], operation_id=uuid4())
    stale_answer = api.post(f"{path}/{original['id']}/resolve", json=answer)
    assert stale_answer.status_code == 409
    assert stale_answer.json()["detail"]["code"] == "gate_question_changed"
    answer.update(expected_question_version=2, client_operation_id=str(uuid4()))
    resolved = api.post(f"{path}/{original['id']}/resolve", json=answer)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["question"] == current["question"]
    assert resolved.json()["previous_questions"] == current["previous_questions"]
    refused, _ = revise(api, path, current, "Cannot edit a resolved question")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "gate_already_resolved"
    assert api.post(f"{path}/{original['id']}/resolve", json=answer).json() == resolved.json()


@pytest.mark.postgres
def test_question_history_is_append_only_at_database_boundary(
    api, project, work_payload, postgres_engine
):
    work = create_work(api, project, work_payload, title="Retained question prose")
    path = gate_path(project, work)
    original = api.post(path, json=gate_request()).json()
    response, _ = revise(api, path, original, "Updated prose")
    assert response.status_code == 201, response.text
    for assignment in (
        "question_revisions = '[]'::jsonb",
        "question_revisions = jsonb_set(question_revisions, '{0,question}', '\"Replaced\"')",
        "question = 'Overwritten original'",
    ):
        with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
            connection.execute(text(f"UPDATE work_gates SET {assignment} WHERE id = :id"),
                               {"id": original["id"]})


@pytest.mark.postgres
def test_question_migration_preserves_original_prose_and_refuses_used_rollback(
    api, project, work_payload, postgres_engine
):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    work = create_work(api, project, work_payload, title="Original question survives upgrade")
    path = gate_path(project, work)
    original = api.post(path, json=gate_request(question="  Keep exact prose.\r\n")).json()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0029_artifact_links_sensitive")
        assert connection.scalar(text("SELECT question FROM work_gates")) == original["question"]
        command.upgrade(config, "head")
    assert api.get(path).json()["items"][0] == original
    revised, _ = revise(api, path, original, "Revised prose also stays exact.\r\n")
    assert revised.status_code == 201, revised.text
    with pytest.raises(RuntimeError, match="Question versions or receipts exist"):
        with postgres_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0029_artifact_links_sensitive")
    assert api.get(path).json()["items"][0] == revised.json()


@pytest.mark.postgres
def test_competing_question_rewrites_have_one_winner(api, project, work_payload):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    work = create_work(api, project, work_payload, title="Concurrent question authors")
    path = gate_path(project, work)
    original = api.post(path, json=gate_request()).json()
    barrier = Barrier(2)

    def rewrite(question):
        barrier.wait(timeout=5)
        return revise(api, path, original, question)[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(rewrite, question) for question in ("First rewrite?", "Second?")]
        responses = [future.result(timeout=15) for future in futures]
    assert sorted(response.status_code for response in responses) == [201, 409]
    current = api.get(path).json()["items"][0]
    assert current["question_version"] == 2
    assert current["previous_questions"][0]["question"] == original["question"]
    assert current == next(response.json() for response in responses if response.status_code == 201)


@pytest.mark.postgres
def test_rewriting_a_question_preserves_a_human_hold(api, project, work_payload):
    work = create_work(api, project, work_payload, title="Held work with a changing question")
    path = gate_path(project, work)
    original = api.post(path, json=gate_request()).json()
    deferred = api.post(f"{collection(project)}/{work['work_item']['id']}/defer",
                        json={"expected_version": 1})
    assert deferred.status_code == 200, deferred.text
    rewritten, _ = revise(api, path, original, "Updated facts for the decision while work is held.")
    assert rewritten.status_code == 201, rewritten.text
    assert rewritten.json()["question_version"] == 2
    queue = api.get(f"/api/v1/projects/{project['id']}/human-attention").json()
    assert queue["items"][0]["summary"]["work_item"]["status"] == "deferred"
    assert queue["items"][0]["gate"]["id"] == original["id"]
