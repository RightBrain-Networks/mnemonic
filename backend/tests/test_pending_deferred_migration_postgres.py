"""Populated open-to-pending migration coverage."""

import os
from uuid import uuid4

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


def test_open_rows_upgrade_to_pending_and_clean_history_downgrades():
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(
        database_url=raw_url,
        api_key="pending-deferred-migration-test-key-is-long-enough",
    )
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_pending_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(
        url.update_query_dict({"options": f"-c search_path={schema} -c timezone=UTC"}),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    project_id = uuid4()
    work_item_id = uuid4()
    checkpoint_id = uuid4()

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0011_project_settings")
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, slug) "
                    "VALUES (:project_id, 'Pending migration', 'pending-migration')"
                ),
                {"project_id": project_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO work_items (
                        id, project_id, title, summary, status, priority,
                        initial_checkpoint_id, version
                    ) VALUES (
                        :work_item_id, :project_id, 'Existing open work',
                        'Must become pending.', 'open', 50, :checkpoint_id, 1
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "work_item_id": work_item_id,
                    "checkpoint_id": checkpoint_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO checkpoints (
                        id, work_item_id, kind, prompt, source_client, source_session_id
                    ) VALUES (
                        :checkpoint_id, :work_item_id, 'context', 'Existing context.',
                        'migration-test', 'migration-session'
                    )
                    """
                ),
                {"checkpoint_id": checkpoint_id, "work_item_id": work_item_id},
            )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0012_pending_deferred_statuses")

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT status FROM work_items WHERE id = :id"),
                {"id": work_item_id},
            ) == "pending"
            default = connection.scalar(
                text(
                    """
                    SELECT column_default
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'work_items'
                      AND column_name = 'status'
                    """
                )
            )
            assert default == "'pending'::character varying"
            predicate = connection.scalar(
                text(
                    """
                    SELECT pg_get_expr(indexprs.indpred, indexprs.indrelid)
                    FROM pg_index AS indexprs
                    JOIN pg_class AS index_class ON index_class.oid = indexprs.indexrelid
                    WHERE index_class.relname = 'ix_work_items_ready_order'
                    """
                )
            )
            assert "'pending'" in predicate
            assert "'open'" not in predicate
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE work_items SET status = 'deferred' WHERE id = :id"),
                {"id": work_item_id},
            )
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE work_items SET status = 'open' WHERE id = :id"),
                    {"id": work_item_id},
                )

        # Downgrade is deliberately guarded once Deferred history exists.
        with pytest.raises(RuntimeError, match="deferred work history"):
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "0011_project_settings")

        with engine.begin() as connection:
            connection.execute(
                text("UPDATE work_items SET status = 'pending' WHERE id = :id"),
                {"id": work_item_id},
            )
            config.attributes["connection"] = connection
            command.downgrade(config, "0011_project_settings")
            assert connection.scalar(
                text("SELECT status FROM work_items WHERE id = :id"),
                {"id": work_item_id},
            ) == "open"
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
