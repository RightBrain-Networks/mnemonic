"""Populated Phase 5 event migration and downgrade coverage."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api.config import Settings

from .conftest import BACKEND_DIR

pytestmark = pytest.mark.postgres


def test_populated_phase5_backfill_is_truthful_ordered_and_reversible():
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(
        database_url=raw_url,
        api_key="phase-five-migration-test-key-is-long-enough",
    )
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_phase5_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(
        url.update_query_dict({"options": f"-c search_path={schema} -c timezone=UTC"}),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    config = Config(str(BACKEND_DIR / "alembic.ini"))

    project_id = UUID("10000000-0000-0000-0000-000000000001")
    first_work_id = UUID("20000000-0000-0000-0000-000000000001")
    deleted_work_id = UUID("20000000-0000-0000-0000-000000000002")
    first_initial_id = UUID("30000000-0000-0000-0000-000000000001")
    deleted_initial_id = UUID("30000000-0000-0000-0000-000000000002")
    progress_id = UUID("30000000-0000-0000-0000-000000000003")
    completion_id = UUID("30000000-0000-0000-0000-000000000004")
    relationship_id = UUID("40000000-0000-0000-0000-000000000001")
    created_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    progress_at = created_at + timedelta(hours=1)
    completion_at = created_at + timedelta(hours=2)
    deleted_at = created_at + timedelta(hours=3)

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0009_ready_work_indexes")
            normalized_tags = connection.execute(
                text(
                    """
                    SELECT
                        mnemonic_normalized_tags(
                            ARRAY['MiXeD', 'other', 'mixed', NULL]::varchar[]
                        ),
                        mnemonic_normalized_tags(ARRAY[]::varchar[]),
                        mnemonic_normalized_tags(ARRAY['İ']::varchar[])
                    """
                )
            ).one()
            assert normalized_tags == (["mixed", "other"], [], ["i"])
            assert "İ".lower() == "i\u0307"
            connection.execute(
                text(
                    """
                    INSERT INTO projects (id, name, slug, created_at, updated_at)
                    VALUES (
                        :id, 'Phase 5 migration', 'phase-5-migration',
                        :created_at, :created_at
                    )
                    """
                ),
                {"id": project_id, "created_at": created_at},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO work_items (
                        id, project_id, title, summary, status, priority,
                        initial_checkpoint_id, version, created_at, updated_at,
                        deleted_at
                    ) VALUES
                    (
                        :first_id, :project_id, 'First work', 'First summary.',
                        'open', 80, :first_initial_id, 3, :created_at,
                        :completion_at, NULL
                    ),
                    (
                        :deleted_id, :project_id, 'Deleted work',
                        'Retained soft deletion.', 'wont-do', 10,
                        :deleted_initial_id, 4, :created_at, :deleted_at,
                        :deleted_at
                    )
                    """
                ),
                {
                    "first_id": first_work_id,
                    "deleted_id": deleted_work_id,
                    "project_id": project_id,
                    "first_initial_id": first_initial_id,
                    "deleted_initial_id": deleted_initial_id,
                    "created_at": created_at,
                    "completion_at": completion_at,
                    "deleted_at": deleted_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO checkpoints (
                        id, work_item_id, kind, prompt, source_client,
                        source_session_id, source_model, created_at
                    ) VALUES
                    (
                        :first_initial_id, :first_id, 'context',
                        'Initial context.', 'migration-client',
                        'initial-session', 'initial-model', :created_at
                    ),
                    (
                        :deleted_initial_id, :deleted_id, 'context',
                        'Deleted initial context.', :nbsp,
                        'deleted-session', NULL, :created_at
                    ),
                    (
                        :progress_id, :first_id, 'progress',
                        'Historical progress.', :tab,
                        'progress-session', NULL, :progress_at
                    ),
                    (
                        :completion_id, :first_id, 'completion',
                        'Historical completion.', 'migration-client',
                        'completion-session', 'completion-model', :completion_at
                    )
                    """
                ),
                {
                    "first_initial_id": first_initial_id,
                    "deleted_initial_id": deleted_initial_id,
                    "progress_id": progress_id,
                    "completion_id": completion_id,
                    "first_id": first_work_id,
                    "deleted_id": deleted_work_id,
                    "created_at": created_at,
                    "progress_at": progress_at,
                    "completion_at": completion_at,
                    "nbsp": "\u00a0",
                    "tab": "\t",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO work_relationships (
                        id, project_id, relationship_type, source_work_item_id,
                        target_work_item_id, context_checkpoint_work_item_id,
                        context_checkpoint_id, created_by_client,
                        created_by_session_id, created_by_model, created_at
                    ) VALUES (
                        :id, :project_id, 'discovered-from', :source_id,
                        :target_id, :target_id, :context_id,
                        'relationship-client', 'relationship-session',
                        'relationship-model', :created_at
                    )
                    """
                ),
                {
                    "id": relationship_id,
                    "project_id": project_id,
                    "source_id": deleted_work_id,
                    "target_id": first_work_id,
                    "context_id": progress_id,
                    "created_at": created_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO work_leases (
                        work_item_id, holder_client, holder_session_id,
                        claim_request_id, lease_token, acquired_at, renewed_at,
                        expires_at
                    ) VALUES (
                        :work_item_id, 'lease-client', :em_space,
                        'retained-request', 'retained-token', :created_at,
                        :created_at, :expires_at
                    )
                    """
                ),
                {
                    "work_item_id": first_work_id,
                    "em_space": "\u2003",
                    "created_at": created_at,
                    "expires_at": created_at + timedelta(days=1),
                },
            )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0010_work_events")

        with engine.connect() as connection:
            lease = connection.execute(
                text(
                    "SELECT lease_generation_id, pending_release_id "
                    "FROM work_leases WHERE work_item_id = :work_item_id"
                ),
                {"work_item_id": first_work_id},
            ).one()
            assert isinstance(lease.lease_generation_id, UUID)
            assert lease.pending_release_id is None
            events = connection.execute(
                text(
                    """
                    SELECT id, work_item_id, event_type, actor_kind, metadata,
                           checkpoint_id, lease_generation_id, relationship_id,
                           relationship_source_work_item_id,
                           relationship_target_work_item_id,
                           relationship_context_checkpoint_work_item_id,
                           relationship_context_checkpoint_id, origin
                    FROM work_events
                    ORDER BY id
                    """
                )
            ).mappings().all()
            assert [(row["event_type"], row["work_item_id"]) for row in events] == [
                ("work_created", first_work_id),
                ("work_created", deleted_work_id),
                ("relationship_added", deleted_work_id),
                ("relationship_added", first_work_id),
                ("work_claimed", first_work_id),
                ("checkpoint_added", first_work_id),
                ("work_completed", first_work_id),
                ("work_deleted", deleted_work_id),
            ]
            assert all(row["origin"] == "backfill" for row in events)
            lookup = {(row["event_type"], row["work_item_id"]): row for row in events}
            assert lookup[("work_created", deleted_work_id)]["actor_kind"] == "unattributed"
            assert lookup[("work_deleted", deleted_work_id)]["actor_kind"] == "unattributed"
            assert lookup[("checkpoint_added", first_work_id)]["actor_kind"] == "unattributed"
            claim = lookup[("work_claimed", first_work_id)]
            assert claim["actor_kind"] == "unattributed"
            assert claim["lease_generation_id"] == lease.lease_generation_id
            assert claim["metadata"]["expiry_basis"] == "retained_lease_at_cutover"
            relationship = lookup[("relationship_added", deleted_work_id)]
            assert relationship["actor_kind"] == "client"
            assert relationship["relationship_id"] == relationship_id
            assert relationship["relationship_source_work_item_id"] == deleted_work_id
            assert relationship["relationship_target_work_item_id"] == first_work_id
            assert relationship["relationship_context_checkpoint_work_item_id"] == first_work_id
            assert relationship["relationship_context_checkpoint_id"] == progress_id
            assert lookup[("work_deleted", deleted_work_id)]["metadata"] == {
                "final_status": "wont-do",
                "final_version": 4,
            }
            whitespace = connection.execute(
                text(
                    """
                    SELECT mnemonic_has_non_whitespace('x'),
                           mnemonic_has_non_whitespace(:spaces),
                           mnemonic_has_non_whitespace(:nbsp),
                           mnemonic_has_non_whitespace(:em_space)
                    """
                ),
                {"spaces": " \t\n", "nbsp": "\u00a0", "em_space": "\u2003"},
            ).one()
            assert whitespace == (True, False, False, False)

        mark_release = text(
            """
            UPDATE work_leases
            SET pending_release_id = :release_id
            WHERE work_item_id = :work_item_id
            """
        )
        insert_release = text(
            """
            INSERT INTO work_events (
                project_id, work_item_id, event_type, actor_kind,
                lease_generation_id, lease_release_id, metadata
            )
            SELECT work.project_id, lease.work_item_id, 'work_released',
                   'unattributed', lease.lease_generation_id, :release_id,
                   '{"lease_holder_kind": "unattributed"}'::jsonb
            FROM work_leases AS lease
            JOIN work_items AS work ON work.id = lease.work_item_id
            WHERE lease.work_item_id = :work_item_id
            """
        )
        delete_lease = text(
            "DELETE FROM work_leases WHERE work_item_id = :work_item_id"
        )
        force_constraints = text("SET CONSTRAINTS ALL IMMEDIATE")
        lease_parameters = {"work_item_id": first_work_id}

        def marker_parameters(release_id):
            return {**lease_parameters, "release_id": release_id}

        release_to_clear = uuid4()
        with pytest.raises(DBAPIError, match="marker transition requires one release event"):
            with engine.begin() as connection:
                connection.execute(mark_release, marker_parameters(release_to_clear))
                connection.execute(mark_release, marker_parameters(None))
                connection.execute(force_constraints)

        first_marker = uuid4()
        replacement_marker = uuid4()
        with pytest.raises(DBAPIError, match="marker transition requires one release event"):
            with engine.begin() as connection:
                connection.execute(mark_release, marker_parameters(first_marker))
                connection.execute(mark_release, marker_parameters(replacement_marker))
                connection.execute(insert_release, marker_parameters(replacement_marker))
                connection.execute(delete_lease, lease_parameters)
                connection.execute(force_constraints)

        retained_marker = uuid4()
        with pytest.raises(DBAPIError, match="release marker cannot remain set at commit"):
            with engine.begin() as connection:
                connection.execute(mark_release, marker_parameters(retained_marker))
                connection.execute(insert_release, marker_parameters(retained_marker))
                connection.execute(force_constraints)

        missing_event_marker = uuid4()
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(mark_release, marker_parameters(missing_event_marker))
                connection.execute(delete_lease, lease_parameters)
                connection.execute(force_constraints)

        canonical_marker = uuid4()
        with engine.begin() as connection:
            connection.execute(mark_release, marker_parameters(canonical_marker))
            connection.execute(insert_release, marker_parameters(canonical_marker))
            connection.execute(delete_lease, lease_parameters)
            connection.execute(force_constraints)

        with engine.connect() as connection:
            lease_and_event_counts = connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM work_leases
                         WHERE work_item_id = :work_item_id) AS lease_count,
                        (SELECT count(*) FROM work_events
                         WHERE lease_release_id = :release_id) AS release_event_count
                    """
                ),
                marker_parameters(canonical_marker),
            ).one()
            assert lease_and_event_counts == (0, 1)

        with pytest.raises(DBAPIError, match="work events are immutable"):
            with engine.begin() as connection:
                connection.execute(text("UPDATE work_events SET origin = 'live' WHERE id = 1"))
        with pytest.raises(DBAPIError, match="work events are immutable"):
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM work_events WHERE id = 1"))

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0009_ready_work_indexes")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT to_regclass('work_events')")
            ).scalar_one() is None
            lease_columns = connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'work_leases'
                      AND column_name IN (
                          'lease_generation_id', 'pending_release_id'
                      )
                    """
                )
            ).scalars().all()
            assert lease_columns == []
            assert connection.execute(
                text("SELECT to_regprocedure('mnemonic_normalized_tags(character varying[])')")
            ).scalar_one() is not None
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
