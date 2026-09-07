"""Integration tests use a disposable schema, never the application's tables."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api.config import Settings
from mnemonic_api.main import create_app

TEST_API_KEY = "mnemonic-integration-test-key-32-characters"
BACKEND_DIR = Path(__file__).resolve().parents[1]
# Alembic's own bookkeeping survives a reset; every other table is emptied.
PRESERVED_TABLES = ("alembic_version",)


class _SchemaReset(NamedTuple):
    """How to empty one disposable schema, and the DDL that plan assumes."""

    schema: str
    catalog_digest: str
    statements: tuple[str, ...]


_RESET_PLANS: dict[str, _SchemaReset] = {}
_SCHEMA_TABLES_SQL = """
    SELECT relation.relname
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = :schema
      AND relation.relkind IN ('r', 'p')
      AND relation.relname <> ALL(CAST(:preserved AS text[]))
    ORDER BY relation.relname
"""
# tgtype bit 32 marks a TRUNCATE trigger; information_schema omits those
# entirely. Only origin-enabled guards are cycled, so re-arming restores the
# exact firing mode each one already had.
_TRUNCATE_GUARDS_SQL = """
    SELECT relation.relname AS table_name,
           trigger_row.tgname AS trigger_name
    FROM pg_catalog.pg_trigger AS trigger_row
    JOIN pg_catalog.pg_class AS relation
      ON relation.oid = trigger_row.tgrelid
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = :schema
      AND NOT trigger_row.tgisinternal
      AND trigger_row.tgenabled = 'O'
      AND (trigger_row.tgtype & 32) <> 0
    ORDER BY relation.relname, trigger_row.tgname
