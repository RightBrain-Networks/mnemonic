"""Populated upgrade and direct-SQL guards for identity-preserving moves."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema

from .conftest import BACKEND_DIR
from .test_duplicate_merge_invariants_postgres import _create_project_with_work

pytestmark = pytest.mark.postgres


def _move(
    connection: Connection,
    *,
    work_item_id: UUID,
    source_project_id: UUID,
    target_project_id: UUID,
    source_work_version: int = 1,
    preserved_status: str = "pending",
    event_roles: tuple[str, str] = ("source", "target"),
) -> UUID:
    move_id = uuid4()
    resulting_work_version = source_work_version + 1
    moved_at = connection.scalar(text("SELECT clock_timestamp()"))
    assert isinstance(moved_at, datetime)
    connection.execute(
        text(
            """
            INSERT INTO work_item_moves (
                id, work_item_id, source_project_id, target_project_id,
                source_work_version, resulting_work_version, preserved_status,
                actor_kind, actor_client, actor_session_id, created_at
            ) VALUES (
                :move_id, :work_item_id, :source_project_id, :target_project_id,
                :source_work_version, :resulting_work_version, :preserved_status,
                'client', 'test-client', 'test-session', :moved_at
            )
            """
        ),
        {
            "move_id": move_id,
            "work_item_id": work_item_id,
            "source_project_id": source_project_id,
            "target_project_id": target_project_id,
            "source_work_version": source_work_version,
            "resulting_work_version": resulting_work_version,
            "preserved_status": preserved_status,
            "moved_at": moved_at,
        },
    )
    connection.execute(
        text(
            """
            UPDATE work_items
            SET project_id = :target_project_id,
                version = :resulting_work_version,
                updated_at = :moved_at
            WHERE id = :work_item_id
            """
        ),
        {
            "target_project_id": target_project_id,
            "work_item_id": work_item_id,
            "resulting_work_version": resulting_work_version,
            "moved_at": moved_at,
        },
    )
    project_by_role = {
        "source": source_project_id,
        "target": target_project_id,
    }
    for role in event_roles:
        project_id = project_by_role[role]
        connection.execute(
            text(
                """
                INSERT INTO work_events (
                    project_id, work_item_id, event_type, actor_kind,
                    actor_client, actor_session_id, work_move_id, metadata,
                    origin, created_at
                ) VALUES (
                    :project_id, :work_item_id, 'work_moved', 'client',
                    'test-client', 'test-session', :move_id,
                    CAST(:metadata AS jsonb), 'live', :moved_at
                )
                """
            ),
            {
                "project_id": project_id,
                "work_item_id": work_item_id,
                "move_id": move_id,
                "metadata": json.dumps(
                    {
                        "move_id": str(move_id),
                        "source_project_id": str(source_project_id),
                        "target_project_id": str(target_project_id),
                        "role": role,
                        "work_version": resulting_work_version,
                    }
                ),
                "moved_at": moved_at,
            },
        )
    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return move_id


def _create_work_in_project(connection: Connection, project_id: UUID, title: str) -> UUID:
    work_item_id = uuid4()
    checkpoint_id = uuid4()
    created_at = connection.scalar(text("SELECT clock_timestamp()"))
    assert isinstance(created_at, datetime)
    connection.execute(
        text(
            """
            INSERT INTO work_items (
                id,project_id,title,summary,status,priority,
                initial_checkpoint_id,version,created_at,updated_at
            ) VALUES (
                :work_item_id,:project_id,:title,'Migration provenance chain.',
                'pending',0,:checkpoint_id,1,:created_at,:created_at
            )
            """
        ),
        {
            "work_item_id": work_item_id,
            "project_id": project_id,
            "title": title,
            "checkpoint_id": checkpoint_id,
            "created_at": created_at,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO checkpoints (
                id,work_item_id,kind,prompt,source_client,source_session_id,created_at
            ) VALUES (
                :checkpoint_id,:work_item_id,'context','Migration provenance context.',
                'test-client','test-session',:created_at
            )
            """
        ),
        {
            "checkpoint_id": checkpoint_id,
            "work_item_id": work_item_id,
            "created_at": created_at,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO work_events (
                project_id,work_item_id,event_type,actor_kind,actor_client,
                actor_session_id,checkpoint_id,metadata,origin,created_at
            ) VALUES (
                :project_id,:work_item_id,'work_created','client','test-client',
                'test-session',:checkpoint_id,CAST(:metadata AS jsonb),'live',:created_at
            )
            """
        ),
        {
            "project_id": project_id,
            "work_item_id": work_item_id,
            "checkpoint_id": checkpoint_id,
            "metadata": json.dumps(
                {
                    "initial": {
                        "title": title,
                        "summary": "Migration provenance chain.",
                        "status": "pending",
                        "priority": 0,
                        "version": 1,
                    }
                }
            ),
            "created_at": created_at,
        },
    )
    return work_item_id


def _close_wont_do(connection: Connection, project_id: UUID, work_item_id: UUID) -> UUID:
    report_id = uuid4()
    closed_at = connection.scalar(text("SELECT clock_timestamp()"))
    assert isinstance(closed_at, datetime)
    title = connection.scalar(
        text(
            """
            UPDATE work_items
            SET status='wont-do',version=2,updated_at=:closed_at
            WHERE id=:work_item_id AND project_id=:project_id
            RETURNING title
            """
        ),
        {
            "work_item_id": work_item_id,
            "project_id": project_id,
            "closed_at": closed_at,
        },
    )
    assert isinstance(title, str)
    event_id = connection.scalar(
        text(
            """
            INSERT INTO work_events (
                project_id,work_item_id,event_type,actor_kind,actor_client,
                actor_session_id,job_completion_report_id,metadata,origin,created_at
            ) VALUES (
                :project_id,:work_item_id,'work_status_changed','client','test-client',
                'test-session',:report_id,CAST(:metadata AS jsonb),'live',:closed_at
            )
            RETURNING id
            """
        ),
        {
            "project_id": project_id,
            "work_item_id": work_item_id,
            "report_id": report_id,
            "metadata": json.dumps(
                {
                    "changes": {
                        "status": {"before": "pending", "after": "wont-do"},
                    },
                    "from_status": "pending",
                    "to_status": "wont-do",
                    "work_version": 2,
                }
            ),
            "closed_at": closed_at,
        },
    )
    assert isinstance(event_id, int)
    connection.execute(
        text(
            """
            INSERT INTO job_completion_reports (
                id,project_id,work_item_id,closeout_event_id,closeout_work_version,
                closeout_status,completion_checkpoint_id,work_title_at_closeout,
                summary,fyi_items,prompt_revision,prompt_sha256,prompt_text,
                actor_client,actor_session_id
            )
            SELECT :report_id,:project_id,:work_item_id,:event_id,2,
                   'wont-do',NULL,:title,'Migration provenance closeout.',
                   '{}'::text[],settings.revision,
                   encode(sha256(convert_to(settings.job_completion_report_prompt,'UTF8')),'hex'),
                   settings.job_completion_report_prompt,'test-client','test-session'
            FROM project_settings settings
            WHERE settings.project_id=:project_id
            """
        ),
        {
            "report_id": report_id,
            "project_id": project_id,
            "work_item_id": work_item_id,
            "event_id": event_id,
            "title": title,
        },
    )
    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    return report_id


def _create_follow_up(
    connection: Connection,
    project_id: UUID,
    report_id: UUID,
    source_work_item_id: UUID,
    title: str,
) -> tuple[UUID, UUID]:
    follow_up_work_item_id = _create_work_in_project(connection, project_id, title)
    follow_up_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO job_completion_report_follow_ups (
                id,project_id,report_id,source_work_item_id,follow_up_work_item_id,
                actor_client,actor_session_id
            ) VALUES (
                :id,:project_id,:report_id,:source_work_item_id,:follow_up_work_item_id,
                'test-client','test-session'
            )
            """
        ),
        {
            "id": follow_up_id,
            "project_id": project_id,
            "report_id": report_id,
            "source_work_item_id": source_work_item_id,
            "follow_up_work_item_id": follow_up_work_item_id,
        },
    )
    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    return follow_up_id, follow_up_work_item_id


