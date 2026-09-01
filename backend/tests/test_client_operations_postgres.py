"""Real PostgreSQL coverage for the Phase 6 receipt protocol."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import ClientOperation
from mnemonic_api.schemas import (
    MutationActor,
    RelationshipRemovalCreate,
    WorkDeletionCreate,
    WorkDeletionRead,
    WorkItemPatch,
)
from mnemonic_api.services.client_operations import (
    MAX_RESPONSE_BYTES,
    ReplayedOperation,
    ReservedOperation,
    complete_client_operation,
    prepare_client_operation,
    reserve_client_operation,
)

pytestmark = pytest.mark.postgres

PROJECT_ID = UUID("10000000-0000-0000-0000-000000000016")
WORK_ID = UUID("20000000-0000-0000-0000-000000000016")
OTHER_WORK_ID = UUID("20000000-0000-0000-0000-000000000017")


class OversizedDeletionRead(WorkDeletionRead):
    padding: str


@pytest.fixture
def clean_client_operations(
    api: TestClient,
    postgres_engine: Engine,
):
    del api
    yield
    with postgres_engine.begin() as connection:
        connection.execute(text("TRUNCATE client_operations RESTART IDENTITY"))


def deletion_request(
    operation_id: UUID,
    *,
    expected_version: int = 1,
    actor_session_id: str = "phase-6-postgres",
) -> WorkDeletionCreate:
    return WorkDeletionCreate(
        expected_version=expected_version,
        actor=MutationActor(
            actor_client="pytest",
            actor_session_id=actor_session_id,
            actor_model="test-model",
        ),
        client_operation_id=operation_id,
    )


def prepared_deletion(
    operation_id: UUID,
    *,
    expected_version: int = 1,
    actor_session_id: str = "phase-6-postgres",
):
    return prepare_client_operation(
        "delete_work",
        PROJECT_ID,
        {"work_item_id": WORK_ID},
        deletion_request(
            operation_id,
            expected_version=expected_version,
            actor_session_id=actor_session_id,
        ),
    )


def deletion_result(version: int = 2) -> dict[str, object]:
    return {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "version": version,
    }


def test_reserve_finalize_and_exact_replay(
    clean_client_operations,
    postgres_engine: Engine,
):
    operation_id = uuid4()
    prepared = prepared_deletion(operation_id)
    with Session(postgres_engine) as database:
        reserved = reserve_client_operation(database, prepared, wait_seconds=2)
        assert isinstance(reserved, ReservedOperation)
        assert database.scalar(select(func.count()).select_from(ClientOperation)) == 1
        assert database.scalar(
            select(ClientOperation.state).where(ClientOperation.id == reserved.receipt_id)
        ) == "pending"
        assert database.execute(text("SHOW lock_timeout")).scalar_one() == "0"
        assert database.execute(text("SHOW statement_timeout")).scalar_one() == "0"

        completed = complete_client_operation(
            database,
            reserved,
            deletion_result(),
            mutation_applied=True,
        )
        original_body = json.loads(completed.response.body)
        database.commit()

    with Session(postgres_engine) as database:
        replay = reserve_client_operation(database, prepared, wait_seconds=2)
        assert isinstance(replay, ReplayedOperation)
        assert replay.status == 200
        assert replay.mutation_applied is True
        assert json.loads(replay.response.body) == original_body
        assert replay.typed_body.model_dump(mode="json") == original_body
        database.commit()

    with postgres_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT operation_kind, state, response_status, response_body,
                       mutation_applied,
                       octet_length(request_fingerprint_salt) AS salt_bytes,
                       octet_length(request_fingerprint) AS fingerprint_bytes
                FROM client_operations
                """
            )
        ).mappings().one()
        assert row == {
            "operation_kind": "delete_work",
            "state": "completed",
            "response_status": 200,
            "response_body": original_body,
            "mutation_applied": True,
            "salt_bytes": 32,
            "fingerprint_bytes": 32,
        }


