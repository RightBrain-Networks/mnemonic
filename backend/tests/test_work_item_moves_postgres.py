"""Identity-preserving work-item move API, history, receipt, and guard coverage."""

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import mnemonic_api.application.mutations as registered_mutations
import mnemonic_api.application.routes.project_reports as project_reports_routes

from .report_fixtures import reported
from .test_duplicate_handling_postgres import merge_work

pytestmark = pytest.mark.postgres


def _project(api, name: str) -> dict:
    response = api.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _work(api, project: dict, work_payload: dict, title: str) -> dict:
    response = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={
            **work_payload,
            "title": title,
            "summary": f"Durable move coverage for {title}.",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "source_session_id": f"move-{uuid4()}",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _path(project: dict, work: dict) -> str:
    return f"/api/v1/projects/{project['id']}/work-items/{work['id']}"


def _move_payload(target: dict, version: int, *, operation_id=None) -> dict:
    return {
        "target_project_id": target["id"],
        "expected_version": version,
        "actor": {
            "actor_client": "dashboard",
            "actor_session_id": "move-work-item-tests",
            "actor_model": "pytest",
        },
        "client_operation_id": str(operation_id or uuid4()),
    }


def _wait_for_application_locks(
    postgres_engine,
    connection_names,
    expected_count,
    *futures,
    failure_message,
):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with postgres_engine.connect() as observer:
            waiting = observer.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name=ANY(:names) AND wait_event_type='Lock'"
                ),
                {"names": list(connection_names)},
            )
        if waiting == expected_count:
            return
        for future in futures:
            if future.done():
                future.result()
        time.sleep(0.01)
    pytest.fail(failure_message)


def test_move_preserves_identity_status_history_and_replays_from_source(
    api, project, work_payload, postgres_engine
):
    target = _project(api, "Move destination")
    created = _work(api, project, work_payload, "Move deferred work")
    work = created["work_item"]
    source_path = _path(project, work)
    deferred = api.post(
        source_path + "/defer",
        json={
            "expected_version": 1,
            "actor": {
                "actor_client": "dashboard",
                "actor_session_id": "move-deferral",
            },
        },
    )
    assert deferred.status_code == 200, deferred.text
    payload = _move_payload(target, 2)

    response = api.post(source_path + "/move", json=payload)
    assert response.status_code == 200, response.text
    moved = response.json()
    assert moved["source_project_id"] == project["id"]
    assert moved["target_project_id"] == target["id"]
    assert moved["preserved_status"] == "deferred"
    assert moved["work_item"]["id"] == work["id"]
    assert moved["work_item"]["project_id"] == target["id"]
    assert moved["work_item"]["status"] == "deferred"
    assert moved["work_item"]["version"] == 3

    assert api.get(source_path).status_code == 404
    target_path = _path(target, work)
    assert api.get(target_path).json()["work_item"] == moved["work_item"]
    checkpoints = api.get(target_path + "/checkpoints").json()
    assert checkpoints["total"] == 1
    assert checkpoints["items"][0]["id"] == created["initial_checkpoint"]["id"]

    history = api.get(target_path + "/events", params={"order": "oldest"}).json()
    moved_events = [
        event for event in history["items"] if event["event_type"] == "work_moved"
    ]
    assert [event["metadata"]["role"] for event in moved_events] == ["source", "target"]
    assert [event["project_id"] for event in moved_events] == [project["id"], target["id"]]
    assert all(event["metadata"]["work_version"] == 3 for event in moved_events)
    assert all(
        event["metadata"]["move_id"] == moved_events[0]["metadata"]["move_id"]
        for event in moved_events
    )
    source_activity = api.get(f"/api/v1/projects/{project['id']}/activity").json()
    target_activity = api.get(f"/api/v1/projects/{target['id']}/activity").json()
    for activity, role in ((source_activity, "source"), (target_activity, "target")):
        moves = [
            item
            for item in activity["items"]
            if item["kind"] == "work_event" and item["event_type"] == "work_moved"
        ]
        assert len(moves) == 1
        assert moves[0]["work_item_id"] == work["id"]
        event_roles = {str(event["id"]): event["metadata"]["role"] for event in moved_events}
        assert event_roles[moves[0]["work_event_id"]] == role

    with postgres_engine.connect() as connection:
        move_row = connection.execute(
            text(
                "SELECT work_item_id,source_project_id,target_project_id,"
                "source_work_version,resulting_work_version,preserved_status "
                "FROM work_item_moves"
            )
        ).one()
        assert tuple(map(str, move_row[:3])) == (work["id"], project["id"], target["id"])
        assert move_row[3:] == (2, 3, "deferred")
        activity_projects = list(
            connection.scalars(
                text(
                    "SELECT activity.project_id FROM project_activity activity "
                    "JOIN work_events event ON event.id=activity.work_event_id "
                    "WHERE event.work_move_id IS NOT NULL ORDER BY event.id"
                )
            )
        )
        assert list(map(str, activity_projects)) == [project["id"], target["id"]]

    replay = api.post(source_path + "/move", json=payload)
    assert replay.status_code == 200
    assert replay.json() == moved
    assert (
        api.post(
            source_path + "/move",
            json={**payload, "target_project_id": _project(api, "Other move target")["id"]},
        ).json()["detail"]["code"]
        == "client_operation_conflict"
    )
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE client_operations "
                "DISABLE TRIGGER client_operation_mutation_guard"
            )
        )
        connection.execute(
            text(
                """
                UPDATE client_operations
                SET response_body=jsonb_set(
                    jsonb_set(response_body,'{preserved_status}','"pending"'::jsonb),
                    '{work_item,status}','"pending"'::jsonb
                )
                WHERE client_operation_id=:operation_id
                """
            ),
            {"operation_id": payload["client_operation_id"]},
        )
        connection.execute(
            text(
                "ALTER TABLE client_operations "
                "ENABLE TRIGGER client_operation_mutation_guard"
            )
        )
    corrupt_replay = api.post(source_path + "/move", json=payload)
    assert corrupt_replay.status_code == 503
    assert corrupt_replay.json()["detail"]["code"] == "client_operation_unavailable"


