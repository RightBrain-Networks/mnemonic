"""Real PostgreSQL proof for guarded container startup and migration ordering."""

import hashlib
import os
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api import container_runtime as runtime
from mnemonic_api.container_backup import dump_environment
from mnemonic_api.container_storage import SAMPLE_LIMIT
from mnemonic_api.prompt_storage import default_prompt

pytestmark = pytest.mark.postgres
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def container_database(tmp_path, monkeypatch):
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL for container migration integration tests")
    url = make_url(raw)
    admin = create_engine(url, hide_parameters=True)
    schema = "mnemonic_container_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(url.update_query_dict({"options": f"-c search_path={schema}"}),
                           hide_parameters=True)
    stores = {name: tmp_path / name for name in runtime.STORES}
    for path in stores.values():
        path.mkdir()
    monkeypatch.setattr(runtime, "STORES", stores)
    monkeypatch.setattr(runtime, "MIGRATION_LOCK", uuid4().int % (2**63))
    monkeypatch.setenv("MNEMONIC_PROMPT_ROOT", str(stores["prompts"]))
    monkeypatch.delenv("MNEMONIC_PRE_MIGRATION_BACKUP", raising=False)
    config = Config(str(BACKEND / "alembic.ini"))
    try:
        with engine.connect() as connection:
            yield connection, config, raw
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def _previous_schema(connection, config):
    # Derive the immediate predecessor, so adding a new head keeps this test useful.
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(config)
    predecessor = script.get_revision(script.get_current_head()).down_revision
    config.attributes["connection"] = connection
    command.upgrade(config, predecessor)
    connection.commit()
    return predecessor


def _reference_table(connection, table):
    if table == "artifacts":
        connection.execute(text("CREATE TABLE artifacts (id uuid PRIMARY KEY, revision integer, "
                                "deleted_at timestamptz, relative_path text)"))
    else:
        connection.execute(text("CREATE TABLE transcripts (id uuid PRIMARY KEY, copy_status text, "
                                "storage_key text)"))


def _reference(connection, table, pointer="retained/content.bin", identifier=1):
    if table == "artifacts":
        query = "INSERT INTO artifacts VALUES (:id, 1, NULL, :pointer)"
    else:
        query = "INSERT INTO transcripts VALUES (:id, 'ready', :pointer)"
    connection.execute(text(query), {"id": UUID(int=identifier), "pointer": pointer})


def test_dump_pins_the_actual_connected_database(container_database):
    connection, _, _ = container_database
    env = dump_environment("postgresql+psycopg://other:wrong@other:1/wrong", connection)
    info = connection.connection.driver_connection.info
    assert env["PGHOST"] == info.host
    assert env["PGPORT"] == str(info.port)
    assert env["PGUSER"] == info.user
    assert env["PGDATABASE"] == info.dbname
    assert env["PGPASSWORD"] == info.password
    assert env["PGOPTIONS"] == info.get_parameters()["options"]


@pytest.mark.parametrize("table", ["artifacts", "transcripts"])
def test_empty_or_unrelated_stores_fail_before_backup_and_migration(
    container_database, monkeypatch, table,
):
    connection, config, url = container_database
    _reference_table(connection, table)
    _reference(connection, table)
    connection.commit()
    backup, upgrade = Mock(), Mock()
    monkeypatch.setattr(runtime, "backup_database", backup)
    monkeypatch.setattr(runtime.command, "upgrade", upgrade)
    for unrelated in (False, True):
        if unrelated:
            (runtime.STORES[table] / "unrelated-content").write_text("wrong store")
        with pytest.raises(RuntimeError, match=f"Database references {table}"):
            runtime.migrate(connection, config, url)
        backup.assert_not_called()
        upgrade.assert_not_called()


@pytest.mark.parametrize("table", ["artifacts", "transcripts"])
def test_one_historically_missing_file_does_not_block_a_matching_store(container_database, table):
    connection, _, _ = container_database
    _reference_table(connection, table)
    _reference(connection, table, "missing/content.bin")
    _reference(connection, table, identifier=2)
    path = runtime.STORES[table] / "retained/content.bin"
    path.parent.mkdir()
    path.write_text("retained bytes")
    runtime.require_existing_storage(connection)


def test_store_guard_bounds_its_sample_and_explains_incomplete_match(container_database):
    connection, _, _ = container_database
    _reference_table(connection, "artifacts")
    for identifier in range(1, SAMPLE_LIMIT + 2):
        _reference(connection, "artifacts", f"content-{identifier}", identifier)
    (runtime.STORES["artifacts"] / f"content-{SAMPLE_LIMIT + 1}").write_text("outside sample")
    with pytest.raises(RuntimeError, match=f"none of {SAMPLE_LIMIT} sampled files"):
        runtime.require_existing_storage(connection)


