"""Scope growth reaches the current holder without altering historical receipts."""

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from mnemonic_api.decision_change_integrity import DECISION_CHANGE_FINDINGS
from mnemonic_api.models import DecisionChangeAssessment

from .test_decision_changes_postgres import group, root_review, update
from .test_decision_reviews_postgres import settings
from .test_knowledge_postgres import path, put, put_body
from .test_leases_postgres import claim_payload, item_path

pytestmark = pytest.mark.postgres


def extend(api, project, target, current, work):
    intent = put_body(target, f"Expanded scope {current['revision'] + 1}",
                      decision_id=current["decision_id"],
                      expected_revision=current["revision"], source_work_item_id=work["id"],
                      agent_session_id="scope-author", model_id="scope-model")
    return put(api, project, intent), intent


def acknowledge(api, project, change_id, version, token=None):
    if token:
        result = api.post(path(project) + "/changes", json={
            "action": "acknowledge", "change_id": change_id, "expected_scope_version": version,
            "lease_token": token, "actor_client": "codex", "actor_session_id": "review-holder",
            "client_operation_id": str(uuid4()),
        })
    else:
        result, _ = update(api, project, "acknowledge", change_id=change_id,
                           expected_scope_version=version)
    assert result.status_code == 200, result.text


def test_holder_notices_persist_until_acknowledged_and_receipts_remain_exact(
    api, project, checkpoint_fields, postgres_engine,
):
    target, _, current, work = root_review(api, project)
    change_id = current["change"]["change_id"]
    base = item_path(project, work)
    receipt = api.post(base + "/claim", json={
        **claim_payload(str(uuid4()), client="codex", session="review-holder"),
        "session_transcript": None,
    })
    assert receipt.status_code == 200, receipt.text
    token = receipt.json()["lease_token"]
    acknowledge(api, project, change_id, 1, token)
    changed, put_intent = extend(api, project, target, current, work)
    notice = changed["extended_review"]["review_scope_changed"]
    assert changed["extended_review"]["holder"]["holder_session_id"] == "review-holder"
    assert (notice["old_scope_version"], notice["new_scope_version"], notice["episodes_added"]) == (
        1, 2, 1,
    )
    assert notice["captured_code_impact_rows_added"] == 1
    assert notice["distinct_references_in_added_rows"] == 1
    assert notice["distinct_decisions_in_added_rows"] == 1
    assert api.post(base + "/renew-claim", json={"lease_token": token}).json()[
        "review_scope_changed"
    ] == notice
    status = api.get(base, params={"status_only": True}).json()
    assert status["review_scope_changed"] == notice
    assert "scope-author" not in json.dumps(status) and "Expanded scope" not in json.dumps(status)
    checkpoint_intent = {**checkpoint_fields, "prompt": "Continuing investigation.",
                         "lease_token": token, "client_operation_id": str(uuid4())}
    checkpoint = api.post(base + "/checkpoints", json=checkpoint_intent)
    assert checkpoint.status_code == 201, checkpoint.text
    assert checkpoint.json()["review_scope_changed"] == notice
    event_intent = {"body": "Review in progress", "lease_token": token,
                    "actor": {"actor_client": "codex", "actor_session_id": "review-holder"},
                    "client_operation_id": str(uuid4())}
    event = api.post(base + "/events", json=event_intent)
    assert event.status_code == 201, event.text
    assert event.json()["review_scope_changed"] == notice
    token_free = api.post(base + "/checkpoints", json={
        **checkpoint_fields, "prompt": "Read-only contributor note",
        "client_operation_id": str(uuid4()),
    })
    assert "review_scope_changed" not in token_free.json()
    checkpoints = api.get(base + "/checkpoints").json()["items"]
    growth = next(row for row in checkpoints if row["source_metadata"].get("review_scope_changed"))
    assert "scope-author" in growth["prompt"]
    assert changed["decision"]["reference"] in growth["prompt"]
    assert "Previous scope: 1 episodes" in growth["prompt"]
    assert "Current scope: 2 episodes" in growth["prompt"]
    assert ('decision_impact(kind="code-impact") on each episode\'s prior pinned reference'
            in growth["prompt"])
    assert "page every fresh result, and compare it" in growth["prompt"]
    assert 'decision_impact(kind="context")' in growth["prompt"]
    assert "contradicts prerequisites or related records" in growth["prompt"]
    assert "extra live candidates and their evidenced conclusions separately" in growth["prompt"]
    assert "episode's frozen scope and do not fabricate ledger entries" in growth["prompt"]
    assert "episode/reference pair" in growth["prompt"]
    acknowledge(api, project, change_id, 2, token)
    assert "review_scope_changed" not in api.get(base, params={"status_only": True}).json()
    renewed = api.post(base + "/renew-claim", json={"lease_token": token}).json()
    assert "review_scope_changed" not in renewed
    assert api.post(base + "/checkpoints", json=checkpoint_intent).json() == checkpoint.json()
    assert api.post(base + "/events", json=event_intent).json() == event.json()
    assert api.put(path(project) + "/decisions", json=put_intent).json() == changed
    with Session(postgres_engine) as database:
        assert list(database.scalars(select(DecisionChangeAssessment.attachment_scope_version)
                    .where(DecisionChangeAssessment.change_id == UUID(change_id))
                    .order_by(DecisionChangeAssessment.attachment_scope_version))) == [1, 2]
        for name, query in DECISION_CHANGE_FINDINGS.items():
            assert database.scalar(text(query)) == 0, name
        with pytest.raises(DBAPIError), database.begin_nested():
            database.execute(text("UPDATE decision_change_assessments "
                                  "SET attachment_scope_version=99 WHERE change_id=:id"),
                             {"id": UUID(change_id)})