@pytest.mark.parametrize(
    "changed",
    [
        {"expected_version": 2},
        {"actor_session_id": "changed-session"},
    ],
)
def test_same_scope_semantic_mismatch_is_sanitized_conflict(
    clean_client_operations,
    postgres_engine: Engine,
    changed,
):
    operation_id = uuid4()
    with Session(postgres_engine) as database:
        reserved = reserve_client_operation(
            database,
            prepared_deletion(operation_id),
            wait_seconds=2,
        )
        assert isinstance(reserved, ReservedOperation)
        complete_client_operation(
            database,
            reserved,
            deletion_result(),
            mutation_applied=True,
        )
        database.commit()

    with Session(postgres_engine) as database:
        with pytest.raises(ApplicationError) as captured:
            reserve_client_operation(
                database,
                prepared_deletion(operation_id, **changed),
                wait_seconds=2,
            )
        assert captured.value.status_code == 409
        assert captured.value.detail == {
            "code": "client_operation_conflict",
            "message": (
                "This client operation ID is already bound to a different successful request. "
                "Use a new ID only for a genuinely new intent."
            ),
            "context": {},
        }
        assert not database.in_transaction()

    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 1


def test_operation_target_and_project_are_part_of_the_bound_scope(
    clean_client_operations,
    postgres_engine: Engine,
):
    operation_id = uuid4()
    original = prepared_deletion(operation_id)
    with Session(postgres_engine) as database:
        reserved = reserve_client_operation(database, original, wait_seconds=2)
        assert isinstance(reserved, ReservedOperation)
        complete_client_operation(
            database,
            reserved,
            deletion_result(),
            mutation_applied=True,
        )
        database.commit()

    changed_target = prepare_client_operation(
        "delete_work",
        PROJECT_ID,
        {"work_item_id": OTHER_WORK_ID},
        deletion_request(operation_id),
    )
    changed_kind = prepare_client_operation(
        "remove_relationship",
        PROJECT_ID,
        {"relationship_id": WORK_ID},
        RelationshipRemovalCreate(
            actor=MutationActor(
                actor_client="pytest",
                actor_session_id="phase-6-postgres",
                actor_model="test-model",
            ),
            client_operation_id=operation_id,
        ),
    )
    for mismatch in (changed_target, changed_kind):
        with Session(postgres_engine) as database:
            with pytest.raises(ApplicationError) as captured:
                reserve_client_operation(database, mismatch, wait_seconds=2)
            assert captured.value.status_code == 409
            assert captured.value.detail["code"] == "client_operation_conflict"
            assert captured.value.detail["context"] == {}
            assert not database.in_transaction()

    other_project = prepare_client_operation(
        "delete_work",
        uuid4(),
        {"work_item_id": WORK_ID},
        deletion_request(operation_id),
    )
    with Session(postgres_engine) as database:
        independent = reserve_client_operation(database, other_project, wait_seconds=2)
        assert isinstance(independent, ReservedOperation)
        database.rollback()

    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 1


def test_rolled_back_reservation_leaves_key_reusable(
    clean_client_operations,
    postgres_engine: Engine,
):
    operation_id = uuid4()
    prepared = prepared_deletion(operation_id)
    with Session(postgres_engine) as database:
        first = reserve_client_operation(database, prepared, wait_seconds=2)
        assert isinstance(first, ReservedOperation)
        database.rollback()

    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 0

    with Session(postgres_engine) as database:
        second = reserve_client_operation(database, prepared, wait_seconds=2)
        assert isinstance(second, ReservedOperation)
        complete_client_operation(
            database,
            second,
            deletion_result(),
            mutation_applied=True,
        )
        database.commit()

    with Session(postgres_engine) as database:
        replay = reserve_client_operation(database, prepared, wait_seconds=2)
        assert isinstance(replay, ReplayedOperation)
        assert replay.mutation_applied is True
        database.commit()


