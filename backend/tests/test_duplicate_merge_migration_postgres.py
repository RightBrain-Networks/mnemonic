"""Populated-upgrade coverage for the duplicate-handling migration."""

import os
from hashlib import sha256
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from .conftest import BACKEND_DIR
from .test_duplicate_merge_invariants_postgres import _create_project_with_work

pytestmark = pytest.mark.postgres


def _event_validator_definition(connection) -> str:
    definition = connection.scalar(
        text(
            """
            SELECT pg_get_functiondef(procedure.oid)
            FROM pg_proc AS procedure
            JOIN pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = current_schema()
              AND procedure.proname = 'mnemonic_work_event_metadata_v2_is_valid'
            """
        )
    )
    assert isinstance(definition, str)
    return definition


def test_0016_populated_upgrade_preserves_history_and_adds_no_merge() -> None:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    url = make_url(raw_url)
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_duplicate_0016_" + uuid4().hex
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
            command.upgrade(config, "0015_gate_review_fixes")

        with engine.begin() as connection:
            project_id, work = _create_project_with_work(connection, work_count=3)
            relationship_ids = (uuid4(), uuid4(), uuid4())
            relationship_rows = (
                (relationship_ids[0], work[0][0], work[1][0]),
                (relationship_ids[1], work[1][0], work[0][0]),
                (relationship_ids[2], work[0][0], work[2][0]),
            )
            for relationship_id, source_id, destination_id in relationship_rows:
                connection.execute(
                    text(
                        """
                        INSERT INTO work_relationships (
                            id, project_id, relationship_type, source_work_item_id,
                            target_work_item_id, created_by_client,
                            created_by_session_id
                        ) VALUES (
                            :id, :project_id, 'duplicate-of', :source_id,
                            :destination_id, 'legacy-client', 'legacy-session'
                        )
                        """
                    ),
                    {
                        "id": relationship_id,
                        "project_id": project_id,
                        "source_id": source_id,
                        "destination_id": destination_id,
                    },
                )
            tables = (
                "projects",
                "project_settings",
                "work_items",
                "checkpoints",
                "work_item_embeddings",
                "work_leases",
                "work_relationships",
                "work_events",
                "work_gates",
                "client_operations",
            )
            counts_before = {
                table: connection.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in tables
            }
            relationships_before = connection.execute(
                text(
                    """
                    SELECT id, project_id, relationship_type, source_work_item_id,
                           target_work_item_id, context_checkpoint_work_item_id,
                           context_checkpoint_id, created_by_client,
                           created_by_session_id, created_by_model, created_at
                    FROM work_relationships
                    ORDER BY id
                    """
                )
            ).all()
            event_validator_hash_before = sha256(
                _event_validator_definition(connection).encode()
            ).hexdigest()

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0016_duplicate_handling")

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0016_duplicate_handling"
            counts_after = {
                table: connection.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in tables
            }
            assert counts_after == counts_before
            assert connection.execute(
                text(
                    """
                    SELECT id, project_id, relationship_type, source_work_item_id,
                           target_work_item_id, context_checkpoint_work_item_id,
                           context_checkpoint_id, created_by_client,
                           created_by_session_id, created_by_model, created_at
                    FROM work_relationships
                    ORDER BY id
                    """
                )
            ).all() == relationships_before
            assert connection.scalar(text("SELECT count(*) FROM work_duplicate_merges")) == 0
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM work_relationships "
                    "WHERE created_for_duplicate_merge_id IS NOT NULL"
                )
            ) == 0
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM work_events "
                    "WHERE created_for_duplicate_merge_id IS NOT NULL "
                    "OR work_duplicate_merge_id IS NOT NULL"
                )
            ) == 0
            assert sha256(_event_validator_definition(connection).encode()).hexdigest() == (
                event_validator_hash_before
            )

        with pytest.raises(RuntimeError, match="unsupported"):
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "0015_gate_review_fixes")
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