def test_scope_notice_survives_without_active_holder(api, project):
    target, _, current, work = root_review(api, project)
    change_id = current["change"]["change_id"]
    acknowledge(api, project, change_id, 1)
    changed, _ = extend(api, project, target, current, work)
    assert changed["extended_review"]["holder"] is None
    status = api.get(item_path(project, work), params={"status_only": True}).json()
    assert status["review_scope_changed"] == changed["extended_review"]["review_scope_changed"]
    assert group(api, project, change_id)["acknowledged_scope_version"] == 1


def test_legacy_unknown_boundaries_stay_unknown_and_new_bindings_are_exact(
    api, project, postgres_engine,
):
    from sqlalchemy import event

    def historical_attachment(_mapper, _connection, target):
        target.attachment_scope_version = None

    event.listen(DecisionChangeAssessment, "before_insert", historical_attachment)
    try:
        target, _, current, work = root_review(api, project)
        change_id = current["change"]["change_id"]
        acknowledge(api, project, change_id, 1)
        changed, _ = extend(api, project, target, current, work)
    finally:
        event.remove(DecisionChangeAssessment, "before_insert", historical_attachment)
    base = item_path(project, work)
    notice = api.get(base, params={"status_only": True}).json()["review_scope_changed"]
    assert notice["episodes_added"] == 1
    assert notice["captured_code_impact_rows_added"] is None
    assert notice["distinct_references_in_added_rows"] is None
    assert notice["distinct_decisions_in_added_rows"] is None
    newer, _ = extend(api, project, target, changed["decision"], work)
    assert newer["extended_review"]["review_scope_changed"]["captured_code_impact_rows_added"] == 1
    notice = api.get(base, params={"status_only": True}).json()["review_scope_changed"]
    assert notice["episodes_added"] == 2 and notice["captured_code_impact_rows_added"] is None
    stale, _ = update(api, project, "acknowledge", change_id=change_id, expected_scope_version=2)
    assert stale.status_code == 409
    acknowledge(api, project, change_id, 3)
    extend(api, project, target, newer["decision"], work)
    notice = api.get(base, params={"status_only": True}).json()["review_scope_changed"]
    assert notice["episodes_added"] == 1 and notice["captured_code_impact_rows_added"] == 1
    with Session(postgres_engine) as database:
        versions = list(database.scalars(select(DecisionChangeAssessment.attachment_scope_version)
                        .where(DecisionChangeAssessment.change_id == UUID(change_id))))
        assert versions.count(None) == 2 and {3, 4}.issubset(versions)


