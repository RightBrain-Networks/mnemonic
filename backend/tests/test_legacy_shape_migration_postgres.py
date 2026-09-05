"""The migration chain must accept history that only superseded code could write.

Every other migration test starts from an empty schema or populates one through
the current services, so it can only ever produce head-shaped rows.  That is the
blind spot this file covers: a preflight can demand an invariant the chain never
established, pass every test, and still refuse a real database on start-up.

Migration 0019 shipped exactly that defect.  It required every ``done`` work item
to own a ``work_completed`` event, which is true of anything the current
completion path writes and false of anything completed before 0010 introduced the
event timeline.  Nothing in CI ever presented the older shape, so nothing failed
until the API crash-looped against production data.

Add a shape here whenever one is discovered.  A migration that cannot accept a
shape this corpus stages is a migration that cannot accept the deployed database,
and a route that cannot serve one has only moved the same assumption upstack.
"""

import importlib.util
import json
from collections.abc import Iterator
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api.config import Settings
from mnemonic_api.main import create_app

from .conftest import BACKEND_DIR, TEST_API_KEY

pytestmark = pytest.mark.postgres

_LEGACY_SHAPES = json.loads(
    (BACKEND_DIR.parent / "tests" / "fixtures" / "legacy-shapes-v1.json").read_text(
        encoding="utf-8"
    )
)["shapes"]

_PREFLIGHT_REVISION = "0018_repository_freshness"
_PREFLIGHT_MIGRATION = "0019_structured_completion_evidence"


def _migration_module(revision: str) -> ModuleType:
    """Load a migration for its pure SQL builders, never to run it.

    Importing the module defines its helpers without entering a migration
    context; only ``upgrade`` touches ``op``.
    """

    path = BACKEND_DIR / "alembic" / "versions" / f"{revision}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config(connection: Connection) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture(scope="session")
def legacy_engine(postgres_engine: Engine) -> Iterator[Engine]:
    """A database of this worker's own, because catalog scans are per database.

    Replaying the chain needs a real schema per shape, and dropping those
    schemas in the shared test database raced the suites that digest a whole
    catalog: a relation can vanish between a scan reading ``pg_class`` and the
    ``pg_get_*def`` call over its OID, and PostgreSQL reports "could not open
    relation with OID". Nothing else connects to this database, so no scan can
    observe the churn, and each xdist worker gets its own.
    """

    server = postgres_engine.url.set(database="postgres").difference_update_query(["options"])
    name = "mnemonic_legacy_" + uuid4().hex
    admin = create_engine(server, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(
        postgres_engine.url.set(database=name).difference_update_query(["options"]),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        yield engine
    finally:
        # Every connection must be gone before the database can be dropped.
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def disposable_schema(legacy_engine: Engine) -> Iterator[str]:
    """A schema of this test's own, so a partial chain cannot disturb others."""

    schema = "mnemonic_test_legacy_" + uuid4().hex
    with legacy_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        yield schema
    finally:
        with legacy_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))


def _scope_to_schema(connection: Connection, schema: str) -> None:
    quoted = connection.dialect.identifier_preparer.quote_identifier(schema)
    connection.execute(
        text("SELECT pg_catalog.set_config('search_path', :path, true)"),
        {"path": quoted},
    )


def _shape(name: str) -> dict[str, Any]:
    for shape in _LEGACY_SHAPES:
        if shape["name"] == name:
            return shape
    raise AssertionError(f"The legacy shape corpus no longer defines {name}")


def _stage_shape(engine: Engine, schema: str, shape: dict[str, Any]) -> dict[str, str]:
    """Write a shape at the revision that could produce it, and commit it."""

    identifiers = {name: str(uuid4()) for name in shape["identifiers"]}
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _scope_to_schema(connection, schema)
            command.upgrade(_alembic_config(connection), shape["staged_at"])
            # A migration in this same transaction left constraints immediate,
            # and work item and checkpoint reference each other.
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            for statement in shape["statements"]:
                connection.execute(text(statement), identifiers)
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise
    return identifiers


