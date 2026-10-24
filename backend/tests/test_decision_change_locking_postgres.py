"""Scope mutations lock moved work projects and retain ordinary timeline witnesses."""

import io
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mnemonic_api.knowledge_schemas import DecisionPut
from mnemonic_api.schemas import APIModel
from mnemonic_api.services import decision_change_locks
from mnemonic_backup.archive import restore_project

from .test_decision_changes_postgres import group, root_review, update
from .test_decision_reviews_postgres import context
from .test_human_gates_postgres import create_work
from .test_knowledge_postgres import path, put, put_body
from .test_project_activity_audit_postgres import _audit
from .test_project_backup_archive import _export
from .test_work_item_moves_postgres import _move_payload, _project

pytestmark = pytest.mark.postgres


def _move(api, source, target, work):
    result = api.post(f"/api/v1/projects/{source['id']}/work-items/{work['id']}/move",
                      json=_move_payload(target, work["version"]))
    assert result.status_code == 200, result.text
    return result.json()["work_item"]


def test_scope_discovery_includes_source_change_and_moved_driver_projects(
    api, project, work_payload, postgres_engine,
):
    target, _, revision, review = root_review(api, project)
    destination = _project(api, "Moved review")
    _move(api, project, destination, review)
    source_project = _project(api, "Review descendant")
    child = create_work(api, source_project, {
        **work_payload, "discovered_from_work_item_id": review["id"],
    }, title="Review-derived cross-project source")["work_item"]
    declaration = DecisionPut.model_validate(put_body(
        target, "Additional explicit ruling", decision_id=revision["decision_id"],
        expected_revision=revision["revision"], source_work_item_id=child["id"],
    ))
    expected = frozenset(UUID(value["id"]) for value in (project, destination, source_project))
    with Session(postgres_engine) as database:
        scope = decision_change_locks.discover_scope(database, "put_decision", {}, declaration)
        assert scope is not None and scope.project_ids == expected
        for kind in ("request_human_input", "resolve_human_input"):
            scope = decision_change_locks.discover_scope(
                database, kind, {"work_item_id": UUID(child["id"])}, APIModel(),
            )
            assert scope is not None and scope.project_ids == expected


def test_driver_moving_after_discovery_aborts_before_revision_and_exact_retry_succeeds(
    api, project, postgres_engine, monkeypatch,
):
    target, _, revision, review = root_review(api, project)
    destination = _project(api, "First review destination")
    moved = _move(api, project, destination, review)
    final = _project(api, "Concurrent review destination")
    operation_id = uuid4()
    payload = put_body(
        target, "Ruling after the driver moved", decision_id=revision["decision_id"],
        expected_revision=revision["revision"], client_operation_id=str(operation_id),
        change={"kind": "substantive", "change_id": revision["change"]["change_id"]},
    )
    discovered, release = Event(), Event()
    original = decision_change_locks.discover_scope

    def pause(database, kind, target, body):
        scope = original(database, kind, target, body)
        if (kind == "put_decision" and getattr(body, "client_operation_id", None) == operation_id
                and not discovered.is_set()):
            discovered.set()
            assert release.wait(timeout=10), "Scope discovery was not released"
        return scope

    monkeypatch.setattr(decision_change_locks, "discover_scope", pause)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(api.put, path(project) + "/decisions", json=payload)
        try:
            assert discovered.wait(timeout=5), "Decision scope was not discovered"
            _move(api, destination, final, moved)
        finally:
            release.set()
        response = pending.result(timeout=10)
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "client_operation_unavailable"
    unchanged = api.get(path(project) + f"/decisions/{revision['decision_id']}").json()
    assert unchanged["revision"] == revision["revision"]
    assert group(api, project, revision["change"]["change_id"])["scope_version"] == 1
    replay = put(api, project, payload)["decision"]
    assert replay["revision"] == revision["revision"] + 1
    assert group(api, project, revision["change"]["change_id"])["scope_version"] == 2


def test_adoption_acknowledgment_and_moved_scope_extension_survive_backup_and_audit(
    api, project, work_payload, postgres_engine,
):
    target, _, revision, review = root_review(api, project)
    change_id = revision["change"]["change_id"]
    destination = _project(api, "Source-owned moved review")
    _move(api, project, destination, review)
    acknowledged, _ = update(api, project, "acknowledge", change_id=change_id,
                             expected_scope_version=1)
    assert acknowledged.status_code == 200, acknowledged.text
    put(api, project, put_body(
        target, "Extended moved review ruling", decision_id=revision["decision_id"],
        expected_revision=revision["revision"],
        change={"kind": "substantive", "change_id": change_id},
    ))
    acknowledged, _ = update(api, project, "acknowledge", change_id=change_id,
                             expected_scope_version=2)
    assert acknowledged.status_code == 200, acknowledged.text
    ordinary = create_work(api, project, work_payload, title="Combined investigation")["work_item"]
    episodes = api.get(path(project) + "/assessments", params={"change_id": change_id}).json()
    adopted, _ = update(
        api, project, "adopt", work_item_id=ordinary["id"],
        expected_work_version=ordinary["version"],
        assessment_ids=[item["id"] for item in episodes["items"]],
    )
    assert adopted.status_code == 200, adopted.text
    adopted_id = adopted.json()["id"]
    acknowledged, _ = update(api, project, "acknowledge", change_id=adopted_id,
                             expected_scope_version=adopted.json()["scope_version"])
    assert acknowledged.status_code == 200, acknowledged.text
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("""
            SELECT count(*) FROM checkpoints checkpoint
            WHERE checkpoint.source_session_id LIKE 'decision-change:%' AND NOT EXISTS (
                SELECT 1 FROM work_events event WHERE event.checkpoint_id=checkpoint.id
                  AND event.event_type='checkpoint_added'
            )
        """)) == 0
    assert _audit(postgres_engine)["result"] == "pass"
    before = context(api, destination, review["id"])
    for selected in (destination, project):
        archive = _export(postgres_engine, selected)
        restore_project(postgres_engine, UUID(selected["id"]), io.BytesIO(archive))
    assert context(api, destination, review["id"]) == before
    assert _audit(postgres_engine)["result"] == "pass"
