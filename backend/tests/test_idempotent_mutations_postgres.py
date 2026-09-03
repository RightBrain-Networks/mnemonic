"""PostgreSQL acceptance coverage for all Phase 6-enrolled REST mutations."""

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

import mnemonic_api.application.middleware as middleware_module


def work_collection(project: dict) -> str:
    return f"/api/v1/projects/{project['id']}/work-items"


def work_path(project: dict, work_item: dict) -> str:
    return f"{work_collection(project)}/{work_item['id']}"


def actor(session: str) -> dict[str, str]:
    return {
        "actor_client": "pytest",
        "actor_session_id": session,
        "actor_model": "phase-6-test",
    }


def create_unkeyed(api, project: dict, work_payload: dict, title: str) -> dict:
    response = api.post(
        work_collection(project),
        json={**work_payload, "title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()["work_item"]


def table_count(engine, table: str) -> int:
    assert table in {
        "client_operations",
        "projects",
        "work_items",
        "checkpoints",
        "work_relationships",
        "work_events",
        "work_leases",
    }
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def work_event_count(engine, work_item_id: str) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT count(*) FROM work_events WHERE work_item_id = :work_item_id"),
            {"work_item_id": work_item_id},
        ).scalar_one()


def domain_lock_barrier(relation_name: str):
    barrier = Barrier(3)

    def pause_before_domain_lock(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        normalized = " ".join(statement.lower().split())
        if f"from {relation_name}" in normalized and "for update" in normalized:
            barrier.wait(timeout=5)

    return barrier, pause_before_domain_lock


def assert_exact_replay(api, method: str, path: str, body: dict, status: int):
    first = api.request(method, path, json=body)
    assert first.status_code == status, first.text
    replay = api.request(method, path, json=body)
    assert replay.status_code == status, replay.text
    assert replay.json() == first.json()
    return first.json()


@pytest.mark.parametrize(
    ("transport", "name", "location"),
    [
        ("query", "unexpected", "query"),
        ("query", "CLIENT_OPERATION_ID", "query"),
        ("query", "Client-Operation-Id", "query"),
        ("query", "Idempotency-Key", "query"),
        ("query", "X-Idempotency-Key", "query"),
        ("query", "X-Client-Operation-Id", "query"),
        ("header", "client_operation_id", "header"),
        ("header", "Client-Operation-Id", "header"),
        ("header", "Idempotency-Key", "header"),
        ("header", "X-Idempotency-Key", "header"),
        ("header", "X-Client-Operation-Id", "header"),
        ("cookie", "Client_Operation_Id", "cookie"),
        ("cookie", "Client-Operation-Id", "cookie"),
        ("cookie", "Idempotency-Key", "cookie"),
        ("cookie", "X-Idempotency-Key", "cookie"),
        ("cookie", "X-Client-Operation-Id", "cookie"),
    ],
)
def test_non_body_operation_id_transports_are_sanitized_and_have_no_effect(
    api, work_payload, postgres_engine, transport, name, location
):
    leaked_value = str(uuid4())
    body_operation_id = str(uuid4())
    request_options = {}
    if transport == "query":
        request_options["params"] = {name: leaked_value}
    elif transport == "header":
        request_options["headers"] = {name: leaked_value}
    else:
        request_options["headers"] = {"Cookie": f"{name}={leaked_value}"}

    response = api.post(
        f"/api/v1/projects/{uuid4()}/work-items",
        json={**work_payload, "client_operation_id": body_operation_id},
        **request_options,
    )

    assert response.status_code == 422
    expected_location = (
        ["query"]
        if transport == "query" and name == "unexpected"
        else [location, "client_operation_id"]
    )
    expected_message = (
        "Query parameters are not accepted for registered mutations."
        if transport == "query" and name == "unexpected"
        else (
            "Client operation IDs are accepted only in supported JSON "
            "request bodies."
        )
    )
    assert response.json() == {
        "detail": [
            {
                "type": "extra_forbidden",
                "loc": expected_location,
                "msg": expected_message,
            }
        ]
    }
    assert leaked_value not in response.text
    assert body_operation_id not in response.text
    assert table_count(postgres_engine, "projects") == 0
    assert table_count(postgres_engine, "work_items") == 0
    assert table_count(postgres_engine, "client_operations") == 0


def test_legitimate_read_queries_are_not_caught_by_operation_transport_guard(api):
    response = api.get("/api/v1/projects", params={"limit": 1, "offset": 0})
    assert response.status_code == 200, response.text


def test_authentication_precedes_operation_transport_rejection(
    api, work_payload, postgres_engine
):
    leaked_value = str(uuid4())
    authorization = api.headers.pop("Authorization")
    try:
        response = api.post(
            f"/api/v1/projects/{uuid4()}/work-items",
            headers={"Idempotency-Key": leaked_value},
            json={**work_payload, "client_operation_id": str(uuid4())},
        )
    finally:
        api.headers["Authorization"] = authorization

    assert response.status_code == 401
    assert response.json() == {"detail": "Valid bearer authentication is required"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert leaked_value not in response.text
    assert table_count(postgres_engine, "projects") == 0
    assert table_count(postgres_engine, "work_items") == 0
    assert table_count(postgres_engine, "client_operations") == 0


def test_keyed_checkpoint_metadata_reserved_names_never_reach_history_or_receipts(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine,
):
    rejected_create = api.post(
        work_collection(project),
        json={
            **work_payload,
            "title": "Rejected reserved create metadata",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "source_metadata": {
                    "nested": [{"LeAsE_ToKeN": "not-a-known-secret"}]
                },
            },
            "client_operation_id": str(uuid4()),
        },
    )
    assert rejected_create.status_code == 422
    assert rejected_create.json()["detail"]["code"] == "client_operation_secret_echo"
    assert table_count(postgres_engine, "work_items") == 0
    assert table_count(postgres_engine, "checkpoints") == 0
    assert table_count(postgres_engine, "work_events") == 0
    assert table_count(postgres_engine, "client_operations") == 0

    work = create_unkeyed(api, project, work_payload, "Reserved metadata target")
    endpoint = work_path(project, work)
    baseline = {
        table: table_count(postgres_engine, table)
        for table in ("work_items", "checkpoints", "work_events", "client_operations")
    }
    requests = [
        (
            f"{endpoint}/checkpoints",
            {
                **checkpoint_fields,
                "kind": "progress",
                "source_metadata": {
                    "nested": {"CLAIM_REQUEST_ID": "not-a-known-secret"}
                },
                "client_operation_id": str(uuid4()),
            },
        ),
        (
            f"{endpoint}/complete",
            {
                "expected_version": work["version"],
                "checkpoint": {
                    **checkpoint_fields,
                    "source_metadata": {
                        "nested": [{"Authorization": "not-a-known-secret"}]
                    },
                },
                "client_operation_id": str(uuid4()),
            },
        ),
    ]
    for path, body in requests:
        response = api.post(path, json=body)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "client_operation_secret_echo"
        assert {
            table: table_count(postgres_engine, table)
            for table in baseline
        } == baseline


def test_keyed_progress_gate_metadata_is_rejected_before_receipt_reservation(
    api,
    project,
    work_payload,
    postgres_engine,
):
    work = create_unkeyed(api, project, work_payload, "Reserved gate metadata target")
    endpoint = work_path(project, work)
    baseline = {
        table: table_count(postgres_engine, table)
        for table in ("work_items", "work_events", "client_operations")
    }

    for metadata in (
        {"gate_id": str(uuid4())},
        {"nested": [{"GaTe_TyPe": "human"}]},
    ):
        response = api.post(
            f"{endpoint}/events",
            json={
                "event_type": "progress",
                "body": "This must not reserve or append.",
                "metadata": metadata,
                "actor": actor("reserved-gate-metadata"),
                "client_operation_id": str(uuid4()),
            },
        )
        assert response.status_code == 422, response.text
        assert {
            table: table_count(postgres_engine, table)
            for table in baseline
        } == baseline


def test_case_varied_operation_uuid_cannot_enter_history_or_receipts(
    api,
    project,
    work_payload,
    postgres_engine,
):
    operation_id = str(uuid4()).upper()
    response = api.post(
        work_collection(project),
        json={
            **work_payload,
            "title": "Reject a case-varied operation echo",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "prompt": operation_id,
                "source_metadata": {
                    "nested": [{operation_id: operation_id.replace("-", "")}]
                },
            },
            "client_operation_id": operation_id,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "client_operation_secret_echo"
    assert operation_id not in response.text
    for table in ("work_items", "checkpoints", "work_events", "client_operations"):
        assert table_count(postgres_engine, table) == 0


def test_concurrent_keyed_create_executes_once_and_mismatch_conflicts(
    api, project, work_payload, postgres_engine
):
    operation_id = str(uuid4())
    payload = {
        **work_payload,
        "title": "Concurrent idempotent create",
        "client_operation_id": operation_id,
    }
    barrier = Barrier(2)

    def create():
        barrier.wait(timeout=5)
        return api.post(work_collection(project), json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: create(), range(2)))

    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    assert table_count(postgres_engine, "work_items") == 1
    assert table_count(postgres_engine, "checkpoints") == 1
    assert table_count(postgres_engine, "work_events") == 1
    assert table_count(postgres_engine, "client_operations") == 1

    conflict = api.post(
        work_collection(project),
        json={**payload, "title": "Changed under the same operation key"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "client_operation_conflict",
        "message": (
            "This client operation ID is already bound to a different successful request. "
            "Use a new ID only for a genuinely new intent."
        ),
        "context": {},
    }
    assert operation_id not in conflict.text
    assert table_count(postgres_engine, "work_items") == 1
    assert table_count(postgres_engine, "client_operations") == 1


def test_different_keys_reach_same_work_lock_without_receipt_deadlock(
    api, project, work_payload, postgres_engine
):
    work_item = create_unkeyed(api, project, work_payload, "Receipt-aware work race")
    endpoint = work_path(project, work_item)
    bodies = [
        {
            "expected_version": 1,
            "priority": priority,
            "actor": actor(f"same-work-{priority}"),
            "client_operation_id": str(uuid4()),
        }
        for priority in (41, 42)
    ]
    start = Barrier(3)
    authorization = api.headers["Authorization"]

    def update(body):
        with TestClient(api.app) as client:
            start.wait(timeout=5)
            return client.patch(
                endpoint,
                json=body,
                headers={"Authorization": authorization},
            )

    locker = postgres_engine.connect()
    locker_transaction = locker.begin()
    lock_barrier, observe_lock = domain_lock_barrier("work_items")
    try:
        locker.execute(
            text(
                "SELECT id FROM work_items "
                "WHERE id = CAST(:work_item_id AS uuid) FOR UPDATE"
            ),
            {"work_item_id": work_item["id"]},
        )
        event.listen(postgres_engine, "before_cursor_execute", observe_lock)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(update, body) for body in bodies]
            start.wait(timeout=5)
            try:
                lock_barrier.wait(timeout=5)
            except BaseException:
                lock_barrier.abort()
                locker_transaction.rollback()
                for future in futures:
                    future.result(timeout=5)
                raise
            else:
                locker_transaction.commit()
            responses = [future.result(timeout=5) for future in futures]
    finally:
        event.remove(postgres_engine, "before_cursor_execute", observe_lock)
        if locker_transaction.is_active:
            locker_transaction.rollback()
        locker.close()

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner_index = next(
        index for index, response in enumerate(responses) if response.status_code == 200
    )
    loser_index = 1 - winner_index
    winner = responses[winner_index]
    loser = responses[loser_index]
    assert winner.json()["priority"] == bodies[winner_index]["priority"]
    assert loser.json()["detail"]["code"] == "version_conflict"
    assert table_count(postgres_engine, "client_operations") == 1
    assert work_event_count(postgres_engine, work_item["id"]) == 2

    winner_replay = api.patch(endpoint, json=bodies[winner_index])
    assert winner_replay.status_code == 200
    assert winner_replay.json() == winner.json()
    loser_retry = api.patch(endpoint, json=bodies[loser_index])
    assert loser_retry.status_code == 409
    assert loser_retry.json()["detail"]["code"] == "version_conflict"
    assert table_count(postgres_engine, "client_operations") == 1


def test_different_keys_reach_same_graph_lock_and_replay_true_and_false(
    api, project, work_payload, postgres_engine
):
    source = create_unkeyed(api, project, work_payload, "Receipt-aware graph source")
    target = create_unkeyed(api, project, work_payload, "Receipt-aware graph target")
    endpoint = f"/api/v1/projects/{project['id']}/relationships"
    bodies = [
        {
            "relationship_type": "related",
            "source_work_item_id": source["id"],
            "target_work_item_id": target["id"],
            "created_by_client": "pytest",
            "created_by_session_id": f"same-graph-{index}",
            "created_by_model": "phase-6-test",
            "client_operation_id": str(uuid4()),
        }
        for index in range(2)
    ]
    start = Barrier(3)
    authorization = api.headers["Authorization"]

    def add(body):
        with TestClient(api.app) as client:
            start.wait(timeout=5)
            return client.post(
                endpoint,
                json=body,
                headers={"Authorization": authorization},
            )

    locker = postgres_engine.connect()
    locker_transaction = locker.begin()
    lock_barrier, observe_lock = domain_lock_barrier("projects")
    try:
        locker.execute(
            text(
                "SELECT id FROM projects "
                "WHERE id = CAST(:project_id AS uuid) FOR UPDATE"
            ),
            {"project_id": project["id"]},
        )
        event.listen(postgres_engine, "before_cursor_execute", observe_lock)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(add, body) for body in bodies]
            start.wait(timeout=5)
            try:
                lock_barrier.wait(timeout=5)
            except BaseException:
                lock_barrier.abort()
                locker_transaction.rollback()
                for future in futures:
                    future.result(timeout=5)
                raise
            else:
                locker_transaction.commit()
            responses = [future.result(timeout=5) for future in futures]
    finally:
        event.remove(postgres_engine, "before_cursor_execute", observe_lock)
        if locker_transaction.is_active:
            locker_transaction.rollback()
        locker.close()

    assert [response.status_code for response in responses] == [200, 200]
    assert {response.json()["created"] for response in responses} == {True, False}
    assert table_count(postgres_engine, "work_relationships") == 1
    assert table_count(postgres_engine, "client_operations") == 2
    for response, body in zip(responses, bodies, strict=True):
        replay = api.post(endpoint, json=body)
        assert replay.status_code == 200
        assert replay.json() == response.json()


def test_initial_related_direction_is_one_canonical_replay(
    api, project, work_payload, postgres_engine
):
    counterpart = create_unkeyed(
        api, project, work_payload, "Undirected replay counterpart"
    )
    operation_id = str(uuid4())
    incoming = {
        **work_payload,
        "title": "Undirected initial relationship replay",
        "initial_relationships": [
            {
                "type": "related",
                "direction": "incoming",
                "other_work_item_id": counterpart["id"],
            }
        ],
        "client_operation_id": operation_id,
    }
    first = api.post(work_collection(project), json=incoming)
    assert first.status_code == 201, first.text
    counts = {
        table: table_count(postgres_engine, table)
        for table in (
            "work_items",
            "checkpoints",
            "work_relationships",
            "work_events",
            "client_operations",
        )
    }

    outgoing = {
        **incoming,
        "initial_relationships": [
            {**incoming["initial_relationships"][0], "direction": "outgoing"}
        ],
    }
    replay = api.post(work_collection(project), json=outgoing)
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    assert {
        table: table_count(postgres_engine, table)
        for table in counts
    } == counts


def test_duplicate_initial_related_contexts_have_one_canonical_domain_winner(
    api, project, work_payload, checkpoint_fields, postgres_engine
):
    counterpart = create_unkeyed(
        api, project, work_payload, "Duplicate context counterpart"
    )
    first_context = counterpart["initial_checkpoint_id"]
    appended = api.post(
        f"{work_path(project, counterpart)}/checkpoints",
        json={**checkpoint_fields, "kind": "context"},
    )
    assert appended.status_code == 201, appended.text
    operation_id = str(uuid4())
    first_body = {
        **work_payload,
        "title": "Deterministic duplicate initial edge",
        "initial_relationships": [
            {
                "type": "related",
                "direction": "incoming",
                "other_work_item_id": counterpart["id"],
                "context_checkpoint_id": first_context,
            },
            {
                "type": "related",
                "direction": "outgoing",
                "other_work_item_id": counterpart["id"],
                "context_checkpoint_id": appended.json()["id"],
            },
        ],
        "client_operation_id": operation_id,
    }
    first = api.post(work_collection(project), json=first_body)
    assert first.status_code == 201, first.text
    expected_context = min(first_context, appended.json()["id"])
    assert len(first.json()["initial_relationships"]) == 1
    assert (
        first.json()["initial_relationships"][0]["context_checkpoint_id"]
        == expected_context
    )
    counts = {
        table: table_count(postgres_engine, table)
        for table in (
            "work_items",
            "checkpoints",
            "work_relationships",
            "work_events",
            "client_operations",
        )
    }
    replay_body = {
        **first_body,
        "initial_relationships": [
            {**first_body["initial_relationships"][0], "direction": "outgoing"},
            {**first_body["initial_relationships"][1], "direction": "incoming"},
        ],
    }
    replay = api.post(work_collection(project), json=replay_body)
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    assert {
        table: table_count(postgres_engine, table)
        for table in counts
    } == counts


def test_checkpoint_progress_and_update_replay_without_duplicate_history(
    api, project, work_payload, checkpoint_fields, postgres_engine
):
    work = create_unkeyed(api, project, work_payload, "Idempotent append target")
    endpoint = work_path(project, work)

    checkpoint_key = str(uuid4())
    checkpoint_body = {
        **checkpoint_fields,
        "kind": "progress",
        "client_operation_id": checkpoint_key,
    }
    checkpoint = assert_exact_replay(
        api,
        "POST",
        f"{endpoint}/checkpoints",
        checkpoint_body,
        201,
    )
    assert checkpoint["work_item_id"] == work["id"]

    progress_key = str(uuid4())
    progress_body = {
        "event_type": "progress",
        "body": "The receipt path is implemented.",
        "metadata": {"checks": ["database", "response"]},
        "actor": actor("progress"),
        "client_operation_id": progress_key,
    }
    event = assert_exact_replay(
        api,
        "POST",
        f"{endpoint}/events",
        progress_body,
        201,
    )
    assert event["work_item_id"] == work["id"]

    update_key = str(uuid4())
    update_body = {
        "expected_version": 1,
        "title": "Idempotent append target updated",
        "actor": actor("update-one"),
        "client_operation_id": update_key,
    }
    original_update = assert_exact_replay(api, "PATCH", endpoint, update_body, 200)
    assert original_update["version"] == 2

    later = api.patch(
        endpoint,
        json={
            "expected_version": 2,
            "priority": 77,
            "actor": actor("update-two"),
            "client_operation_id": str(uuid4()),
        },
    )
    assert later.status_code == 200, later.text
    assert later.json()["version"] == 3
    historical = api.patch(endpoint, json=update_body)
    assert historical.status_code == 200
    assert historical.json() == original_update
    current = api.get(endpoint).json()["work_item"]
    assert current["version"] == 3
    assert current["priority"] == 77

    mismatch = api.post(
        f"{endpoint}/checkpoints",
        json={**checkpoint_body, "prompt": "Changed prompt under the same key."},
    )
    assert mismatch.status_code == 409
    assert table_count(postgres_engine, "client_operations") == 4
    assert work_event_count(postgres_engine, work["id"]) == 5


def test_relationship_true_and_false_results_are_permanently_replayed(
    api, project, work_payload, postgres_engine
):
    source = create_unkeyed(api, project, work_payload, "Relationship source")
    target = create_unkeyed(api, project, work_payload, "Relationship target")
    collection = f"/api/v1/projects/{project['id']}/relationships"
    base = {
        "relationship_type": "related",
        "source_work_item_id": source["id"],
        "target_work_item_id": target["id"],
        "created_by_client": "pytest",
        "created_by_session_id": "relationship-add",
        "created_by_model": "phase-6-test",
    }

    add_key = str(uuid4())
    created = assert_exact_replay(
        api,
        "POST",
        collection,
        {**base, "client_operation_id": add_key},
        200,
    )
    assert created["created"] is True
    relationship_id = created["relationship"]["id"]

    no_op_add = assert_exact_replay(
        api,
        "POST",
        collection,
        {**base, "client_operation_id": str(uuid4())},
        200,
    )
    assert no_op_add["created"] is False

    removal_path = f"{collection}/{relationship_id}"
    removed = assert_exact_replay(
        api,
        "DELETE",
        removal_path,
        {"actor": actor("relationship-remove"), "client_operation_id": str(uuid4())},
        200,
    )
    assert removed["removed"] is True
    absent = assert_exact_replay(
        api,
        "DELETE",
        removal_path,
        {"actor": actor("relationship-absent"), "client_operation_id": str(uuid4())},
        200,
    )
    assert absent["removed"] is False

    assert table_count(postgres_engine, "client_operations") == 4
    assert table_count(postgres_engine, "work_relationships") == 0
    assert work_event_count(postgres_engine, source["id"]) == 3
    assert work_event_count(postgres_engine, target["id"]) == 3


def test_terminal_and_release_replay_before_disappeared_or_replaced_state(
    api, project, work_payload, checkpoint_fields, postgres_engine
):
    completion_work = create_unkeyed(api, project, work_payload, "Completion replay")
    completion_path = work_path(project, completion_work)
    completion_body = {
        "expected_version": 1,
        "checkpoint": {**checkpoint_fields, "prompt": "Completion evidence."},
        "client_operation_id": str(uuid4()),
    }
    completed = api.post(f"{completion_path}/complete", json=completion_body)
    assert completed.status_code == 200, completed.text
    reopened = api.patch(
        completion_path,
        json={
            "expected_version": 2,
            "status": "pending",
            "actor": actor("reopen-after-completion"),
            "client_operation_id": str(uuid4()),
        },
    )
    assert reopened.status_code == 200, reopened.text
    completion_replay = api.post(f"{completion_path}/complete", json=completion_body)
    assert completion_replay.status_code == 200
    assert completion_replay.json() == completed.json()
    assert api.get(completion_path).json()["work_item"]["status"] == "pending"

    deletion_work = create_unkeyed(api, project, work_payload, "Deletion replay")
    deletion_path = work_path(project, deletion_work)
    deletion_body = {
        "expected_version": 1,
        "actor": actor("delete"),
        "client_operation_id": str(uuid4()),
    }
    deleted = api.post(f"{deletion_path}/delete", json=deletion_body)
    assert deleted.status_code == 200, deleted.text
    assert api.get(deletion_path).status_code == 404
    deletion_replay = api.post(f"{deletion_path}/delete", json=deletion_body)
    assert deletion_replay.status_code == 200
    assert deletion_replay.json() == deleted.json()

    lease_work = create_unkeyed(api, project, work_payload, "Release replay")
    lease_path = work_path(project, lease_work)
    first_claim = api.post(
        f"{lease_path}/claim",
        json={
            "holder_client": "pytest",
            "holder_session_id": "first-holder",
            "claim_request_id": "first-phase6-claim",
        },
    )
    assert first_claim.status_code == 200, first_claim.text
    release_body = {
        "lease_token": first_claim.json()["lease_token"],
        "actor": actor("first-release"),
        "client_operation_id": str(uuid4()),
    }
    first_release = api.post(f"{lease_path}/release-claim", json=release_body)
    assert first_release.status_code == 200
    assert first_release.json()["released"] is True

    replacement = api.post(
        f"{lease_path}/claim",
        json={
            "holder_client": "pytest",
            "holder_session_id": "replacement-holder",
            "claim_request_id": "replacement-phase6-claim",
        },
    )
    assert replacement.status_code == 200, replacement.text
    release_replay = api.post(f"{lease_path}/release-claim", json=release_body)
    assert release_replay.status_code == 200
    assert release_replay.json() == first_release.json()
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT claim_request_id FROM work_leases WHERE work_item_id = :work_item_id"),
            {"work_item_id": lease_work["id"]},
        ).scalar_one() == "replacement-phase6-claim"

    replacement_release_body = {
        "lease_token": replacement.json()["lease_token"],
        "actor": actor("replacement-release"),
        "client_operation_id": str(uuid4()),
    }
    replacement_release = assert_exact_replay(
        api,
        "POST",
        f"{lease_path}/release-claim",
        replacement_release_body,
        200,
    )
    assert replacement_release["released"] is True
    absent_release = assert_exact_replay(
        api,
        "POST",
        f"{lease_path}/release-claim",
        {**replacement_release_body, "client_operation_id": str(uuid4())},
        200,
    )
    assert absent_release["released"] is False
    assert table_count(postgres_engine, "work_leases") == 0
    assert table_count(postgres_engine, "client_operations") == 6


