"""The per-test schema reset empties rows in place, and only replays on damage."""

from uuid import uuid4

import alembic.command
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from .conftest import _RESET_PLANS, PRESERVED_TABLES, reset_disposable_schema
from .report_fixtures import reported

pytestmark = pytest.mark.postgres

_GUARDED_TABLES = (
    "artifact_references",
    "client_operations",
    "verification_results",
    "work_events",
    "project_activity",
    "project_activity_heads",
    "project_settings",
    "job_completion_reports",
    "job_completion_report_reviews",
    "job_completion_report_follow_ups",
    "project_job_completion_report_counts",
)
_POPULATED_TABLES = (
    *(table for table in _GUARDED_TABLES if table != "job_completion_report_follow_ups"),
    "checkpoints", "projects", "work_items",
)


def _current_schema(engine: Engine) -> str:
    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
    assert isinstance(schema, str)
    return schema


def _relation_oids(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT relation.relname AS table_name,
                       relation.oid AS relation_oid
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = pg_catalog.current_schema()
                  AND relation.relkind IN ('r', 'p')
                """
            )
        ).mappings()
        return {str(row["table_name"]): int(row["relation_oid"]) for row in rows}


def _truncate_guards(engine: Engine) -> dict[tuple[str, str], str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT relation.relname AS table_name,
                       trigger_row.tgname AS trigger_name,
                       trigger_row.tgenabled AS enabled
                FROM pg_catalog.pg_trigger AS trigger_row
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid = trigger_row.tgrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = pg_catalog.current_schema()
                  AND NOT trigger_row.tgisinternal
                  AND (trigger_row.tgtype & 32) <> 0
                """
            )
        ).mappings()
        return {
            (str(row["table_name"]), str(row["trigger_name"])): str(row["enabled"]) for row in rows
        }


def _row_triggers(engine: Engine) -> set[tuple[str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT relation.relname AS table_name,
                       trigger_row.tgname AS trigger_name
                FROM pg_catalog.pg_trigger AS trigger_row
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid = trigger_row.tgrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = pg_catalog.current_schema()
                  AND NOT trigger_row.tgisinternal
                """
            )
        ).mappings()
        return {(str(row["table_name"]), str(row["trigger_name"])) for row in rows}


def _row_counts(engine: Engine) -> dict[str, int]:
    """Count every table a reset is expected to empty, so a new one is covered too."""
    tables = sorted(set(_relation_oids(engine)) - set(PRESERVED_TABLES))
    with engine.connect() as connection:
        return {
            table: int(connection.scalar(text(f'SELECT count(*) FROM "{table}"')) or 0)
            for table in tables
        }


def _complete_with_evidence(api: TestClient, work_payload: dict) -> None:
    """Populate every table a completion touches, guarded history included."""
    project = api.post("/api/v1/projects", json={"name": "Schema reset fixture"})
    assert project.status_code == 201, project.text
    collection = f"/api/v1/projects/{project.json()['id']}/work-items"
    created = api.post(collection, json=work_payload)
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    completed = api.post(
        f"{collection}/{work['id']}/complete",
        json=reported({
            "expected_version": 1,
            "client_operation_id": str(uuid4()),
            "checkpoint": {
                "prompt": "Completed with evidence so the reset has history to clear.",
                "source_client": "pytest",
                "source_session_id": "schema-reset",
                "source_model": "test-model",
                "verified_against": "7ad62e4",
            },
            "completion_evidence": {
                "verification_results": [
                    {
                        "verification_type": "command",
                        "name": "Backend suite",
                        "outcome": "passed",
                        "summary": "The backend suite passed against the disposable schema.",
                        "command": "uv run pytest -q",
                        "exit_code": 0,
                    }
                ],
                "artifact_references": [
                    {
                        "artifact_type": "commit",
                        "label": "Reviewed commit",
                        "reference": "7ad62e4",
                    }
                ],
            },
        }),
    )
    assert completed.status_code == 200, completed.text


def test_reset_of_an_intact_schema_never_replays_the_migration_chain(
    api: TestClient, postgres_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replaying all nineteen migrations costs ~435 ms a test against ~30 ms."""

    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("The per-test schema reset must not replay Alembic migrations")

    monkeypatch.setattr(alembic.command, "upgrade", _refuse)
    monkeypatch.setattr(alembic.command, "downgrade", _refuse)
    reset_disposable_schema(postgres_engine)


def test_reset_keeps_the_schema_and_its_relations_in_place(
    api: TestClient, postgres_engine: Engine
) -> None:
    """Relation OIDs change whenever a reset drops and rebuilds the schema."""
    before = _relation_oids(postgres_engine)
    assert set(before) >= {*_POPULATED_TABLES, *PRESERVED_TABLES}
    reset_disposable_schema(postgres_engine)
    assert _relation_oids(postgres_engine) == before


def test_reset_empties_every_table_but_keeps_the_migration_head(
    api: TestClient, postgres_engine: Engine, work_payload: dict
) -> None:
    _complete_with_evidence(api, work_payload)
    seeded = _row_counts(postgres_engine)
    assert all(seeded[table] > 0 for table in _POPULATED_TABLES), seeded

    with postgres_engine.connect() as connection:
        head = connection.scalar(text("SELECT version_num FROM alembic_version"))

    reset_disposable_schema(postgres_engine)

    emptied = _row_counts(postgres_engine)
    assert {table: count for table, count in emptied.items() if count} == {}
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == head


def test_reset_rearms_every_truncate_guard(api: TestClient, postgres_engine: Engine) -> None:
    """A reset disarms the guards only for its own transaction."""
    before = _truncate_guards(postgres_engine)
    assert {table for table, _ in before} == set(_GUARDED_TABLES)

    reset_disposable_schema(postgres_engine)

    after = _truncate_guards(postgres_engine)
    assert after == before
    assert set(after.values()) == {"O"}, after
    with pytest.raises(DBAPIError) as rejected:
        with postgres_engine.begin() as connection:
            connection.execute(text("TRUNCATE work_events CASCADE"))
    assert "authoritative event and receipt history cannot be truncated" in str(rejected.value)


def test_a_failed_reset_leaves_the_guards_armed(
    api: TestClient, postgres_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash between the disarm and the re-arm must not strand a guard.

    Replacing the cached plan is the only way to interrupt a reset midway; both
    halves share one transaction, so the rollback re-arms every guard.
    """
    armed = _truncate_guards(postgres_engine)
    schema = _current_schema(postgres_engine)
    plan = _RESET_PLANS[schema]
    disarms = [statement for statement in plan.statements if "DISABLE TRIGGER" in statement]
    assert len(disarms) == len(armed)
    monkeypatch.setitem(_RESET_PLANS, schema, plan._replace(statements=(*disarms, "SELECT 1 / 0")))

    with pytest.raises(DBAPIError):
        reset_disposable_schema(postgres_engine)

    assert _truncate_guards(postgres_engine) == armed


def test_reset_replays_the_migrations_for_a_schema_a_test_damaged(
    api: TestClient, postgres_engine: Engine
) -> None:
    """Tests that drop a guard to write a corrupt row still get the guard back."""
    intact = _relation_oids(postgres_engine)
    with postgres_engine.begin() as connection:
        connection.execute(text("DROP TRIGGER events_immutable ON work_events"))
    assert ("work_events", "events_immutable") not in _row_triggers(postgres_engine)

    reset_disposable_schema(postgres_engine)

    assert ("work_events", "events_immutable") in _row_triggers(postgres_engine)
    assert _relation_oids(postgres_engine) != intact
    assert {table for table, _ in _truncate_guards(postgres_engine)} == set(_GUARDED_TABLES)