def _upgrade_to_head(engine: Engine, schema: str) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _scope_to_schema(connection, schema)
            command.upgrade(_alembic_config(connection), "head")
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise


def _engine_for_schema(legacy_engine: Engine, schema: str) -> Engine:
    return create_engine(
        legacy_engine.url.update_query_dict(
            {"options": f"-c search_path={schema} -c timezone=UTC"}
        ),
        pool_pre_ping=True,
        hide_parameters=True,
    )


@pytest.mark.parametrize("shape", _LEGACY_SHAPES, ids=lambda shape: shape["name"])
def test_legacy_row_shapes_migrate_to_head(
    legacy_engine: Engine,
    disposable_schema: str,
    shape: dict[str, Any],
):
    """Stage a shape at the revision that could write it, then run the chain."""

    identifiers = _stage_shape(legacy_engine, disposable_schema, shape)
    _upgrade_to_head(legacy_engine, disposable_schema)

    with legacy_engine.connect() as connection:
        transaction = connection.begin()
        try:
            _scope_to_schema(connection, disposable_schema)
            for expectation in shape["expectations"]:
                observed = connection.scalar(text(expectation["sql"]), identifiers)
                assert observed == expectation["equals"], (
                    f"{shape['name']}: {expectation['description']}"
                )
        finally:
            transaction.rollback()


