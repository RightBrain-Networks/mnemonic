"""The per-test schema reset empties rows in place, and only replays on damage."""

import json
import runpy
from pathlib import Path
from uuid import uuid4

import alembic.command
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from mnemonic_api.artifact_storage import ArtifactStorage

from .conftest import (
    _RESET_PLANS,
    BACKEND_DIR,
    PRESERVED_TABLES,
    _catalog_digest,
    reset_disposable_schema,
)
from .report_fixtures import reported

pytestmark = pytest.mark.postgres

# One statement per category of the operational audit's guard catalog, each of
# which that audit reports as drift. The reset digest is meant to be a superset of
# what the audit inspects, so every one of these must also move the reset digest -
# otherwise the damage survives an in-place TRUNCATE and every later audit test on
# that worker's schema reports catalog drift it did not cause.
_AUDIT_VISIBLE_DAMAGE = (
    ("relation_state", "ALTER TABLE work_items REPLICA IDENTITY FULL"),
    ("columns", "ALTER TABLE work_items ALTER COLUMN priority SET DEFAULT 99"),
    ("functions", "ALTER FUNCTION mnemonic_reject_checkpoint_mutation() PARALLEL SAFE"),
    # work_item_embeddings is the only table carrying internal foreign-key triggers
    # and no user triggers, so this moves the foreign_key_triggers category alone.
    ("foreign_key_triggers", "ALTER TABLE work_item_embeddings DISABLE TRIGGER ALL"),
    ("triggers", "ALTER TABLE work_events DISABLE TRIGGER ALL"),
    ("constraints", "ALTER TABLE work_items ADD CONSTRAINT ck_probe CHECK (version > 0)"),
    ("indexes", "CREATE INDEX ix_probe ON work_items (version)"),
    # Replacing an index in place under its own name, changing only one attribute
    # pg_get_indexdef renders. Adding or dropping an index moves the digest through
    # the relation branch, so only a same-name swap exercises attribute parity.
    (
        "indexes",
        "DROP INDEX ix_work_items_project_status_updated; "
        "CREATE INDEX ix_work_items_project_status_updated ON work_items USING btree "
        '(project_id, status COLLATE "C", updated_at DESC, id DESC) '
        "WHERE (deleted_at IS NULL)",
    ),
    (
        "indexes",
        "DROP INDEX uq_work_events_gate_fact; "
        "CREATE UNIQUE INDEX uq_work_events_gate_fact ON work_events USING btree "
        "(work_item_id, gate_id, event_type) NULLS NOT DISTINCT "
        "WHERE (gate_id IS NOT NULL)",
    ),
    (
        "function_permissions",
        "REVOKE EXECUTE ON FUNCTION mnemonic_reject_checkpoint_mutation() FROM PUBLIC",
    ),
    ("column_permissions", "GRANT SELECT (title) ON work_items TO PUBLIC"),
)