def test_shared_episode_bindings_count_actual_additions_and_distinct_identities(
    api, project, work_payload, postgres_engine,
):
    from .test_human_gates_postgres import create_work

    target, _, current, work = root_review(api, project)
    changed, _ = extend(api, project, target, current, work)
    base = item_path(project, work)
    pending = api.get(base, params={"status_only": True}).json()["review_scope_changed"]
    assert pending["episodes_added"] == 2
    assert pending["captured_code_impact_rows_added"] == 2
    assert pending["distinct_references_in_added_rows"] == 2
    assert pending["distinct_decisions_in_added_rows"] == 1
    episodes = api.get(path(project) + "/assessments", params={
        "change_id": current["change"]["change_id"],
    }).json()["items"]
    combined = create_work(api, project, work_payload, title="Combined review")["work_item"]
    adopted, _ = update(api, project, "adopt", work_item_id=combined["id"],
                        expected_work_version=combined["version"],
                        assessment_ids=[row["id"] for row in episodes])
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["scope_version"] == 2
    again, _ = update(api, project, "adopt", work_item_id=combined["id"],
                      expected_work_version=combined["version"],
                      assessment_ids=[row["id"] for row in episodes])
    assert again.status_code == 200 and again.json()["scope_version"] == 2
    assert changed["extended_review"]["review_scope_changed"]["episodes_added"] == 1
    with Session(postgres_engine) as database:
        for name, query in DECISION_CHANGE_FINDINGS.items():
            assert database.scalar(text(query)) == 0, name
        rows = list(database.scalars(select(DecisionChangeAssessment).where(
            DecisionChangeAssessment.assessment_id == UUID(episodes[0]["id"]),
        )))
        assert len(rows) == 2 and all(row.attachment_scope_version is not None for row in rows)


def test_backup_retains_attachment_versions_and_refuses_forged_order(
    api, project, postgres_engine,
):
    import io

    from mnemonic_backup.archive import BackupError, restore_project

    from .test_project_backup_archive import _export, _rewrite

    target, _, current, work = root_review(api, project)
    extend(api, project, target, current, work)
    archive = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    notice = api.get(item_path(project, work), params={"status_only": True}).json()[
        "review_scope_changed"
    ]
    assert notice["episodes_added"] == 2 and notice["captured_code_impact_rows_added"] == 2

    def forge(_header, rows):
        rows["decision_change_assessments"][0]["attachment_scope_version"] = 99

    with pytest.raises(BackupError, match="integrity"):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(archive, forge)))


def test_attachment_migration_keeps_legacy_unknowns_and_refuses_loss_of_known_versions(
    api, project, postgres_engine,
):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import event

    from .conftest import BACKEND_DIR

    def historical_attachment(_mapper, _connection, target):
        target.attachment_scope_version = None

    event.listen(DecisionChangeAssessment, "before_insert", historical_attachment)
    try:
        target, _, current, work = root_review(api, project)
    finally:
        event.remove(DecisionChangeAssessment, "before_insert", historical_attachment)
    # Domain policy is unrelated to the historical attachment-order migration.
    settings(api, project, automatic_decision_reviews=False, decision_review_priority=40)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0061_batch_dispositions")
        command.upgrade(config, "head")
        assert connection.scalar(text(
            "SELECT count(*) FROM decision_change_assessments "
            "WHERE attachment_scope_version IS NOT NULL"
        )) == 0
    extend(api, project, target, current, work)
    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        with postgres_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0061_batch_dispositions")


def test_attachment_guard_rejects_fabricated_order(api, project, postgres_engine):
    from mnemonic_api.models import DecisionChange

    _, _, current, _ = root_review(api, project)
    with Session(postgres_engine) as database:
        episode = database.scalar(select(DecisionChangeAssessment.assessment_id).where(
            DecisionChangeAssessment.change_id == UUID(current["change"]["change_id"]),
        ))
        with pytest.raises(DBAPIError), database.begin_nested():
            forged = DecisionChange(project_id=UUID(project["id"]), scope_version=1,
                                    acknowledged_scope_version=0)
            database.add(forged)
            database.flush()
            database.add(DecisionChangeAssessment(change_id=forged.id, assessment_id=episode,
                                                   attachment_scope_version=2))
            database.flush()
            database.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