def test_defer_replay_mismatch_and_domain_failure_are_durable_and_single_apply(
    api, project, work_payload, postgres_engine
):
    work_item = create_unkeyed(api, project, work_payload, "Idempotent deferral")
    endpoint = work_path(project, work_item)
    operation_id = str(uuid4())
    body = {
        "expected_version": 1,
        "actor": actor("defer-replay"),
        "client_operation_id": operation_id,
    }

    first = api.post(f"{endpoint}/defer", json=body)
    replay = api.post(f"{endpoint}/defer", json=body)

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert first.json()["status"] == "deferred"
    assert first.json()["version"] == 2
    mismatch = api.post(
        f"{endpoint}/defer",
        json={**body, "expected_version": 2},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "client_operation_conflict"
    assert api.get(endpoint).json()["work_item"] == first.json()
    assert work_event_count(postgres_engine, work_item["id"]) == 2
    assert table_count(postgres_engine, "client_operations") == 1

    pending = create_unkeyed(api, project, work_payload, "Deferral domain failure")
    pending_endpoint = work_path(project, pending)
    claimed = api.post(
        f"{pending_endpoint}/claim",
        json={
            "holder_client": "pytest",
            "holder_session_id": "defer-domain-failure",
            "claim_request_id": "defer-domain-failure",
        },
    )
    assert claimed.status_code == 200, claimed.text
    rejected_body = {
        "expected_version": 1,
        "actor": actor("defer-domain-failure"),
        "client_operation_id": str(uuid4()),
    }
    rejected = api.post(f"{pending_endpoint}/defer", json=rejected_body)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "lease_held"
    assert api.get(pending_endpoint).json()["work_item"]["status"] == "pending"
    assert table_count(postgres_engine, "client_operations") == 1


def test_defer_postcommit_response_loss_recovers_by_exact_replay(
    api, project, work_payload, postgres_engine, monkeypatch, caplog
):
    work_item = create_unkeyed(api, project, work_payload, "Lost deferral response")
    endpoint = work_path(project, work_item)
    body = {
        "expected_version": 1,
        "actor": actor("defer-response-loss"),
        "client_operation_id": str(uuid4()),
    }
    publications = []

    async def fail_once(event):
        publications.append(event)
        if len(publications) == 1:
            raise RuntimeError("synthetic lost defer response")

    monkeypatch.setattr(api.app.state.live_sync_hub, "publish", fail_once)
    middleware_module.logger.disabled = False
    caplog.set_level(logging.INFO, logger="mnemonic_api.application.middleware")
    caplog.clear()
    with pytest.raises(RuntimeError, match="synthetic lost defer response"):
        api.post(f"{endpoint}/defer", json=body)

    current = api.get(endpoint)
    assert current.status_code == 200
    assert current.json()["work_item"]["status"] == "deferred"
    assert current.json()["work_item"]["version"] == 2
    replay = api.post(f"{endpoint}/defer", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == current.json()["work_item"]
    assert len(publications) == 1
    outcomes = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Client operation outcome")
    ]
    assert outcomes == [
        "Client operation outcome kind=defer_work outcome=executed",
        "Client operation outcome kind=defer_work outcome=replayed",
    ]
    assert work_event_count(postgres_engine, work_item["id"]) == 2
    assert table_count(postgres_engine, "client_operations") == 1
