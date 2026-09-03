"""Populated 0016-to-0017 preservation coverage for Advisory-only derived SQL."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api.config import Settings

from .conftest import BACKEND_DIR
from .test_duplicate_merge_invariants_postgres import _create_project_with_work

pytestmark = pytest.mark.postgres

_DOMAIN_TABLES = (
    "projects",
    "project_settings",
    "work_items",
    "checkpoints",
    "work_item_embeddings",
    "work_leases",
    "work_relationships",
    "work_duplicate_merges",
    "work_events",
    "work_gates",
    "client_operations",
)


def _domain_digests(connection):
    return {
        table: connection.scalar(
            text(
                f"SELECT md5(COALESCE(string_agg(row_value, E'\\n' ORDER BY row_value), '')) "
                f"FROM (SELECT to_jsonb(domain_row)::text AS row_value FROM {table} AS domain_row) "
                "AS serialized"
            )
        )
        for table in _DOMAIN_TABLES
    }


def test_0017_populated_upgrade_preserves_every_domain_row_and_can_remove_advisory_sql():
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(
        database_url=raw_url,
        api_key="duplicate-suggestion-migration-key-long-enough",
    )
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_duplicate_0017_" + uuid4().hex
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
            command.upgrade(config, "0016_duplicate_handling")

        with engine.begin() as connection:
            _project_id, work = _create_project_with_work(connection, work_count=2)
            connection.execute(
                text(
                    """
                    INSERT INTO work_item_embeddings (work_item_id, model, digest, vector)
                    VALUES (:work_item_id, 'retained-model', :digest, ARRAY[0.25, 0.75]::real[])
                    """
                ),
                {"work_item_id": work[0][0], "digest": "a" * 64},
            )
            before = _domain_digests(connection)

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0017_duplicate_suggestion_title_key")
            assert _domain_digests(connection) == before
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0017_duplicate_suggestion_title_key"
            )
            assert connection.scalar(
                text("SELECT to_regprocedure('mnemonic_duplicate_title_key_v1(text)')")
            ) is not None

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0016_duplicate_handling")
            assert _domain_digests(connection) == before
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0016_duplicate_handling"
            )
            assert connection.scalar(
                text("SELECT to_regprocedure('mnemonic_duplicate_title_key_v1(text)')")
            ) is None
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