def test_oversized_response_rolls_back_receipt_and_flushed_domain_state(
    clean_client_operations,
    postgres_engine: Engine,
):
    with Session(postgres_engine) as database:
        reserved = reserve_client_operation(
            database, prepared_deletion(uuid4()), wait_seconds=2
        )
        assert isinstance(reserved, ReservedOperation)
        database.execute(
            text(
                "INSERT INTO projects (id, name, slug, description) "
                "VALUES (:id, 'Atomic response', 'atomic-response', '')"
            ),
            {"id": PROJECT_ID},
        )
        oversized_spec = replace(
            reserved.spec,
            response_model=OversizedDeletionRead,
        )
        oversized_operation = replace(reserved, spec=oversized_spec)
        with pytest.raises(ApplicationError) as captured:
            complete_client_operation(
                database,
                oversized_operation,
                {
                    **deletion_result(),
                    "padding": "x" * (MAX_RESPONSE_BYTES + 1),
                },
                mutation_applied=True,
            )
        assert captured.value.status_code == 503
        assert captured.value.detail["code"] == "client_operation_unavailable"
        assert not database.in_transaction()

    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM projects")).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 0


def test_commit_failure_after_receipt_completion_leaves_no_partial_state(
    clean_client_operations,
    postgres_engine: Engine,
):
    missing_checkpoint_id = uuid4()
    with Session(postgres_engine) as database:
        reserved = reserve_client_operation(
            database, prepared_deletion(uuid4()), wait_seconds=2
        )
        assert isinstance(reserved, ReservedOperation)
        database.execute(
            text(
                "INSERT INTO projects (id, name, slug, description) "
                "VALUES (:id, 'Deferred failure', 'deferred-failure', '')"
            ),
            {"id": PROJECT_ID},
        )
        database.execute(
            text(
                """
                INSERT INTO work_items (
                    id, project_id, title, summary, status, priority,
                    initial_checkpoint_id, version
                ) VALUES (
                    :work_id, :project_id, 'Deferred failure',
                    'The missing initial checkpoint rejects commit.', 'pending', 0,
                    :checkpoint_id, 1
                )
                """
            ),
            {
                "work_id": WORK_ID,
                "project_id": PROJECT_ID,
                "checkpoint_id": missing_checkpoint_id,
            },
        )
        complete_client_operation(
            database,
            reserved,
            deletion_result(),
            mutation_applied=True,
        )
        with pytest.raises(DBAPIError):
            database.commit()
        database.rollback()

    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM projects")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM work_items")).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 0


@pytest.mark.parametrize(
    ("response_body", "mutation_applied"),
    [
        ({}, True),
        (
            {
                "project_id": str(PROJECT_ID),
                "work_item_id": str(WORK_ID),
                "version": 2,
            },
            True,
        ),
        (
            {
                "deleted": "false",
                "project_id": str(PROJECT_ID),
                "work_item_id": str(WORK_ID),
                "version": 2,
            },
            False,
        ),
        (
            {
                "deleted": True,
                "project_id": str(PROJECT_ID),
                "work_item_id": str(WORK_ID),
                "version": "2",
            },
            True,
        ),
        (
            {
                "deleted": True,
                "project_id": str(PROJECT_ID),
                "work_item_id": "not-a-uuid",
                "version": 2,
            },
            True,
        ),
        (
            {
                "deleted": True,
                "project_id": str(PROJECT_ID),
                "work_item_id": str(WORK_ID),
                "version": 2,
                "unexpected": "field",
            },
            True,
        ),
        (
            {
                "deleted": True,
                "project_id": str(PROJECT_ID),
                "work_item_id": str(OTHER_WORK_ID),
                "version": 2,
            },
            True,
        ),
        (
            {
                "deleted": True,
                "project_id": str(PROJECT_ID),
                "work_item_id": str(WORK_ID),
                "version": 2,
            },
            False,
        ),
    ],
    ids=[
        "missing-shape",
        "missing-default",
        "coercible-bool",
        "coercible-int",
        "invalid-uuid",
        "extra-field",
        "wrong-target",
        "wrong-outcome",
    ],
)
def test_invalid_completed_snapshot_fails_closed_without_domain_fallback(
    clean_client_operations,
    postgres_engine: Engine,
    response_body,
    mutation_applied,
):
    operation_id = uuid4()
    prepared = prepared_deletion(operation_id)
    with Session(postgres_engine) as database:
        reserved = reserve_client_operation(database, prepared, wait_seconds=2)
        assert isinstance(reserved, ReservedOperation)
        database.execute(
            update(ClientOperation)
            .where(ClientOperation.id == reserved.receipt_id)
            .values(
                state="completed",
                response_status=200,
                response_body=response_body,
                mutation_applied=mutation_applied,
                completed_at=func.greatest(
                    ClientOperation.created_at,
                    func.clock_timestamp(),
                ),
            )
        )
        database.commit()

    with Session(postgres_engine) as database:
        with pytest.raises(ApplicationError) as captured:
            reserve_client_operation(database, prepared, wait_seconds=2)
        assert captured.value.status_code == 503
        assert captured.value.detail == {
            "code": "client_operation_unavailable",
            "message": (
                "Client operation safety is unavailable. Retry the same ID with the exact "
                "same request."
            ),
            "context": {},
        }
        assert not database.in_transaction()

    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 1