def _foreign_key_columns(connection: Connection, name: str) -> tuple[list[str], list[str]]:
    row = connection.execute(
        text(
            """
            SELECT ARRAY(
                       SELECT attribute.attname
                       FROM unnest(constraint_row.conkey) WITH ORDINALITY AS key(attnum, position)
                       JOIN pg_attribute AS attribute
                         ON attribute.attrelid = constraint_row.conrelid
                        AND attribute.attnum = key.attnum
                       ORDER BY key.position
                   ) AS local_columns,
                   ARRAY(
                       SELECT attribute.attname
                       FROM unnest(constraint_row.confkey)
                            WITH ORDINALITY AS key(attnum, position)
                       JOIN pg_attribute AS attribute
                         ON attribute.attrelid = constraint_row.confrelid
                        AND attribute.attnum = key.attnum
                       ORDER BY key.position
                   ) AS remote_columns
            FROM pg_constraint AS constraint_row
            JOIN pg_namespace AS namespace
              ON namespace.oid = constraint_row.connamespace
            WHERE namespace.nspname = current_schema()
              AND constraint_row.conname = :name
            """
        ),
        {"name": name},
    ).one()
    return list(row.local_columns), list(row.remote_columns)


def _assert_missing_closeout_slot_move_guards(
    engine: Engine,
    source_project_id: UUID,
    target_project_id: UUID,
) -> None:
    for terminal_status in ("done", "wont-do", "promoted"):
        with engine.begin() as connection:
            terminal_work_item_id = _create_work_in_project(
                connection,
                source_project_id,
                f"Missing {terminal_status} report slot",
            )
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE work_items DISABLE TRIGGER USER"))
            connection.execute(
                text("UPDATE work_items SET status=:status WHERE id=:work_item_id"),
                {
                    "status": terminal_status,
                    "work_item_id": terminal_work_item_id,
                },
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            connection.execute(text("ALTER TABLE work_items ENABLE TRIGGER USER"))
        guard_message = (
            "sealed completion evidence"
            if terminal_status == "done"
            else "sealed closeout report"
        )
        with pytest.raises(DBAPIError, match=guard_message):
            with engine.begin() as connection:
                _move(
                    connection,
                    work_item_id=terminal_work_item_id,
                    source_project_id=source_project_id,
                    target_project_id=target_project_id,
                    preserved_status=terminal_status,
                )

    with engine.begin() as connection:
        deferred_work_item_id = _create_work_in_project(
            connection,
            source_project_id,
            "Deferred work without report slot",
        )
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE work_items DISABLE TRIGGER USER"))
        connection.execute(
            text("UPDATE work_items SET status='deferred' WHERE id=:work_item_id"),
            {"work_item_id": deferred_work_item_id},
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        connection.execute(text("ALTER TABLE work_items ENABLE TRIGGER USER"))
    with engine.begin() as connection:
        _move(
            connection,
            work_item_id=deferred_work_item_id,
            source_project_id=source_project_id,
            target_project_id=target_project_id,
            preserved_status="deferred",
        )


def _assert_source_precedes_target_move_event(
    engine: Engine,
    source_project_id: UUID,
    target_project_id: UUID,
) -> None:
    with engine.begin() as connection:
        work_item_id = _create_work_in_project(
            connection,
            source_project_id,
            "Reject reversed move witnesses",
        )
    with pytest.raises(DBAPIError, match="paired events"):
        with engine.begin() as connection:
            _move(
                connection,
                work_item_id=work_item_id,
                source_project_id=source_project_id,
                target_project_id=target_project_id,
                event_roles=("target", "source"),
            )


def _set_closeout_slot_without_guards(
    engine: Engine,
    work_item_id: UUID,
    slot: int | None,
) -> None:
    triggers = ("job_report_transition_guard", "job_report_transition_sealed")
    with engine.begin() as connection:
        for trigger in triggers:
            connection.execute(text(f"ALTER TABLE work_items DISABLE TRIGGER {trigger}"))
        connection.execute(
            text(
                "UPDATE work_items SET last_reportable_closeout_version=:slot "
                "WHERE id=:work_item_id"
            ),
            {"slot": slot, "work_item_id": work_item_id},
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        for trigger in triggers:
            connection.execute(text(f"ALTER TABLE work_items ENABLE TRIGGER {trigger}"))


def test_0023_populated_upgrade_preserves_facts_and_guards_move_history() -> None:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    url = make_url(raw_url)
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_move_0023_" + uuid4().hex
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
            command.upgrade(config, "0022_external_references")
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            source_project_id, work = _create_project_with_work(connection, work_count=2)
            work_item_id, _checkpoint_id = work[0]
            other_work_item_id, _other_checkpoint_id = work[1]
            target_project_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, slug) "
                    "VALUES (:id, 'Move target', :slug)"
                ),
                {"id": target_project_id, "slug": f"move-target-{target_project_id.hex}"},
            )
            original_event_id = connection.scalar(
                text(
                    "SELECT id FROM work_events "
                    "WHERE work_item_id = :work_item_id AND event_type = 'work_created'"
                ),
                {"work_item_id": work_item_id},
            )
            assert isinstance(original_event_id, int)

        with engine.begin() as connection:
            provenance_project_id, provenance_work = _create_project_with_work(
                connection,
                work_count=1,
            )
            provenance_source_id, _ = provenance_work[0]
        with engine.begin() as connection:
            first_report_id = _close_wont_do(
                connection,
                provenance_project_id,
                provenance_source_id,
            )
        with engine.begin() as connection:
            first_follow_up_id, chained_work_item_id = _create_follow_up(
                connection,
                provenance_project_id,
                first_report_id,
                provenance_source_id,
                "Follow-up that later becomes a source",
            )
        with engine.begin() as connection:
            second_report_id = _close_wont_do(
                connection,
                provenance_project_id,
                chained_work_item_id,
            )
        with engine.begin() as connection:
            second_follow_up_id, _ = _create_follow_up(
                connection,
                provenance_project_id,
                second_report_id,
                chained_work_item_id,
                "Second-generation follow-up",
            )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0023_work_item_moves")

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0023_work_item_moves"
            )
            assert connection.scalar(text("SELECT count(*) FROM work_item_moves")) == 0
            allocator_body = connection.scalar(
                text(
                    "SELECT prosrc FROM pg_proc WHERE pronamespace=current_schema()::regnamespace "
                    "AND proname='mnemonic_activity_follow_up_source'"
                )
            )
            assert isinstance(allocator_body, str)
            assert allocator_body.index(
                "RETURNING last_sequence INTO NEW.follow_up_work_sequence;"
            ) < allocator_body.index("INTO NEW.created_at")
            assert "NEW.created_at:=clock_timestamp();" not in allocator_body
            assert connection.execute(
                text(
                    """
                    SELECT follow_up_work_sequence
                    FROM job_completion_report_follow_ups WHERE id=:first_id
                    UNION ALL
                    SELECT source_work_sequence
                    FROM job_completion_report_follow_ups WHERE id=:second_id
                    ORDER BY 1
                    """
                ),
                {"first_id": first_follow_up_id, "second_id": second_follow_up_id},
            ).scalars().all() == [1, 2]
            assert connection.scalar(
                text(
                    "SELECT last_sequence FROM work_report_provenance_heads "
                    "WHERE work_item_id=:work_item_id"
                ),
                {"work_item_id": chained_work_item_id},
            ) == 2
            for name in (
                "fk_verification_results_work_item",
                "fk_artifact_references_work_item",
                "fk_work_gates_work_item",
                "fk_work_events_work_item",
                "fk_work_events_relationship_source_work_item",
                "fk_work_events_relationship_target_work_item",
                "fk_project_activity_work",
                "fk_job_reports_work",
                "fk_job_report_follow_ups_work",
            ):
                local, remote = _foreign_key_columns(connection, name)
                assert len(local) == len(remote) == 1
                assert remote == ["id"]
            assert connection.execute(
                text("SELECT project_id, work_item_id FROM work_events WHERE id = :id"),
                {"id": original_event_id},
            ).one() == (source_project_id, work_item_id)

        # With no new facts, 0023 is reversibly removable even on populated history.
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0022_external_references")
            assert connection.scalar(text("SELECT to_regclass('work_item_moves')")) is None
            assert _foreign_key_columns(connection, "fk_work_events_work_item") == (
                ["project_id", "work_item_id"],
                ["project_id", "id"],
            )
            command.upgrade(config, "0023_work_item_moves")

        # A move row without its project transition and paired events fails at the
        # deferred completeness boundary and leaves no partial fact behind.
        with pytest.raises(DBAPIError, match="paired events"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO work_item_moves (
                            work_item_id, source_project_id, target_project_id,
                            source_work_version, resulting_work_version,
                            preserved_status, actor_kind
                        ) VALUES (
                            :work_item_id, :source_project_id, :target_project_id,
                            1, 2, 'pending', 'unattributed'
                        )
                        """
                    ),
                    {
                        "work_item_id": work_item_id,
                        "source_project_id": source_project_id,
                        "target_project_id": target_project_id,
                    },
                )
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        _set_closeout_slot_without_guards(engine, work_item_id, 1)
        with pytest.raises(DBAPIError, match="sealed closeout report"):
            with engine.begin() as connection:
                _move(
                    connection,
                    work_item_id=work_item_id,
                    source_project_id=source_project_id,
                    target_project_id=target_project_id,
                )
        _set_closeout_slot_without_guards(engine, work_item_id, None)

        _assert_missing_closeout_slot_move_guards(
            engine,
            source_project_id,
            target_project_id,
        )
        _assert_source_precedes_target_move_event(
            engine,
            source_project_id,
            target_project_id,
        )

        holder = engine.connect()
        held_transaction = holder.begin()
        waiter_started = Event()
        waiter_name = "mnemonic_move_lease_waiter_" + uuid4().hex

        def move_while_lease_is_uncommitted() -> None:
            with engine.begin() as connection:
                connection.execute(
                    text("SELECT set_config('application_name', :name, true)"),
                    {"name": waiter_name},
                )
                connection.execute(text("SET LOCAL lock_timeout='3s'"))
                waiter_started.set()
                _move(
                    connection,
                    work_item_id=work_item_id,
                    source_project_id=source_project_id,
                    target_project_id=target_project_id,
                )

        try:
            holder.execute(
                text(
                    """
                    INSERT INTO work_leases (
                        work_item_id,holder_client,holder_session_id,claim_request_id,
                        lease_token,lease_generation_id,acquired_at,renewed_at,expires_at
                    ) VALUES (
                        :work_item_id,'test-client','move-lease-race','move-lease-race',
                        'move-lease-race-token',:generation,clock_timestamp(),
                        clock_timestamp(),clock_timestamp()+interval '1 hour'
                    )
                    """
                ),
                {"work_item_id": work_item_id, "generation": uuid4()},
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(move_while_lease_is_uncommitted)
                assert waiter_started.wait(timeout=2)
                deadline = time.monotonic() + 3
                move_is_blocked = False
                while time.monotonic() < deadline:
                    with engine.connect() as observer:
                        move_is_blocked = bool(
                            observer.scalar(
                                text(
                                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                    "WHERE application_name=:name "
                                    "AND wait_event_type='Lock')"
                                ),
                                {"name": waiter_name},
                            )
                        )
                    if move_is_blocked:
                        break
                    time.sleep(0.01)
                assert move_is_blocked
                held_transaction.commit()
                with pytest.raises(DBAPIError, match="active work leases"):
                    future.result(timeout=3)
        finally:
            if held_transaction.is_active:
                held_transaction.rollback()
            holder.close()
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE work_leases
                    SET acquired_at=clock_timestamp()-interval '3 seconds',
                        renewed_at=clock_timestamp()-interval '2 seconds',
                        expires_at=clock_timestamp()-interval '1 second'
                    WHERE work_item_id=:work_item_id
                    """
                ),
                {"work_item_id": work_item_id},
            )

        with engine.begin() as connection:
            move_id = _move(
                connection,
                work_item_id=work_item_id,
                source_project_id=source_project_id,
                target_project_id=target_project_id,
            )

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT project_id, status, version FROM work_items WHERE id = :id"),
                {"id": work_item_id},
            ).one() == (target_project_id, "pending", 2)
            assert connection.execute(
                text(
                    "SELECT project_id, metadata->>'role' "
                    "FROM work_events WHERE work_move_id = :move_id ORDER BY project_id"
                ),
                {"move_id": move_id},
            ).all() == sorted(
                [(source_project_id, "source"), (target_project_id, "target")],
                key=lambda row: row[0],
            )
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM project_activity "
                    "WHERE work_item_id = :work_item_id AND work_event_id IN ("
                    "SELECT id FROM work_events WHERE work_move_id = :move_id)"
                ),
                {"work_item_id": work_item_id, "move_id": move_id},
            ) == 2
            assert connection.execute(
                text("SELECT project_id FROM work_events WHERE id = :id"),
                {"id": original_event_id},
            ).scalar_one() == source_project_id

        with pytest.raises(DBAPIError, match="immutable"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE work_item_moves SET actor_model = 'rewrite' WHERE id = :id"),
                    {"id": move_id},
                )
        with pytest.raises(DBAPIError, match="current project ownership"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO work_events (
                            project_id,work_item_id,event_type,actor_kind,body,metadata
                        ) VALUES (
                            :project_id,:work_item_id,'progress','unattributed',
                            'Forged source-project progress.','{}'::jsonb
                        )
                        """
                    ),
                    {
                        "project_id": source_project_id,
                        "work_item_id": work_item_id,
                    },
                )
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO work_events (
                            project_id,work_item_id,event_type,actor_kind,
                            relationship_id,relationship_source_work_item_id,
                            relationship_target_work_item_id,metadata
                        ) VALUES (
                            :project_id,:work_item_id,'relationship_added','unattributed',
                            :relationship_id,:work_item_id,:other_work_item_id,
                            '{"relationship_type":"related"}'::jsonb
                        )
                        """
                    ),
                    {
                        "project_id": target_project_id,
                        "work_item_id": work_item_id,
                        "other_work_item_id": other_work_item_id,
                        "relationship_id": uuid4(),
                    },
                )
        holder = engine.connect()
        held_transaction = holder.begin()
        try:
            holder.execute(
                text(
                    """
                    INSERT INTO work_events (
                        project_id,work_item_id,event_type,actor_kind,
                        actor_client,actor_session_id,body,metadata
                    ) VALUES (
                        :project_id,:work_item_id,'progress','client',
                        'test-client','event-move-race',
                        'Hold current event ownership through commit.','{}'::jsonb
                    )
                    """
                ),
                {
                    "project_id": source_project_id,
                    "work_item_id": other_work_item_id,
                },
            )
            with pytest.raises(DBAPIError) as raced:
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout='250ms'"))
                    connection.execute(
                        text(
                            """
                            UPDATE work_items
                            SET project_id=:target_project_id,
                                version=version+1,
                                updated_at=clock_timestamp()
                            WHERE id=:work_item_id
                            """
                        ),
                        {
                            "target_project_id": target_project_id,
                            "work_item_id": other_work_item_id,
                        },
                    )
            assert getattr(raced.value.orig, "sqlstate", None) == "55P03"
        finally:
            held_transaction.rollback()
            holder.close()
        with pytest.raises(RuntimeError, match="downgrade would lose facts"):
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "0022_external_references")
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_0025_populated_upgrade_retains_relationship_and_allows_move() -> None:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    url = make_url(raw_url)
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_relationship_0025_" + uuid4().hex
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
            command.upgrade(config, "0024_code_reviews")
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            source_project_id, work = _create_project_with_work(connection, work_count=2)
            target_project_id, _ = _create_project_with_work(connection, work_count=0)
            moving_work_item_id = work[0][0]
            counterpart_work_item_id = work[1][0]
            relationship_source_id, relationship_target_id = sorted(
                (moving_work_item_id, counterpart_work_item_id)
            )
            relationship_id = uuid4()
            relationship_created_at = connection.scalar(text("SELECT clock_timestamp()"))
            assert isinstance(relationship_created_at, datetime)
            connection.execute(
                text(
                    """
                    INSERT INTO work_relationships (
                        id, project_id, relationship_type, source_work_item_id,
                        target_work_item_id, created_by_client,
                        created_by_session_id, created_at
                    ) VALUES (
                        :id, :project_id, 'related', :source_id, :target_id,
                        'migration-test', 'relationship-retention', :created_at
                    )
                    """
                ),
                {
                    "id": relationship_id,
                    "project_id": source_project_id,
                    "source_id": relationship_source_id,
                    "target_id": relationship_target_id,
                    "created_at": relationship_created_at,
                },
            )
            for endpoint_id in (relationship_source_id, relationship_target_id):
                connection.execute(
                    text(
                        """
                        INSERT INTO work_events (
                            project_id, work_item_id, event_type, actor_kind,
                            actor_client, actor_session_id, relationship_id,
                            relationship_source_work_item_id,
                            relationship_target_work_item_id, metadata, origin,
                            created_at
                        ) VALUES (
                            :project_id, :endpoint_id, 'relationship_added', 'client',
                            'migration-test', 'relationship-retention', :relationship_id,
                            :source_id, :target_id,
                            '{"relationship_type":"related"}'::jsonb, 'live',
                            :created_at
                        )
                        """
                    ),
                    {
                        "project_id": source_project_id,
                        "endpoint_id": endpoint_id,
                        "relationship_id": relationship_id,
                        "source_id": relationship_source_id,
                        "target_id": relationship_target_id,
                        "created_at": relationship_created_at,
                    },
                )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        with pytest.raises(DBAPIError, match="related or duplicate work cannot be moved"):
            with engine.begin() as connection:
                _move(
                    connection,
                    work_item_id=moving_work_item_id,
                    source_project_id=source_project_id,
                    target_project_id=target_project_id,
                )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0025_cross_project_relationships")

        with engine.begin() as connection:
            move_id = _move(
                connection,
                work_item_id=moving_work_item_id,
                source_project_id=source_project_id,
                target_project_id=target_project_id,
            )

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0025_cross_project_relationships"
            )
            assert connection.execute(
                text(
                    "SELECT project_id,source_work_item_id,target_work_item_id "
                    "FROM work_relationships WHERE id=:relationship_id"
                ),
                {"relationship_id": relationship_id},
            ).one() == (
                source_project_id,
                relationship_source_id,
                relationship_target_id,
            )
            assert connection.execute(
                text("SELECT project_id,status,version FROM work_items WHERE id=:id"),
                {"id": moving_work_item_id},
            ).one() == (target_project_id, "pending", 2)
            assert connection.scalar(
                text("SELECT count(*) FROM work_events WHERE work_move_id=:move_id"),
                {"move_id": move_id},
            ) == 2
            assert _foreign_key_columns(
                connection, "fk_work_relationships_source_work_item"
            ) == (["source_work_item_id"], ["id"])
            assert _foreign_key_columns(
                connection, "fk_work_relationships_target_work_item"
            ) == (["target_work_item_id"], ["id"])

        with engine.begin() as connection:
            removed_at = connection.scalar(text("SELECT clock_timestamp()"))
            assert isinstance(removed_at, datetime)
            endpoint_projects = sorted(
                (
                    (moving_work_item_id, target_project_id),
                    (counterpart_work_item_id, source_project_id),
                ),
                key=lambda placement: placement[1],
            )
            for endpoint_id, event_project_id in endpoint_projects:
                connection.execute(
                    text(
                        """
                        INSERT INTO work_events (
                            project_id, work_item_id, event_type, actor_kind,
                            actor_client, actor_session_id, relationship_id,
                            relationship_source_work_item_id,
                            relationship_target_work_item_id, metadata, origin,
                            created_at
                        ) VALUES (
                            :project_id, :endpoint_id, 'relationship_removed', 'client',
                            'migration-test', 'relationship-removal', :relationship_id,
                            :source_id, :target_id,
                            '{"relationship_type":"related"}'::jsonb, 'live',
                            :created_at
                        )
                        """
                    ),
                    {
                        "project_id": event_project_id,
                        "endpoint_id": endpoint_id,
                        "relationship_id": relationship_id,
                        "source_id": relationship_source_id,
                        "target_id": relationship_target_id,
                        "created_at": removed_at,
                    },
                )
            connection.execute(
                text("DELETE FROM work_relationships WHERE id=:relationship_id"),
                {"relationship_id": relationship_id},
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT count(*) FROM work_relationships WHERE id=:id"),
                {"id": relationship_id},
            ) == 0
            assert connection.scalar(
                text(
                    "SELECT count(DISTINCT project_id) FROM work_events "
                    "WHERE relationship_id=:id"
                ),
                {"id": relationship_id},
            ) == 2

        with pytest.raises(RuntimeError, match="event history"):
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "0024_code_reviews")

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0025_cross_project_relationships"
            )
            assert _foreign_key_columns(
                connection, "fk_work_relationships_source_work_item"
            ) == (["source_work_item_id"], ["id"])
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