def test_move_replay_rejects_updated_at_that_differs_from_move_fact(
    api, project, work_payload, postgres_engine
):
    target = _project(api, "Timestamp receipt destination")
    work = _work(api, project, work_payload, "Move receipt timestamp binding")[
        "work_item"
    ]
    payload = _move_payload(target, work["version"])
    source_path = _path(project, work)
    moved = api.post(source_path + "/move", json=payload)
    assert moved.status_code == 200, moved.text

    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE client_operations "
                "DISABLE TRIGGER client_operation_mutation_guard"
            )
        )
        connection.execute(
            text(
                """
                UPDATE client_operations
                SET response_body=jsonb_set(
                    response_body,
                    '{work_item,updated_at}',
                    '"2000-01-01T00:00:00Z"'::jsonb,
                    false
                )
                WHERE client_operation_id=:operation_id
                """
            ),
            {"operation_id": payload["client_operation_id"]},
        )
        connection.execute(
            text(
                "ALTER TABLE client_operations "
                "ENABLE TRIGGER client_operation_mutation_guard"
            )
        )

    replay = api.post(source_path + "/move", json=payload)
    assert replay.status_code == 503, replay.text
    assert replay.json()["detail"]["code"] == "client_operation_unavailable"


def test_moved_work_report_provenance_is_global_and_snapshot_paged(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine,
    monkeypatch,
):
    target = _project(api, "Provenance move destination")
    work = _work(api, project, work_payload, "Move report provenance")["work_item"]
    completed = api.patch(
        _path(project, work),
        json=reported(
            {
                "expected_version": 1,
                "status": "wont-do",
            },
            retirement=True,
        ),
    )
    assert completed.status_code == 200, completed.text
    completion = completed.json()
    report = completion["job_completion_report"]
    report_path = (
        f"/api/v1/projects/{project['id']}/job-completion-reports/{report['id']}"
    )
    follow_ups = []
    for index in range(2):
        session_id = f"move-provenance-follow-up-{index}"
        response = api.post(
            report_path + "/follow-ups",
            json={
                "client_operation_id": str(uuid4()),
                "actor": {
                    "actor_client": checkpoint_fields["source_client"],
                    "actor_session_id": session_id,
                    "actor_model": checkpoint_fields.get("source_model"),
                },
                "title": f"Follow up after move {index}",
                "summary": "Confirm historical report provenance remains navigable.",
                "initial_checkpoint": {
                    **checkpoint_fields,
                    "source_session_id": session_id,
                },
            },
        )
        assert response.status_code == 201, response.text
        follow_ups.append(response.json())

    moved_source = api.post(
        _path(project, work) + "/move",
        json=_move_payload(target, completion["version"]),
    )
    assert moved_source.status_code == 200, moved_source.text
    created_path = _path(target, work) + "/report-follow-ups"
    first = api.get(
        created_path, params={"direction": "created", "limit": 1}
    )
    assert first.status_code == 200, first.text
    assert first.json()["has_more"] is True

    blocked_name = "move-provenance-writer-" + uuid4().hex
    writer_started = Event()
    original_create_follow_up = project_reports_routes.create_follow_up

    def tagged_create_follow_up(database, project_id, report_id, payload):
        database.execute(
            text("SELECT set_config('application_name', :name, true)"),
            {"name": blocked_name},
        )
        writer_started.set()
        return original_create_follow_up(database, project_id, report_id, payload)

    monkeypatch.setattr(
        project_reports_routes,
        "create_follow_up",
        tagged_create_follow_up,
    )
    third_session = "move-provenance-follow-up-uncommitted"
    third_payload = {
        "client_operation_id": str(uuid4()),
        "actor": {
            "actor_client": checkpoint_fields["source_client"],
            "actor_session_id": third_session,
            "actor_model": checkpoint_fields.get("source_model"),
        },
        "title": "Follow up committed between provenance pages",
        "summary": "Exercise the stable-work provenance high-water boundary.",
        "initial_checkpoint": {
            **checkpoint_fields,
            "source_session_id": third_session,
        },
    }

    holder = postgres_engine.connect()
    held_transaction = holder.begin()
    try:
        holder.execute(
            text(
                "SELECT report_id FROM job_completion_report_reviews "
                "WHERE report_id=:report_id FOR UPDATE"
            ),
            {"report_id": report["id"]},
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(api.post, report_path + "/follow-ups", json=third_payload)
            assert writer_started.wait(timeout=2)
            deadline = time.monotonic() + 3
            writer_is_blocked = False
            while time.monotonic() < deadline:
                with postgres_engine.connect() as observer:
                    writer_is_blocked = bool(
                        observer.scalar(
                            text(
                                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                "WHERE application_name=:name AND wait_event_type='Lock')"
                            ),
                            {"name": blocked_name},
                        )
                    )
                if writer_is_blocked:
                    break
                time.sleep(0.01)
            assert writer_is_blocked

            first = api.get(
                created_path,
                params={"direction": "created", "limit": 1},
            )
            assert first.status_code == 200, first.text
            first_page = first.json()
            assert first_page["has_more"] is True
            assert first_page["next_cursor"] is not None
            assert first_page["as_of_sequence"] == "2"
            held_transaction.commit()
            third = future.result(timeout=3)
    finally:
        if held_transaction.is_active:
            held_transaction.rollback()
        holder.close()
    assert third.status_code == 201, third.text

    second = api.get(
        created_path,
        params={
            "direction": "created",
            "limit": 1,
            "cursor": first_page["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    second_page = second.json()
    assert second_page["has_more"] is False
    assert second_page["as_of_sequence"] == first_page["as_of_sequence"]
    assert {item["id"] for item in first_page["items"] + second_page["items"]} == {
        follow_up["follow_up"]["id"] for follow_up in follow_ups
    }
    fresh = api.get(created_path, params={"direction": "created"})
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["as_of_sequence"] == "3"
    assert {item["id"] for item in fresh.json()["items"]} == {
        *(follow_up["follow_up"]["id"] for follow_up in follow_ups),
        third.json()["follow_up"]["id"],
    }
    created_rows = fresh.json()["items"]
    assert [item["created_at"] for item in created_rows] == sorted(
        item["created_at"] for item in created_rows
    )
    with pytest.raises(DBAPIError, match="immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE job_completion_report_follow_ups "
                    "SET created_at='2000-01-01T00:00:00Z' "
                    "WHERE id=:follow_up_id"
                ),
                {"follow_up_id": third.json()["follow_up"]["id"]},
            )
    with pytest.raises(DBAPIError, match="source managed"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE work_report_provenance_heads "
                    "SET last_sequence=last_sequence+1 WHERE work_item_id=:work_item_id"
                ),
                {"work_item_id": work["id"]},
            )
    assert api.get(
        _path(project, work) + "/report-follow-ups",
        params={"direction": "created"},
    ).status_code == 404

    moved_follow_up = api.post(
        _path(project, follow_ups[0]["work_item"]) + "/move",
        json=_move_payload(target, 1),
    )
    assert moved_follow_up.status_code == 200, moved_follow_up.text
    origin = api.get(
        _path(target, follow_ups[0]["work_item"]) + "/report-follow-ups",
        params={"direction": "origin"},
    )
    assert origin.status_code == 200, origin.text
    assert origin.json()["items"] == [follow_ups[0]["follow_up"]]
    assert api.get(report_path).status_code == 200
    assert (
        api.get(
            f"/api/v1/projects/{target['id']}/job-completion-reports/{report['id']}"
        ).status_code
        == 404
    )


def test_provenance_allocator_serializes_overlapping_origin_projects(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine,
):
    target = _project(api, "Second provenance origin")
    source = _work(api, project, work_payload, "Cross-origin provenance source")[
        "work_item"
    ]
    first_completion = api.patch(
        _path(project, source),
        json=reported(
            {
                "expected_version": 1,
                "status": "wont-do",
            },
            retirement=True,
        ),
    )
    assert first_completion.status_code == 200, first_completion.text
    first_report = first_completion.json()["job_completion_report"]
    moved = api.post(
        _path(project, source) + "/move",
        json=_move_payload(target, first_completion.json()["version"]),
    )
    assert moved.status_code == 200, moved.text
    reopened = api.patch(
        _path(target, source),
        json={
            "expected_version": moved.json()["work_item"]["version"],
            "status": "pending",
            "actor": {
                "actor_client": "dashboard",
                "actor_session_id": "second-provenance-reopen",
            },
        },
    )
    assert reopened.status_code == 200, reopened.text
    second_completion = api.post(
        _path(target, source) + "/complete",
        json=reported(
            {
                "expected_version": reopened.json()["version"],
                "checkpoint": {
                    **checkpoint_fields,
                    "source_session_id": "second-provenance-origin",
                },
            }
        ),
    )
    assert second_completion.status_code == 200, second_completion.text
    second_report = second_completion.json()["job_completion_report"]

    source_uuid = UUID(source["id"])
    lower_work = None
    for index in range(64):
        candidate = _work(
            api,
            project,
            work_payload,
            f"Lower provenance endpoint {index}",
        )["work_item"]
        if UUID(candidate["id"]) < source_uuid:
            lower_work = candidate
            break
    assert lower_work is not None
    with postgres_engine.begin() as connection:
        allocator_definition = connection.scalar(
            text(
                "SELECT pg_get_functiondef("
                "'mnemonic_activity_follow_up_source()'::regprocedure)"
            )
        )
        freshness_check = (
            "AND event.xmin=(pg_current_xact_id()::text::numeric % "
            "4294967296)::text::xid"
        )
        assert allocator_definition.count(freshness_check) == 1
        connection.execute(
            text(allocator_definition.replace(freshness_check, "AND true"))
        )
        connection.execute(
            text(
                "ALTER TABLE work_report_provenance_heads "
                "DISABLE TRIGGER work_report_provenance_head_guard"
            )
        )
        connection.execute(
            text(
                "INSERT INTO work_report_provenance_heads(work_item_id,last_sequence) "
                "VALUES (:lower_work_item_id,0),(:source_work_item_id,0)"
            ),
            {
                "lower_work_item_id": lower_work["id"],
                "source_work_item_id": source["id"],
            },
        )
        connection.execute(
            text(
                "ALTER TABLE work_report_provenance_heads "
                "ENABLE TRIGGER work_report_provenance_head_guard"
            )
        )

    blocked_name = "provenance-head-wait-" + uuid4().hex
    origin_follow_up_id = uuid4()

    def insert_origin_follow_up():
        with postgres_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('application_name', :name, true)"),
                {"name": blocked_name},
            )
            actor = connection.execute(
                text(
                    """
                    SELECT actor_client,actor_session_id,actor_model
                    FROM work_events
                    WHERE work_item_id=:work_item_id AND event_type='work_created'
                    ORDER BY id
                    LIMIT 1
                    """
                ),
                {"work_item_id": lower_work["id"]},
            ).one()
            connection.execute(
                text(
                    """
                    INSERT INTO job_completion_report_follow_ups (
                        id,project_id,report_id,source_work_item_id,
                        follow_up_work_item_id,actor_client,actor_session_id,
                        actor_model,created_at
                    ) VALUES (
                        :id,:project_id,:report_id,:source_work_item_id,
                        :follow_up_work_item_id,:actor_client,:actor_session_id,
                        :actor_model,'2000-01-01T00:00:00Z'
                    )
                    """
                ),
                {
                    "id": origin_follow_up_id,
                    "project_id": project["id"],
                    "report_id": first_report["id"],
                    "source_work_item_id": source["id"],
                    "follow_up_work_item_id": lower_work["id"],
                    **actor._mapping,
                },
            )
        return origin_follow_up_id

    holder = postgres_engine.connect()
    held_transaction = holder.begin()
    try:
        holder.execute(
            text(
                "SELECT work_item_id FROM work_report_provenance_heads "
                "WHERE work_item_id=:work_item_id FOR UPDATE"
            ),
            {"work_item_id": lower_work["id"]},
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(insert_origin_follow_up)
            deadline = time.monotonic() + 3
            waiting_on_head = False
            while time.monotonic() < deadline:
                with postgres_engine.connect() as observer:
                    waiting_on_head = bool(
                        observer.scalar(
                            text(
                                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                "WHERE application_name=:name AND wait_event_type='Lock')"
                            ),
                            {"name": blocked_name},
                        )
                    )
                if waiting_on_head:
                    break
                time.sleep(0.01)
            if future.done():
                future.result()
            assert waiting_on_head

            target_follow_up = api.post(
                f"/api/v1/projects/{target['id']}/job-completion-reports/"
                f"{second_report['id']}/follow-ups",
                json={
                    "client_operation_id": str(uuid4()),
                    "actor": {
                        "actor_client": checkpoint_fields["source_client"],
                        "actor_session_id": "overlapping-provenance-target",
                        "actor_model": checkpoint_fields.get("source_model"),
                    },
                    "title": "Committed while origin provenance waits",
                    "summary": "Prove provenance sequence and timestamp lock ordering.",
                    "initial_checkpoint": {
                        **checkpoint_fields,
                        "source_session_id": "overlapping-provenance-target",
                    },
                },
            )
            assert target_follow_up.status_code == 201, target_follow_up.text
            held_transaction.commit()
            assert future.result(timeout=3) == origin_follow_up_id
    finally:
        if held_transaction.is_active:
            held_transaction.rollback()
        holder.close()

    with postgres_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id,source_work_sequence,created_at "
                "FROM job_completion_report_follow_ups "
                "WHERE id IN (:origin_id,:target_id) ORDER BY source_work_sequence"
            ),
            {
                "origin_id": origin_follow_up_id,
                "target_id": target_follow_up.json()["follow_up"]["id"],
            },
        ).all()
        assert [row.source_work_sequence for row in rows] == [1, 2]
        assert str(rows[0].id) == target_follow_up.json()["follow_up"]["id"]
        assert rows[0].created_at < rows[1].created_at
        assert rows[1].created_at.year != 2000
        assert connection.scalar(
            text(
                "SELECT last_sequence FROM work_report_provenance_heads "
                "WHERE work_item_id=:work_item_id"
            ),
            {"work_item_id": source["id"]},
        ) == 2
    with postgres_engine.begin() as connection:
        connection.execute(text(allocator_definition))