def test_completed_snapshot_echoing_operation_id_fails_closed(
    clean_client_operations,
    postgres_engine: Engine,
):
    operation_id = uuid4()
    prepared = prepared_deletion(operation_id)
    with Session(postgres_engine) as database:
        reserved = reserve_client_operation(database, prepared, wait_seconds=2)
        assert isinstance(reserved, ReservedOperation)
        database.execute(
            update(ClientOperation)
            .where(ClientOperation.id == reserved.receipt_id)
            .values(
                state="completed",
                response_status=200,
                response_body={
                    "deleted": True,
                    "project_id": str(operation_id),
                    "work_item_id": str(WORK_ID),
                    "version": 2,
                },
                mutation_applied=True,
                completed_at=func.greatest(
                    ClientOperation.created_at,
                    func.clock_timestamp(),
                ),
            )
        )
        database.commit()

    with Session(postgres_engine) as database:
        with pytest.raises(ApplicationError) as captured:
            reserve_client_operation(database, prepared, wait_seconds=2)
        assert captured.value.status_code == 503
        assert captured.value.detail["code"] == "client_operation_unavailable"
        assert captured.value.detail["context"] == {}
        assert not database.in_transaction()


@pytest.mark.parametrize(
    "echo_value",
    [
        "40000000-0000-0000-0000-000000000016".upper(),
        "40000000000000000000000000000016",
        "{40000000-0000-0000-0000-000000000016}",
    ],
)
def test_stored_coherent_snapshot_with_equivalent_operation_id_fails_closed(
    clean_client_operations,
    postgres_engine: Engine,
    echo_value: str,
):
    operation_id = UUID("40000000-0000-0000-0000-000000000016")
    prepared = prepare_client_operation(
        "update_work",
        PROJECT_ID,
        {"work_item_id": WORK_ID},
        WorkItemPatch(
            expected_version=1,
            priority=2,
            actor=MutationActor(
                actor_client="pytest",
                actor_session_id="phase-6-postgres",
            ),
            client_operation_id=operation_id,
        ),
    )
    response_body = {
        "id": str(WORK_ID),
        "project_id": str(PROJECT_ID),
        "title": echo_value,
        "summary": "A coherent stored response with a forbidden control-data echo.",
        "status": "pending",
        "priority": 2,
        "initial_checkpoint_id": "30000000-0000-0000-0000-000000000016",
        "version": 2,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:01Z",
    }
    with Session(postgres_engine) as database:
        reserved = reserve_client_operation(database, prepared, wait_seconds=2)
        assert isinstance(reserved, ReservedOperation)
        database.execute(
            update(ClientOperation)
            .where(ClientOperation.id == reserved.receipt_id)
            .values(
                state="completed",
                response_status=200,
                response_body=response_body,
                mutation_applied=True,
                completed_at=func.greatest(
                    ClientOperation.created_at,
                    func.clock_timestamp(),
                ),
            )
        )
        database.commit()

    with Session(postgres_engine) as database:
        with pytest.raises(ApplicationError) as captured:
            reserve_client_operation(database, prepared, wait_seconds=2)

    assert captured.value.status_code == 503
    assert captured.value.detail["code"] == "client_operation_unavailable"


