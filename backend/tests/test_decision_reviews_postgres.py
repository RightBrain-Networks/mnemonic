"""Automatic investigations retain pinned decisions and use ordinary work lifecycles."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import mnemonic_api.application.mutations as registered_mutations
import mnemonic_api.services.decision_reviews as decision_reviews
from mnemonic_api.errors import ApplicationError
from mnemonic_api.prompt_storage import PromptStorage

from .test_decision_impact_postgres import changed, decision, dependency
from .test_duplicate_handling_postgres import merge_work
from .test_human_gates_postgres import create_work, gate_path, gate_request, resolution_payload
from .test_knowledge_postgres import domain, path, put, put_body
from .test_manual_review_requests_postgres import request_review
from .test_project_reports_postgres import close
from .test_prompt_library_postgres import save_prompt
from .test_work_item_moves_postgres import _wait_for_application_locks

pytestmark = pytest.mark.postgres


def settings(api, project, **changes):
    endpoint = f"/api/v1/projects/{project['id']}/settings"
    current = api.get(endpoint).json()
    result = api.patch(endpoint, json={"expected_revision": current["revision"], **changes})
    assert result.status_code == 200, result.text
    return result.json()


def reviews(api, project):
    result = api.get(f"/api/v1/projects/{project['id']}/tasks", params={"status": "all"})
    assert result.status_code == 200, result.text
    return [item for item in result.json()["items"] if item.get("decision_review_revision_id")]


def context(api, project, work_id):
    result = api.get(f"/api/v1/projects/{project['id']}/work-items/{work_id}/context")
    assert result.status_code == 200, result.text
    return result.json()


def assess(api, project, assessment_id, reference, disposition="still-valid", remediation=None):
    endpoint = path(project) + f"/assessments/{assessment_id}"
    current = api.get(endpoint).json()
    result = api.post(endpoint + "/dispositions", json={
        "candidate_reference": reference, "expected_version": current["version"],
        "disposition": disposition, "remediation_work_item_id": remediation,
        "reason": "Inspected the pinned decision and its recorded implementation evidence.",
        "actor": {"actor_client": "codex", "actor_session_id": "impact-investigation"},
        "client_operation_id": str(uuid4()),
    })
    assert result.status_code == 201, result.text


def finish(api, project, work, checkpoint_fields, status="done"):
    revision = api.get(f"/api/v1/projects/{project['id']}/settings").json()["revision"]
    return close(api, project, work, checkpoint_fields, status=status, job_completion_report={
        "summary": "Investigated the pinned decision transition and tracked all findings.",
        "fyi_items": [], "prompt_revision": revision,
        "decision_provenance": {"disposition": "not-code", "reason": "Investigation only."},
    }, **({"checkpoint": {**checkpoint_fields, "affected_paths": [], "verified_against": None}}
           if status == "done" else {}))[0]


def test_decision_reviews_opt_in_priority_and_no_backfill(api, project):
    target = domain(api, project)
    first = decision(api, project, target, "Initial policy")
    second = changed(api, project, target, first)
    assert reviews(api, project) == []
    settings(api, project, automatic_decision_reviews=True)
    assert reviews(api, project) == []
    decision(api, project, target, "Independent initial policy")
    assert reviews(api, project) == []
    third = changed(api, project, target, second, "Policy after opt in")
    spawned = reviews(api, project)
    assert len(spawned) == 1
    work = context(api, project, spawned[0]["work_item_id"])["work_item"]
    assert work["priority"] == 40 and work["status"] == "pending"
    settings(api, project, automatic_decision_reviews=False)
    changed(api, project, target, third, "Policy after disabling")
    assert reviews(api, project) == spawned


def test_revision_creates_one_pinned_standard_task_with_custom_prompt(
    api, project, checkpoint_fields,
):
    base = f"/api/v1/projects/{project['id']}"
    settings(api, project, automatic_decision_reviews=True, decision_review_priority=32)
    saved = save_prompt(
        api, base, "decision-impact-review",
        "Investigate $WORK_ITEM_ID in $PROJECT_NAME at priority $WORK_ITEM_PRIORITY. "
        "Do not change code; use create_work for remediation.",
    )
    target = domain(api, project)
    before = decision(api, project, target, "Old implementation policy")
    payload = put_body(target, "Replacement implementation policy",
                       decision_id=before["decision_id"], expected_revision=0)
    result = put(api, project, payload)
    after = result["decision"]
    assert put(api, project, payload) == result
    assert not put(api, project, {**payload, "client_operation_id": str(uuid4())})["created"]
    spawned, = reviews(api, project)
    assert spawned["kind"] == "work_item" and spawned["review_state"] is None
    work_id = spawned["work_item_id"]
    full = context(api, project, work_id)
    work, initial = full["work_item"], full["initial_checkpoint"]
    marker = work["decision_review_revision_id"]
    assert marker == spawned["decision_review_revision_id"]
    assert work["priority"] == 32 and work["status"] == "pending"
    assert work_id in initial["prompt"] and "$WORK_ITEM_ID" not in initial["prompt"]
    assert before["reference"] in initial["prompt"] and after["reference"] in initial["prompt"]
    metadata = initial["source_metadata"]
    assert metadata["prompt_revision"] == saved["revision"]
    assert metadata["decision_revision_id"] == marker
    assert len(metadata["assessment_ids"]) == 1
    assert metadata["assessment_ids"][0] in initial["prompt"]
    ready = api.get(base + "/ready-work").json()
    assert ready["items"][0]["work_item"]["decision_review_revision_id"] == marker
    search = api.post(base + "/search", json={
        "facets": ["work_items"], "q": "Decision impact review",
    })
    assert search.status_code == 200, search.text
    assert marker in search.text
    saved_prompt = initial["prompt"]
    save_prompt(api, base, "decision-impact-review", "Later instructions")
    assert context(api, project, work_id)["initial_checkpoint"]["prompt"] == saved_prompt
    claimed = api.post(base + f"/work-items/{work_id}/claim", json={
        "holder_client": checkpoint_fields["source_client"],
        "holder_session_id": checkpoint_fields["source_session_id"],
        "claim_request_id": str(uuid4()), "session_transcript": None,
    })
    assert claimed.status_code == 200, claimed.text
    active = context(api, project, work_id)["readiness"]["active_lease"]
    assert active.get("purpose", "implementation") == "implementation"
    report_prompt = api.post(base + "/prompts/job-completion-report/render",
                             json={"work_item_id": work_id}).json()
    completed, completion_intent = close(
        api, project, work, checkpoint_fields, job_completion_report={
        "summary": "Compared the pinned decision revisions and inspected affected code.",
        "fyi_items": [],
        "prompt_revision": report_prompt["prompt_revision"],
        "decision_provenance": {"disposition": "not-code", "reason": "Investigation only."},
    }, checkpoint={**checkpoint_fields, "affected_paths": [], "verified_against": None},
        lease_token=claimed.json()["lease_token"])
    assert completed.status_code == 409, completed.text
    assert completed.json()["detail"]["code"] == "decision_impact_review_incomplete"
    assert completed.json()["detail"]["context"]["missing_dispositions"] == 1
    assess(api, project, metadata["assessment_ids"][0], before["reference"])
    completed = api.post(base + f"/work-items/{work_id}/complete", json=completion_intent)
    assert completed.status_code == 200, completed.text
    assert completed.json()["work_item"]["status"] == "done"
    assert completed.json().get("code_review_request") is None
    assess(api, project, metadata["assessment_ids"][0], before["reference"], "change-required")
    replay = api.post(base + f"/work-items/{work_id}/complete", json=completion_intent)
    assert replay.status_code == 200 and replay.json() == completed.json()
    requested, _ = request_review(
        api, project, completed.json()["work_item"], request_code_review_mode="warm",
        request_code_review_checkpoint_id=completed.json()["checkpoint"]["id"],
    )
    assert requested.status_code == 200, requested.text
    tasks = api.get(base + "/tasks", params={"status": "all"}).json()["items"]
    investigation = next(item for item in tasks if item["kind"] == "work_item")
    code_review = next(item for item in tasks if item["kind"] == "code_review")
    assert investigation["decision_review_revision_id"] == marker
    assert "decision_review_revision_id" not in code_review


def test_metadata_revision_and_supersession_are_grouped_per_trigger(api, project):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = decision(api, project, target, "First policy")
    other = decision(api, project, target, "Other policy")
    replacement = decision(api, project, target, "Replacement policy",
                           supersedes=[first["decision_id"], other["decision_id"]])
    spawned, = reviews(api, project)
    initial = context(api, project, spawned["work_item_id"])["initial_checkpoint"]
    assert len(initial["source_metadata"]["assessment_ids"]) == 2
    assert first["reference"] in initial["prompt"] and other["reference"] in initial["prompt"]
    changed(api, project, target, replacement, "Replacement policy", state="accepted",
            supersedes=[first["decision_id"], other["decision_id"]])
    assert len(reviews(api, project)) == 2
    latest = reviews(api, project)[0]
    initial = context(api, project, latest["work_item_id"])["initial_checkpoint"]
    assert len(initial["source_metadata"]["assessment_ids"]) == 3


def test_decision_impact_marker_survives_all_work_list_and_hierarchy_projections(
    api, project, work_payload,
):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = decision(api, project, target, "Decision impact marker visibility")
    changed(api, project, target, first)
    spawned, = reviews(api, project)
    marker = spawned["decision_review_revision_id"]
    base = f"/api/v1/projects/{project['id']}"
    for view in ("full", "roots"):
        for detail in ("full", "compact"):
            result = api.get(base + "/work-items", params={
                "status": "pending", "sort": "updated", "detail": detail, "view": view,
                "limit": 20, "offset": 0, "duplicate_scope": "canonical",
                "status_scope": "work_item",
            })
            assert result.status_code == 200, result.text
            item, = result.json()["items"]
            work = item["summary"]["work_item"] if detail == "full" else item
            assert work["decision_review_revision_id"] == marker, (view, detail)
    parent = create_work(api, project, work_payload, title="Investigations")["work_item"]
    relationship = api.post(base + "/relationships", json={
        "relationship_type": "parent-child", "source_work_item_id": parent["id"],
        "target_work_item_id": spawned["work_item_id"], "created_by_client": "dashboard",
        "created_by_session_id": "impact-review-hierarchy",
    })
    assert relationship.status_code == 200, relationship.text
    children = api.get(base + f"/work-items/{parent['id']}/children", params={"status": "pending"})
    assert children.status_code == 200, children.text
    child = children.json()["items"][0]["summary"]["work_item"]
    assert child["decision_review_revision_id"] == marker


def test_managed_product_decision_revisions_and_answers_create_standard_work(
    api, project, work_payload,
):
    settings(api, project, automatic_decision_reviews=True)
    work = create_work(api, project, work_payload, title="Choose export behavior")
    endpoint = gate_path(project, work)
    request = gate_request()
    original = api.post(endpoint, json=request).json()
    assert reviews(api, project) == []
    revision = {**request, "gate_id": original["id"], "expected_question_version": 1,
                "question": "Choose the final export behavior?",
                "client_operation_id": str(uuid4())}
    updated = api.post(endpoint, json=revision)
    assert updated.status_code == 201, updated.text
    assert len(reviews(api, project)) == 1
    answer = {**resolution_payload(revision=updated.json()["current_context_revision"]),
              "expected_question_version": 2}
    resolved = api.post(endpoint + f"/{original['id']}/resolve", json=answer)
    assert resolved.status_code == 200, resolved.text
    assert api.post(endpoint, json=revision).json() == updated.json()
    assert api.post(endpoint + f"/{original['id']}/resolve", json=answer).json() == resolved.json()
    spawned = reviews(api, project)
    assert len(spawned) == 2 and all(item["kind"] == "work_item" for item in spawned)
    assert all(item["status"] == "pending" for item in spawned)


def test_prompt_failure_rolls_back_decision_and_review_work(api, project, monkeypatch):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = decision(api, project, target, "Initial decision")
    original_read = PromptStorage.read

    def unavailable(storage, project_id, prompt_id):
        if prompt_id == "decision-impact-review":
            raise ApplicationError(503, "prompt_unavailable", "Prompt is unavailable")
        return original_read(storage, project_id, prompt_id)

    monkeypatch.setattr(PromptStorage, "read", unavailable)
    payload = put_body(target, "Changed decision", decision_id=first["decision_id"],
                       expected_revision=0)
    response = api.put(path(project) + "/decisions", json=payload)
    assert response.status_code == 503, response.text
    assert api.get(path(project) + f"/decisions/{first['decision_id']}").json()["revision"] == 0
    assert reviews(api, project) == []
    assert api.get(path(project) + "/assessments").json()["total"] == 0
    monkeypatch.setattr(PromptStorage, "read", original_read)
    assert put(api, project, payload)["created"]
    assert len(reviews(api, project)) == 1


def test_decision_review_marker_is_immutable_and_unique(api, project, postgres_engine):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = decision(api, project, target, "First decision")
    changed(api, project, target, first)
    spawned, = reviews(api, project)
    with postgres_engine.begin() as connection:
        with pytest.raises(DBAPIError, match="decision review origin is immutable"):
            with connection.begin_nested():
                connection.execute(text("UPDATE work_items SET decision_review_revision_id=NULL "
                                        "WHERE id=:id"), {"id": spawned["work_item_id"]})
        with pytest.raises(DBAPIError, match="uq_work_items_decision_review_revision_id"):
            with connection.begin_nested():
                connection.execute(text(
                    "INSERT INTO work_items(id, project_id, title, summary, "
                    "initial_checkpoint_id, decision_review_revision_id) "
                    "SELECT :new_id, project_id, title, summary, initial_checkpoint_id, "
                    "decision_review_revision_id FROM work_items WHERE id=:id"
                ), {"new_id": uuid4(), "id": spawned["work_item_id"]})
        assert connection.scalar(text("SELECT count(*) FROM work_events WHERE "
                                      "work_item_id=:id AND event_type='work_created'"),
                                 {"id": spawned["work_item_id"]}) == 1


def test_decision_review_can_move_and_merge_with_retained_origin(api, project, work_payload):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = decision(api, project, target, "Moveable policy review")
    changed(api, project, target, first)
    spawned, = reviews(api, project)
    work_id = spawned["work_item_id"]
    original = context(api, project, work_id)
    destination = api.post("/api/v1/projects", json={"name": "Review destination"}).json()
    moved = api.post(f"/api/v1/projects/{project['id']}/work-items/{work_id}/move", json={
        "target_project_id": destination["id"],
        "expected_version": original["work_item"]["version"],
        "client_operation_id": str(uuid4()),
        "actor": {"actor_client": "dashboard", "actor_session_id": "review-move-test"},
    })
    assert moved.status_code == 200, moved.text
    assert moved.json()["work_item"]["decision_review_revision_id"] == \
        spawned["decision_review_revision_id"]
    retained = context(api, destination, work_id)
    assert retained["initial_checkpoint"] == original["initial_checkpoint"]
    assert retained["initial_checkpoint"]["source_metadata"]["decision_project_id"] == project["id"]
    ordinary = create_work(api, destination, work_payload, title="Consolidated investigation")
    merged, _ = merge_work(api, destination, retained["work_item"], ordinary["work_item"])
    assert merged["canonical_work_item"]["id"] == ordinary["work_item"]["id"]
    alias = context(api, destination, work_id)
    assert alias["work_item"]["decision_review_revision_id"] == \
        spawned["decision_review_revision_id"]
    assert alias["initial_checkpoint"] == original["initial_checkpoint"]


def test_done_requires_every_candidate_and_linked_remediation(
    api, project, work_payload, checkpoint_fields,
):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = decision(api, project, target, "Original implementation contract")
    dependent = decision(api, project, target, "Dependent implementation contract",
                         depends_on=[dependency(first)])
    changed(api, project, target, first)
    spawned, = reviews(api, project)
    retained = context(api, project, spawned["work_item_id"])
    work = retained["work_item"]
    assessment_id = retained["initial_checkpoint"]["source_metadata"]["assessment_ids"][0]
    assess(api, project, assessment_id, first["reference"], "not-affected")
    blocked = finish(api, project, work, checkpoint_fields)
    assert blocked.status_code == 409 and \
        blocked.json()["detail"]["context"]["missing_dispositions"] == 1
    assess(api, project, assessment_id, dependent["reference"], "change-required")
    blocked = finish(api, project, work, checkpoint_fields)
    assert blocked.status_code == 409 and \
        blocked.json()["detail"]["context"]["untracked_changes"] == 1
    assess(api, project, assessment_id, dependent["reference"], "change-required", work["id"])
    assert finish(api, project, work, checkpoint_fields).status_code == 409
    remediation = create_work(api, project, {
        **work_payload, "discovered_from_work_item_id": work["id"],
    }, title="Update the dependent contract implementation")["work_item"]
    assess(api, project, assessment_id, dependent["reference"],
           "change-required", remediation["id"])
    completed = finish(api, project, work, checkpoint_fields)
    assert completed.status_code == 200, completed.text
    assert context(api, project, remediation["id"])["work_item"]["status"] == "pending"


def test_moved_impact_review_completes_using_source_assessment_ledger(
    api, project, checkpoint_fields, work_payload,
):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = decision(api, project, target, "Original policy for moved investigation")
    changed(api, project, target, first)
    spawned, = reviews(api, project)
    retained = context(api, project, spawned["work_item_id"])
    work = retained["work_item"]
    assessment_id = retained["initial_checkpoint"]["source_metadata"]["assessment_ids"][0]
    destination = api.post("/api/v1/projects", json={"name": "Moved impact review"}).json()
    moved = api.post(f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move", json={
        "target_project_id": destination["id"], "expected_version": work["version"],
        "client_operation_id": str(uuid4()),
        "actor": {"actor_client": "dashboard", "actor_session_id": "impact-review-move"},
    })
    assert moved.status_code == 200, moved.text
    moved_work = moved.json()["work_item"]
    assert finish(api, destination, moved_work, checkpoint_fields).status_code == 409
    remediation = create_work(api, project, {
        **work_payload, "discovered_from_work_item_id": work["id"],
    }, title="Source-project remediation from the moved review")["work_item"]
    assess(api, project, assessment_id, first["reference"],
           "change-required", remediation["id"])
    completed = finish(api, destination, moved_work, checkpoint_fields)
    assert completed.status_code == 200, completed.text


@pytest.mark.parametrize("status", ["wont-do", "promoted"])
def test_impact_review_retirement_remains_ordinary(api, project, checkpoint_fields, status):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = decision(api, project, target, "Retired investigation policy")
    changed(api, project, target, first)
    spawned, = reviews(api, project)
    work = context(api, project, spawned["work_item_id"])["work_item"]
    closed = finish(api, project, work, checkpoint_fields, status=status)
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == status


def test_moved_review_holds_remediation_until_done_commits(
    api, project, checkpoint_fields, work_payload, postgres_engine, monkeypatch,
):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = decision(api, project, target, "Policy with concurrent remediation deletion")
    changed(api, project, target, first)
    spawned, = reviews(api, project)
    retained = context(api, project, spawned["work_item_id"])
    work = retained["work_item"]
    assessment_id = retained["initial_checkpoint"]["source_metadata"]["assessment_ids"][0]
    destination = api.post("/api/v1/projects", json={"name": "Concurrent destination"}).json()
    moved = api.post(f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move", json={
        "target_project_id": destination["id"], "expected_version": work["version"],
        "client_operation_id": str(uuid4()),
        "actor": {"actor_client": "dashboard", "actor_session_id": "concurrent-review-move"},
    })
    assert moved.status_code == 200, moved.text
    remediation = create_work(api, project, work_payload,
                              title="Separately tracked remediation")["work_item"]
    assess(api, project, assessment_id, first["reference"],
           "change-required", remediation["id"])

    checked, release = Event(), Event()
    original_guard = decision_reviews.require_decision_impact_review_complete
    original_mutation = registered_mutations.project_mutation
    delete_name = "impact-delete-" + uuid4().hex

    def paused_guard(database, item):
        original_guard(database, item)
        if item.id == UUID(work["id"]):
            checked.set()
            assert release.wait(timeout=5), "Review completion was not released"

    @contextmanager
    def tagged_mutation(database, project_id, **kwargs):
        if str(project_id) == project["id"]:
            database.execute(text("SELECT set_config('application_name', :name, true)"),
                             {"name": delete_name})
        with original_mutation(database, project_id, **kwargs):
            yield

    monkeypatch.setattr(decision_reviews, "require_decision_impact_review_complete", paused_guard)
    monkeypatch.setattr(registered_mutations, "project_mutation", tagged_mutation)
    with ThreadPoolExecutor(max_workers=2) as executor:
        completion = executor.submit(finish, api, destination,
                                     moved.json()["work_item"], checkpoint_fields)
        try:
            assert checked.wait(timeout=3), "Decision impact guard did not acquire its locks"
            deletion = executor.submit(api.post,
                f"/api/v1/projects/{project['id']}/work-items/{remediation['id']}/delete", json={
                    "subagent_transcripts": None, "expected_version": remediation["version"],
                })
            _wait_for_application_locks(postgres_engine, [delete_name], 1, completion, deletion,
                failure_message="Source-project deletion did not wait for the review guard")
            assert not deletion.done()
        finally:
            release.set()
        completed, deleted = completion.result(timeout=3), deletion.result(timeout=3)
    assert completed.status_code == 200, completed.text
    assert deleted.status_code == 200, deleted.text
    with postgres_engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT work.deleted_at > checkpoint.created_at FROM work_items work, "
            "checkpoints checkpoint WHERE work.id=:work AND checkpoint.id=:checkpoint"
        ), {"work": remediation["id"], "checkpoint": completed.json()["checkpoint"]["id"]})