def test_migrated_legacy_completion_is_servable(
    legacy_engine: Engine,
    disposable_schema: str,
):
    """Migrating the row is only half of it: the route has to serve it too.

    The completion-evidence route made the same assumption the preflight did --
    that ``done`` work resolves to exactly one current completion checkpoint --
    and answered 503 for an item whose completion predates the event timeline.
    A migration that lands and a dashboard that then errors is not a fix.
    """

    shape = _shape("done-before-the-event-timeline")
    identifiers = _stage_shape(legacy_engine, disposable_schema, shape)
    _upgrade_to_head(legacy_engine, disposable_schema)

    engine = _engine_for_schema(legacy_engine, disposable_schema)
    try:
        settings = Settings(
            database_url=engine.url.render_as_string(hide_password=False),
            api_key=TEST_API_KEY,
        )
        with TestClient(create_app(settings, engine=engine)) as client:
            client.headers["Authorization"] = f"Bearer {TEST_API_KEY}"
            response = client.get(
                f"/api/v1/projects/{identifiers['project_id']}"
                f"/work-items/{identifiers['work_id']}/completion-evidence"
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["lifecycle_status"] == "done"
            # No episode exists, so absent is the honest answer -- not an error,
            # and not a fabricated pointer.
            assert body["current_completion_checkpoint_id"] is None
            assert body["items"] == []
            assert body["total"] == 0
            assert body["structured_completion_total"] == 0
    finally:
        engine.dispose()


def test_migrated_provable_completion_still_resolves_its_pointer(
    legacy_engine: Engine,
    disposable_schema: str,
):
    """The control case: a completion with real evidence keeps its exact pointer."""

    shape = _shape("completion-checkpoint-before-the-event-timeline")
    identifiers = _stage_shape(legacy_engine, disposable_schema, shape)
    _upgrade_to_head(legacy_engine, disposable_schema)

    engine = _engine_for_schema(legacy_engine, disposable_schema)
    try:
        settings = Settings(
            database_url=engine.url.render_as_string(hide_password=False),
            api_key=TEST_API_KEY,
        )
        with TestClient(create_app(settings, engine=engine)) as client:
            client.headers["Authorization"] = f"Bearer {TEST_API_KEY}"
            response = client.get(
                f"/api/v1/projects/{identifiers['project_id']}"
                f"/work-items/{identifiers['work_id']}/completion-evidence"
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["current_completion_checkpoint_id"] == identifiers["completion_id"]
            assert body["total"] == 1
    finally:
        engine.dispose()


def test_legacy_shape_corpus_stages_below_the_revision_it_exercises():
    """A shape staged at head proves nothing about history."""

    assert _LEGACY_SHAPES, "The legacy shape corpus must not be empty"
    for shape in _LEGACY_SHAPES:
        assert shape["staged_at"] < _PREFLIGHT_REVISION, (
            f"{shape['name']} is staged at {shape['staged_at']}, which is not "
            "old enough to represent history the current code cannot write"
        )
        assert shape["history"].strip(), (
            f"{shape['name']} must record which superseded code wrote it"
        )
        assert shape["expectations"], (
            f"{shape['name']} must assert what survives, not merely that the chain ran"
        )


def test_every_preflight_condition_is_named_and_independently_executable(
    legacy_engine: Engine,
    disposable_schema: str,
):
    """A preflight that reports one message for many conditions cannot be acted on.

    The 0019 defect took a database session to locate because four unrelated
    conditions shared the message "invalid completion or reopen history": the
    operator learned only that something was wrong somewhere.  Keeping each
    condition separate is what makes the failure name the rows to look at, so
    this asserts the separation rather than trusting it, and runs each condition
    on its own -- a disjunction can hide a condition that no longer parses.
    """

    preflight_conditions = _migration_module(_PREFLIGHT_MIGRATION)._preflight_conditions
    names = [name for name, _ in preflight_conditions("probe_schema")]

    assert len(names) == len(set(names)), "Preflight condition names must be unique"
    assert all(name and name.strip() for name in names)

    with legacy_engine.connect() as connection:
        transaction = connection.begin()
        try:
            _scope_to_schema(connection, disposable_schema)
            command.upgrade(_alembic_config(connection), _PREFLIGHT_REVISION)
            quoted = connection.dialect.identifier_preparer.quote_identifier(disposable_schema)
            for name, condition in preflight_conditions(quoted):
                assert "OR EXISTS" not in condition.upper(), (
                    f"{name} bundles several conditions behind one message"
                )
                observed = connection.scalar(text(f"SELECT EXISTS ({condition})"))
                assert observed is False, (
                    f"{name} reports a violation against a schema with no rows"
                )
        finally:
            transaction.rollback()


def test_preflight_names_the_condition_a_real_violation_trips(
    legacy_engine: Engine,
    disposable_schema: str,
):
    """An unpaired completion checkpoint must name itself, not "something is wrong"."""

    identifiers = {name: str(uuid4()) for name in ("project_id", "work_id", "checkpoint_id")}
    with legacy_engine.connect() as connection:
        transaction = connection.begin()
        try:
            _scope_to_schema(connection, disposable_schema)
            command.upgrade(_alembic_config(connection), _PREFLIGHT_REVISION)
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, slug) VALUES "
                    "(CAST(:project_id AS uuid), 'Unpaired completion', 'unpaired-completion')"
                ),
                identifiers,
            )
            connection.execute(
                text(
                    "INSERT INTO work_items (id, project_id, title, summary, status, "
                    "initial_checkpoint_id, version) VALUES (CAST(:work_id AS uuid), "
                    "CAST(:project_id AS uuid), 'Completion without its event', "
                    "'A real integrity fault, not a legacy shape', 'done', "
                    "CAST(:checkpoint_id AS uuid), 2)"
                ),
                identifiers,
            )
            connection.execute(
                text(
                    "INSERT INTO checkpoints (id, work_item_id, kind, prompt, source_client, "
                    "source_session_id) VALUES (CAST(:checkpoint_id AS uuid), "
                    "CAST(:work_id AS uuid), 'completion', 'A completion with no event.', "
                    "'legacy-client', 'legacy-session')"
                ),
                identifiers,
            )

            with pytest.raises(RuntimeError) as failure:
                command.upgrade(_alembic_config(connection), "head")

            # A completion checkpoint that owns no event is a genuine fault and
            # must still fail closed: the legacy exemption covers only items with
            # no completion checkpoint at all.
            assert "completion_checkpoint_event_pairing" in str(failure.value)
        finally:
            transaction.rollback()


