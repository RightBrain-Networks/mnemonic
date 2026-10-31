"""Bounded review cycles survive lineage, retry, scope growth and explicit adoption."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import DecisionChange, WorkItem
from mnemonic_api.services.decision_changes import lock_change, require_open

from .test_decision_impact_postgres import changed, decision
from .test_decision_reviews_postgres import assess, context, reviews, settings
from .test_human_gates_postgres import create_work, gate_path, gate_request, resolution_payload
from .test_knowledge_postgres import domain, path, put, put_body
from .test_project_activity_audit_postgres import _audit
from .test_project_reports_postgres import close as close_report
from .test_work_item_moves_postgres import _move_payload, _project

pytestmark = pytest.mark.postgres
HEADERS = {"host": "localhost:3000", "origin": "http://localhost:3000"}


def close(api, project, work, checkpoint_fields, **kwargs):
    prompt = api.post(f"/api/v1/projects/{project['id']}/prompts/job-completion-report/render",
                      json={"work_item_id": work["id"]}).json()
    return close_report(api, project, work, checkpoint_fields, **kwargs, job_completion_report={
        "summary": "Investigated all retained review obligations.", "fyi_items": [],
        "prompt_revision": prompt["prompt_revision"],
        "decision_provenance": {"disposition": "not-code", "reason": "Investigation only."},
    })


def update(api, project, action, **fields):
    body = {"action": action, "actor_client": "dashboard", "actor_session_id": "owner",
            "client_operation_id": str(uuid4()), **fields}
    endpoint = path(project).replace("/api/v1/", "/api/mnemonic/") + "/changes"
    result = api.post(endpoint, json=body, headers=HEADERS)
    return result, body


def group(api, project, identity):
    result = api.get(path(project) + f"/changes/{identity}")
    assert result.status_code == 200, result.text
    return result.json()


def root_review(api, project):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    old = decision(api, project, target, "Old ruling")
    new = changed(api, project, target, old, "New ruling")
    review, = reviews(api, project)
    work = context(api, project, review["work_item_id"])["work_item"]
    return target, old, new, work


def retire(api, project, work, checkpoint_fields):
    response, _ = close(api, project, work, checkpoint_fields, status="wont-do")
    assert response.status_code == 200, response.text


def test_related_revisions_extend_one_review_and_stale_ack_fails(
    api, project, work_payload, checkpoint_fields,
):
    target, old, new, review = root_review(api, project)
    change_id = new["change"]["change_id"]
    assert review["decision_review_change_id"] == change_id
    child = create_work(api, project, {**work_payload,
        "discovered_from_work_item_id": review["id"]}, title="Investigate rule")["work_item"]
    assert child["decision_change_ids"] == [change_id]
    acknowledged, intent = update(api, project, "acknowledge", change_id=change_id,
                                  expected_scope_version=1)
    assert acknowledged.status_code == 200, acknowledged.text
    next_revision = put(api, project, put_body(target, "Extended ruling",
        decision_id=new["decision_id"], expected_revision=1,
        source_work_item_id=child["id"]))["decision"]
    assert next_revision["change"]["change_id"] == change_id
    assert len(reviews(api, project)) == 1
    current = group(api, project, change_id)
    assert current["scope_version"] == 2 and current["acknowledged_scope_version"] == 1
    stale, _ = update(api, project, "acknowledge", change_id=change_id, expected_scope_version=1)
    assert stale.status_code == 409 and "decision_change_scope_conflict" in stale.text
    endpoint = path(project).replace("/api/v1/", "/api/mnemonic/") + "/changes"
    replay = api.post(endpoint, json=intent, headers=HEADERS)
    assert replay.json() == acknowledged.json()
    assert group(api, project, change_id)["acknowledged_scope_version"] == 1
    assessments = api.get(path(project) + "/assessments", params={"change_id": change_id}).json()
    assert assessments["total"] == 2
    for episode in assessments["items"]:
        detail = api.get(path(project) + f"/assessments/{episode['id']}").json()
        for candidate in detail["candidates"]:
            assess(api, project, episode["id"], candidate["reference"])
    result, _ = close(api, project, review, checkpoint_fields)
    assert result.status_code == 409 and "decision_change_scope_unacknowledged" in result.text
    acknowledged, _ = update(api, project, "acknowledge", change_id=change_id,
                             expected_scope_version=2)
    assert acknowledged.status_code == 200, acknowledged.text
    result, _ = close(api, project, review, checkpoint_fields)
    assert result.status_code == 200, result.text


def test_closed_lineage_automatically_advances_and_never_reopens(
    api, project, work_payload, checkpoint_fields,
):
    target, _, new, review = root_review(api, project)
    change_id = new["change"]["change_id"]
    child = create_work(api, project, {**work_payload,
        "discovered_from_work_item_id": review["id"]}, title="Remediate ruling")["work_item"]
    retire(api, project, review, checkpoint_fields)
    body = put_body(target, "New exception", decision_id=new["decision_id"],
                    expected_revision=1, source_work_item_id=child["id"])
    result = put(api, project, body)
    successor = result["decision"]["change"]["change_id"]
    assert successor != change_id
    assert group(api, project, successor)["parent_change_id"] == change_id
    assert group(api, project, change_id)["scope_version"] == 1
    assert len(reviews(api, project)) == 2
    assert context(api, project, review["id"])["work_item"]["status"] == "wont-do"
    assert put(api, project, body) == result
    retired, _ = update(api, project, "admit", change_id=change_id,
                        expected_scope_version=1)
    assert retired.status_code == 422
    assert "decision_change_admission_retired" in retired.text


def test_maintenance_is_audited_and_record_existing_requires_exact_governance(api, project):
    target, _, new, review = root_review(api, project)
    change_id = new["change"]["change_id"]
    maintained = put(api, project, put_body(target, "New ruling\n",
        decision_id=new["decision_id"], expected_revision=1,
        source_work_item_id=review["id"],
        change={"kind": "maintenance", "reason": "Normalize the final newline."}))["decision"]
    assert maintained["change"]["kind"] == "maintenance"
    assert group(api, project, change_id)["scope_version"] == 1
    assert len(reviews(api, project)) == 1
    bad = put_body(target, "Recorded unrelated ruling", change={
        "kind": "record-existing", "change_id": change_id,
        "governing_reference": maintained["reference"], "reason": "Record the exception."})
    refused = api.put(path(project) + "/decisions", json=bad)
    assert refused.status_code == 422 and "decision_change_governance_invalid" in refused.text


def test_fresh_agent_must_declare_source(api, project):
    target = domain(api, project)
    body = put_body(target)
    body.pop("source_work_item_id")
    refused = api.put(path(project) + "/decisions", json=body)
    assert refused.status_code == 422 and "decision_change_source_required" in refused.text


def test_adoption_binds_ordinary_work_and_enforces_full_scope(
    api, project, work_payload, checkpoint_fields,
):
    _, old, new, review = root_review(api, project)
    episode = api.get(path(project) + "/assessments").json()["items"][0]
    ordinary = create_work(api, project, work_payload, title="Combined investigation")["work_item"]
    adopted, intent = update(api, project, "adopt", work_item_id=ordinary["id"],
                            expected_work_version=1, assessment_ids=[episode["id"]])
    assert adopted.status_code == 200, adopted.text
    bound = context(api, project, ordinary["id"])["work_item"]
    assert "decision_review_revision_id" not in bound
    assert bound["decision_review_change_id"] == adopted.json()["id"]
    ack, _ = update(api, project, "acknowledge", change_id=adopted.json()["id"],
                    expected_scope_version=1)
    assert ack.status_code == 200, ack.text
    result, _ = close(api, project, bound, checkpoint_fields)
    assert result.status_code == 409 and "decision_impact_review_incomplete" in result.text
    assess(api, project, episode["id"], old["reference"])
    result, _ = close(api, project, bound, checkpoint_fields)
    assert result.status_code == 200, result.text


def test_accepted_answer_automatically_creates_next_review(
    api, project, work_payload, checkpoint_fields,
):
    _, _, new, review = root_review(api, project)
    child = create_work(api, project, {**work_payload,
        "discovered_from_work_item_id": review["id"]}, title="Ask about a new exception")
    retire(api, project, review, checkpoint_fields)
    answered = answer_question(api, project, child)
    retained = api.get(path(project) + f"/decisions/{answered['decision']['reference']}").json()
    change_id = new["change"]["change_id"]
    assert retained["change"].get("requires_admission", False) is False
    successor = group(api, project, change_id)["successor_change_id"]
    assert successor == retained["change"]["change_id"]
    assert group(api, project, successor)["scope_version"] == 1
    assert group(api, project, change_id)["pending_revision_count"] == 0
    assert len(reviews(api, project)) == 2


def answer_question(api, project, created):
    gates = gate_path(project, created)
    requested = api.post(gates, json=gate_request())
    assert requested.status_code == 201, requested.text
    gate = requested.json()
    answered = api.post(gates + f"/{gate['id']}/resolve", json=resolution_payload(
        revision=gate["current_context_revision"]))
    assert answered.status_code == 200, answered.text
    return answered.json()


def test_combined_adoption_preserves_lineage_and_advances_after_completion(
    api, project, work_payload, checkpoint_fields,
):
    target, _, new, first = root_review(api, project)
    other = decision(api, project, target, "Another original ruling")
    changed(api, project, target, other, "Another changed ruling")
    for task in reviews(api, project):
        retire(api, project, context(api, project, task["work_item_id"])["work_item"],
               checkpoint_fields)
    combined = create_work(api, project, work_payload, title="Combined historical review")
    work = combined["work_item"]
    child = create_work(api, project, {**work_payload,
        "discovered_from_work_item_id": work["id"]}, title="Existing review follow-up")
    episodes = api.get(path(project) + "/assessments").json()["items"]
    adopted, _ = update(api, project, "adopt", work_item_id=work["id"],
                        expected_work_version=1, assessment_ids=[item["id"] for item in episodes])
    assert adopted.status_code == 200, adopted.text
    change_id = adopted.json()["id"]
    inherited = context(api, project, child["work_item"]["id"])["work_item"]["decision_change_ids"]
    assert change_id in inherited and len(inherited) == 3
    saved = api.put(path(project) + "/decisions", json=put_body(
        target, "A new ruling grouped under the combined investigation",
        source_work_item_id=child["work_item"]["id"],
        change={"kind": "substantive", "change_id": change_id},
    ))
    assert saved.status_code == 200, saved.text
    retire(api, project, context(api, project, work["id"])["work_item"], checkpoint_fields)
    answer_question(api, project, child)
    successor = group(api, project, change_id)["successor_change_id"]
    assert group(api, project, successor)["revision_count"] == 1
    before = len(reviews(api, project))
    answer_question(api, project, child)
    assert group(api, project, change_id)["pending_revision_count"] == 0
    assert len(reviews(api, project)) == before
    assert group(api, project, successor)["scope_version"] == 2


def test_moved_followup_can_record_owner_answer_without_losing_source_boundary(
    api, project, work_payload, checkpoint_fields,
):
    _, _, new, review = root_review(api, project)
    child = create_work(api, project, {**work_payload,
        "discovered_from_work_item_id": review["id"]}, title="Cross-project follow-up")
    retire(api, project, review, checkpoint_fields)
    destination = _project(api, "Moved review destination")
    settings(api, destination, automatic_decision_reviews=True)
    moved = api.post(f"/api/v1/projects/{project['id']}/work-items/{child['work_item']['id']}/move",
                     json=_move_payload(destination, 1))
    assert moved.status_code == 200, moved.text
    answer = answer_question(api, destination, child)
    reference = answer["decision"]["reference"]
    retained = api.get(path(destination) + f"/decisions/{reference}").json()
    local_change = retained["change"]["change_id"]
    assert retained["change"].get("requires_admission", False) is False
    assert local_change != new["change"]["change_id"]
    assert len(reviews(api, destination)) == 1
    assert group(api, destination, local_change)["pending_revision_count"] == 0


def test_recorded_supersession_reuses_exact_obligation_but_preserves_new_revision_scope(
    api, project,
):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    old = decision(api, project, target, "Previous ruling")
    governing = decision(api, project, target, "Ruling that supersedes the previous rule",
                         supersedes=[old["decision_id"]])
    change_id = governing["change"]["change_id"]
    original = api.get(path(project) + "/assessments", params={"change_id": change_id}).json()
    original_episode = original["items"][0]
    assess(api, project, original_episode["id"], old["reference"])
    amended = changed(api, project, target, governing, "Ruling with an explicit provenance note",
                      supersedes=[old["decision_id"]], change={
                          "kind": "record-existing", "change_id": change_id,
                          "governing_reference": governing["reference"],
                          "reason": "Record the already enacted supersession without changing it.",
                      })
    assert amended["change"]["change_id"] == change_id
    assert len(reviews(api, project)) == 1
    episodes = api.get(path(project) + "/assessments", params={"change_id": change_id}).json()
    assert episodes["total"] == 2
    retained = next(item for item in episodes["items"] if item["id"] == original_episode["id"])
    assert retained["unassessed_count"] == 0
    added = next(item for item in episodes["items"] if item["id"] != original_episode["id"])
    assert added["before_reference"] == governing["reference"]
    assert added["after_reference"] == amended["reference"]
    assert added["unassessed_count"] > 0


def test_change_lock_refreshes_driver_closed_by_another_transaction(
    api, project, checkpoint_fields, postgres_engine,
):
    _, _, new, review = root_review(api, project)
    with Session(postgres_engine) as database:
        cached = database.get(WorkItem, UUID(review["id"]))
        change = database.get(DecisionChange, UUID(new["change"]["change_id"]))
        assert cached is not None and cached.status == "pending" and change is not None
        retire(api, project, review, checkpoint_fields)
        assert cached.status == "pending"  # the original reader still has its cached row
        lock_change(database, change)
        assert cached.status == "wont-do"
        with pytest.raises(ApplicationError) as failure:
            require_open(database, change)
        assert failure.value.detail["code"] == "decision_change_closed"


def _change_supersession_evidence(api, project, target, old, details, mutation):
    if mutation == "scope":
        result = api.post(path(project) + f"/decisions/{old['decision_id']}/scope", json={
            "revision": 0, "commit_hash": "a" * 40, "repository": "example/supersession",
            "client_operation_id": str(uuid4()),
        })
        assert result.status_code == 200, result.text
    elif mutation == "incoming-candidate":
        decision(api, project, target, "New incoming citation to the previous ruling",
                 related_decision_ids=[old["decision_id"]])
    else:
        details[0][mutation] = (
            "partial" if mutation == "extent" else "Clarify which earlier guarantee is replaced."
        )


@pytest.mark.parametrize("mutation", ["scope", "incoming-candidate", "extent", "reason"])
def test_changed_supersession_evidence_uses_current_annotation_without_rewriting_old_episode(
    api, project, postgres_engine, mutation,
):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    old = decision(api, project, target, "Previous ruling")
    details = [{"decision_id": old["decision_id"], "revision": 0,
                "extent": "full", "reason": "Replace the previous ruling."}]
    governing = decision(api, project, target, "Enacted replacement ruling",
                         supersession_details=details)
    change_id = governing["change"]["change_id"]
    original = api.get(path(project) + "/assessments", params={"change_id": change_id}).json()
    original_id = original["items"][0]["id"]
    assess(api, project, original_id, old["reference"])
    before = api.get(path(project) + f"/assessments/{original_id}").json()
    acknowledged, _ = update(api, project, "acknowledge", change_id=change_id,
                             expected_scope_version=1)
    assert acknowledged.status_code == 200, acknowledged.text
    _change_supersession_evidence(api, project, target, old, details, mutation)
    annotation = changed(api, project, target, governing, "Replacement ruling with provenance note",
                         supersession_details=details, change={
        "kind": "record-existing", "change_id": change_id,
        "governing_reference": governing["reference"], "reason": "Document the enacted ruling.",
    })
    assert annotation["change"]["governing_reference"] == governing["reference"]
    episodes = api.get(path(project) + "/assessments", params={"change_id": change_id}).json()
    assert episodes["total"] == 3 and len(reviews(api, project)) == 1
    fresh = next(item for item in episodes["items"]
                 if item["trigger"] == "supersession" and item["id"] != original_id)
    assert fresh["before_reference"] == old["reference"]
    assert fresh["after_reference"] == annotation["reference"]
    assert fresh["diff"]["after_sha256"] == annotation["sha256"]
    assert f"+++ {annotation['reference']}" in fresh["diff"]["unified_diff"]
    assert (fresh["extent"], fresh["reason"]) == (details[0]["extent"], details[0]["reason"])
    assert fresh["version"] == 0 and fresh["unassessed_count"] > 0
    retained = api.get(path(project) + f"/assessments/{original_id}").json()
    for key in ("before_reference", "after_reference", "diff", "candidates", "dispositions"):
        assert retained[key] == before[key]
    current = group(api, project, change_id)
    assert current["scope_version"] == 3 and current["acknowledged_scope_version"] == 1
    fresh_detail = api.get(path(project) + f"/assessments/{fresh['id']}").json()
    if mutation == "scope":
        assert fresh_detail["candidates"][0]["scopes"][0]["commit_hash"] == "a" * 40
    if mutation == "incoming-candidate":
        assert fresh_detail["candidate_count"] == before["candidate_count"] + 1
    assert _audit(postgres_engine)["result"] == "pass"
