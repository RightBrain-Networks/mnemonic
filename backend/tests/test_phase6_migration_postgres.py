"""Populated Phase 6 receipt migration and preservation coverage."""

import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api.config import Settings

from .conftest import BACKEND_DIR

pytestmark = pytest.mark.postgres


@pytest.fixture
def empty_phase6_migration_engine() -> Iterator[Engine]:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(
        database_url=raw_url,
        api_key="phase-six-downgrade-test-key-is-long-enough",
    )
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_phase6_downgrade_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(
        url.update_query_dict({"options": f"-c search_path={schema} -c timezone=UTC"}),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def _complete_test_receipt(connection: Connection, operation_id: UUID) -> None:
    connection.execute(
        text(
            """
            INSERT INTO client_operations (
                project_id, client_operation_id, operation_kind,
                request_fingerprint_salt, request_fingerprint
            ) VALUES (
                :project_id, :operation_id, 'delete_work',
                decode(repeat('a1', 32), 'hex'),
                decode(repeat('b2', 32), 'hex')
            )
            """
        ),
        {
            "project_id": UUID("10000000-0000-0000-0000-000000000012"),
            "operation_id": operation_id,
        },
    )
    connection.execute(
        text(
            """
            UPDATE client_operations
            SET state = 'completed',
                response_status = 200,
                response_body = '{}'::jsonb,
                mutation_applied = false,
                completed_at = clock_timestamp()
            WHERE client_operation_id = :operation_id
            """
        ),
        {"operation_id": operation_id},
    )


def _wait_for_relation_lock(
    engine: Engine,
    *,
    waiting_pid: int,
    blocking_pid: int,
    relation_oid: int,
    mode: str,
) -> None:
    deadline = time.monotonic() + 3
    observed = False
    with engine.connect() as observer:
        while time.monotonic() < deadline:
            lock_wait = observer.execute(
                text(
                    """
                    SELECT
                        EXISTS (
                            SELECT 1
                            FROM pg_locks
                            WHERE pid = :waiting_pid
                              AND relation = :relation_oid
                              AND mode = :mode
                              AND NOT granted
                        ),
                        :blocking_pid = ANY(pg_blocking_pids(:waiting_pid))
                    """
                ),
                {
                    "waiting_pid": waiting_pid,
                    "blocking_pid": blocking_pid,
                    "relation_oid": relation_oid,
                    "mode": mode,
                },
            ).one()
            if lock_wait == (True, True):
                observed = True
                break
    assert observed, (
        f"backend {waiting_pid} did not wait for {mode} on relation {relation_oid} "
        f"behind backend {blocking_pid}"
    )


def _phase5_function_definition(connection) -> str:
    return connection.execute(
        text(
            """
            SELECT pg_get_functiondef(procedure.oid)
            FROM pg_proc AS procedure
            JOIN pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = current_schema()
              AND procedure.proname =
                  'mnemonic_work_event_metadata_v1_is_valid'
            """
        )
    ).scalar_one()


def test_0013_upgrade_preserves_legacy_metadata_and_empty_downgrade():
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(
        database_url=raw_url,
        api_key="phase-six-migration-test-key-is-long-enough",
    )
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_phase6_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(
        url.update_query_dict({"options": f"-c search_path={schema} -c timezone=UTC"}),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    config = Config(str(BACKEND_DIR / "alembic.ini"))

    project_id = UUID("10000000-0000-0000-0000-000000000006")
    work_id = UUID("20000000-0000-0000-0000-000000000006")
    checkpoint_id = UUID("30000000-0000-0000-0000-000000000006")
    created_at = datetime(2026, 9, 1, 12, tzinfo=UTC)

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0011_project_settings")

        with engine.begin() as connection:
            phase5_definition = _phase5_function_definition(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO projects (
                        id, name, slug, description, created_at, updated_at
                    ) VALUES (
                        :project_id, 'Phase 6 migration', 'phase-6-migration',
                        'Preserved project.', :created_at, :created_at
                    )
                    """
                ),
                {"project_id": project_id, "created_at": created_at},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO work_items (
                        id, project_id, title, summary, status, priority,
                        initial_checkpoint_id, version, created_at, updated_at
                    ) VALUES (
                        :work_id, :project_id, 'Preserved work',
                        'Preserve legacy progress metadata.', 'open', 10,
                        :checkpoint_id, 1, :created_at, :created_at
                    )
                    """
                ),
                {
                    "work_id": work_id,
                    "project_id": project_id,
                    "checkpoint_id": checkpoint_id,
                    "created_at": created_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO checkpoints (
                        id, work_item_id, kind, prompt, source_client,
                        source_session_id, source_metadata, created_at
                    ) VALUES (
                        :checkpoint_id, :work_id, 'context',
                        'Preserved checkpoint.', 'pytest',
                        'phase-6-migration', '{}'::jsonb, :created_at
                    )
                    """
                ),
                {
                    "checkpoint_id": checkpoint_id,
                    "work_id": work_id,
                    "created_at": created_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO work_events (
                        project_id, work_item_id, event_type, actor_kind,
                        actor_client, actor_session_id, body, metadata, origin,
                        created_at
                    ) VALUES (
                        :project_id, :work_id, 'progress', 'client',
                        'pytest', 'phase-6-migration', 'Legacy progress.',
                        CAST(:metadata AS jsonb), 'live', :created_at
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "work_id": work_id,
                    "metadata": (
                        '{"outer":[{"Client_Operation_ID":"historically-legal"}]}'
                    ),
                    "created_at": created_at,
                },
            )

            # The progress row leaves an 0010 deferred state trigger queued in
            # this transaction. 0012 must drain it before ALTER TABLE.
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        with engine.connect() as connection:
            assert _phase5_function_definition(connection) == phase5_definition
            assert connection.execute(
                text("SELECT count(*) FROM client_operations")
            ).scalar_one() == 0
            legacy_metadata = connection.execute(
                text(
                    """
                    SELECT metadata
                    FROM work_events
                    WHERE work_item_id = :work_id
                      AND body = 'Legacy progress.'
                    """
                ),
                {"work_id": work_id},
            ).scalar_one()
            assert legacy_metadata == {
                "outer": [{"Client_Operation_ID": "historically-legal"}]
            }
            constraint = connection.execute(
                text(
                    """
                    SELECT constraint_row.convalidated
                    FROM pg_constraint AS constraint_row
                    JOIN pg_class AS relation
                      ON relation.oid = constraint_row.conrelid
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND relation.relname = 'work_events'
                      AND constraint_row.conname =
                          'ck_work_events_client_operation_id_reserved'
                    """
                )
            ).scalar_one()
            assert constraint is False
            foreign_keys = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_constraint AS constraint_row
                    WHERE constraint_row.conrelid = 'client_operations'::regclass
                      AND constraint_row.contype = 'f'
                    """
                )
            ).scalar_one()
            assert foreign_keys == 0

        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO work_events (
                            project_id, work_item_id, event_type, actor_kind,
                            actor_client, actor_session_id, body, metadata, origin
                        ) VALUES (
                            :project_id, :work_id, 'progress', 'client',
                            'pytest', 'phase-6-migration', 'Rejected progress.',
                            CAST(:metadata AS jsonb), 'live'
                        )
                        """
                    ),
                    {
                        "project_id": project_id,
                        "work_id": work_id,
                        "metadata": '{"nested":{"CLIENT_OPERATION_ID":"new"}}',
                    },
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO work_events (
                        project_id, work_item_id, event_type, actor_kind,
                        actor_client, actor_session_id, body, metadata, origin
                    ) VALUES (
                        :project_id, :work_id, 'progress', 'client',
                        'pytest', 'phase-6-migration', 'Safe progress.',
                        '{"external_reference":"client_operation_id"}'::jsonb,
                        'live'
                    )
                    """
                ),
                {"project_id": project_id, "work_id": work_id},
            )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0012_pending_deferred_statuses")

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT to_regclass('client_operations')")
            ).scalar_one() is None
            assert connection.execute(
                text(
                    "SELECT to_regprocedure("
                    "'mnemonic_phase6_progress_metadata_is_valid(jsonb)')"
                )
            ).scalar_one() is None
            assert _phase5_function_definition(connection) == phase5_definition
            assert connection.execute(
                text(
                    """
                    SELECT metadata
                    FROM work_events
                    WHERE work_item_id = :work_id
                      AND body = 'Legacy progress.'
                    """
                ),
                {"work_id": work_id},
            ).scalar_one() == {
                "outer": [{"Client_Operation_ID": "historically-legal"}]
            }
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_0013_downgrade_waits_for_winning_writer_then_refuses(
    empty_phase6_migration_engine: Engine,
):
    engine = empty_phase6_migration_engine
    operation_id = uuid4()
    downgrade_pid_ready = Event()
    downgrade_pid: list[int] = []

    def downgrade() -> None:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            downgrade_pid.append(connection.scalar(text("SELECT pg_backend_pid()")))
            downgrade_pid_ready.set()
            config.attributes["connection"] = connection
            command.downgrade(config, "0012_pending_deferred_statuses")

    writer_connection = engine.connect()
    writer_transaction = writer_connection.begin()
    try:
        writer_pid = writer_connection.scalar(text("SELECT pg_backend_pid()"))
        relation_oid = writer_connection.scalar(
            text("SELECT 'client_operations'::regclass::oid")
        )
        _complete_test_receipt(writer_connection, operation_id)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(downgrade)
            assert downgrade_pid_ready.wait(timeout=2)
            try:
                _wait_for_relation_lock(
                    engine,
                    waiting_pid=downgrade_pid[0],
                    blocking_pid=writer_pid,
                    relation_oid=relation_oid,
                    mode="AccessExclusiveLock",
                )
            finally:
                writer_transaction.commit()

            with pytest.raises(
                RuntimeError,
                match="Cannot downgrade idempotent mutations after a client operation receipt",
            ):
                future.result(timeout=5)
    finally:
        if writer_transaction.is_active:
            writer_transaction.rollback()
        writer_connection.close()

    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0014_human_gates"
        receipt = connection.execute(
            text(
                """
                SELECT state, response_status, mutation_applied
                FROM client_operations
                WHERE client_operation_id = :operation_id
                """
            ),
            {"operation_id": operation_id},
        ).one()
        assert receipt == ("completed", 200, False)