def test_unkeyed_move_allows_omitted_actor(api, project, work_payload):
    target = _project(api, "Unattributed move destination")
    work = _work(api, project, work_payload, "Move without actor")["work_item"]
    payload = _move_payload(target, 1)
    payload.pop("actor")
    payload.pop("client_operation_id")

    response = api.post(_path(project, work) + "/move", json=payload)
    assert response.status_code == 200, response.text
    history = api.get(_path(target, work) + "/events", params={"order": "oldest"}).json()
    events = [event for event in history["items"] if event["event_type"] == "work_moved"]
    assert len(events) == 2
    assert all(event["actor_kind"] == "unattributed" for event in events)
    assert all(event["actor_client"] is None for event in events)


@pytest.mark.parametrize("status", ("pending", "deferred", "wont-do", "promoted"))
def test_move_preserves_lifecycle_without_review_policy(
    api, project, work_payload, checkpoint_fields, status
):
    target = _project(api, f"{status} move destination")
    work = _work(api, project, work_payload, f"Move {status} work")["work_item"]
    report = None
    if status == "deferred":
        transitioned = api.post(
            _path(project, work) + "/defer",
            json={"expected_version": 1},
        )
        assert transitioned.status_code == 200, transitioned.text
        work = transitioned.json()
    elif status in {"wont-do", "promoted"}:
        transitioned = api.patch(
            _path(project, work),
            json=reported(
                {"expected_version": 1, "status": status},
                retirement=True,
            ),
        )
        assert transitioned.status_code == 200, transitioned.text
        work = transitioned.json()
        report = work["job_completion_report"]

    moved = api.post(
        _path(project, work) + "/move",
        json=_move_payload(target, work["version"]),
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["preserved_status"] == status
    assert body["work_item"]["status"] == status
    assert body["work_item"]["id"] == work["id"]
    assert body["work_item"]["version"] == work["version"] + 1
    if report is not None:
        origin_report = (
            f"/api/v1/projects/{project['id']}/job-completion-reports/{report['id']}"
        )
        target_report = (
            f"/api/v1/projects/{target['id']}/job-completion-reports/{report['id']}"
        )
        assert api.get(origin_report).status_code == 200
        assert api.get(target_report).status_code == 404


def test_move_rejects_new_done_policy_preserving_episode_and_origin_report(
    api, project, work_payload, checkpoint_fields, postgres_engine
):
    target = _project(api, "Done move destination")
    work = _work(api, project, work_payload, "Move completed work")["work_item"]
    completed = api.post(
        _path(project, work) + "/complete",
        json=reported(
            {
                "expected_version": 1,
                "checkpoint": {
                    **checkpoint_fields,
                    "source_session_id": "move-done-completion",
                },
            }
        ),
    )
    assert completed.status_code == 200, completed.text
    completion = completed.json()

    moved = api.post(
        _path(project, work) + "/move",
        json=_move_payload(target, completion["work_item"]["version"]),
    )
    assert moved.status_code == 409, moved.text
    assert moved.json()["detail"]["code"] == "work_move_review_history_conflict"
    assert api.get(_path(target, work)).status_code == 404
    evidence = api.get(_path(project, work) + "/completion-evidence").json()
    assert evidence["total"] == 1
    assert evidence["current_completion_checkpoint_id"] == completion["checkpoint"]["id"]
    with postgres_engine.connect() as connection:
        assert str(
            connection.scalar(
                text("SELECT project_id FROM job_completion_reports WHERE work_item_id=:id"),
                {"id": work["id"]},
            )
        ) == project["id"]


def test_origin_report_resolves_canonical_work_after_move_then_target_merge(
    api, project, work_payload, checkpoint_fields
):
    target = _project(api, "Report canonical move destination")
    source = _work(api, project, work_payload, "Reported work moved before merge")[
        "work_item"
    ]
    completed = api.patch(
        _path(project, source),
        json=reported(
            {
                "expected_version": 1,
                "status": "wont-do",
            },
            retirement=True,
        ),
    )
    assert completed.status_code == 200, completed.text
    report = completed.json()["job_completion_report"]
    moved = api.post(
        _path(project, source) + "/move",
        json=_move_payload(target, completed.json()["version"]),
    )
    assert moved.status_code == 200, moved.text

    destination = _work(api, target, work_payload, "Canonical target after move")[
        "work_item"
    ]
    merged, _ = merge_work(api, target, moved.json()["work_item"], destination)
    assert merged["canonical_work_item"]["id"] == destination["id"]

    origin_report_path = (
        f"/api/v1/projects/{project['id']}/job-completion-reports/{report['id']}"
    )
    detail = api.get(origin_report_path)
    assert detail.status_code == 200, detail.text
    assert detail.json()["source_work_state"] == {
        "work_item_id": source["id"],
        "status": "wont-do",
        "canonical_work_item_id": destination["id"],
        "deleted": False,
    }
    assert (
        api.get(
            f"/api/v1/projects/{target['id']}/job-completion-reports/{report['id']}"
        ).status_code
        == 404
    )


def test_move_surfaces_unsealed_closeout_slot_as_stable_conflict(
    api, project, work_payload, postgres_engine
):
    target = _project(api, "Unsealed report move destination")
    work = _work(api, project, work_payload, "Move unsealed report slot")["work_item"]
    with postgres_engine.begin() as connection:
        for trigger in ("job_report_transition_guard", "job_report_transition_sealed"):
            connection.execute(
                text(f"ALTER TABLE work_items DISABLE TRIGGER {trigger}")
            )
        connection.execute(
            text(
                "UPDATE work_items SET last_reportable_closeout_version=version "
                "WHERE id=:work_item_id"
            ),
            {"work_item_id": work["id"]},
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        for trigger in ("job_report_transition_guard", "job_report_transition_sealed"):
            connection.execute(
                text(f"ALTER TABLE work_items ENABLE TRIGGER {trigger}")
            )

    response = api.post(
        _path(project, work) + "/move",
        json=_move_payload(target, 1),
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "closeout_report_unsealed"


@pytest.mark.parametrize("status", ("done", "wont-do", "promoted"))
def test_move_rejects_terminal_work_with_missing_closeout_slot(
    api, project, work_payload, checkpoint_fields, postgres_engine, status
):
    target = _project(api, f"Missing {status} report-slot destination")
    work = _work(api, project, work_payload, f"Move {status} without report slot")[
        "work_item"
    ]
    if status == "done":
        closed = api.post(
            _path(project, work) + "/complete",
            json=reported(
                {
                    "expected_version": work["version"],
                    "checkpoint": {
                        **checkpoint_fields,
                        "source_session_id": "move-missing-report-slot",
                    },
                }
            ),
        )
        assert closed.status_code == 200, closed.text
        work = closed.json()["work_item"]
    else:
        closed = api.patch(
            _path(project, work),
            json=reported(
                {"expected_version": work["version"], "status": status},
                retirement=True,
            ),
        )
        assert closed.status_code == 200, closed.text
        work = closed.json()

    with postgres_engine.begin() as connection:
        for trigger in ("job_report_transition_guard", "job_report_transition_sealed"):
            connection.execute(text(f"ALTER TABLE work_items DISABLE TRIGGER {trigger}"))
        connection.execute(
            text(
                "UPDATE work_items SET last_reportable_closeout_version=NULL "
                "WHERE id=:work_item_id"
            ),
            {"work_item_id": work["id"]},
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        for trigger in ("job_report_transition_guard", "job_report_transition_sealed"):
            connection.execute(text(f"ALTER TABLE work_items ENABLE TRIGGER {trigger}"))

    response = api.post(
        _path(project, work) + "/move",
        json=_move_payload(target, work["version"]),
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "closeout_report_unsealed"


def test_move_waiting_on_project_lock_uses_post_wait_lease_expiry(
    api, project, work_payload, postgres_engine, monkeypatch
):
    target = _project(api, "Post-wait lease move destination")
    work = _work(api, project, work_payload, "Lease expires during move wait")[
        "work_item"
    ]
    claimed = api.post(
        _path(project, work) + "/claim",
        json={
            "holder_client": "pytest",
            "holder_session_id": "lease-expiry-move-wait",
            "claim_request_id": "lease-expiry-move-wait",
        },
    )
    assert claimed.status_code == 200, claimed.text
    with postgres_engine.begin() as connection:
        expires_at = connection.scalar(
            text(
                "UPDATE work_leases SET expires_at=clock_timestamp()+interval '500 ms' "
                "WHERE work_item_id=:work_item_id RETURNING expires_at"
            ),
            {"work_item_id": work["id"]},
        )

    request_started = Event()
    original_prepare = registered_mutations.prepare_client_operation

    def tagged_prepare(*args, **kwargs):
        request_started.set()
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(registered_mutations, "prepare_client_operation", tagged_prepare)
    holder = postgres_engine.connect()
    held_transaction = holder.begin()
    try:
        holder.execute(
            text("SELECT id FROM projects WHERE id=:project_id FOR UPDATE"),
            {"project_id": project["id"]},
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                api.post,
                _path(project, work) + "/move",
                json=_move_payload(target, 1),
            )
            assert request_started.wait(timeout=2)
            time.sleep(0.05)
            assert not future.done()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with postgres_engine.connect() as observer:
                    if observer.scalar(text("SELECT clock_timestamp() > :expires_at"), {
                        "expires_at": expires_at,
                    }):
                        break
                time.sleep(0.01)
            else:
                pytest.fail("lease did not expire before the move lock was released")
            held_transaction.commit()
            moved = future.result(timeout=3)
    finally:
        if held_transaction.is_active:
            held_transaction.rollback()
        holder.close()

    assert moved.status_code == 200, moved.text
    assert moved.json()["preserved_status"] == "pending"
    readiness = api.get(_path(target, work) + "/context").json()["readiness"]
    assert readiness["display_state"] == "dropped"
    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM work_leases WHERE work_item_id=:work_item_id"),
            {"work_item_id": work["id"]},
        ) == 1


def test_opposite_direction_moves_share_uuid_sorted_project_lock_order(
    api, project, work_payload, postgres_engine, monkeypatch
):
    other = _project(api, "Opposite-direction move project")
    project_work = _work(api, project, work_payload, "Move from first to second")[
        "work_item"
    ]
    other_work = _work(api, other, work_payload, "Move from second to first")[
        "work_item"
    ]
    cases = {
        project["id"]: (project, other, project_work),
        other["id"]: (other, project, other_work),
    }
    lower_project_id, upper_project_id = sorted(cases)
    connection_names = {
        lower_project_id: "move-lower-first-" + uuid4().hex,
        upper_project_id: "move-upper-first-" + uuid4().hex,
    }
    original_project_mutation = registered_mutations.project_mutation

    @contextmanager
    def tagged_project_mutation(database, project_id, **kwargs):
        database.execute(
            text("SELECT set_config('application_name', :name, true)"),
            {"name": connection_names[str(project_id)]},
        )
        with original_project_mutation(database, project_id, **kwargs):
            yield

    monkeypatch.setattr(
        registered_mutations,
        "project_mutation",
        tagged_project_mutation,
    )

    def invoke(source, target, work):
        payload = _move_payload(target, 1)
        payload.pop("client_operation_id")
        return api.post(_path(source, work) + "/move", json=payload)

    holder = postgres_engine.connect()
    held_transaction = holder.begin()
    try:
        holder.execute(
            text("SELECT id FROM projects WHERE id=:project_id FOR UPDATE"),
            {"project_id": lower_project_id},
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            lower_future = executor.submit(invoke, *cases[lower_project_id])
            futures = {lower_project_id: lower_future}
            _wait_for_application_locks(
                postgres_engine,
                [connection_names[lower_project_id]],
                1,
                lower_future,
                failure_message="lower-source move did not wait on the shared first lock",
            )

            upper_future = executor.submit(invoke, *cases[upper_project_id])
            futures[upper_project_id] = upper_future
            _wait_for_application_locks(
                postgres_engine,
                connection_names.values(),
                2,
                lower_future,
                upper_future,
                failure_message="opposite moves did not converge on the shared first lock",
            )

            held_transaction.commit()
            responses = {
                project_id: future.result(timeout=5)
                for project_id, future in futures.items()
            }
    finally:
        if held_transaction.is_active:
            held_transaction.rollback()
        holder.close()

    for source_id, response in responses.items():
        source, target, work = cases[source_id]
        assert response.status_code == 200, response.text
        assert response.json()["source_project_id"] == source["id"]
        assert response.json()["target_project_id"] == target["id"]
        assert api.get(_path(source, work)).status_code == 404
        assert api.get(_path(target, work)).status_code == 200


def test_move_preserves_relationships_and_rejects_other_conflicts(
    api, project, work_payload, postgres_engine
):
    target = _project(api, "Guard move destination")

    scope_work = _work(api, project, work_payload, "Move scope guard")["work_item"]
    same = api.post(
        _path(project, scope_work) + "/move",
        json=_move_payload(project, 1),
    )
    assert same.status_code == 409
    assert same.json()["detail"]["code"] == "work_move_same_project"
    stale = api.post(
        _path(project, scope_work) + "/move",
        json=_move_payload(target, 2),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "version_conflict"
    missing = api.post(
        _path(project, scope_work) + "/move",
        json={
            **_move_payload(target, 1),
            "target_project_id": str(uuid4()),
        },
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "project_not_found"

    leased = _work(api, project, work_payload, "Move active lease guard")["work_item"]
    claim = api.post(
        _path(project, leased) + "/claim",
        json={
            "holder_client": "pytest",
            "holder_session_id": "move-active-lease",
            "claim_request_id": "move-active-lease-request",
        },
    )
    assert claim.status_code == 200, claim.text
    held = api.post(
        _path(project, leased) + "/move",
        json=_move_payload(target, 1),
    )
    assert held.status_code == 409
    assert held.json()["detail"]["code"] == "work_move_active_lease"
    assert held.json()["detail"]["context"] == {
        "holder_client": "pytest", "holder_session_id": "move-active-lease",
        "purpose": "implementation", "expires_at": claim.json()["expires_at"],
    }
    assert claim.json()["lease_token"] not in held.text
    assert "move-active-lease-request" not in held.text
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE work_leases SET "
                "acquired_at=clock_timestamp()-interval '3 seconds',"
                "renewed_at=clock_timestamp()-interval '2 seconds',"
                "expires_at=clock_timestamp()-interval '1 second' "
                "WHERE work_item_id=:work_item_id"
            ),
            {"work_item_id": leased["id"]},
        )
    dropped = api.get(_path(project, leased) + "/context").json()["readiness"]
    assert dropped["display_state"] == "dropped"
    moved_dropped = api.post(
        _path(project, leased) + "/move",
        json=_move_payload(target, 1),
    )
    assert moved_dropped.status_code == 200, moved_dropped.text
    retained = api.get(_path(target, leased) + "/context").json()["readiness"]
    assert retained["display_state"] == "dropped"
    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM work_leases WHERE work_item_id=:work_item_id"),
            {"work_item_id": leased["id"]},
        ) == 1

    related = _work(api, project, work_payload, "Move relationship guard")["work_item"]
    counterpart = _work(api, project, work_payload, "Move relationship counterpart")[
        "work_item"
    ]
    edge = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "related",
            "source_work_item_id": related["id"],
            "target_work_item_id": counterpart["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "move-relationship",
        },
    )
    assert edge.status_code == 200, edge.text
    relationship = edge.json()["relationship"]
    moved_related = api.post(
        _path(project, related) + "/move",
        json=_move_payload(target, 1),
    )
    assert moved_related.status_code == 200, moved_related.text
    assert moved_related.json()["preserved_status"] == related["status"]

    moved_context = api.get(_path(target, related) + "/context").json()
    counterpart_context = api.get(_path(project, counterpart) + "/context").json()
    assert moved_context["relationship_counts"]["total"] == 1
    assert counterpart_context["relationship_counts"]["total"] == 1
    moved_edge = moved_context["undirected_relationships"][0]
    counterpart_edge = counterpart_context["undirected_relationships"][0]
    assert moved_edge["relationship"] == relationship
    assert moved_edge["counterpart"]["project_id"] == project["id"]
    assert counterpart_edge["counterpart"]["project_id"] == target["id"]

    guarded_delete = api.post(
        _path(target, related) + "/delete", json={"expected_version": 2}
    )
    assert guarded_delete.status_code == 409
    assert guarded_delete.json()["detail"]["code"] == "active_relationships"
    removed = api.delete(
        f"/api/v1/projects/{project['id']}/relationships/{relationship['id']}"
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["removed"] is True
    with postgres_engine.connect() as connection:
        relationship_events = connection.execute(
            text(
                "SELECT work_item_id,project_id,event_type FROM work_events "
                "WHERE relationship_id=:relationship_id ORDER BY event_type,work_item_id"
            ),
            {"relationship_id": relationship["id"]},
        ).all()
    event_facts = {
        (str(item), str(event_project), event_type)
        for item, event_project, event_type in relationship_events
    }
    assert event_facts == {
        (related["id"], project["id"], "relationship_added"),
        (counterpart["id"], project["id"], "relationship_added"),
        (related["id"], target["id"], "relationship_removed"),
        (counterpart["id"], project["id"], "relationship_removed"),
    }

    gated = _work(api, project, work_payload, "Move gate guard")["work_item"]
    gate = api.post(
        _path(project, gated) + "/gates",
        json={
            "question": "Which project should own this work?",
            "requested_by_client": "pytest",
            "requested_by_session_id": "move-gate",
        },
    )
    assert gate.status_code == 201, gate.text
    rejected_gate = api.post(
        _path(project, gated) + "/move",
        json=_move_payload(target, 1),
    )
    assert rejected_gate.status_code == 409
    assert rejected_gate.json()["detail"]["code"] == "work_gated"

    alias = _work(api, project, work_payload, "Move duplicate alias")["work_item"]
    canonical = _work(api, project, work_payload, "Move duplicate canonical")["work_item"]
    merge_work(api, project, alias, canonical)
    rejected_duplicate = api.post(
        _path(project, canonical) + "/move",
        json=_move_payload(target, 2),
    )
    assert rejected_duplicate.status_code == 409
    assert rejected_duplicate.json()["detail"]["code"] == "work_move_duplicate_membership"