def test_database_guards_reject_invalid_lifecycle(
    clean_client_operations,
    postgres_engine: Engine,
):
    values = {
        "project_id": PROJECT_ID,
        "client_operation_id": uuid4(),
        "operation_kind": "delete_work",
        "salt": bytes(range(32)),
        "fingerprint": bytes(reversed(range(32))),
        "response_body": '{"deleted":true}',
    }
    pending_insert = text(
        """
        INSERT INTO client_operations (
            project_id, client_operation_id, operation_kind,
            request_fingerprint_salt, request_fingerprint
        ) VALUES (
            :project_id, :client_operation_id, :operation_kind,
            :salt, :fingerprint
        )
        RETURNING id
        """
    )
    direct_completed_insert = text(
        """
        INSERT INTO client_operations (
            project_id, client_operation_id, operation_kind,
            request_fingerprint_salt, request_fingerprint, state,
            response_status, response_body, mutation_applied, completed_at
        ) VALUES (
            :project_id, :client_operation_id, :operation_kind,
            :salt, :fingerprint, 'completed', 200,
            CAST(:response_body AS jsonb), true, clock_timestamp()
        )
        """
    )

    with pytest.raises(DBAPIError, match="pending client operation cannot commit") as pending:
        with postgres_engine.begin() as connection:
            connection.execute(pending_insert, values)
    assert getattr(pending.value.orig, "sqlstate", None) == "55000"

    with pytest.raises(DBAPIError, match="must be inserted pending") as completed:
        with postgres_engine.begin() as connection:
            connection.execute(
                direct_completed_insert,
                {**values, "client_operation_id": uuid4()},
            )
    assert getattr(completed.value.orig, "sqlstate", None) == "55000"

    with postgres_engine.begin() as connection:
        receipt_id = connection.execute(
            pending_insert,
            {**values, "client_operation_id": uuid4()},
        ).scalar_one()
        connection.execute(
            text(
                """
                UPDATE client_operations
                SET state = 'completed',
                    response_status = 200,
                    response_body = CAST(:response_body AS jsonb),
                    mutation_applied = true,
                    completed_at = greatest(created_at, clock_timestamp())
                WHERE id = :receipt_id
                """
            ),
            {
                "receipt_id": receipt_id,
                "response_body": values["response_body"],
            },
        )

    for statement in (
        "UPDATE client_operations SET response_status = 201 WHERE id = :receipt_id",
        "DELETE FROM client_operations WHERE id = :receipt_id",
    ):
        with pytest.raises(DBAPIError) as immutable:
            with postgres_engine.begin() as connection:
                connection.execute(text(statement), {"receipt_id": receipt_id})
        assert getattr(immutable.value.orig, "sqlstate", None) == "55000"


def test_concurrent_same_key_waits_then_replays_one_completed_receipt(
    clean_client_operations,
    postgres_engine: Engine,
):
    operation_id = uuid4()
    prepared = prepared_deletion(operation_id)
    waiter_ready = Event()
    waiter_application_name = "mnemonic_phase6_waiter_" + uuid4().hex

    def wait_for_receipt():
        with Session(postgres_engine) as database:
            database.execute(
                text("SELECT set_config('application_name', :name, true)"),
                {"name": waiter_application_name},
            )
            waiter_ready.set()
            result = reserve_client_operation(database, prepared, wait_seconds=5)
            database.commit()
            return result

    with Session(postgres_engine) as owner:
        reserved = reserve_client_operation(owner, prepared, wait_seconds=5)
        assert isinstance(reserved, ReservedOperation)
        complete_client_operation(
            owner,
            reserved,
            deletion_result(),
            mutation_applied=True,
        )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(wait_for_receipt)
            assert waiter_ready.wait(timeout=2)
            deadline = time.monotonic() + 2
            waiter_is_blocked = False
            while time.monotonic() < deadline:
                with postgres_engine.connect() as observer:
                    waiter_is_blocked = bool(
                        observer.execute(
                            text(
                                """
                                SELECT EXISTS (
                                    SELECT 1
                                    FROM pg_stat_activity
                                    WHERE application_name = :name
                                      AND wait_event_type = 'Lock'
                                )
                                """
                            ),
                            {"name": waiter_application_name},
                        ).scalar_one()
                    )
                if waiter_is_blocked:
                    break
                time.sleep(0.01)
            assert waiter_is_blocked
            owner.commit()
            replay = future.result(timeout=3)

    assert isinstance(replay, ReplayedOperation)
    assert replay.mutation_applied is True
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 1