def test_0013_downgrade_lock_prevents_writer_after_empty_check(
    empty_phase6_migration_engine: Engine,
):
    engine = empty_phase6_migration_engine
    operation_id = uuid4()
    relation_oid: int
    with engine.connect() as connection:
        relation_oid = connection.scalar(
            text("SELECT 'client_operations'::regclass::oid")
        )

    empty_check_complete = Event()
    allow_downgrade = Event()
    downgrade_pid_ready = Event()
    writer_pid_ready = Event()
    downgrade_pid: list[int] = []
    writer_pid: list[int] = []

    def pause_after_empty_check(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ) -> None:
        normalized = " ".join(statement.lower().split())
        if "select exists" not in normalized or "client_operations" not in normalized:
            return
        empty_check_complete.set()
        assert allow_downgrade.wait(timeout=5), (
            "test did not release the downgrade after its empty-ledger check"
        )

    def downgrade() -> None:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            downgrade_pid.append(connection.scalar(text("SELECT pg_backend_pid()")))
            downgrade_pid_ready.set()
            config.attributes["connection"] = connection
            command.downgrade(config, "0012_pending_deferred_statuses")

    def write_receipt() -> None:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            writer_pid.append(connection.scalar(text("SELECT pg_backend_pid()")))
            writer_pid_ready.set()
            _complete_test_receipt(connection, operation_id)

    event.listen(engine, "after_cursor_execute", pause_after_empty_check)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            downgrade_future = executor.submit(downgrade)
            assert downgrade_pid_ready.wait(timeout=2)
            assert empty_check_complete.wait(timeout=2)

            writer_future = executor.submit(write_receipt)
            assert writer_pid_ready.wait(timeout=2)
            try:
                _wait_for_relation_lock(
                    engine,
                    waiting_pid=writer_pid[0],
                    blocking_pid=downgrade_pid[0],
                    relation_oid=relation_oid,
                    mode="RowExclusiveLock",
                )
            finally:
                allow_downgrade.set()

            downgrade_future.result(timeout=5)
            with pytest.raises(DBAPIError) as blocked_writer:
                writer_future.result(timeout=5)
            assert getattr(blocked_writer.value.orig, "sqlstate", None) == "42P01"
    finally:
        allow_downgrade.set()
        event.remove(engine, "after_cursor_execute", pause_after_empty_check)

    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0012_pending_deferred_statuses"
        assert connection.scalar(text("SELECT to_regclass('client_operations')")) is None