"""
# One digest over everything a test could alter: relations and their options and
# grants, columns, constraints, indexes, triggers, and routine bodies. Names and
# flags catch a dropped guard or a forced pg_index flag; the pg_node_tree and
# prosrc digests catch a same-named definition swapped underneath. Node trees
# embed object OIDs, so this is comparable only within one built schema, which
# is exactly the comparison a reset makes.
#
# The attribute list is deliberately a superset of the operational audit's
# catalog_snapshot in scripts/audit_project_activity.py: anything that audit would
# report as drift also moves this digest, so a schema a test damaged is rebuilt
# rather than quietly reused by a later audit test. Internal triggers are included
# here, unlike in _TRUNCATE_GUARDS_SQL which must only cycle user TRUNCATE guards,
# because the audit's foreign_key_triggers category reads exactly those rows. The
# closing pg_depend branch is a membership tripwire for object kinds nobody
# enumerated - a domain, a collation, a text-search dictionary - joining or
# leaving the schema.
#
# Rejected, measured: reusing the audit's own nine queries on every reset is a
# superset by construction rather than by curation, but costs +43% suite wall
# (269.65 s against 188.58 s); widening this one query costs about +2.7 ms a call.
_CATALOG_DIGEST_SQL = """
    WITH target AS (
        SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = :schema
    ),
    entries AS (
        SELECT pg_catalog.concat_ws(
                   '|', 'relation', relation.relname, relation.relkind,
                   relation.relpersistence, relation.relrowsecurity,
                   relation.relforcerowsecurity, relation.relispartition,
                   relation.relreplident, relation.relowner,
                   relation.reltablespace, relation.relam,
                   COALESCE(CAST(relation.relacl AS text), ''),
                   COALESCE(CAST(relation.reloptions AS text), '')
               ) AS entry
        FROM pg_catalog.pg_class AS relation
        JOIN target ON target.oid = relation.relnamespace
        UNION ALL
        SELECT pg_catalog.concat_ws(
                   '|', 'column', relation.relname, attribute.attname,
                   attribute.attnum, CAST(attribute.atttypid AS regtype),
                   attribute.atttypmod, attribute.attnotnull, attribute.atthasdef,
                   attribute.attidentity, attribute.attgenerated,
                   COALESCE(CAST(attribute.attacl AS text), ''),
                   pg_catalog.md5(COALESCE(CAST(default_row.adbin AS text), ''))
               )
        FROM pg_catalog.pg_attribute AS attribute
        JOIN pg_catalog.pg_class AS relation ON relation.oid = attribute.attrelid
        JOIN target ON target.oid = relation.relnamespace
        LEFT JOIN pg_catalog.pg_attrdef AS default_row
          ON default_row.adrelid = attribute.attrelid
         AND default_row.adnum = attribute.attnum
        WHERE attribute.attnum > 0 AND NOT attribute.attisdropped
        UNION ALL
        SELECT pg_catalog.concat_ws(
                   '|', 'constraint', relation.relname, constraint_row.conname,
                   constraint_row.contype, constraint_row.convalidated,
                   constraint_row.condeferrable, constraint_row.condeferred,
                   constraint_row.connoinherit, constraint_row.confupdtype,
                   constraint_row.confdeltype, constraint_row.confmatchtype,
                   CAST(constraint_row.confrelid AS regclass),
                   CAST(constraint_row.conindid AS regclass),
                   COALESCE(CAST(constraint_row.conkey AS text), ''),
                   COALESCE(CAST(constraint_row.confkey AS text), ''),
                   COALESCE(CAST(constraint_row.conexclop AS text), ''),
                   pg_catalog.md5(COALESCE(CAST(constraint_row.conbin AS text), ''))
               )
        FROM pg_catalog.pg_constraint AS constraint_row
        JOIN pg_catalog.pg_class AS relation ON relation.oid = constraint_row.conrelid
        JOIN target ON target.oid = relation.relnamespace
        UNION ALL
        SELECT pg_catalog.concat_ws(
                   '|', 'index', index_relation.relname, index_row.indisunique,
                   index_row.indisprimary, index_row.indisexclusion,
                   index_row.indisvalid, index_row.indisready,
                   index_row.indnatts, index_row.indnkeyatts,
                   CAST(index_row.indkey AS text), CAST(index_row.indclass AS text),
                   CAST(index_row.indoption AS text),
                   pg_catalog.md5(COALESCE(CAST(index_row.indexprs AS text), '')),
                   pg_catalog.md5(COALESCE(CAST(index_row.indpred AS text), ''))
               )
        FROM pg_catalog.pg_index AS index_row
        JOIN pg_catalog.pg_class AS index_relation
          ON index_relation.oid = index_row.indexrelid
        JOIN target ON target.oid = index_relation.relnamespace
        UNION ALL
        SELECT pg_catalog.concat_ws(
                   '|', 'trigger', relation.relname, trigger_row.tgname,
                   trigger_row.tgenabled, trigger_row.tgtype,
                   trigger_row.tgisinternal, trigger_row.tgdeferrable,
                   trigger_row.tginitdeferred,
                   CAST(trigger_row.tgconstrrelid AS regclass),
                   CAST(trigger_row.tgfoid AS regprocedure),
                   COALESCE(CAST(trigger_row.tgattr AS text), ''),
                   COALESCE(trigger_row.tgoldtable, ''),
                   COALESCE(trigger_row.tgnewtable, ''),
                   pg_catalog.md5(COALESCE(CAST(trigger_row.tgqual AS text), '')),
                   pg_catalog.encode(trigger_row.tgargs, 'hex')
               )
        FROM pg_catalog.pg_trigger AS trigger_row
        JOIN pg_catalog.pg_class AS relation ON relation.oid = trigger_row.tgrelid
        JOIN target ON target.oid = relation.relnamespace
        UNION ALL
        SELECT pg_catalog.concat_ws(
                   '|', 'routine', routine.proname,
                   pg_catalog.oidvectortypes(routine.proargtypes),
                   routine.prokind, routine.provolatile, routine.proisstrict,
                   routine.proretset, routine.proleakproof, routine.proparallel,
                   routine.prosecdef, routine.procost, routine.prorows,
                   routine.prolang, routine.proowner,
                   CAST(routine.prorettype AS regtype),
                   COALESCE(CAST(routine.proargnames AS text), ''),
                   COALESCE(CAST(routine.proargmodes AS text), ''),
                   COALESCE(CAST(routine.proargdefaults AS text), ''),
                   COALESCE(CAST(routine.proconfig AS text), ''),
                   COALESCE(CAST(routine.proacl AS text), ''),
                   pg_catalog.md5(COALESCE(routine.prosrc, ''))
               )
        FROM pg_catalog.pg_proc AS routine
        JOIN target ON target.oid = routine.pronamespace
        UNION ALL
        SELECT pg_catalog.concat_ws(
                   '|', 'member', dependency.classid, dependency.objid,
                   dependency.objsubid, dependency.deptype
               )
        FROM pg_catalog.pg_depend AS dependency
        JOIN target ON target.oid = dependency.refobjid
        WHERE dependency.refclassid = CAST('pg_catalog.pg_namespace' AS regclass)
    )
    SELECT pg_catalog.md5(pg_catalog.string_agg(entry, E'\n' ORDER BY entry))
    FROM entries