def test_waiter_becomes_owner_after_first_reservation_rolls_back(
    clean_client_operations,
    postgres_engine: Engine,
):
    operation_id = uuid4()
    prepared = prepared_deletion(operation_id)
    waiter_ready = Event()
    waiter_application_name = "mnemonic_p6_rb_" + uuid4().hex

    def wait_then_complete():
        with Session(postgres_engine) as database:
            database.execute(
                text("SELECT set_config('application_name', :name, true)"),
                {"name": waiter_application_name},
            )
            waiter_ready.set()
            result = reserve_client_operation(database, prepared, wait_seconds=5)
            assert isinstance(result, ReservedOperation)
            complete_client_operation(
                database,
                result,
                deletion_result(),
                mutation_applied=True,
            )
            database.commit()
            return result

    with Session(postgres_engine) as owner:
        first = reserve_client_operation(owner, prepared, wait_seconds=5)
        assert isinstance(first, ReservedOperation)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(wait_then_complete)
            assert waiter_ready.wait(timeout=2)
            deadline = time.monotonic() + 2
            blocked = False
            while time.monotonic() < deadline:
                with postgres_engine.connect() as observer:
                    blocked = observer.execute(
                        text(
                            """
                            SELECT EXISTS (
                                SELECT 1
                                FROM pg_stat_activity
                                WHERE application_name = :name
                                  AND wait_event_type = 'Lock'
                            )
                            """
                        ),
                        {"name": waiter_application_name},
                    ).scalar_one()
                if blocked:
                    break
                time.sleep(0.01)
            assert blocked
            owner.rollback()
            second = future.result(timeout=3)

    assert isinstance(second, ReservedOperation)
    with Session(postgres_engine) as database:
        replay = reserve_client_operation(database, prepared, wait_seconds=2)
        assert isinstance(replay, ReplayedOperation)
        assert replay.mutation_applied is True
        database.commit()

    with postgres_engine.connect() as connection:
        row = connection.execute(
            text("SELECT state, mutation_applied FROM client_operations")
        ).mappings().one()
        assert row == {"state": "completed", "mutation_applied": True}


def test_saturated_waiter_pool_releases_capacity_and_exact_retry_recovers(
    clean_client_operations,
    postgres_engine: Engine,
):
    limited_engine = create_engine(
        postgres_engine.url,
        pool_size=3,
        max_overflow=0,
        pool_timeout=3,
        pool_pre_ping=True,
        hide_parameters=True,
    )
    operation_id = uuid4()
    prepared = prepared_deletion(operation_id)
    waiters_ready = Barrier(3)

    def bounded_waiter():
        with Session(limited_engine) as database:
            database.execute(text("SELECT 1"))
            waiters_ready.wait(timeout=3)
            with pytest.raises(ApplicationError) as captured:
                reserve_client_operation(database, prepared, wait_seconds=1)
            assert captured.value.status_code == 503
            assert captured.value.detail["code"] == "client_operation_unavailable"
            assert captured.value.detail["context"] == {}
            assert not database.in_transaction()
            return captured.value.status_code

    def unrelated_query():
        with Session(limited_engine) as database:
            return database.execute(text("SELECT 42")).scalar_one()

    try:
        with Session(limited_engine) as owner:
            reserved = reserve_client_operation(owner, prepared, wait_seconds=2)
            assert isinstance(reserved, ReservedOperation)
            with ThreadPoolExecutor(max_workers=3) as executor:
                waiters = [executor.submit(bounded_waiter) for _ in range(2)]
                waiters_ready.wait(timeout=3)
                started = time.monotonic()
                unrelated = executor.submit(unrelated_query)
                assert [future.result(timeout=3) for future in waiters] == [503, 503]
                assert unrelated.result(timeout=3) == 42
                elapsed = time.monotonic() - started
            assert 0.7 <= elapsed < 2.5
            owner.rollback()

        with Session(limited_engine) as database:
            retry = reserve_client_operation(database, prepared, wait_seconds=2)
            assert isinstance(retry, ReservedOperation)
            complete_client_operation(
                database,
                retry,
                deletion_result(),
                mutation_applied=True,
            )
            database.commit()
        assert limited_engine.pool.checkedout() == 0
    finally:
        limited_engine.dispose()

    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 1