def test_prompt_guard_preserves_customizations_but_allows_lazy_defaults(container_database):
    connection, _, _ = container_database
    connection.execute(text("CREATE TABLE project_settings (project_id uuid PRIMARY KEY, "
                            "prompt_context_revision bigint, recall_pointer_sha256 text, "
                            "job_completion_report_prompt_sha256 text)"))
    project = uuid4()
    connection.execute(text(
        "INSERT INTO project_settings VALUES (:project, 1, :recall, :report)"), {
        "project": project,
        "recall": hashlib.sha256(default_prompt("recall-pointer").encode()).hexdigest(),
        "report": hashlib.sha256(default_prompt("job-completion-report").encode()).hexdigest(),
    })
    runtime.require_existing_storage(connection)
    connection.execute(text("UPDATE project_settings SET prompt_context_revision=2"))
    with pytest.raises(RuntimeError, match="customized prompts"):
        runtime.require_existing_storage(connection)
    retained = runtime.STORES["prompts"] / "projects" / str(project) / "recall-pointer.md"
    retained.parent.mkdir(parents=True)
    retained.write_text("preserved prompt")
    runtime.require_existing_storage(connection)


def test_new_install_migrates_and_current_head_is_a_noop(container_database, monkeypatch):
    connection, config, url = container_database
    backup = Mock()
    monkeypatch.setattr(runtime, "backup_database", backup)
    runtime.migrate(connection, config, url)
    runtime.require_schema_head(connection, config)
    upgrade = Mock()
    monkeypatch.setattr(runtime.command, "upgrade", upgrade)
    runtime.migrate(connection, config, url)
    backup.assert_not_called()
    upgrade.assert_not_called()


def test_schema_ahead_refuses_startup_and_upgrade_before_dump(container_database, monkeypatch):
    connection, config, url = container_database
    connection.execute(text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)"))
    connection.execute(text("INSERT INTO alembic_version VALUES ('9999_future')"))
    connection.commit()
    with pytest.raises(RuntimeError, match="Database schema differs"):
        runtime.require_schema_head(connection, config)
    backup, upgrade = Mock(), Mock()
    monkeypatch.setattr(runtime, "backup_database", backup)
    monkeypatch.setattr(runtime.command, "upgrade", upgrade)
    with pytest.raises(RuntimeError, match="newer than or incompatible"):
        runtime.migrate(connection, config, url)
    backup.assert_not_called()
    upgrade.assert_not_called()


def test_unversioned_populated_database_refuses_migration(container_database, monkeypatch):
    connection, config, url = container_database
    connection.execute(text("CREATE TABLE existing_data (value text)"))
    connection.commit()
    backup, upgrade = Mock(), Mock()
    monkeypatch.setattr(runtime, "backup_database", backup)
    monkeypatch.setattr(runtime.command, "upgrade", upgrade)
    with pytest.raises(RuntimeError, match="Unversioned database contains tables"):
        runtime.migrate(connection, config, url)
    backup.assert_not_called()
    upgrade.assert_not_called()


def test_explicit_backup_opt_out_and_invalid_configuration(container_database, monkeypatch):
    connection, config, url = container_database
    previous = _previous_schema(connection, config)
    monkeypatch.setenv("MNEMONIC_PRE_MIGRATION_BACKUP", "yes")
    backup = Mock()
    monkeypatch.setattr(runtime, "backup_database", backup)
    with pytest.raises(RuntimeError, match="must be true or false"):
        runtime.migrate(connection, config, url)
    assert runtime.schema_heads(connection, config)[0] == {previous}
    monkeypatch.setenv("MNEMONIC_PRE_MIGRATION_BACKUP", "false")
    runtime.migrate(connection, config, url)
    runtime.require_schema_head(connection, config)
    backup.assert_not_called()


def test_backup_failure_leaves_previous_schema_and_releases_migration_lock(
    container_database, monkeypatch,
):
    connection, config, url = container_database
    previous = _previous_schema(connection, config)
    with pytest.raises(RuntimeError, match="Database schema differs"):
        runtime.require_schema_head(connection, config)
    monkeypatch.setattr(runtime, "backup_database", Mock(side_effect=RuntimeError("dump failed")))
    upgrade = Mock()
    monkeypatch.setattr(runtime.command, "upgrade", upgrade)
    with pytest.raises(RuntimeError, match="dump failed"):
        runtime.migrate(connection, config, url)
    upgrade.assert_not_called()
    assert runtime.schema_heads(connection, config)[0] == {previous}
    with connection.engine.connect() as other:
        assert other.scalar(text("SELECT pg_try_advisory_lock(:key)"),
                            {"key": runtime.MIGRATION_LOCK})
        other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": runtime.MIGRATION_LOCK})


def test_upgrade_starts_only_after_a_successful_backup(container_database, monkeypatch):
    connection, config, url = container_database
    _previous_schema(connection, config)
    calls = []
    upgrade = runtime.command.upgrade

    def backup(*args):
        calls.append("backup")

    def guarded_upgrade(*args):
        assert calls == ["backup"]
        calls.append("upgrade")
        return upgrade(*args)

    monkeypatch.setattr(runtime, "backup_database", backup)
    monkeypatch.setattr(runtime.command, "upgrade", guarded_upgrade)
    runtime.migrate(connection, config, url)
    runtime.require_schema_head(connection, config)
    assert calls == ["backup", "upgrade"]


def test_another_migration_holder_prevents_work(container_database, monkeypatch):
    connection, config, url = container_database
    backup = Mock()
    monkeypatch.setattr(runtime, "backup_database", backup)
    with connection.engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": runtime.MIGRATION_LOCK})
        try:
            with pytest.raises(RuntimeError, match="already running"):
                runtime.migrate(connection, config, url)
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": runtime.MIGRATION_LOCK})
    backup.assert_not_called()
