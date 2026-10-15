"""Browser writes retain the same PostgreSQL receipts, history, and publication rules."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import text

from mnemonic_api.application.browser_responses import BrowserResponse
from mnemonic_api.application.routes import work_items
from mnemonic_api.live_sync import MutationEvent

from .code_review_fixtures import configure
from .report_fixtures import reported

pytestmark = pytest.mark.postgres
HEADERS = {"Host": "localhost:3000", "Origin": "http://localhost:3000"}
ACTOR = {"actor_client": "dashboard", "actor_session_id": "browser-mutation-tests"}
CHECKPOINT = {
    "prompt": "Human-authored browser regression context.",
    "source_client": "dashboard",
    "source_session_id": ACTOR["actor_session_id"],
}
DOMAIN_TABLES = (
    "work_items", "checkpoints", "work_events", "work_relationships", "work_item_moves",
    "work_duplicate_merges", "work_gates", "work_agent_follow_ups",
    "work_agent_follow_up_answers", "job_completion_reports", "project_activity",
    "client_operations",
)


@dataclass
class Case:
    kind: str
    path: str
    body: dict
    status: int = 200
    method: str = "POST"
    publishes: bool = True


@dataclass
class Seed:
    api: object
    project: dict
    work_payload: dict

    @property
    def project_path(self):
        return f"/api/v1/projects/{self.project['id']}"

    def create(self, title="Browser mutation target"):
        response = self.api.post(
            self.project_path + "/work-items",
            json={**self.work_payload, "title": title, "initial_checkpoint": CHECKPOINT},
        )
        assert response.status_code == 201, response.text
        return response.json()["work_item"]

    def work_path(self, work):
        return self.project_path + f"/work-items/{work['id']}"

    def complete(self, work):
        payload = reported({"expected_version": work["version"], "checkpoint": CHECKPOINT})
        payload["job_completion_report"]["prompt_revision"] = self.api.get(
            self.project_path + "/settings"
        ).json()["revision"]
        response = self.api.post(
            self.work_path(work) + "/complete",
            json=payload,
        )
        assert response.status_code == 200, response.text
        return response.json()


def create_case(seed):
    return Case("create_work", seed.project_path + "/work-items", {
        "title": "New browser work", "summary": "Retain one creation and initial checkpoint.",
        "priority": 30, "status": "pending", "initial_checkpoint": CHECKPOINT,
    }, status=201)


def checkpoint_case(seed):
    work = seed.create()
    return Case("add_checkpoint", seed.work_path(work) + "/checkpoints",
                {**CHECKPOINT, "kind": "progress"}, status=201)


def event_case(seed):
    work = seed.create()
    return Case("append_event", seed.work_path(work) + "/events", {
        "event_type": "progress", "body": "One retained progress event.",
        "metadata": {}, "actor": ACTOR,
    }, status=201)


def update_case(seed):
    work = seed.create()
    return Case("update_work", seed.work_path(work), {
        "expected_version": work["version"], "title": "Updated browser work", "actor": ACTOR,
    }, method="PATCH")


def complete_case(seed):
    work = seed.create()
    return Case("complete_work", seed.work_path(work) + "/complete", reported({
        "expected_version": work["version"], "checkpoint": CHECKPOINT,
    }))


def defer_case(seed):
    work = seed.create()
    return Case("defer_work", seed.work_path(work) + "/defer", {
        "expected_version": work["version"], "actor": ACTOR,
    })


def delete_case(seed):
    work = seed.create()
    return Case("delete_work", seed.work_path(work) + "/delete", {
        "expected_version": work["version"], "actor": ACTOR, "subagent_transcripts": None,
    })


def move_case(seed):
    work = seed.create()
    target = seed.api.post("/api/v1/projects", json={"name": "Browser move destination"})
    assert target.status_code == 201, target.text
    return Case("move_work", seed.work_path(work) + "/move", {
        "expected_version": work["version"], "target_project_id": target.json()["id"],
        "actor": ACTOR,
    })


def merge_case(seed):
    source = seed.create("Browser merge source")
    destination = seed.create("Browser merge destination")
    source_context = seed.api.get(seed.work_path(source) + "/context").json()
    destination_context = seed.api.get(seed.work_path(destination) + "/context").json()
    return Case("merge_work", seed.work_path(source) + "/merge", {
        "destination_work_item_id": destination["id"],
        "reviewed_source_revision": source_context["merge_review_revision"],
        "reviewed_destination_revision": destination_context["merge_review_revision"],
        "rationale": "Both entries describe the same browser work.",
        "merged_by_client": "dashboard", "merged_by_session_id": ACTOR["actor_session_id"],
        "merged_by_model": None, "subagent_transcripts": None,
    }, status=201)


def relationship_case(seed):
    source = seed.create("Browser relationship source")
    target = seed.create("Browser relationship target")
    return Case("add_relationship", seed.project_path + "/relationships", {
        "relationship_type": "related", "source_work_item_id": source["id"],
        "target_work_item_id": target["id"], "created_by_client": "dashboard",
        "created_by_session_id": ACTOR["actor_session_id"],
    })


def removal_case(seed):
    creation = relationship_case(seed)
    response = seed.api.post(creation.path, json=creation.body)
    assert response.status_code == 200, response.text
    return Case("remove_relationship", creation.path + "/" + response.json()["relationship"]["id"],
                {"actor": ACTOR}, method="DELETE")


def gate_case(seed):
    work = seed.create()
    path = seed.work_path(work) + "/gates"
    gate = seed.api.post(path, json={
        "question": "Which browser disposition should be retained?",
        "requested_by_client": "test-agent", "requested_by_session_id": "browser-gate-origin",
    })
    assert gate.status_code == 201, gate.text
    return Case("resolve_human_input", path + f"/{gate.json()['id']}/resolve", {
        "resolution": "Keep the existing configuration.", "resolved_by_client": "dashboard",
        "resolved_by_session_id": ACTOR["actor_session_id"],
        "reviewed_context_revision": gate.json()["current_context_revision"],
        "expected_question_version": 1,
    })


def dismissal_case(seed):
    completed = seed.complete(seed.create())
    path = seed.project_path + f"/job-completion-reports/{completed['job_completion_report']['id']}"
    return Case("dismiss_job_completion_report", path + "/dismiss", {"actor": ACTOR},
                publishes=False)


def report_follow_up_case(seed):
    completed = seed.complete(seed.create())
    path = seed.project_path + f"/job-completion-reports/{completed['job_completion_report']['id']}"
    return Case("create_job_completion_report_follow_up", path + "/follow-ups", {
        "title": "Browser report follow-up", "summary": "Track the follow-up independently.",
        "actor": ACTOR, "initial_checkpoint": CHECKPOINT,
    }, status=201, publishes=False)


def answer_case(seed):
    configure(seed.api, seed.project, code_review_optional_min_priority=0)
    completed = seed.complete(seed.create())
    follow_up = completed["agent_follow_ups"][0]
    return Case("respond_to_work_follow_up",
                seed.work_path(completed["work_item"]) +
                f"/agent-follow-ups/{follow_up['id']}/answer", {
        "expected_follow_up_version": follow_up["version"], "actor": ACTOR,
        "answer": {"kind": "code_review_recommendation", "recommend_review": False,
                   "rationale": "Focused validation covers this change."},
    })


CASES = [
    create_case, checkpoint_case, event_case, update_case, complete_case, defer_case,
    delete_case, move_case, merge_case, relationship_case, removal_case, gate_case,
    dismissal_case, report_follow_up_case, answer_case,
]


def snapshot(engine):
    """Compare all retained domain rows without exposing their contents on failure."""
    with engine.connect() as connection:
        return {
            table: tuple(connection.execute(text(
                f"SELECT count(*), md5(string_agg(to_jsonb(t)::text, '' "
                f"ORDER BY to_jsonb(t)::text)) FROM {table} AS t"
            )).one())
            for table in DOMAIN_TABLES
        }


def receipt(engine, operation_id):
    with engine.connect() as connection:
        return dict(connection.execute(text(
            "SELECT operation_kind, response_status, response_body, mutation_applied "
            "FROM client_operations WHERE client_operation_id = :operation_id"
        ), {"operation_id": operation_id}).mappings().one())


def changed_intent(body):
    body = dict(body)
    if "initial_checkpoint" in body:
        body["title"] = "Changed browser title"
    elif "actor" in body:
        body["actor"] = {**body["actor"], "actor_session_id": "changed-browser-session"}
    elif "checkpoint" in body:
        body["checkpoint"] = {**body["checkpoint"], "prompt": "Changed completion context."}
    elif "prompt" in body:
        body["prompt"] = "Changed checkpoint context."
    elif "rationale" in body:
        body["rationale"] = "Changed merge rationale."
    elif "resolution" in body:
        body["resolution"] = "Changed answer."
    else:
        body["created_by_session_id"] = "changed-browser-session"
    return body


@pytest.mark.parametrize("build", CASES, ids=lambda build: build.__name__.removesuffix("_case"))
def test_browser_mutations_recover_lost_responses_and_share_machine_receipts(
    api, project, work_payload, postgres_engine, monkeypatch, build,
):
    case = build(Seed(api, project, work_payload))
    case.body["client_operation_id"] = str(uuid4())
    browser_path = case.path.replace("/api/v1/", "/api/mnemonic/", 1)
    before = snapshot(postgres_engine)
    publications = []

    async def publish(event):
        publications.append(event)

    original_response = BrowserResponse.response

    def lose_response(self):
        monkeypatch.setattr(BrowserResponse, "response", original_response)
        raise RuntimeError("synthetic response loss after transaction and publication")

    monkeypatch.setattr(api.app.state.live_sync_hub, "publish", publish)
    monkeypatch.setattr(BrowserResponse, "response", lose_response)
    lost = api.request(case.method, browser_path, json=case.body, headers=HEADERS)
    assert lost.status_code == 502, lost.text
    retained = receipt(postgres_engine, case.body["client_operation_id"])
    assert retained["operation_kind"] == case.kind
    assert retained["response_status"] == case.status
    assert retained["mutation_applied"] is True
    committed = snapshot(postgres_engine)
    assert committed["client_operations"][0] == before["client_operations"][0] + 1
    assert committed["project_activity"][0] > before["project_activity"][0]
    expected_publications = [MutationEvent("work-items")] if case.publishes else []
    assert publications == expected_publications

    for path, headers in [(browser_path, HEADERS), (case.path, {})]:
        replay = api.request(case.method, path, json=case.body, headers=headers)
        assert replay.status_code == case.status, replay.text
        assert replay.json() == retained["response_body"]
        assert snapshot(postgres_engine) == committed
    conflict = api.request(
        case.method, browser_path, json=changed_intent(case.body), headers=HEADERS,
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "client_operation_conflict"
    assert snapshot(postgres_engine) == committed
    assert publications == expected_publications


def test_browser_relationship_noop_preserves_activity_and_suppresses_publication(
    api, project, work_payload, postgres_engine, monkeypatch,
):
    case = relationship_case(Seed(api, project, work_payload))
    path = case.path.replace("/api/v1/", "/api/mnemonic/", 1)
    case.body["client_operation_id"] = str(uuid4())
    publications = []

    async def publish(event):
        publications.append(event)

    monkeypatch.setattr(api.app.state.live_sync_hub, "publish", publish)
    first = api.post(path, json=case.body, headers=HEADERS)
    assert first.status_code == 200 and first.json()["created"] is True, first.text
    committed = snapshot(postgres_engine)
    noop_body = {**case.body, "client_operation_id": str(uuid4())}
    noop = api.post(path, json=noop_body, headers=HEADERS)
    assert noop.status_code == 200 and noop.json()["created"] is False, noop.text
    after_noop = snapshot(postgres_engine)
    assert {k: v for k, v in after_noop.items() if k != "client_operations"} == {
        k: v for k, v in committed.items() if k != "client_operations"
    }
    assert receipt(postgres_engine, noop_body["client_operation_id"])["mutation_applied"] is False
    assert api.post(path, json=noop_body, headers=HEADERS).json() == noop.json()
    assert snapshot(postgres_engine) == after_noop
    assert len(publications) == 1


def test_concurrent_browser_updates_apply_one_version_and_replay_the_winner(
    api, project, work_payload, postgres_engine, monkeypatch,
):
    seed = Seed(api, project, work_payload)
    work = seed.create()
    path = seed.work_path(work).replace("/api/v1/", "/api/mnemonic/", 1)
    bodies = [{
        "expected_version": work["version"], "title": f"Concurrent browser edit {index}",
        "actor": ACTOR, "client_operation_id": str(uuid4()),
    } for index in range(2)]
    before = snapshot(postgres_engine)
    publications = []

    async def publish(event):
        publications.append(event)

    monkeypatch.setattr(api.app.state.live_sync_hub, "publish", publish)
    both_admitted = Barrier(2)
    run_mutation = work_items.run_registered_mutation

    def concurrent_mutation(*args, **kwargs):
        # Both requests must pass browser admission before either starts its
        # transaction; keep the real receipt and project-lock behavior intact.
        both_admitted.wait(timeout=5)
        return run_mutation(*args, **kwargs)

    with monkeypatch.context() as simultaneous:
        simultaneous.setattr(work_items, "run_registered_mutation", concurrent_mutation)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(api.patch, path, json=body, headers=HEADERS) for body in bodies]
            responses = [future.result(timeout=10) for future in futures]

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner_body, winner = next(
        (body, response) for body, response in zip(bodies, responses, strict=True)
        if response.status_code == 200
    )
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["detail"] == {
        "code": "version_conflict",
        "message": "This work item changed. Recall it again before editing or deleting.",
        "context": {},
    }
    final = api.get(path, headers=HEADERS).json()["work_item"]
    assert final["title"] == winner.json()["title"] == winner_body["title"]
    assert final["version"] == winner.json()["version"] == work["version"] + 1
    committed = snapshot(postgres_engine)
    for table in ("work_events", "project_activity", "client_operations"):
        assert committed[table][0] == before[table][0] + 1
    assert publications == [MutationEvent("work-items")]
    assert receipt(postgres_engine, winner_body["client_operation_id"])["mutation_applied"] is True

    replay = api.patch(path, json=winner_body, headers=HEADERS)
    assert replay.status_code == 200, replay.text
    assert replay.content == winner.content
    assert snapshot(postgres_engine) == committed
    assert publications == [MutationEvent("work-items")]