def test_pool_checkout_and_receipt_lock_share_one_absolute_wait_budget(
    clean_client_operations,
    postgres_engine: Engine,
):
    limited_engine = create_engine(
        postgres_engine.url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=1,
        pool_pre_ping=True,
        hide_parameters=True,
    )
    operation_id = uuid4()
    prepared = prepared_deletion(operation_id)
    queued_started = Event()
    release_unrelated = Event()

    def queued_waiter():
        started = time.monotonic()
        queued_started.set()
        with Session(limited_engine) as database:
            with pytest.raises(ApplicationError) as captured:
                reserve_client_operation(database, prepared, wait_seconds=1)
            elapsed = time.monotonic() - started
            assert captured.value.status_code == 503
            assert captured.value.detail["code"] == "client_operation_unavailable"
            assert captured.value.detail["context"] == {}
            assert not database.in_transaction()
        return elapsed

    def occupy_unrelated_slot():
        with Session(limited_engine) as database:
            database.execute(text("SELECT 1"))
            assert release_unrelated.wait(timeout=3)

    try:
        with Session(limited_engine) as owner:
            reserved = reserve_client_operation(owner, prepared, wait_seconds=1)
            assert isinstance(reserved, ReservedOperation)
            with ThreadPoolExecutor(max_workers=2) as executor:
                unrelated = executor.submit(occupy_unrelated_slot)
                deadline = time.monotonic() + 2
                while limited_engine.pool.checkedout() != 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert limited_engine.pool.checkedout() == 2

                waiter = executor.submit(queued_waiter)
                assert queued_started.wait(timeout=1)
                # Consume part of the one-second budget in QueuePool, then let
                # the waiter acquire a slot while the same-key owner remains
                # uncommitted and locked.
                time.sleep(0.45)
                release_unrelated.set()
                unrelated.result(timeout=2)
                elapsed = waiter.result(timeout=2)
            assert 0.75 <= elapsed < 1.5
            owner.rollback()
        assert limited_engine.pool.checkedout() == 0
    finally:
        release_unrelated.set()
        limited_engine.dispose()

    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 0


def test_same_key_wait_timeout_is_bounded_and_never_executes(
    clean_client_operations,
    postgres_engine: Engine,
):
    operation_id = uuid4()
    prepared = prepared_deletion(operation_id)
    with Session(postgres_engine) as owner:
        reserved = reserve_client_operation(owner, prepared, wait_seconds=2)
        assert isinstance(reserved, ReservedOperation)

        started = time.monotonic()
        with Session(postgres_engine) as waiter:
            with pytest.raises(ApplicationError) as captured:
                reserve_client_operation(waiter, prepared, wait_seconds=1)
            elapsed = time.monotonic() - started
            assert captured.value.status_code == 503
            assert captured.value.detail["code"] == "client_operation_unavailable"
            assert captured.value.detail["context"] == {}
            assert not waiter.in_transaction()
        assert 0.7 <= elapsed < 2.5
        owner.rollback()

    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 0