def _generation(engine: Engine, work_item_id: str) -> int | None:
    with engine.connect() as connection:
        return connection.scalar(
            text("SELECT completion_generation FROM work_items WHERE id = CAST(:id AS uuid)"),
            {"id": work_item_id},
        )


def test_legacy_completion_cannot_leave_done(
    legacy_engine: Engine,
    disposable_schema: str,
):
    """Documented in architecture.md and operations.md, so pin it here.

    The refusal is permanent rather than a transient database fault, and it
    comes from the episode-departure guard, not from anything about the
    generation number.
    """

    shape = _shape("done-before-the-event-timeline")
    identifiers = _stage_shape(legacy_engine, disposable_schema, shape)
    _upgrade_to_head(legacy_engine, disposable_schema)

    engine = _engine_for_schema(legacy_engine, disposable_schema)
    try:
        settings = Settings(
            database_url=engine.url.render_as_string(hide_password=False),
            api_key=TEST_API_KEY,
        )
        with TestClient(create_app(settings, engine=engine)) as client:
            client.headers["Authorization"] = f"Bearer {TEST_API_KEY}"
            base = (
                f"/api/v1/projects/{identifiers['project_id']}"
                f"/work-items/{identifiers['work_id']}"
            )
            work = client.get(base).json()["work_item"]
            assert work["status"] == "done"
            assert _generation(engine, identifiers["work_id"]) == 0

            reopen = client.patch(
                base, json={"status": "pending", "expected_version": work["version"]}
            )
            assert reopen.status_code == 503, reopen.text
            assert reopen.json()["detail"]["code"] == "database_unavailable"
            # The refusal must not have moved the row.
            assert client.get(base).json()["work_item"]["status"] == "done"
            assert _generation(engine, identifiers["work_id"]) == 0
    finally:
        engine.dispose()


def test_completion_generation_advances_only_on_reopen(
    api: TestClient,
    postgres_engine: Engine,
    project: dict[str, Any],
    work_payload: dict[str, Any],
):
    """The lifecycle architecture.md describes, observed end to end.

    Generation counts reopen cycles, not completions, so completing never
    advances it and a once-completed item stays at 0 -- the same value work that
    was never completed carries.
    """

    collection = f"/api/v1/projects/{project['id']}/work-items"
    created = api.post(collection, json=work_payload)
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    item = f"{collection}/{work['id']}"

    assert _generation(postgres_engine, work["id"]) == 0

    def complete(version: int, prompt: str) -> None:
        response = api.post(
            f"{item}/complete",
            json={
                "expected_version": version,
                "checkpoint": {
                    "prompt": prompt,
                    "source_client": "pytest",
                    "source_session_id": str(uuid4()),
                },
            },
        )
        assert response.status_code == 200, response.text

    complete(work["version"], "Completed the first cycle.")
    assert _generation(postgres_engine, work["id"]) == 0, "completing must not advance it"

    done = api.get(item).json()["work_item"]
    reopened = api.patch(
        item, json={"status": "pending", "expected_version": done["version"]}
    )
    assert reopened.status_code == 200, reopened.text
    assert _generation(postgres_engine, work["id"]) == 1, "reopening advances it"

    complete(reopened.json()["version"], "Completed the second cycle.")
    assert _generation(postgres_engine, work["id"]) == 1

    with postgres_engine.connect() as connection:
        checkpoint_generations = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT completion_generation FROM checkpoints "
                    "WHERE work_item_id = CAST(:id AS uuid) AND kind = 'completion' "
                    "ORDER BY created_at"
                ),
                {"id": work["id"]},
            )
        ]
        reopen_generations = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT reopen_generation FROM work_events "
                    "WHERE work_item_id = CAST(:id AS uuid) "
                    "AND event_type = 'work_reopened' ORDER BY id"
                ),
                {"id": work["id"]},
            )
        ]
    # Each completion checkpoint carries the cycle it belongs to, and the reopen
    # witness carries the generation its reopen produced.
    assert checkpoint_generations == [0, 1]
    assert reopen_generations == [1]