_GUARDED_TABLES = (
    "artifacts", "artifact_revisions", "artifact_audit",
    "artifact_work_links", "artifact_operations",
    "work_completion_review_policies",
    "work_agent_follow_ups",
    "work_agent_follow_up_answers",
    "code_reviews",
    "code_review_scopes",
    "code_review_handoffs",
    "code_review_results",
    "code_review_findings",
    "code_review_remediations",
    "artifact_references",
    "client_operations",
    "work_item_moves",
    "work_report_provenance_heads",
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
    *(table for table in _GUARDED_TABLES if table not in {
        "work_agent_follow_ups",
        "work_agent_follow_up_answers",
        "code_reviews",
        "code_review_scopes",
        "code_review_handoffs",
        "code_review_results",
        "code_review_findings",
        "code_review_remediations",
        "job_completion_report_follow_ups",
        "work_item_moves",
        "work_report_provenance_heads",
    }),
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
    uploaded = api.post(
        f"/api/v1/projects/{project.json()['id']}/artifacts", content=b"reset fixture bytes",
        headers={"X-Client-Operation-ID": str(uuid4()), "X-Artifact-Metadata": json.dumps({
            "filename": "reset.txt", "work_item_id": work["id"],
            "agent_session_id": "schema-reset", "actor_client": "pytest",
        })},
    )
    assert uploaded.status_code == 201, uploaded.text


def test_reset_of_an_intact_schema_never_replays_the_migration_chain(
    api: TestClient, postgres_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replaying all nineteen migrations costs ~435 ms a test against ~30 ms."""

    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("The per-test schema reset must not replay Alembic migrations")

    monkeypatch.setattr(alembic.command, "upgrade", _refuse)
    monkeypatch.setattr(alembic.command, "downgrade", _refuse)
    reset_disposable_schema(postgres_engine)
    # Twice, because a digest that is not stable across its own reset would replay
    # on every test rather than only on the damaged ones.
    reset_disposable_schema(postgres_engine)


def test_pristine_engine_restores_validators_after_an_earlier_test_downgraded(
    postgres_engine: Engine, request: pytest.FixtureRequest,
) -> None:
    """Direct SQL tests must not inherit a prior test's historical migration head."""
    reset_disposable_schema(postgres_engine)
    schema = _current_schema(postgres_engine)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with postgres_engine.begin() as connection:
        head = connection.scalar(text("SELECT version_num FROM alembic_version"))
        config.attributes["connection"] = connection
        alembic.command.downgrade(config, "0021_job_completion_reports")
        assert connection.scalar(text(
            "SELECT to_regprocedure('mnemonic_external_url_is_valid(text)')"
        )) is None
        assert connection.scalar(text(
            "SELECT to_regprocedure('mnemonic_external_references_is_valid(jsonb)')"
        )) is None

    restored = request.getfixturevalue("pristine_postgres_engine")

    assert restored is postgres_engine
    assert _current_schema(restored) == schema
    with restored.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == head
        assert connection.scalar(text(
            "SELECT mnemonic_external_url_is_valid('https://example.com')"
        )) is True
        assert connection.scalar(text(
            "SELECT mnemonic_external_references_is_valid('[]'::jsonb)"
        )) is True


def test_reset_keeps_the_schema_and_its_relations_in_place(
    api: TestClient, postgres_engine: Engine
) -> None:
    """Relation OIDs change whenever a reset drops and rebuilds the schema."""
    before = _relation_oids(postgres_engine)
    assert set(before) >= {*_POPULATED_TABLES, *PRESERVED_TABLES}
    reset_disposable_schema(postgres_engine)
    assert _relation_oids(postgres_engine) == before


def test_reset_empties_every_table_but_keeps_the_migration_head(
    api: TestClient, postgres_engine: Engine, work_payload: dict, tmp_path: Path
) -> None:
    api.app.state.artifact_storage = ArtifactStorage(tmp_path / "artifacts", max_bytes=1024)
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


@pytest.mark.parametrize(("category", "statement"), _AUDIT_VISIBLE_DAMAGE)
def test_reset_digest_notices_damage_the_operational_audit_can_see(
    postgres_engine: Engine, category: str, statement: str
) -> None:
    """Damage the audit reports must also force a replay, never survive a TRUNCATE.

    Each case runs its DDL inside a transaction it rolls back, so the schema is
    pristine afterwards and no case provokes a real replay. Leaving the damage
    committed would model exactly the poisoning this digest exists to prevent.
    """
    reset_disposable_schema(postgres_engine)
    audit = runpy.run_path(str(BACKEND_DIR.parent / "scripts/audit_project_activity.py"))

    with postgres_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        intact_catalog = audit["catalog_snapshot"](connection)
        intact_digest = _catalog_digest(connection, schema)
    # Pin the comparison to the value reset_disposable_schema actually branches on.
    assert intact_digest == _RESET_PLANS[schema].catalog_digest

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            # exec_driver_sql, so a case may be a same-name drop-and-recreate pair.
            connection.exec_driver_sql(statement)
            damaged_catalog = audit["catalog_snapshot"](connection)
            damaged_digest = _catalog_digest(connection, schema)
        finally:
            transaction.rollback()

    assert damaged_catalog[category] != intact_catalog[category], category
    assert damaged_digest != intact_digest, statement


def test_the_reset_damage_table_covers_every_audited_catalog_category() -> None:
    """A tenth audit category must not arrive without a reset-digest case for it."""
    audit = runpy.run_path(str(BACKEND_DIR.parent / "scripts/audit_project_activity.py"))
    assert set(audit["CATALOG_STATEMENTS"]) == {category for category, _ in _AUDIT_VISIBLE_DAMAGE}
