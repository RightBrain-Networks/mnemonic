"""Automatic successors preserve closed scope, lineage and retained owner answers."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.models import DecisionChange, DecisionRevisionChange
from mnemonic_api.services.decision_change_recovery import RecoveryCursor, recover_held_answers
from mnemonic_api.services.decision_changes import bind_work

from .test_decision_changes_postgres import answer_question, group, retire, root_review
from .test_decision_impact_postgres import decision
from .test_decision_reviews_postgres import context, reviews, settings
from .test_human_gates_postgres import create_work
from .test_knowledge_postgres import domain, path, put, put_body
from .test_work_item_moves_postgres import _project

pytestmark = pytest.mark.postgres


def _followup(api, project, work_payload, review):
    return create_work(api, project, {**work_payload,
        "discovered_from_work_item_id": review["id"]}, title="Follow up on a completed review")


@pytest.mark.parametrize("explicit", [False, True])
def test_multiple_generations_follow_existing_successors(
    api, project, work_payload, checkpoint_fields, explicit,
):
    target, _, current, review = root_review(api, project)
    first = current["change"]["change_id"]
    child = _followup(api, project, work_payload, review)["work_item"]
    for generation in range(3):
        old_change = current["change"]["change_id"]
        retire(api, project, review, checkpoint_fields)
        fields = {"change": {"kind": "substantive", "change_id": first}} if explicit else {}
        current = put(api, project, put_body(target, f"Ruling generation {generation}",
            decision_id=current["decision_id"], expected_revision=current["revision"],
            source_work_item_id=child["id"], **fields))["decision"]
        next_change = group(api, project, current["change"]["change_id"])
        assert next_change["parent_change_id"] == old_change
        assert group(api, project, old_change)["scope_version"] == 1
        assert context(api, project, review["id"])["work_item"]["status"] == "wont-do"
        review = context(api, project, next_change["review_work_item_id"])["work_item"]
    assert len(reviews(api, project)) == 4
    # An explicitly selected later descendant is accepted from the original source.
    latest = current["change"]["change_id"]
    annotation = put(api, project, put_body(target, "Another rule in the existing open cycle",
        decision_id=current["decision_id"], expected_revision=current["revision"],
        source_work_item_id=child["id"], change={"kind": "substantive", "change_id": latest}))
    assert annotation["decision"]["change"]["change_id"] == latest
    assert len(reviews(api, project)) == 4


def test_domain_off_records_successor_evidence_without_review(
    api, project, work_payload, checkpoint_fields,
):
    target, _, current, review = root_review(api, project)
    child = _followup(api, project, work_payload, review)["work_item"]
    retire(api, project, review, checkpoint_fields)
    settings(api, project, automatic_decision_reviews=False)
    result = put(api, project, put_body(target, "New unreviewed rule",
        decision_id=current["decision_id"], expected_revision=1,
        source_work_item_id=child["id"]))["decision"]
    successor = group(api, project, result["change"]["change_id"])
    assert successor["scope_version"] == 1 and successor["review_work_item_id"] is None
    assert len(reviews(api, project)) == 1


def test_foreign_closed_lineage_does_not_block_local_decision(
    api, project, work_payload, checkpoint_fields,
):
    _, _, current, review = root_review(api, project)
    retire(api, project, review, checkpoint_fields)
    destination = _project(api, "Independent local decision")
    settings(api, destination, automatic_decision_reviews=True)
    child = _followup(api, destination, work_payload, review)["work_item"]
    local = domain(api, destination)
    before = decision(api, destination, local, "Before local decision")
    saved = put(api, destination, put_body(local, "After local decision",
        decision_id=before["decision_id"], expected_revision=0,
        source_work_item_id=child["id"]))["decision"]
    assert saved["change"]["change_id"] != current["change"]["change_id"]
    assert len(reviews(api, destination)) == 1
    assert current["change"]["change_id"] in context(
        api, destination, child["id"],
    )["work_item"]["decision_change_ids"]


def test_concurrent_writes_share_one_successor_and_replay(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    target, _, current, review = root_review(api, project)
    other = decision(api, project, target, "Another rule")
    child = _followup(api, project, work_payload, review)["work_item"]
    retire(api, project, review, checkpoint_fields)
    bodies = [put_body(target, f"Concurrent change {index}", decision_id=before["decision_id"],
        expected_revision=before["revision"], source_work_item_id=child["id"],
        change={"kind": "substantive", "change_id": current["change"]["change_id"]})
        for index, before in enumerate([current, other])]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda body: api.put(path(project) + "/decisions", json=body), bodies,
        ))
    results = []
    for body, response in zip(bodies, responses, strict=True):
        if response.status_code == 503:
            assert response.json()["detail"]["code"] == "client_operation_unavailable"
            results.append(put(api, project, body))  # Exact receipt intent after scope changed.
        else:
            assert response.status_code == 200, response.text
            results.append(response.json())
    assert len({result["decision"]["change"]["change_id"] for result in results}) == 1
    assert len(reviews(api, project)) == 2
    for body, result in zip(bodies, results, strict=True):
        assert put(api, project, body) == result
    with Session(postgres_engine) as database:
        assert database.scalar(select(func.count()).select_from(DecisionChange).where(
            DecisionChange.parent_change_id == UUID(current["change"]["change_id"]),
        )) == 1


def test_closed_record_existing_keeps_governance_and_queues_new_obligations(
    api, project, checkpoint_fields,
):
    target, _, current, review = root_review(api, project)
    retire(api, project, review, checkpoint_fields)
    old = current["change"]["change_id"]
    result = put(api, project, put_body(target, "Record original rule with provenance",
        decision_id=current["decision_id"], expected_revision=1,
        source_work_item_id=review["id"], change={"kind": "record-existing", "change_id": old,
            "governing_reference": current["reference"], "reason": "Record the existing ruling."},
    ))["decision"]
    assert result["change"]["change_id"] == old
    assert result["change"]["governing_reference"] == current["reference"]
    successor = group(api, project, old)["successor_change_id"]
    assert group(api, project, successor)["scope_version"] == 1
    assert group(api, project, old)["scope_version"] == 1
    assert len(reviews(api, project)) == 2


def _legacy_answer(api, project, child, parent_id, monkeypatch):
    from mnemonic_api.services import decision_impact

    original = decision_impact.create_assessments

    def capture(database, document, revision, payload, **kwargs):
        if payload.author_kind != "human":
            return original(database, document, revision, payload, **kwargs)
        database.add(DecisionRevisionChange(
            revision_id=revision.id, change_id=UUID(parent_id), kind="substantive",
            source_work_item_id=UUID(child["work_item"]["id"]), requires_admission=True,
        ))
        bind_work(database, UUID(child["work_item"]["id"]), UUID(parent_id))
        database.flush()

    with monkeypatch.context() as legacy:
        legacy.setattr(decision_impact, "create_assessments", capture)
        return answer_question(api, project, child)


@pytest.mark.parametrize("automatic", [False, True])
def test_legacy_held_answers_follow_transitive_successor_without_rewriting_history(
    api, project, work_payload, checkpoint_fields, postgres_engine, monkeypatch, automatic,
):
    target, _, current, review = root_review(api, project)
    original = current["change"]["change_id"]
    child = _followup(api, project, work_payload, review)
    retire(api, project, review, checkpoint_fields)
    answer = _legacy_answer(api, project, child, original, monkeypatch)
    pinned = answer["decision"]["reference"]
    before = api.get(path(project) + f"/decisions/{pinned}").json()
    assert before["change"]["requires_admission"] is True
    # Two later cycles have already advanced independently of the retained answer.
    for generation in range(2):
        current = put(api, project, put_body(target, f"Later ruling {generation}",
            decision_id=current["decision_id"], expected_revision=current["revision"],
            source_work_item_id=child["work_item"]["id"]))["decision"]
        cycle = group(api, project, current["change"]["change_id"])
        retire(api, project, context(api, project, cycle["review_work_item_id"])["work_item"],
               checkpoint_fields)
    settings(api, project, automatic_decision_reviews=automatic)
    with Session(postgres_engine, info={"prompt_root": api.app.state.settings.prompt_root}) as db:
        assert recover_held_answers(db) == 1
        assert recover_held_answers(db) == 0
    retained = api.get(path(project) + f"/decisions/{pinned}").json()
    assert retained["sha256"] == before["sha256"]
    assert retained["change"]["change_id"] == original
    assert not retained["change"].get("requires_admission", False)
    assert group(api, project, original)["pending_revision_count"] == 0
    next_id = group(api, project, current["change"]["change_id"])["successor_change_id"]
    assert group(api, project, next_id)["scope_version"] == 1
    assert bool(group(api, project, next_id)["review_work_item_id"]) is automatic
    with Session(postgres_engine) as db:
        row = db.scalar(select(DecisionRevisionChange).where(
            DecisionRevisionChange.requires_admission.is_(True),
        ))
        assert row is not None and str(row.change_id) == original


def test_recovery_cursor_skips_failing_old_groups(
    api, project, work_payload, checkpoint_fields, postgres_engine, monkeypatch,
):
    from mnemonic_api.services import decision_change_recovery

    _, _, current, review = root_review(api, project)
    child = _followup(api, project, work_payload, review)
    retire(api, project, review, checkpoint_fields)
    identities = [UUID(current["change"]["change_id"])]
    with Session(postgres_engine) as db:
        for _ in range(10):
            change = DecisionChange(project_id=UUID(project["id"]), scope_version=0,
                                    acknowledged_scope_version=0)
            db.add(change)
            db.flush()
            identities.append(change.id)
        db.commit()
    for identity in identities:
        _legacy_answer(api, project, child, str(identity), monkeypatch)
    failing = set(sorted(identities)[:10])
    original_recover = decision_change_recovery._recover_change

    def recover(database, project_id, identity):
        if identity in failing:
            raise RuntimeError("temporary failure")
        return original_recover(database, project_id, identity)

    monkeypatch.setattr(decision_change_recovery, "_recover_change", recover)
    cursor = RecoveryCursor()
    with Session(postgres_engine, info={"prompt_root": api.app.state.settings.prompt_root}) as db:
        assert recover_held_answers(db, cursor) == 0
        assert cursor.after == sorted(identities)[9]
        assert recover_held_answers(db, cursor) == 1
        assert cursor.after == max(identities)


def test_historical_admission_receipt_replays_after_fresh_admission_retired(
    api, project, checkpoint_fields, monkeypatch,
):
    from mnemonic_api.services import decision_change_actions
    from mnemonic_api.services.decision_changes import read_change

    from .test_decision_changes_postgres import HEADERS, update

    _, _, current, review = root_review(api, project)
    parent = current["change"]["change_id"]
    retire(api, project, review, checkpoint_fields)

    def historical_admission(database, project_id, payload):
        successor = DecisionChange(
            project_id=project_id, parent_change_id=payload.change_id,
            scope_version=0, acknowledged_scope_version=0,
            admission_actor_client=payload.actor_client,
            admission_actor_session_id=payload.actor_session_id,
        )
        database.add(successor)
        database.flush()
        bind_work(database, UUID(review["id"]), successor.id)
        return read_change(database, successor)

    with monkeypatch.context() as legacy:
        legacy.setattr(decision_change_actions, "update_change", historical_admission)
        saved, intent = update(api, project, "admit", change_id=parent, expected_scope_version=1)
        assert saved.status_code == 200, saved.text
    endpoint = path(project).replace("/api/v1/", "/api/mnemonic/") + "/changes"
    replay = api.post(endpoint, json=intent, headers=HEADERS)
    assert replay.status_code == 200 and replay.json() == saved.json()
    fresh, _ = update(api, project, "admit", change_id=parent, expected_scope_version=1)
    assert fresh.status_code == 422
    assert fresh.json()["detail"]["code"] == "decision_change_admission_retired"


def test_closed_lineage_maintenance_does_not_create_successor(
    api, project, work_payload, checkpoint_fields,
):
    target, _, current, review = root_review(api, project)
    child = _followup(api, project, work_payload, review)["work_item"]
    retire(api, project, review, checkpoint_fields)
    maintained = put(api, project, put_body(target, "New ruling\n",
        decision_id=current["decision_id"], expected_revision=1,
        source_work_item_id=child["id"],
        change={"kind": "maintenance", "reason": "Normalize final newline."}))["decision"]
    assert maintained["change"]["kind"] == "maintenance"
    assert group(api, project, current["change"]["change_id"])["successor_change_id"] is None
    assert len(reviews(api, project)) == 1