"""


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine]:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(database_url=raw_url, api_key=TEST_API_KEY)
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_test_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    # Exclude public from search_path so a real application's alembic_version
    # or tables can never be discovered or mutated by these tests.
    options = f"-c search_path={schema} -c timezone=UTC"
    test_url = url.update_query_dict({"options": options})
    engine = create_engine(
        test_url, pool_pre_ping=True, hide_parameters=True, connect_args={"connect_timeout": 5}
    )
    try:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        # Plan the per-test reset against the pristine schema, so its digest is
        # the one every later reset has to still match.
        _plan_reset(engine, schema)
        yield engine
    finally:
        engine.dispose()
        # Only the schema created above is removed; the database is retained.
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.fixture
def api(postgres_engine: Engine) -> Iterator[TestClient]:
    reset_disposable_schema(postgres_engine)
    settings = Settings(
        database_url=postgres_engine.url.render_as_string(hide_password=False), api_key=TEST_API_KEY
    )
    with TestClient(create_app(settings, engine=postgres_engine)) as client:
        client.headers["Authorization"] = f"Bearer {TEST_API_KEY}"
        yield client


def reset_disposable_schema(engine: Engine) -> None:
    """Empty the random test schema in place while its DDL is still intact.

    Dropping the schema and replaying all nineteen migrations costs about
    435 ms a test and dominated the suite; emptying the tables costs about
    30 ms. A handful of tests deliberately damage the schema — dropping an
    immutability trigger to write a corrupt row, say — so the replay stays as
    the fallback, gated on a catalog digest, and only those tests pay for it.

    Phase 11 puts ``BEFORE TRUNCATE`` guards on the authoritative history
    tables. Emptying in place disarms and re-arms them around a single
    ``TRUNCATE``, all inside one transaction: a failure anywhere rolls the
    disarm back with it and leaves every guard armed. Nothing weakens the
    guards themselves, and a missing one moves the digest, which falls back to
    the replay rather than skipping the guard.
    """
    schema = _disposable_schema(engine)
    plan = _RESET_PLANS.get(schema)
    if plan is not None and _empty_tables(engine, plan):
        return
    _replay_migrations(engine, schema)
    _plan_reset(engine, schema)


def _disposable_schema(engine: Engine) -> str:
    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
    if not isinstance(schema, str) or not schema.startswith("mnemonic_test_"):
        raise RuntimeError("Refusing to reset a non-disposable PostgreSQL schema")
    return schema


def _empty_tables(engine: Engine, plan: _SchemaReset) -> bool:
    """Empty every table, unless a test has altered this schema's DDL since."""
    with engine.begin() as connection:
        if _catalog_digest(connection, plan.schema) != plan.catalog_digest:
            return False
        for statement in plan.statements:
            connection.execute(text(statement))
    return True


def _replay_migrations(engine: Engine, schema: str) -> None:
    with engine.begin() as connection:
        connection.execute(DropSchema(schema, cascade=True))
        connection.execute(CreateSchema(schema))
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def _plan_reset(engine: Engine, schema: str) -> _SchemaReset:
    """Read the schema's tables and TRUNCATE guards back out of the catalog.

    Reading them beats a hand-maintained list: a table or guard a later
    migration adds is emptied, and re-armed, without touching this file.
    """
    with engine.connect() as connection:
        tables = [
            str(name)
            for name in connection.scalars(
                text(_SCHEMA_TABLES_SQL),
                {"schema": schema, "preserved": list(PRESERVED_TABLES)},
            )
        ]
        if not tables:
            raise RuntimeError(f"The disposable schema {schema} holds no tables to reset")
        guards = [
            (str(row["table_name"]), str(row["trigger_name"]))
            for row in connection.execute(
                text(_TRUNCATE_GUARDS_SQL), {"schema": schema}
            ).mappings()
        ]
        digest = _catalog_digest(connection, schema)

    def qualified(table: str) -> str:
        return f"{_quoted(schema)}.{_quoted(table)}"

    truncate = "TRUNCATE " + ", ".join(map(qualified, tables)) + " RESTART IDENTITY CASCADE"
    plan = _SchemaReset(
        schema,
        digest,
        (
            *(
                f"ALTER TABLE {qualified(table)} DISABLE TRIGGER {_quoted(guard)}"
                for table, guard in guards
            ),
            truncate,
            *(
                f"ALTER TABLE {qualified(table)} ENABLE TRIGGER {_quoted(guard)}"
                for table, guard in guards
            ),
        ),
    )
    _RESET_PLANS[schema] = plan
    return plan


def _catalog_digest(connection: Connection, schema: str) -> str:
    digest = connection.scalar(text(_CATALOG_DIGEST_SQL), {"schema": schema})
    if not isinstance(digest, str):
        raise RuntimeError(f"The disposable schema {schema} has no catalog to digest")
    return digest


def _quoted(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


@pytest.fixture
def project(api: TestClient) -> dict:
    response = api.post("/api/v1/projects", json={"name": "First project"})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def checkpoint_fields() -> dict:
    return {
        "prompt": (
            "  Agent-authored proposal; recheck the current tree before acting.\r\n\r\n"
            "Context: investigate cache state in src/cache.py.\n"
            "Outcome: invalidated entries must stop appearing after a branch switch.\n"
            "References: src/cache.py; verified commit abc1234.\n"
            "Hazard: preserve entries belonging to other projects.\n"
            "Verify: reproduce, add a regression test, and run the cache test suite.\n  "
        ),
        "source_client": "claude-code",
        "source_session_id": "3d46fe7a-session:opaque_001",
        "source_model": "origin-model",
        "source_session_url": "https://example.com/sessions/3d46fe7a",
        "repository_branch": "feature/cache",
        "verified_against": "abc1234",
        "tags": ["cache", "correctness"],
        "source_metadata": {"reference": "src/cache.py:42", "author_notes": ["recheck", 2, True]},
    }


@pytest.fixture
def work_payload(checkpoint_fields: dict) -> dict:
    return {
        "title": "Investigate stale cache entries",
        "summary": "Cached state survives invalidation after a branch switch.",
        "priority": 30,
        "initial_checkpoint": dict(checkpoint_fields),
    }
