"""Operational audit coverage for exact Phase 9 through Phase 11 contracts."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.postgres

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _audit_module() -> ModuleType:
    path = REPOSITORY_ROOT / "scripts" / "audit_duplicate_handling.py"
    spec = importlib.util.spec_from_file_location("phase9_duplicate_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phase11_audit_catalog_hashes_are_coupled_to_migration_contract():
    audit = _audit_module()
    migration = audit._phase11_revision_contract()

    assert audit.PHASE11_CATALOG_SHA256 == migration._PHASE11_CATALOG_SHA256


def test_phase11_upgrade_preflight_rejects_duplicate_receipt_key_without_ddl(
    api,
    project,
    work_payload,
    postgres_engine,
):
    audit = _audit_module()
    migration = audit._phase11_revision_contract()
    operation_id = str(uuid4())
    duplicate_operation_id = str(uuid4())
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Duplicate receipt upgrade preflight"},
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    completed = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete",
        json={
            "expected_version": 1,
            "checkpoint": work_payload["initial_checkpoint"],
            "client_operation_id": operation_id,
        },
    )
    assert completed.status_code == 200, completed.text

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        sequence = None
        sequence_state = None
        try:
            sequence = connection.scalar(
                text("SELECT pg_catalog.pg_get_serial_sequence('work_events', 'id')")
            )
            assert isinstance(sequence, str)
            sequence_state = connection.execute(
                text(f"SELECT last_value, is_called FROM {sequence}")
            ).one()

            config = Config(str(REPOSITORY_ROOT / "backend" / "alembic.ini"))
            config.attributes["connection"] = connection
            command.downgrade(config, "0018_repository_freshness")
            connection.execute(
                text("SET CONSTRAINTS client_operation_completion_guard DEFERRED")
            )
            connection.execute(
                text(
                    """
                    INSERT INTO client_operations (
                        project_id, client_operation_id, operation_kind,
                        request_fingerprint_version, request_fingerprint_salt,
                        request_fingerprint, response_contract_version
                    )
                    SELECT project_id, CAST(:duplicate_operation_id AS uuid),
                           operation_kind, request_fingerprint_version,
                           request_fingerprint_salt, request_fingerprint,
                           response_contract_version
                    FROM client_operations
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {
                    "duplicate_operation_id": duplicate_operation_id,
                    "operation_id": operation_id,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations AS duplicate
                    SET state = original.state,
                        response_status = original.response_status,
                        response_body = original.response_body,
                        mutation_applied = original.mutation_applied,
                        completed_at = GREATEST(
                            duplicate.created_at, pg_catalog.clock_timestamp()
                        )
                    FROM client_operations AS original
                    WHERE duplicate.client_operation_id
                            = CAST(:duplicate_operation_id AS uuid)
                      AND original.client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {
                    "duplicate_operation_id": duplicate_operation_id,
                    "operation_id": operation_id,
                },
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

            schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
            assert isinstance(schema, str)
            survivor_before = migration._phase10_survivor_catalog_digest(
                schema, connection=connection
            )
            receipts_before = connection.execute(
                text("SELECT * FROM client_operations ORDER BY id")
            ).all()
            phase11_markers_before = connection.execute(
                text(
                    """
                    SELECT pg_catalog.to_regclass('verification_results'),
                           pg_catalog.to_regclass('artifact_references'),
                           EXISTS (
                               SELECT 1
                               FROM pg_catalog.pg_attribute
                               WHERE attrelid = 'work_items'::regclass
                                 AND attname = 'completion_generation'
                                 AND NOT attisdropped
                           ),
                           pg_catalog.to_regclass(
                               'ix_client_operations_completion_receipt_correspondence'
                           )
                    """
                )
            ).one()

            with pytest.raises(
                RuntimeError,
                match="duplicate completion receipt correspondence",
            ) as raised:
                command.upgrade(config, "0019_structured_completion_evidence")

            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_repository_freshness"
            )
            assert migration._phase10_survivor_catalog_digest(
                schema, connection=connection
            ) == survivor_before
            assert connection.execute(
                text("SELECT * FROM client_operations ORDER BY id")
            ).all() == receipts_before
            assert connection.execute(
                text(
                    """
                    SELECT pg_catalog.to_regclass('verification_results'),
                           pg_catalog.to_regclass('artifact_references'),
                           EXISTS (
                               SELECT 1
                               FROM pg_catalog.pg_attribute
                               WHERE attrelid = 'work_items'::regclass
                                 AND attname = 'completion_generation'
                                 AND NOT attisdropped
                           ),
                           pg_catalog.to_regclass(
                               'ix_client_operations_completion_receipt_correspondence'
                           )
                    """
                )
            ).one() == phase11_markers_before == (None, None, False, None)
            error = str(raised.value)
            assert operation_id not in error
            assert duplicate_operation_id not in error
            assert work["id"] not in error
            assert completed.json()["checkpoint"]["id"] not in error
        finally:
            transaction.rollback()
            if sequence is not None and sequence_state is not None:
                connection.execute(
                    text(
                        "SELECT pg_catalog.setval(CAST(:sequence AS regclass), "
                        ":last_value, :is_called)"
                    ),
                    {
                        "is_called": sequence_state.is_called,
                        "last_value": sequence_state.last_value,
                        "sequence": sequence,
                    },
                )
                connection.commit()


def test_phase11_receipt_correspondence_unique_index_rejects_second_completed_pair(
    api,
    project,
    work_payload,
    postgres_engine,
):
    operation_id = str(uuid4())
    duplicate_operation_id = str(uuid4())
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Unique completion receipt correspondence"},
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    completed = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete",
        json={
            "expected_version": 1,
            "checkpoint": work_payload["initial_checkpoint"],
            "client_operation_id": operation_id,
        },
    )
    assert completed.status_code == 200, completed.text

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            original = connection.execute(
                text(
                    "SELECT * FROM client_operations "
                    "WHERE client_operation_id = CAST(:operation_id AS uuid)"
                ),
                {"operation_id": operation_id},
            ).one()
            duplicate = connection.begin_nested()
            connection.execute(
                text("SET CONSTRAINTS client_operation_completion_guard DEFERRED")
            )
            connection.execute(
                text(
                    """
                    INSERT INTO client_operations (
                        project_id, client_operation_id, operation_kind,
                        request_fingerprint_version, request_fingerprint_salt,
                        request_fingerprint, response_contract_version
                    )
                    SELECT project_id, CAST(:duplicate_operation_id AS uuid),
                           operation_kind, request_fingerprint_version,
                           request_fingerprint_salt, request_fingerprint,
                           response_contract_version
                    FROM client_operations
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {
                    "duplicate_operation_id": duplicate_operation_id,
                    "operation_id": operation_id,
                },
            )
            with pytest.raises(IntegrityError) as rejected:
                connection.execute(
                    text(
                        """
                        UPDATE client_operations AS candidate
                        SET state = original.state,
                            response_status = original.response_status,
                            response_body = original.response_body,
                            mutation_applied = original.mutation_applied,
                            completed_at = GREATEST(
                                candidate.created_at, pg_catalog.clock_timestamp()
                            )
                        FROM client_operations AS original
                        WHERE candidate.client_operation_id
                                = CAST(:duplicate_operation_id AS uuid)
                          AND original.client_operation_id
                                = CAST(:operation_id AS uuid)
                        """
                    ),
                    {
                        "duplicate_operation_id": duplicate_operation_id,
                        "operation_id": operation_id,
                    },
                )
            assert rejected.value.orig.sqlstate == "23505"
            assert rejected.value.orig.diag.constraint_name == (
                "ix_client_operations_completion_receipt_correspondence"
            )
            duplicate.rollback()

            assert connection.execute(
                text(
                    "SELECT * FROM client_operations "
                    "WHERE client_operation_id = CAST(:operation_id AS uuid)"
                ),
                {"operation_id": operation_id},
            ).one() == original
            assert connection.scalar(
                text(
                    """
                    SELECT pg_catalog.count(*)
                    FROM client_operations
                    WHERE operation_kind = 'complete_work'
                      AND state = 'completed'
                      AND response_body #>> '{checkpoint,id}' = :checkpoint_id
                      AND response_body #>> '{work_item,id}' = :work_item_id
                    """
                ),
                {
                    "checkpoint_id": completed.json()["checkpoint"]["id"],
                    "work_item_id": work["id"],
                },
            ) == 1
        finally:
            transaction.rollback()


def test_phase11_survivor_digest_is_search_path_and_index_independent(postgres_engine):
    audit = _audit_module()
    migration = audit._phase11_revision_contract()
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
            original_search_path = connection.scalar(
                text("SELECT pg_catalog.current_setting('search_path')")
            )
            assert isinstance(schema, str)
            assert isinstance(original_search_path, str)

            visible_digest = migration._phase10_survivor_catalog_digest(
                schema, connection=connection
            )
            assert visible_digest == migration._PHASE10_SURVIVOR_CATALOG_SHA256
            assert connection.scalar(
                text("SELECT pg_catalog.current_setting('search_path')")
            ) == original_search_path

            connection.execute(
                text("SELECT pg_catalog.set_config('search_path', 'pg_catalog', true)")
            )
            hidden_digest = migration._phase10_survivor_catalog_digest(
                schema, connection=connection
            )
            assert hidden_digest == visible_digest
            assert connection.scalar(
                text("SELECT pg_catalog.current_setting('search_path')")
            ) == "pg_catalog"
            connection.execute(
                text("SELECT pg_catalog.set_config('search_path', :path, true)"),
                {"path": original_search_path},
            )

            without_phase11_indexes = connection.begin_nested()
            connection.execute(
                text(
                    "DROP INDEX "
                    "ix_client_operations_completion_receipt_correspondence, "
                    "ix_verification_results_completion_checkpoint_id_id, "
                    "ix_artifact_references_completion_checkpoint_id_id"
                )
            )
            assert migration._phase10_survivor_catalog_digest(
                schema, connection=connection
            ) == visible_digest
            without_phase11_indexes.rollback()
        finally:
            transaction.rollback()


class _EntrypointTraceConnection:
    def __init__(self, trace):
        self.trace = trace

    def __enter__(self):
        self.trace.append(("connection_enter", self))
        return self

    def __exit__(self, exception_type, exception, traceback):
        del exception_type, exception, traceback
        self.trace.append(("connection_exit", self))

    def execute(self, statement, parameters=None):
        self.trace.append(("execute", self, str(statement), parameters))

    def rollback(self):
        self.trace.append(("rollback", self))


class _EntrypointTraceEngine:
    def __init__(self, trace, connection):
        self.trace = trace
        self.connection = connection

    def connect(self):
        self.trace.append(("connect", self.connection))
        return self.connection

    def dispose(self):
        self.trace.append(("dispose", self))


def _phase11_main_args(tmp_path, database_url):
    return SimpleNamespace(
        database_url=database_url,
        expected_head="0019_structured_completion_evidence",
        backup_directory=tmp_path,
        minimum_backup_free_bytes=1,
        require_empty_scope=False,
        require_empty_completion_evidence=False,
    )


def test_phase11_audit_main_establishes_one_shared_read_only_snapshot(
    monkeypatch, tmp_path, capsys
):
    audit = _audit_module()
    secret = "receipt-content-must-not-appear"
    trace = []
    connection = _EntrypointTraceConnection(trace)
    engine = _EntrypointTraceEngine(trace, connection)
    args = _phase11_main_args(tmp_path, f"postgresql://audit:{secret}@database/audit")

    def create_traced_engine(database_url, **options):
        assert secret in database_url
        assert options["hide_parameters"] is True
        trace.append(("create_engine", engine))
        return engine

    def snapshot(snapshot_connection, expected_head):
        assert snapshot_connection is connection
        assert expected_head == audit.FINAL_HEAD
        trace.append(("snapshot", snapshot_connection))
        return True, 1, {"aggregate_count": 0}, {"database_bytes": 0}

    monkeypatch.setattr(audit, "_local_settings", lambda: {})
    monkeypatch.setattr(audit, "_parse_args", lambda settings: args)
    monkeypatch.setattr(audit, "create_engine", create_traced_engine)
    monkeypatch.setattr(audit, "_database_audit_snapshot", snapshot)
    monkeypatch.setattr(audit, "_blocking_counts", lambda counts, **options: {})
    monkeypatch.setattr(audit, "_catalog_blocking_counts", lambda catalog: {})

    assert audit.main() == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["result"] == "pass"
    assert secret not in output

    isolation_events = [
        item
        for item in trace
        if item[0] == "execute"
        and item[2] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    ]
    assert len(isolation_events) == 1
    snapshot_index = next(index for index, item in enumerate(trace) if item[0] == "snapshot")
    isolation_index = trace.index(isolation_events[0])
    timeout_index = next(
        index
        for index, item in enumerate(trace)
        if item[0] == "execute" and item[2] == "SET LOCAL statement_timeout = '60s'"
    )
    assert isolation_index < timeout_index < snapshot_index
    assert sum(item[0] == "connect" for item in trace) == 1
    assert trace[snapshot_index][1] is connection
    assert next(index for index, item in enumerate(trace) if item[0] == "rollback") > (
        snapshot_index
    )


def test_phase11_audit_main_redacts_runtime_failure_details(monkeypatch, tmp_path, capsys):
    audit = _audit_module()
    secret = "sensitive evidence and receipt body"
    trace = []
    connection = _EntrypointTraceConnection(trace)
    engine = _EntrypointTraceEngine(trace, connection)
    args = _phase11_main_args(tmp_path, "postgresql://audit@database/audit")

    def fail_snapshot(snapshot_connection, expected_head):
        del snapshot_connection, expected_head
        raise RuntimeError(secret)

    monkeypatch.setattr(audit, "_local_settings", lambda: {})
    monkeypatch.setattr(audit, "_parse_args", lambda settings: args)
    monkeypatch.setattr(audit, "create_engine", lambda *args, **options: engine)
    monkeypatch.setattr(audit, "_database_audit_snapshot", fail_snapshot)

    assert audit.main() == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "audit_runtime_failure": True,
        "audit_version": audit.AUDIT_VERSION,
        "result": "blocked",
    }
    assert secret not in output


def test_audit_requires_exact_advisory_function_and_index(postgres_engine):
    audit = _audit_module()
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            baseline = audit._catalog(connection, audit.FINAL_HEAD)
            assert baseline["missing_function_count"] == 0
            assert baseline["title_key_contract_failure_count"] == 0
            assert baseline["missing_index_count"] == 0

            connection.execute(text("DROP INDEX ix_work_items_duplicate_title_key_v1"))
            connection.execute(text("DROP FUNCTION mnemonic_duplicate_title_key_v1(text)"))
            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_duplicate_title_key_v1(value integer)
                    RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog
                    AS 'SELECT value::text'
                    """
                )
            )
            wrong_overload = audit._catalog(connection, audit.FINAL_HEAD)
            assert wrong_overload["missing_function_count"] == 1
            assert wrong_overload["missing_index_count"] == 1

            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_duplicate_title_key_v1(value text)
                    RETURNS text LANGUAGE sql VOLATILE
                    AS 'SELECT value'
                    """
                )
            )
            wrong_attributes = audit._catalog(connection, audit.FINAL_HEAD)
            assert wrong_attributes["missing_function_count"] == 1

            connection.execute(
                text("DROP FUNCTION mnemonic_duplicate_title_key_v1(text)")
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_duplicate_title_key_v1(value text)
                    RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog
                    AS 'SELECT value'
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE INDEX ix_work_items_duplicate_title_key_v1
                    ON work_items (
                        project_id,
                        mnemonic_duplicate_title_key_v1(title),
                        id
                    )
                    WHERE deleted_at IS NULL
                    """
                )
            )
            wrong_body = audit._catalog(connection, audit.FINAL_HEAD)
            assert wrong_body["missing_function_count"] == 0
            assert wrong_body["missing_index_count"] == 0
            assert wrong_body["title_key_contract_failure_count"] == 1
            assert audit._catalog_blocking_counts(wrong_body) == {
                "duplicate_title_key_contract_failures": 1,
                "phase10_survivor_catalog_failures": 1,
            }
        finally:
            transaction.rollback()


def test_audit_return_type_checks_resist_oid_regtype_operator_capture(postgres_engine):
    audit = _audit_module()
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
            connection.execute(text("DROP INDEX ix_work_items_duplicate_title_key_v1"))
            connection.execute(text("DROP FUNCTION mnemonic_duplicate_title_key_v1(text)"))
            connection.execute(
                text(
                    """
                    CREATE DOMAIN fake_text AS pg_catalog.text;
                    CREATE FUNCTION mnemonic_duplicate_title_key_v1(value pg_catalog.text)
                    RETURNS fake_text
                    LANGUAGE sql
                    IMMUTABLE
                    STRICT
                    PARALLEL SAFE
                    SET search_path = pg_catalog
                    AS $function$
                        SELECT pg_catalog.lower(
                            pg_catalog.regexp_replace(
                                pg_catalog.regexp_replace(
                                    pg_catalog.normalize(value, 'NFKC') COLLATE "C",
                                    '^[[:space:]]+|[[:space:]]+$',
                                    '',
                                    'g'
                                ),
                                '[[:space:]]+',
                                ' ',
                                'g'
                            ) COLLATE "C"
                        )
                    $function$;
                    CREATE INDEX ix_work_items_duplicate_title_key_v1
                    ON work_items (
                        project_id,
                        mnemonic_duplicate_title_key_v1(title::pg_catalog.text),
                        id
                    )
                    WHERE deleted_at IS NULL;
                    CREATE FUNCTION mnemonic_oid_regtype_equal(
                        left_value pg_catalog.oid,
                        right_value pg_catalog.regtype
                    )
                    RETURNS pg_catalog.bool
                    LANGUAGE sql
                    IMMUTABLE
                    STRICT
                    PARALLEL SAFE
                    SET search_path = pg_catalog
                    AS 'SELECT true';
                    CREATE OPERATOR = (
                        LEFTARG = pg_catalog.oid,
                        RIGHTARG = pg_catalog.regtype,
                        FUNCTION = mnemonic_oid_regtype_equal
                    )
                    """
                )
            )
            connection.exec_driver_sql(
                f'SET LOCAL search_path = "{schema}", pg_catalog'
            )
            assert connection.scalar(
                text(
                    "SELECT 'pg_catalog.text'::pg_catalog.regtype::pg_catalog.oid "
                    "= 'pg_catalog.int4'::pg_catalog.regtype"
                )
            )

            captured = audit._catalog(connection, audit.FINAL_HEAD)

            assert captured["missing_function_count"] == 1
            assert captured["missing_index_count"] == 0
            assert captured["title_key_contract_failure_count"] == 0
            assert audit._catalog_blocking_counts(captured)[
                "missing_required_functions"
            ] == 1
        finally:
            transaction.rollback()


def test_audit_blocks_when_core_merge_ledger_is_missing(postgres_engine):
    audit = _audit_module()
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            baseline = audit._catalog(connection, audit.FINAL_HEAD)
            assert baseline["required_table_count"] == 3
            assert baseline["missing_table_count"] == 0

            connection.execute(text("DROP TABLE work_duplicate_merges CASCADE"))
            missing_ledger = audit._catalog(connection, audit.FINAL_HEAD)
            assert missing_ledger["missing_table_count"] == 1
            assert audit._catalog_blocking_counts(missing_ledger)[
                "missing_required_tables"
            ] == 1

            connection.execute(
                text("CREATE VIEW work_duplicate_merges AS SELECT 1 AS placeholder")
            )
            lookalike_view = audit._catalog(connection, audit.FINAL_HEAD)
            assert lookalike_view["missing_table_count"] == 1
        finally:
            transaction.rollback()


def test_audit_requires_one_exact_migration_head(postgres_engine):
    audit = _audit_module()
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            assert audit._migration_head_status(connection, audit.FINAL_HEAD) == (True, 1)
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES ('unexpected_branch')")
            )
            assert audit._migration_head_status(connection, audit.FINAL_HEAD) == (False, 2)
        finally:
            transaction.rollback()


def test_whole_audit_uses_trusted_path_and_cannot_skip_core_counts(postgres_engine):
    audit = _audit_module()
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_name_text_equal(name, text)
                    RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT false';
                    CREATE OPERATOR = (
                        LEFTARG = name,
                        RIGHTARG = text,
                        FUNCTION = mnemonic_name_text_equal
                    )
                    """
                )
            )
            connection.exec_driver_sql(
                f'SET LOCAL search_path = "{schema}", pg_catalog'
            )
            hostile_search_path = connection.scalar(
                text("SELECT pg_catalog.current_setting('search_path')")
            )
            assert connection.scalar(
                text(
                    "SELECT 'work_duplicate_merges'::name "
                    "= 'work_duplicate_merges'::text"
                )
            ) is False

            head_matches, head_count, counts, catalog = (
                audit._database_audit_snapshot(connection, audit.FINAL_HEAD)
            )

            assert head_matches is True
            assert head_count == 1
            assert catalog["missing_table_count"] == 0
            assert "authoritative_merges" in counts
            assert "checkpoint_receipt_scope_violation_count" in counts
            assert connection.scalar(
                text("SELECT pg_catalog.current_setting('search_path')")
            ) == hostile_search_path
        finally:
            transaction.rollback()


def test_audit_requires_exact_repository_freshness_catalog(postgres_engine):
    audit = _audit_module()
    expected_zeroes = {
        "missing_repository_freshness_function_count": 0,
        "repository_freshness_contract_failure_count": 0,
        "repository_freshness_definition_failure_count": 0,
        "checkpoint_affected_paths_column_failure_count": 0,
        "checkpoint_affected_paths_constraint_failure_count": 0,
        "unexpected_affected_paths_index_count": 0,
        "checkpoint_immutability_trigger_failure_count": 0,
    }
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            baseline = audit._catalog(connection, audit.FINAL_HEAD)
            assert {name: baseline[name] for name in expected_zeroes} == expected_zeroes

            altered_attributes = connection.begin_nested()
            connection.execute(
                text(
                    "ALTER TABLE checkpoints "
                    "DROP CONSTRAINT ck_checkpoints_affected_paths_valid_v1"
                )
            )
            connection.execute(
                text("DROP FUNCTION mnemonic_affected_paths_valid_v1(varchar[])")
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_affected_paths_valid_v1(value varchar[])
                    RETURNS boolean
                    LANGUAGE sql
                    VOLATILE
                    AS 'SELECT true'
                    """
                )
            )
            wrong_attributes = audit._catalog(connection, audit.FINAL_HEAD)
            assert wrong_attributes["missing_repository_freshness_function_count"] == 1
            altered_attributes.rollback()

            altered_definition = connection.begin_nested()
            function_definition = connection.scalar(
                text(
                    """
                    SELECT pg_get_functiondef(procedure.oid)
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = current_schema()
                      AND procedure.proname = 'mnemonic_affected_paths_valid_v1'
                    """
                )
            )
            tampered_definition = function_definition.replace(
                "total_bytes integer := 0;",
                "total_bytes integer := 0; -- audit-only definition drift",
            )
            assert tampered_definition != function_definition
            connection.exec_driver_sql(tampered_definition)
            definition_drift = audit._catalog(connection, audit.FINAL_HEAD)
            assert definition_drift["repository_freshness_contract_failure_count"] == 0
            assert definition_drift["repository_freshness_definition_failure_count"] == 1
            altered_definition.rollback()

            altered_configuration = connection.begin_nested()
            connection.execute(
                text(
                    "ALTER FUNCTION mnemonic_affected_paths_valid_v1(varchar[]) "
                    "SET statement_timeout = '5s'"
                )
            )
            configuration_drift = audit._catalog(connection, audit.FINAL_HEAD)
            assert configuration_drift[
                "missing_repository_freshness_function_count"
            ] == 1
            altered_configuration.rollback()

            hostile_array_equality = connection.begin_nested()
            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_text_array_equal(text[], text[])
                    RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT true';
                    CREATE OPERATOR = (
                        LEFTARG = text[],
                        RIGHTARG = text[],
                        FUNCTION = mnemonic_text_array_equal
                    );
                    ALTER FUNCTION mnemonic_affected_paths_valid_v1(varchar[])
                    SET statement_timeout = '5s'
                    """
                )
            )
            schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
            connection.exec_driver_sql(
                f'SET LOCAL search_path = "{schema}", pg_catalog'
            )
            assert connection.scalar(
                text("SELECT ARRAY['left']::text[] = ARRAY['right']::text[]")
            )
            namespace_safe = audit._catalog(connection, audit.FINAL_HEAD)
            assert namespace_safe[
                "missing_repository_freshness_function_count"
            ] == 1
            hostile_array_equality.rollback()

            hostile_internal_char_equality = connection.begin_nested()
            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_internal_char_equal("char", "char")
                    RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT true';
                    CREATE OPERATOR = (
                        LEFTARG = "char",
                        RIGHTARG = "char",
                        FUNCTION = mnemonic_internal_char_equal
                    );
                    ALTER FUNCTION mnemonic_affected_paths_valid_v1(varchar[])
                    VOLATILE
                    """
                )
            )
            connection.exec_driver_sql(
                f'SET LOCAL search_path = "{schema}", pg_catalog'
            )
            hostile_search_path = connection.scalar(
                text("SELECT pg_catalog.current_setting('search_path')")
            )
            assert connection.scalar(
                text("SELECT 'v'::\"char\" = 'i'::\"char\"")
            )
            internal_char_safe = audit._catalog(connection, audit.FINAL_HEAD)
            assert internal_char_safe[
                "missing_repository_freshness_function_count"
            ] == 1
            assert connection.scalar(
                text("SELECT pg_catalog.current_setting('search_path')")
            ) == hostile_search_path
            hostile_internal_char_equality.rollback()

            altered_contract = connection.begin_nested()
            connection.execute(
                text(
                    """
                    CREATE OR REPLACE FUNCTION mnemonic_affected_paths_valid_v1(value varchar[])
                    RETURNS boolean
                    LANGUAGE plpgsql
                    IMMUTABLE
                    STRICT
                    PARALLEL SAFE
                    SET search_path = pg_catalog
                    AS $function$
                    BEGIN
                        RETURN true;
                    END
                    $function$
                    """
                )
            )
            wrong_contract = audit._catalog(connection, audit.FINAL_HEAD)
            assert wrong_contract["missing_repository_freshness_function_count"] == 0
            assert wrong_contract["repository_freshness_contract_failure_count"] == 1
            assert wrong_contract["repository_freshness_definition_failure_count"] == 1
            altered_contract.rollback()

            altered_column = connection.begin_nested()
            connection.execute(
                text("ALTER TABLE checkpoints ALTER COLUMN affected_paths DROP DEFAULT")
            )
            assert audit._catalog(connection, audit.FINAL_HEAD)[
                "checkpoint_affected_paths_column_failure_count"
            ] > 0
            altered_column.rollback()

            altered_constraint = connection.begin_nested()
            connection.execute(
                text(
                    "ALTER TABLE checkpoints "
                    "DROP CONSTRAINT ck_checkpoints_affected_paths_require_commit"
                )
            )
            assert audit._catalog(connection, audit.FINAL_HEAD)[
                "checkpoint_affected_paths_constraint_failure_count"
            ] > 0
            altered_constraint.rollback()

            overloaded_constraint = connection.begin_nested()
            connection.execute(
                text(
                    """
                    CREATE FUNCTION cardinality(value varchar[])
                    RETURNS integer
                    LANGUAGE sql
                    IMMUTABLE
                    STRICT
                    PARALLEL SAFE
                    SET search_path = pg_catalog
                    AS 'SELECT 0'
                    """
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE checkpoints "
                    "DROP CONSTRAINT ck_checkpoints_affected_paths_require_commit"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE checkpoints ADD CONSTRAINT "
                    "ck_checkpoints_affected_paths_require_commit "
                    "CHECK (cardinality(affected_paths) = 0 "
                    "OR verified_against IS NOT NULL)"
                )
            )
            assert audit._catalog(connection, audit.FINAL_HEAD)[
                "checkpoint_affected_paths_constraint_failure_count"
            ] == 1
            overloaded_constraint.rollback()

            overloaded_operator = connection.begin_nested()
            schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_integer_equal(integer, integer)
                    RETURNS boolean
                    LANGUAGE sql
                    IMMUTABLE
                    STRICT
                    PARALLEL SAFE
                    SET search_path = pg_catalog
                    AS 'SELECT true';
                    CREATE OPERATOR = (
                        LEFTARG = integer,
                        RIGHTARG = integer,
                        FUNCTION = mnemonic_integer_equal
                    )
                    """
                )
            )
            connection.exec_driver_sql(
                f'SET LOCAL search_path = "{schema}", pg_catalog'
            )
            connection.execute(
                text(
                    "ALTER TABLE checkpoints "
                    "DROP CONSTRAINT ck_checkpoints_affected_paths_require_commit"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE checkpoints ADD CONSTRAINT "
                    "ck_checkpoints_affected_paths_require_commit "
                    "CHECK (pg_catalog.cardinality(affected_paths) = 0 "
                    "OR verified_against IS NOT NULL)"
                )
            )
            assert audit._catalog(connection, audit.FINAL_HEAD)[
                "checkpoint_affected_paths_constraint_failure_count"
            ] == 1
            overloaded_operator.rollback()

            spoofed_constraint_text = connection.begin_nested()
            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_text_equal(text, text)
                    RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT true';
                    CREATE OPERATOR = (
                        LEFTARG = text,
                        RIGHTARG = text,
                        FUNCTION = mnemonic_text_equal
                    );
                    ALTER TABLE checkpoints
                    DROP CONSTRAINT ck_checkpoints_affected_paths_valid_v1;
                    ALTER TABLE checkpoints
                    DROP CONSTRAINT ck_checkpoints_affected_paths_require_commit;
                    ALTER TABLE checkpoints ADD CONSTRAINT
                    ck_checkpoints_affected_paths_valid_v1 CHECK (TRUE);
                    ALTER TABLE checkpoints ADD CONSTRAINT
                    ck_checkpoints_affected_paths_require_commit CHECK (TRUE)
                    """
                )
            )
            connection.exec_driver_sql(
                f'SET LOCAL search_path = "{schema}", pg_catalog'
            )
            assert connection.scalar(text("SELECT 'left'::text = 'right'::text"))
            spoof_safe = audit._catalog(connection, audit.FINAL_HEAD)
            assert spoof_safe[
                "checkpoint_affected_paths_constraint_failure_count"
            ] == 2
            spoofed_constraint_text.rollback()

            unexpected_index = connection.begin_nested()
            connection.execute(
                text(
                    "CREATE INDEX ix_checkpoints_unexpected_affected_paths "
                    "ON checkpoints USING gin (affected_paths)"
                )
            )
            assert audit._catalog(connection, audit.FINAL_HEAD)[
                "unexpected_affected_paths_index_count"
            ] == 1
            unexpected_index.rollback()

            altered_trigger = connection.begin_nested()
            connection.execute(text("DROP TRIGGER checkpoints_immutable ON checkpoints"))
            assert audit._catalog(connection, audit.FINAL_HEAD)[
                "checkpoint_immutability_trigger_failure_count"
            ] == 1
            assert audit._catalog(connection, audit.ADVISORY_HEAD)[
                "checkpoint_immutability_trigger_failure_count"
            ] == 1
            altered_trigger.rollback()

            altered_trigger_function = connection.begin_nested()
            connection.execute(
                text(
                    """
                    CREATE OR REPLACE FUNCTION mnemonic_reject_checkpoint_mutation()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    AS $function$
                    BEGIN
                        RETURN NEW;
                    END
                    $function$
                    """
                )
            )
            assert audit._catalog(connection, audit.FINAL_HEAD)[
                "checkpoint_immutability_trigger_failure_count"
            ] == 1
            assert audit._catalog(connection, audit.ADVISORY_HEAD)[
                "checkpoint_immutability_trigger_failure_count"
            ] == 1
            altered_trigger_function.rollback()
        finally:
            transaction.rollback()


@pytest.mark.parametrize(
    ("trigger_name", "function_name"),
    (
        (
            "client_operation_insert_guard",
            "mnemonic_guard_client_operation_insert",
        ),
        (
            "client_operation_mutation_guard",
            "mnemonic_guard_client_operation_mutation",
        ),
        (
            "client_operation_completion_guard",
            "mnemonic_require_completed_client_operation",
        ),
    ),
)
def test_audit_requires_exact_client_operation_guards(
    postgres_engine, trigger_name, function_name
):
    audit = _audit_module()
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            baseline = audit._catalog(connection, audit.FINAL_HEAD)
            assert baseline["client_operation_guard_failure_count"] == 0

            missing_trigger = connection.begin_nested()
            connection.execute(
                text(f"DROP TRIGGER {trigger_name} ON client_operations")
            )
            missing = audit._catalog(connection, audit.FINAL_HEAD)
            assert missing["client_operation_guard_failure_count"] == 1
            assert audit._catalog_blocking_counts(missing)[
                "client_operation_guard_failures"
            ] == 1
            missing_trigger.rollback()

            altered_function = connection.begin_nested()
            connection.execute(
                text(
                    f"""
                    CREATE OR REPLACE FUNCTION {function_name}()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    SET search_path = pg_catalog
                    AS $function$
                    BEGIN
                        RETURN NEW;
                    END
                    $function$
                    """
                )
            )
            altered = audit._catalog(connection, audit.FINAL_HEAD)
            assert altered["client_operation_guard_failure_count"] == 1
            altered_function.rollback()
        finally:
            transaction.rollback()


def test_audit_counts_repository_scope_and_receipt_shape_drift(
    api, project, work_payload, checkpoint_fields, postgres_engine
):
    audit = _audit_module()
    create_operation_id = str(uuid4())
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={
            **work_payload,
            "title": "Audit repository freshness",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "affected_paths": ["src/**"],
            },
            "client_operation_id": create_operation_id,
        },
    )
    assert created.status_code == 201, created.text
    work_item_id = created.json()["work_item"]["id"]

    add_operation_id = str(uuid4())
    added = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work_item_id}/checkpoints",
        json={
            **checkpoint_fields,
            "kind": "progress",
            "affected_paths": ["backend/src/**"],
            "client_operation_id": add_operation_id,
        },
    )
    assert added.status_code == 201, added.text

    complete_operation_id = str(uuid4())
    completed = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work_item_id}/complete",
        json={
            "expected_version": 1,
            "checkpoint": {
                **checkpoint_fields,
                "prompt": "Complete after auditing exact repository scope.",
                "affected_paths": ["backend/tests/**"],
            },
            "client_operation_id": complete_operation_id,
        },
    )
    assert completed.status_code == 200, completed.text

    sparse_operation_id = str(uuid4())
    sparse = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={
            **work_payload,
            "title": "Audit historical sparse receipt shape",
            "client_operation_id": sparse_operation_id,
        },
    )
    assert sparse.status_code == 201, sparse.text
    assert "affected_paths" not in sparse.json()["initial_checkpoint"]

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            baseline = audit._repository_freshness_counts(connection)
            assert baseline["invalid_affected_paths_count"] == 0
            assert baseline["commitless_affected_paths_count"] == 0
            assert baseline["scoped_checkpoint_count"] == 3
            assert baseline["checkpoint_receipt_scope_violation_count"] == 0

            connection.execute(
                text(
                    """
                    CREATE FUNCTION cardinality(value varchar[])
                    RETURNS integer
                    LANGUAGE sql
                    IMMUTABLE
                    STRICT
                    PARALLEL SAFE
                    SET search_path = pg_catalog
                    AS 'SELECT 0'
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION to_jsonb(value varchar[])
                    RETURNS jsonb
                    LANGUAGE sql
                    IMMUTABLE
                    STRICT
                    PARALLEL SAFE
                    SET search_path = pg_catalog
                    AS 'SELECT ''[]''::jsonb'
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_varchar_equal(varchar, varchar)
                    RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT false';
                    CREATE OPERATOR = (
                        LEFTARG = varchar,
                        RIGHTARG = varchar,
                        FUNCTION = mnemonic_varchar_equal
                    );
                    CREATE FUNCTION mnemonic_integer_equal(integer, integer)
                    RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT true';
                    CREATE OPERATOR = (
                        LEFTARG = integer,
                        RIGHTARG = integer,
                        FUNCTION = mnemonic_integer_equal
                    );
                    CREATE FUNCTION mnemonic_integer_greater(integer, integer)
                    RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT false';
                    CREATE OPERATOR > (
                        LEFTARG = integer,
                        RIGHTARG = integer,
                        FUNCTION = mnemonic_integer_greater
                    );
                    CREATE FUNCTION mnemonic_path_concat(text[], text)
                    RETURNS text[] LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog AS
                        'SELECT ARRAY[''metadata'']::text[]';
                    CREATE OPERATOR || (
                        LEFTARG = text[],
                        RIGHTARG = text,
                        FUNCTION = mnemonic_path_concat
                    )
                    """
                )
            )
            schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
            connection.exec_driver_sql(
                f'SET LOCAL search_path = "{schema}", pg_catalog'
            )
            assert connection.execute(
                text(
                    "SELECT 'completed'::varchar = 'completed'::varchar, "
                    "1 = 0, 1 > 0, ARRAY[]::text[] || 'child'::text"
                )
            ).one() == (False, True, False, ["metadata"])
            overload_safe = audit._repository_freshness_counts(connection)
            assert overload_safe["scoped_checkpoint_count"] == 3
            assert overload_safe["checkpoint_receipt_scope_violation_count"] == 0

            connection.execute(
                text(
                    "DROP TRIGGER client_operation_mutation_guard "
                    "ON client_operations"
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = jsonb_set(
                        response_body,
                        '{initial_checkpoint,source_metadata,affected_paths}',
                        '["opaque-metadata-is-not-scope"]'::jsonb
                    )
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": create_operation_id},
            )
            assert audit._repository_freshness_counts(connection)[
                "checkpoint_receipt_scope_violation_count"
            ] == 0
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body =
                        response_body #- '{initial_checkpoint,source_metadata,affected_paths}'
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": create_operation_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = jsonb_set(
                        response_body,
                        '{work_item,affected_paths}',
                        '["forbidden-compact-scope"]'::jsonb
                    )
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": create_operation_id},
            )
            assert audit._repository_freshness_counts(connection)[
                "checkpoint_receipt_scope_violation_count"
            ] == 1
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = response_body #- '{work_item,affected_paths}'
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": create_operation_id},
            )
            assert audit._repository_freshness_counts(connection)[
                "checkpoint_receipt_scope_violation_count"
            ] == 0
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = jsonb_set(
                        response_body,
                        '{initial_checkpoint,affected_paths}',
                        '[]'::jsonb
                    )
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": create_operation_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = jsonb_set(
                        response_body,
                        '{affected_paths}',
                        '"malformed"'::jsonb
                    )
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": add_operation_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = response_body #- '{checkpoint,affected_paths}'
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": complete_operation_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = jsonb_set(
                        response_body,
                        '{initial_checkpoint,verified_against}',
                        '"deadbee"'::jsonb
                    )
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": sparse_operation_id},
            )
            assert audit._repository_freshness_counts(connection)[
                "checkpoint_receipt_scope_violation_count"
            ] == 4

            connection.execute(
                text("DROP TRIGGER checkpoints_immutable ON checkpoints")
            )
            connection.execute(
                text(
                    "ALTER TABLE checkpoints "
                    "DROP CONSTRAINT ck_checkpoints_affected_paths_valid_v1"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE checkpoints "
                    "DROP CONSTRAINT ck_checkpoints_affected_paths_require_commit"
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE checkpoints
                    SET affected_paths = ARRAY['bad path']::varchar[],
                        verified_against = NULL
                    WHERE id = CAST(:checkpoint_id AS uuid)
                    """
                ),
                {"checkpoint_id": added.json()["id"]},
            )
            drift = audit._repository_freshness_counts(connection)
            assert drift["invalid_affected_paths_count"] == 1
            assert drift["commitless_affected_paths_count"] == 1
            assert drift["scoped_checkpoint_count"] == 3
        finally:
            transaction.rollback()


def test_audit_rejects_negative_minimum_backup_capacity(monkeypatch):
    audit = _audit_module()
    monkeypatch.setattr(
        "sys.argv", ["audit", "--minimum-backup-free-bytes", "-1"]
    )
    with pytest.raises(SystemExit) as raised:
        audit._parse_args({"DATABASE_URL": "postgresql://localhost/mnemonic"})
    assert raised.value.code == 2


def test_audit_rejects_unsupported_expected_head(monkeypatch):
    audit = _audit_module()
    monkeypatch.setattr("sys.argv", ["audit", "--expected-head", "custom_head"])
    with pytest.raises(SystemExit) as raised:
        audit._parse_args({"DATABASE_URL": "postgresql://localhost/mnemonic"})
    assert raised.value.code == 2


def test_audit_scope_inventory_blocks_only_in_pre_enablement_mode():
    audit = _audit_module()
    counts = {
        "authoritative_merges": 2,
        "scoped_checkpoint_count": 3,
    }

    assert audit._blocking_counts(counts) == {}
    assert audit._blocking_counts(counts, require_empty_scope=True) == {
        "unexpected_pre_enablement_scoped_checkpoint_count": 3
    }


def test_audit_completion_evidence_inventory_blocks_only_when_requested():
    audit = _audit_module()
    counts = {
        "completion_episode_count": 2,
        "structured_completion_episode_count": 1,
        "empty_completion_episode_count": 1,
        "verification_result_count": 1,
        "artifact_reference_count": 0,
        "phase11_downgrade_blocking_count": 2,
    }

    assert audit._blocking_counts(counts) == {}
    assert audit._blocking_counts(
        counts, require_empty_completion_evidence=True
    ) == {"unexpected_pre_enablement_completion_evidence_count": 2}


def test_audit_empty_scope_mode_requires_phase_10_head(monkeypatch):
    audit = _audit_module()
    monkeypatch.setattr(
        "sys.argv",
        [
            "audit",
            "--expected-head",
            "0017_duplicate_suggestion_title_key",
            "--require-empty-scope",
        ],
    )
    with pytest.raises(SystemExit) as raised:
        audit._parse_args({"DATABASE_URL": "postgresql://localhost/mnemonic"})
    assert raised.value.code == 2


def test_audit_phase11_catalog_fingerprints_detect_every_object_class(postgres_engine):
    audit = _audit_module()
    expected_keys = {
        "relation": "completion_evidence_relation_failure_count",
        "relation_acl": "completion_evidence_relation_failure_count",
        "column": "completion_evidence_column_failure_count",
        "column_acl": "completion_evidence_column_failure_count",
        "constraint": "completion_evidence_constraint_failure_count",
        "index": "completion_evidence_index_failure_count",
        "trigger": "completion_evidence_trigger_failure_count",
        "function": "completion_evidence_function_failure_count",
        "function_attribute": "completion_evidence_function_failure_count",
        "function_acl": "completion_evidence_function_failure_count",
    }
    mutations = {
        "relation": "ALTER TABLE verification_results SET (fillfactor = 75)",
        "relation_acl": "GRANT SELECT ON verification_results TO PUBLIC",
        "column": (
            "ALTER TABLE verification_results ALTER COLUMN name TYPE varchar(199)"
        ),
        "column_acl": "GRANT SELECT (summary) ON verification_results TO PUBLIC",
        "constraint": (
            "ALTER TABLE verification_results "
            "DROP CONSTRAINT ck_verification_results_position_range"
        ),
        "index": (
            "ALTER INDEX uq_verification_results_episode_position "
            "SET (fillfactor = 75)"
        ),
        "trigger": (
            "ALTER TABLE work_items DISABLE TRIGGER completion_generation_guard"
        ),
        "function": """
            CREATE OR REPLACE FUNCTION mnemonic_guard_completion_generation()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog
            AS $function$
            BEGIN
                RETURN NEW;
            END
            $function$
        """,
        "function_attribute": (
            "ALTER FUNCTION mnemonic_guard_completion_generation() COST 7"
        ),
        "function_acl": (
            "GRANT EXECUTE ON FUNCTION mnemonic_guard_completion_generation() TO PUBLIC"
        ),
    }

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            baseline = audit._catalog(connection, audit.FINAL_HEAD)
            assert {key: baseline[key] for key in expected_keys.values()} == {
                key: 0 for key in expected_keys.values()
            }
            for object_class, statement in mutations.items():
                tamper = connection.begin_nested()
                connection.execute(text(statement))
                catalog = audit._catalog(connection, audit.FINAL_HEAD)
                key = expected_keys[object_class]
                assert catalog[key] == 1, object_class
                assert audit._catalog_blocking_counts(catalog)[
                    key.removesuffix("_count") + "s"
                ] == 1
                tamper.rollback()
        finally:
            transaction.rollback()


def test_phase11_catalog_normalization_only_rewrites_schema_identifiers():
    audit = _audit_module()

    assert audit._normalize_phase11_catalog_value(
        'CREATE INDEX x ON "tenant-work".work_items (requested_work_id)',
        "tenant-work",
    ) == "CREATE INDEX x ON <schema>.work_items (requested_work_id)"
    assert audit._normalize_phase11_catalog_value(
        "work.work_events work_items requested_work_id AS work",
        "work",
    ) == "<schema>.work_events work_items requested_work_id AS work"
    assert audit._normalize_phase11_catalog_value(
        'search_path="tenant-work",pg_catalog',
        "tenant-work",
    ) == "search_path=<schema>,pg_catalog"
    assert audit._normalize_phase11_catalog_value(
        "'\"tenant''s\".work_events'::regclass",
        "tenant's",
    ) == "'<schema>.work_events'::regclass"


def test_audit_phase11_catalog_detects_internal_fk_trigger_tamper(postgres_engine):
    audit = _audit_module()
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            trigger_name = connection.scalar(
                text(
                    """
                    SELECT trigger_value.tgname
                    FROM pg_catalog.pg_trigger AS trigger_value
                    JOIN pg_catalog.pg_constraint AS constraint_value
                      ON constraint_value.oid = trigger_value.tgconstraint
                    JOIN pg_catalog.pg_class AS relation
                      ON relation.oid = trigger_value.tgrelid
                    WHERE relation.oid = 'verification_results'::regclass
                      AND constraint_value.conname
                            = 'fk_verification_results_work_item'
                      AND trigger_value.tgisinternal
                    ORDER BY trigger_value.tgtype
                    LIMIT 1
                    """
                )
            )
            assert isinstance(trigger_name, str)
            quoted_trigger = '"' + trigger_name.replace('"', '""') + '"'
            connection.exec_driver_sql(
                f"ALTER TABLE verification_results DISABLE TRIGGER {quoted_trigger}"
            )

            catalog = audit._catalog(connection, audit.FINAL_HEAD)
            assert catalog["completion_evidence_trigger_failure_count"] == 1
        finally:
            transaction.rollback()


def test_audit_phase11_sequence_check_honors_is_called(postgres_engine):
    audit = _audit_module()
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        sequence = None
        sequence_state = None
        try:
            assert audit._completion_evidence_counts(connection)[
                "work_event_identity_sequence_violation_count"
            ] == 0
            sequence = connection.scalar(
                text("SELECT pg_catalog.pg_get_serial_sequence('work_events', 'id')")
            )
            assert isinstance(sequence, str)
            sequence_state = connection.execute(
                text(f"SELECT last_value, is_called FROM {sequence}")
            ).one()
            connection.execute(
                text(
                    "SELECT pg_catalog.setval("
                    "CAST(:sequence AS regclass), 9223372036854775807, false)"
                ),
                {"sequence": sequence},
            )

            assert audit._completion_evidence_counts(connection)[
                "work_event_identity_sequence_violation_count"
            ] == 1
        finally:
            transaction.rollback()
            if sequence is not None and sequence_state is not None:
                connection.execute(
                    text(
                        "SELECT pg_catalog.setval(CAST(:sequence AS regclass), "
                        ":last_value, :is_called)"
                    ),
                    {
                        "is_called": sequence_state.is_called,
                        "last_value": sequence_state.last_value,
                        "sequence": sequence,
                    },
                )
                connection.commit()


def _set_based_phase11_reopen_and_id_counts(connection) -> tuple[int, int]:
    reopen_count = connection.scalar(
        text(
            """
            SELECT pg_catalog.count(*)
            FROM (
                SELECT event.id::text
                FROM work_events AS event
                LEFT JOIN work_items AS work ON work.id = event.work_item_id
                WHERE (event.event_type = 'work_reopened')
                        IS DISTINCT FROM (event.reopen_generation IS NOT NULL)
                   OR event.reopen_generation = 0
                   OR (
                       event.reopen_generation < 0
                       AND event.reopen_generation::numeric
                            IS DISTINCT FROM -event.id::numeric
                   )
                   OR (
                       event.reopen_generation > 0
                       AND (
                           work.id IS NULL
                           OR event.project_id IS DISTINCT FROM work.project_id
                           OR event.origin IS DISTINCT FROM 'live'
                           OR event.reopen_generation > work.completion_generation
                           OR pg_catalog.jsonb_typeof(
                                  event.metadata -> 'work_version'
                              ) IS DISTINCT FROM 'number'
                           OR event.metadata ->> 'work_version' !~ '^[1-9][0-9]*$'
                           OR (
                               event.metadata ->> 'from_status'
                                   IN ('done', 'deferred', 'wont-do', 'promoted')
                           ) IS NOT TRUE
                           OR event.metadata ->> 'to_status'
                                IS DISTINCT FROM 'pending'
                           OR event.metadata -> 'changes' -> 'status' ->> 'before'
                                IS DISTINCT FROM event.metadata ->> 'from_status'
                           OR event.metadata -> 'changes' -> 'status' ->> 'after'
                                IS DISTINCT FROM 'pending'
                       )
                   )
                UNION ALL
                SELECT work.id::text
                FROM work_items AS work
                LEFT JOIN LATERAL (
                    SELECT pg_catalog.count(*) AS binding_count,
                           pg_catalog.count(DISTINCT event.reopen_generation)
                               AS distinct_binding_count,
                           pg_catalog.min(event.reopen_generation)
                               AS minimum_generation,
                           pg_catalog.max(event.reopen_generation)
                               AS maximum_generation
                    FROM work_events AS event
                    WHERE event.work_item_id = work.id
                      AND event.event_type = 'work_reopened'
                      AND event.reopen_generation > 0
                ) AS bindings ON true
                WHERE work.completion_generation > 0
                  AND (
                      bindings.binding_count <> work.completion_generation
                      OR bindings.distinct_binding_count
                           <> work.completion_generation
                      OR bindings.minimum_generation <> 1
                      OR bindings.maximum_generation <> work.completion_generation
                  )
                UNION ALL
                SELECT completion.id::text
                FROM work_events AS completion
                JOIN checkpoints AS checkpoint
                  ON checkpoint.work_item_id = completion.work_item_id
                 AND checkpoint.id = completion.checkpoint_id
                 AND checkpoint.kind = 'completion'
                JOIN work_events AS reopen
                  ON reopen.work_item_id = completion.work_item_id
                 AND reopen.event_type = 'work_reopened'
                 AND reopen.reopen_generation = checkpoint.completion_generation
                WHERE completion.event_type = 'work_completed'
                  AND checkpoint.completion_generation > 0
                  AND (
                      pg_catalog.jsonb_typeof(
                          completion.metadata -> 'work_version'
                      ) IS DISTINCT FROM 'number'
                      OR pg_catalog.jsonb_typeof(
                          reopen.metadata -> 'work_version'
                      ) IS DISTINCT FROM 'number'
                      OR completion.metadata ->> 'work_version'
                           !~ '^[1-9][0-9]*$'
                      OR reopen.metadata ->> 'work_version' !~ '^[1-9][0-9]*$'
                      OR CASE
                          WHEN pg_catalog.jsonb_typeof(
                                   completion.metadata -> 'work_version'
                               ) = 'number'
                           AND pg_catalog.jsonb_typeof(
                                   reopen.metadata -> 'work_version'
                               ) = 'number'
                           AND completion.metadata ->> 'work_version'
                                   ~ '^[1-9][0-9]*$'
                           AND reopen.metadata ->> 'work_version'
                                   ~ '^[1-9][0-9]*$'
                          THEN (completion.metadata ->> 'work_version')::numeric
                               <= (reopen.metadata ->> 'work_version')::numeric
                          ELSE true
                         END
                  )
                UNION ALL
                SELECT completion.id::text
                FROM work_events AS completion
                JOIN checkpoints AS checkpoint
                  ON checkpoint.work_item_id = completion.work_item_id
                 AND checkpoint.id = completion.checkpoint_id
                 AND checkpoint.kind = 'completion'
                JOIN work_items AS work ON work.id = completion.work_item_id
                LEFT JOIN work_events AS successor
                  ON successor.work_item_id = completion.work_item_id
                 AND successor.event_type = 'work_reopened'
                 AND successor.reopen_generation
                        = checkpoint.completion_generation + 1
                WHERE completion.event_type = 'work_completed'
                  AND checkpoint.completion_generation >= 0
                  AND checkpoint.completion_generation < work.completion_generation
                  AND (
                      successor.id IS NULL
                      OR pg_catalog.jsonb_typeof(
                          completion.metadata -> 'work_version'
                      ) IS DISTINCT FROM 'number'
                      OR pg_catalog.jsonb_typeof(
                          successor.metadata -> 'work_version'
                      ) IS DISTINCT FROM 'number'
                      OR completion.metadata ->> 'work_version'
                           !~ '^[1-9][0-9]*$'
                      OR successor.metadata ->> 'work_version'
                           !~ '^[1-9][0-9]*$'
                      OR CASE
                          WHEN pg_catalog.jsonb_typeof(
                                   completion.metadata -> 'work_version'
                               ) = 'number'
                           AND pg_catalog.jsonb_typeof(
                                   successor.metadata -> 'work_version'
                               ) = 'number'
                           AND completion.metadata ->> 'work_version'
                                   ~ '^[1-9][0-9]*$'
                           AND successor.metadata ->> 'work_version'
                                   ~ '^[1-9][0-9]*$'
                          THEN (completion.metadata ->> 'work_version')::numeric
                               >= (successor.metadata ->> 'work_version')::numeric
                          ELSE true
                         END
                  )
            ) AS violations
            """
        )
    )
    event_id_count = connection.scalar(
        text(
            """
            SELECT pg_catalog.count(*)
            FROM work_events
            WHERE event_type IN ('work_completed', 'work_reopened')
              AND id NOT BETWEEN 1 AND 9223372036854775806
            """
        )
    )
    return int(reopen_count or 0), int(event_id_count or 0)


def _set_based_phase11_receipt_correspondence_count(connection) -> int:
    count = connection.scalar(
        text(
            r"""
            WITH verification_json AS (
                SELECT result.work_item_id, result.completion_checkpoint_id,
                       result.position,
                       pg_catalog.jsonb_strip_nulls(
                           (pg_catalog.to_jsonb(result) - 'project_id')
                           || pg_catalog.jsonb_build_object(
                               'created_at', pg_catalog.regexp_replace(
                                   pg_catalog.to_char(
                                       pg_catalog.timezone('UTC', result.created_at),
                                       'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                   ),
                                   '\.000000Z$',
                                   'Z'
                               ),
                               'observed_at', CASE
                                   WHEN result.observed_at IS NULL THEN NULL
                                   ELSE pg_catalog.regexp_replace(
                                       pg_catalog.to_char(
                                           pg_catalog.timezone(
                                               'UTC', result.observed_at
                                           ),
                                           'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                       ),
                                       '\.000000Z$',
                                       'Z'
                                   )
                               END
                           )
                       ) AS payload
                FROM verification_results AS result
            ), artifact_json AS (
                SELECT artifact.work_item_id, artifact.completion_checkpoint_id,
                       artifact.position,
                       (pg_catalog.to_jsonb(artifact) - 'project_id')
                       || pg_catalog.jsonb_build_object(
                           'created_at', pg_catalog.regexp_replace(
                               pg_catalog.to_char(
                                   pg_catalog.timezone('UTC', artifact.created_at),
                                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                               ),
                               '\.000000Z$',
                               'Z'
                           )
                       ) AS payload
                FROM artifact_references AS artifact
            ), evidence_checkpoints AS (
                SELECT work_item_id, completion_checkpoint_id FROM verification_json
                UNION
                SELECT work_item_id, completion_checkpoint_id FROM artifact_json
            ), canonical_evidence AS (
                SELECT evidence.work_item_id, evidence.completion_checkpoint_id,
                       pg_catalog.jsonb_build_object(
                           'verification_results', COALESCE((
                               SELECT pg_catalog.jsonb_agg(
                                   result.payload ORDER BY result.position
                               )
                               FROM verification_json AS result
                               WHERE result.work_item_id = evidence.work_item_id
                                 AND result.completion_checkpoint_id
                                     = evidence.completion_checkpoint_id
                           ), '[]'::jsonb),
                           'artifact_references', COALESCE((
                               SELECT pg_catalog.jsonb_agg(
                                   artifact.payload ORDER BY artifact.position
                               )
                               FROM artifact_json AS artifact
                               WHERE artifact.work_item_id = evidence.work_item_id
                                 AND artifact.completion_checkpoint_id
                                     = evidence.completion_checkpoint_id
                           ), '[]'::jsonb)
                       ) AS payload
                FROM evidence_checkpoints AS evidence
            ), completion_receipts AS (
                SELECT operation.id, operation.response_body,
                       operation.response_body #>> '{work_item,id}' AS work_item_id,
                       operation.response_body #>> '{checkpoint,id}' AS checkpoint_id,
                       (operation.response_body ? 'completion_evidence') IS TRUE
                           AS has_evidence
                FROM client_operations AS operation
                WHERE operation.operation_kind = 'complete_work'
                  AND operation.state = 'completed'
            ), receipt_violations AS (
                SELECT receipt.id
                FROM completion_receipts AS receipt
                LEFT JOIN canonical_evidence AS evidence
                  ON evidence.work_item_id::text = receipt.work_item_id
                 AND evidence.completion_checkpoint_id::text = receipt.checkpoint_id
                WHERE pg_catalog.jsonb_typeof(receipt.response_body)
                          IS DISTINCT FROM 'object'
                   OR pg_catalog.jsonb_typeof(receipt.response_body -> 'work_item')
                          IS DISTINCT FROM 'object'
                   OR pg_catalog.jsonb_typeof(receipt.response_body -> 'checkpoint')
                          IS DISTINCT FROM 'object'
                   OR pg_catalog.jsonb_typeof(
                          receipt.response_body #> '{work_item,id}'
                      ) IS DISTINCT FROM 'string'
                   OR pg_catalog.jsonb_typeof(
                          receipt.response_body #> '{checkpoint,id}'
                      ) IS DISTINCT FROM 'string'
                   OR (
                       receipt.has_evidence
                       AND (
                           evidence.completion_checkpoint_id IS NULL
                           OR pg_catalog.jsonb_typeof(
                                  receipt.response_body -> 'completion_evidence'
                              ) IS DISTINCT FROM 'object'
                           OR (receipt.response_body -> 'completion_evidence')::text
                                  IS DISTINCT FROM evidence.payload::text
                       )
                   )
                   OR (
                       NOT receipt.has_evidence
                       AND evidence.completion_checkpoint_id IS NOT NULL
                   )
            ), evidence_violations AS (
                SELECT evidence.completion_checkpoint_id
                FROM canonical_evidence AS evidence
                LEFT JOIN completion_receipts AS receipt
                  ON receipt.work_item_id = evidence.work_item_id::text
                 AND receipt.checkpoint_id = evidence.completion_checkpoint_id::text
                 AND receipt.has_evidence
                 AND pg_catalog.jsonb_typeof(
                       receipt.response_body -> 'completion_evidence'
                     ) = 'object'
                 AND (receipt.response_body -> 'completion_evidence')::text
                       = evidence.payload::text
                GROUP BY evidence.completion_checkpoint_id
                HAVING pg_catalog.count(receipt.id) <> 1
            )
            SELECT (SELECT pg_catalog.count(*) FROM receipt_violations)
                 + (SELECT pg_catalog.count(*) FROM evidence_violations)
            """
        )
    )
    return int(count or 0)


def test_phase11_audit_batches_are_keyset_bounded_and_count_exactly(
    api,
    project,
    work_payload,
    postgres_engine,
    monkeypatch,
):
    audit = _audit_module()
    for index in range(2):
        created = api.post(
            f"/api/v1/projects/{project['id']}/work-items",
            json={**work_payload, "title": f"Bounded audit {index}"},
        )
        assert created.status_code == 201, created.text
        work = created.json()["work_item"]
        completed = api.post(
            f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete",
            json={
                "expected_version": 1,
                "checkpoint": work_payload["initial_checkpoint"],
                "completion_evidence": {
                    "verification_results": [
                        {
                            "verification_type": "observation",
                            "name": f"Batch {index}",
                            "outcome": "passed",
                            "summary": "Bounded audit fixture.",
                        }
                    ]
                },
                "client_operation_id": str(uuid4()),
            },
        )
        assert completed.status_code == 200, completed.text
        if index == 0:
            reopened = api.patch(
                f"/api/v1/projects/{project['id']}/work-items/{work['id']}",
                json={
                    "expected_version": 2,
                    "status": "pending",
                    "actor": {
                        "actor_client": "pytest",
                        "actor_session_id": "bounded-audit-reopen",
                    },
                },
            )
            assert reopened.status_code == 200, reopened.text
            recompleted = api.post(
                f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete",
                json={
                    "expected_version": 3,
                    "checkpoint": work_payload["initial_checkpoint"],
                    "completion_evidence": {
                        "artifact_references": [
                            {
                                "artifact_type": "commit",
                                "label": "Bounded audit commit",
                                "reference": "7ad62e4",
                            }
                        ]
                    },
                    "client_operation_id": str(uuid4()),
                },
            )
            assert recompleted.status_code == 200, recompleted.text

    with postgres_engine.connect() as connection:
        set_based_reopen, set_based_event_id = (
            _set_based_phase11_reopen_and_id_counts(connection)
        )
        set_based_receipt = _set_based_phase11_receipt_correspondence_count(connection)
        expected = audit._completion_evidence_counts(connection)
    assert expected["reopen_binding_violation_count"] == set_based_reopen
    assert expected["completion_event_id_violation_count"] == set_based_event_id
    assert (
        expected["receipt_evidence_correspondence_violation_count"]
        == set_based_receipt
    )

    statements: list[tuple[str, object]] = []

    def capture_statement(conn, cursor, statement, parameters, context, executemany):
        del conn, cursor, context, executemany
        statements.append((statement, parameters))

    monkeypatch.setattr(audit, "PHASE11_AUDIT_BATCH_SIZE", 1)
    event.listen(postgres_engine, "before_cursor_execute", capture_statement)
    try:
        with postgres_engine.connect() as connection:
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            assert connection.scalar(text("SHOW transaction_isolation")) == "repeatable read"
            assert connection.scalar(text("SHOW transaction_read_only")) == "on"
            actual = audit._completion_evidence_counts(connection)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture_statement)

    assert actual == expected
    keyset_statements = [
        statement
        for statement, _ in statements
        if "phase11-audit-keyset" in statement
    ]
    assert keyset_statements
    assert any("id >" in statement for statement in keyset_statements)
    assert all("id <=" in statement for statement in keyset_statements)
    high_water_statements = [
        statement
        for statement, _ in statements
        if "phase11-audit-high-water" in statement
    ]
    assert high_water_statements
    assert all("ORDER BY id DESC" in statement for statement in high_water_statements)
    assert all("LIMIT 1" in statement for statement in high_water_statements)
    candidate_batches = [
        parameters
        for statement, parameters in statements
        if "phase11-audit-batch-candidates" in statement
    ]
    assert candidate_batches
    assert all(
        isinstance(parameters, dict)
        and isinstance(parameters.get("audit_ids"), list)
        and 0 < len(parameters["audit_ids"]) <= audit.PHASE11_AUDIT_BATCH_SIZE
        for parameters in candidate_batches
    )
    sealed_statements = [
        statement
        for statement, _ in statements
        if "mnemonic_completion_episode_is_sealed(" in statement
    ]
    assert sealed_statements
    assert all("checkpoint.id = ANY" in statement for statement in sealed_statements)
    downgrade_receipt_statements = [
        statement
        for statement, _ in statements
        if "request_fingerprint_version IS DISTINCT FROM 1" in statement
    ]
    assert downgrade_receipt_statements
    assert all("operation.id = ANY" in statement for statement in downgrade_receipt_statements)
    reverse_receipt_statements = [
        (statement, parameters)
        for statement, parameters in statements
        if "phase11-audit-reverse-receipt-classification" in statement
    ]
    assert reverse_receipt_statements
    assert all("LIMIT 2" in statement for statement, _ in reverse_receipt_statements)
    assert all(
        "phase11-audit-batch-candidates" in statement
        and isinstance(parameters, dict)
        and isinstance(parameters.get("audit_ids"), list)
        and 0 < len(parameters["audit_ids"]) <= audit.PHASE11_AUDIT_BATCH_SIZE
        for statement, parameters in reverse_receipt_statements
    )


def test_phase11_batched_reopen_and_event_id_counts_match_set_based_corruption(
    api,
    project,
    work_payload,
    postgres_engine,
    monkeypatch,
):
    audit = _audit_module()
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Batched reopen parity"},
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    completed = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete",
        json={
            "expected_version": 1,
            "checkpoint": work_payload["initial_checkpoint"],
            "client_operation_id": str(uuid4()),
        },
    )
    assert completed.status_code == 200, completed.text
    reopened = api.patch(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}",
        json={
            "expected_version": 2,
            "status": "pending",
            "actor": {
                "actor_client": "pytest",
                "actor_session_id": "batched-reopen-parity",
            },
        },
    )
    assert reopened.status_code == 200, reopened.text

    monkeypatch.setattr(audit, "PHASE11_AUDIT_BATCH_SIZE", 1)
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("ALTER TABLE work_events DISABLE TRIGGER USER"))
            connection.execute(
                text(
                    """
                    UPDATE work_events
                    SET reopen_generation = -id - 1
                    WHERE work_item_id = CAST(:work_item_id AS uuid)
                      AND event_type = 'work_reopened'
                    """
                ),
                {"work_item_id": work["id"]},
            )
            connection.execute(
                text("ALTER TABLE work_events ALTER COLUMN id SET GENERATED BY DEFAULT")
            )
            connection.execute(
                text(
                    """
                    UPDATE work_events
                    SET id = 0
                    WHERE work_item_id = CAST(:work_item_id AS uuid)
                      AND event_type = 'work_completed'
                    """
                ),
                {"work_item_id": work["id"]},
            )
            expected_reopen, expected_event_id = (
                _set_based_phase11_reopen_and_id_counts(connection)
            )
            assert expected_reopen > 0
            assert expected_event_id == 1
            assert (
                audit._phase11_batched_reopen_binding_violation_count(connection)
                == expected_reopen
            )
            assert (
                audit._phase11_batched_completion_event_id_violation_count(connection)
                == expected_event_id
            )
        finally:
            transaction.rollback()


class _SyntheticKeysetConnection:
    def __init__(self, high_water, expected_count, pages):
        self.high_water = high_water
        self.expected_count = expected_count
        self.pages = list(pages)
        self.keyset_parameters = []

    def scalar(self, statement, parameters=None):
        del parameters
        statement_text = str(statement)
        if "phase11-audit-high-water" in statement_text:
            return self.high_water
        if "phase11-audit-inventory" in statement_text:
            return self.expected_count
        raise AssertionError("unexpected synthetic scalar query")

    def scalars(self, statement, parameters):
        assert "phase11-audit-keyset" in str(statement)
        self.keyset_parameters.append(parameters)
        return iter(self.pages.pop(0) if self.pages else ())


def test_phase11_audit_keyset_preserves_uuid_progression(monkeypatch):
    audit = _audit_module()
    identifiers = tuple(UUID(int=value) for value in (1, 2, 3))
    connection = _SyntheticKeysetConnection(
        identifiers[-1],
        len(identifiers),
        (identifiers[:2], identifiers[2:]),
    )
    monkeypatch.setattr(audit, "PHASE11_AUDIT_BATCH_SIZE", 2)

    assert tuple(audit._phase11_id_batches(connection, "verification_results")) == (
        identifiers[:2],
        identifiers[2:],
    )
    assert connection.keyset_parameters[0]["audit_high_water"] == identifiers[-1]
    assert "audit_cursor" not in connection.keyset_parameters[0]
    assert connection.keyset_parameters[1]["audit_cursor"] == identifiers[1]
    assert all(
        isinstance(value, UUID)
        for parameters in connection.keyset_parameters
        for name, value in parameters.items()
        if name in {"audit_high_water", "audit_cursor"}
    )


def test_phase11_audit_keyset_preserves_bigint_boundary_progression(monkeypatch):
    audit = _audit_module()
    identifiers = (1, 9223372036854775805, 9223372036854775806)
    connection = _SyntheticKeysetConnection(
        identifiers[-1],
        len(identifiers),
        (identifiers[:2], identifiers[2:]),
    )
    monkeypatch.setattr(audit, "PHASE11_AUDIT_BATCH_SIZE", 2)

    assert tuple(audit._phase11_id_batches(connection, "lifecycle_events")) == (
        identifiers[:2],
        identifiers[2:],
    )
    assert connection.keyset_parameters[1]["audit_cursor"] == identifiers[1]
    assert connection.keyset_parameters[1]["audit_high_water"] == identifiers[-1]


@pytest.mark.parametrize(
    ("high_water", "expected_count", "pages", "batch_size"),
    (
        (
            UUID(int=2),
            2,
            ((UUID(int=1), UUID(int=1)),),
            2,
        ),
        (
            UUID(int=3),
            4,
            (
                (UUID(int=1), UUID(int=2)),
                (UUID(int=2), UUID(int=3)),
            ),
            2,
        ),
        (
            UUID(int=3),
            3,
            ((UUID(int=1), UUID(int=2), UUID(int=3)),),
            2,
        ),
        (
            UUID(int=2),
            1,
            ((UUID(int=3),),),
            2,
        ),
    ),
    ids=("duplicate", "nonadvance", "oversized", "beyond-high-water"),
)
def test_phase11_audit_keyset_rejects_adversarial_typed_pages(
    monkeypatch, high_water, expected_count, pages, batch_size
):
    audit = _audit_module()
    connection = _SyntheticKeysetConnection(high_water, expected_count, pages)
    monkeypatch.setattr(audit, "PHASE11_AUDIT_BATCH_SIZE", batch_size)

    with pytest.raises(RuntimeError, match="did not advance") as raised:
        tuple(audit._phase11_id_batches(connection, "verification_results"))
    assert all(str(identifier) not in str(raised.value) for page in pages for identifier in page)


def test_phase11_audit_incomplete_keyset_scan_fails_closed(monkeypatch):
    audit = _audit_module()

    def incomplete_scan(connection, scan):
        del connection, scan
        yield (1,)
        raise RuntimeError("synthetic incomplete Phase 11 scan")

    monkeypatch.setattr(audit, "_phase11_id_batches", incomplete_scan)
    with pytest.raises(RuntimeError, match="synthetic incomplete Phase 11 scan"):
        audit._phase11_batched_table_count(object(), "verification_results")


def test_phase11_audit_keyset_high_water_cannot_end_early():
    audit = _audit_module()

    class IncompleteConnection:
        def __init__(self):
            self.page = 0

        def scalar(self, statement, parameters=None):
            del statement, parameters
            return 3

        def scalars(self, statement, parameters):
            del statement, parameters
            self.page += 1
            return iter((1, 2) if self.page == 1 else ())

    with pytest.raises(RuntimeError, match="ended before completion") as raised:
        tuple(audit._phase11_id_batches(IncompleteConnection(), "verification_results"))
    assert "3" not in str(raised.value)


def test_phase11_audit_keyset_high_water_cannot_hide_skipped_candidates():
    audit = _audit_module()

    class GappedConnection:
        def scalar(self, statement, parameters=None):
            del statement, parameters
            return 3

        def scalars(self, statement, parameters):
            del statement, parameters
            return iter((1, 3))

    with pytest.raises(RuntimeError, match="did not cover its inventory") as raised:
        tuple(audit._phase11_id_batches(GappedConnection(), "verification_results"))
    assert "3" not in str(raised.value)


def test_phase11_duplicate_matching_receipts_across_size_one_batches(
    api,
    project,
    work_payload,
    postgres_engine,
    monkeypatch,
):
    audit = _audit_module()
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Duplicate receipts across audit pages"},
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    operation_id = str(uuid4())
    completed = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete",
        json={
            "expected_version": 1,
            "checkpoint": work_payload["initial_checkpoint"],
            "completion_evidence": {
                "verification_results": [
                    {
                        "verification_type": "observation",
                        "name": "Duplicate receipt batch parity",
                        "outcome": "passed",
                        "summary": "One evidence episode has two exact receipts.",
                    }
                ]
            },
            "client_operation_id": operation_id,
        },
    )
    assert completed.status_code == 200, completed.text
    checkpoint_id = completed.json()["checkpoint"]["id"]

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("ALTER TABLE client_operations DISABLE TRIGGER USER"))
            connection.execute(
                text("DROP INDEX ix_client_operations_completion_receipt_correspondence")
            )
            connection.execute(
                text(
                    """
                    INSERT INTO client_operations (
                        project_id, client_operation_id, operation_kind,
                        request_fingerprint_version, request_fingerprint_salt,
                        request_fingerprint, response_contract_version, state,
                        response_status, response_body, mutation_applied,
                        created_at, completed_at
                    )
                    SELECT project_id, gen_random_uuid(), operation_kind,
                           request_fingerprint_version, request_fingerprint_salt,
                           request_fingerprint, response_contract_version, state,
                           response_status, response_body, mutation_applied,
                           created_at, completed_at
                    FROM client_operations
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": operation_id},
            )
            matching_operation_ids = set(
                connection.scalars(
                    text(
                        """
                        SELECT id
                        FROM client_operations
                        WHERE operation_kind = 'complete_work'
                          AND state = 'completed'
                          AND response_body #>> '{checkpoint,id}' = :checkpoint_id
                        """
                    ),
                    {"checkpoint_id": checkpoint_id},
                )
            )
            assert len(matching_operation_ids) == 2

            operation_batches = []
            original_batches = audit._phase11_id_batches

            def tracked_batches(batch_connection, scan):
                for batch in original_batches(batch_connection, scan):
                    if scan == "completion_operations":
                        operation_batches.append(batch)
                    yield batch

            monkeypatch.setattr(audit, "PHASE11_AUDIT_BATCH_SIZE", 1)
            monkeypatch.setattr(audit, "_phase11_id_batches", tracked_batches)
            expected = _set_based_phase11_receipt_correspondence_count(connection)
            actual = (
                audit._phase11_batched_receipt_evidence_correspondence_violation_count(
                    connection
                )
            )

            assert actual == expected == 1
            matching_batches = [
                batch
                for batch in operation_batches
                if any(identifier in matching_operation_ids for identifier in batch)
            ]
            assert len(matching_batches) == 2
            assert all(len(batch) == 1 for batch in matching_batches)
        finally:
            transaction.rollback()


def test_phase11_cross_work_evidence_keeps_checkpoint_global_receipt_parity(
    api,
    project,
    work_payload,
    postgres_engine,
    monkeypatch,
):
    audit = _audit_module()
    owner_created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Receipt parity checkpoint owner"},
    )
    other_created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Receipt parity corrupt evidence owner"},
    )
    assert owner_created.status_code == 201, owner_created.text
    assert other_created.status_code == 201, other_created.text
    owner = owner_created.json()["work_item"]
    other = other_created.json()["work_item"]
    completed = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{owner['id']}/complete",
        json={
            "expected_version": 1,
            "checkpoint": work_payload["initial_checkpoint"],
            "completion_evidence": {
                "verification_results": [
                    {
                        "verification_type": "observation",
                        "name": "Checkpoint-global receipt parity",
                        "outcome": "passed",
                        "summary": "The owner has the one exact completion receipt.",
                    }
                ]
            },
            "client_operation_id": str(uuid4()),
        },
    )
    assert completed.status_code == 200, completed.text
    checkpoint_id = completed.json()["checkpoint"]["id"]

    monkeypatch.setattr(audit, "PHASE11_AUDIT_BATCH_SIZE", 1)
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("ALTER TABLE verification_results DISABLE TRIGGER USER"))
            connection.execute(
                text(
                    "ALTER TABLE verification_results DROP CONSTRAINT "
                    "fk_verification_results_completion_checkpoint"
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO verification_results (
                        id, project_id, work_item_id, completion_checkpoint_id,
                        position, verification_type, name, outcome, summary,
                        command, exit_code, observed_at, observed_at_commit, created_at
                    )
                    SELECT gen_random_uuid(), project_id, CAST(:other_work_id AS uuid),
                           completion_checkpoint_id, position, verification_type,
                           name, outcome, summary, command, exit_code, observed_at,
                           observed_at_commit, created_at
                    FROM verification_results
                    WHERE work_item_id = CAST(:owner_work_id AS uuid)
                      AND completion_checkpoint_id = CAST(:checkpoint_id AS uuid)
                    """
                ),
                {
                    "checkpoint_id": checkpoint_id,
                    "other_work_id": other["id"],
                    "owner_work_id": owner["id"],
                },
            )

            expected = _set_based_phase11_receipt_correspondence_count(connection)
            actual = (
                audit._phase11_batched_receipt_evidence_correspondence_violation_count(
                    connection
                )
            )

            assert audit._phase11_batched_evidence_owner_violation_count(connection) == 1
            assert actual == expected == 0
        finally:
            transaction.rollback()


def test_phase11_reverse_receipt_plan_uses_checkpoint_first_indexes(
    api,
    project,
    work_payload,
    postgres_engine,
    monkeypatch,
):
    audit = _audit_module()
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Reverse audit index probes"},
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    operation_id = str(uuid4())
    completed = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete",
        json={
            "expected_version": 1,
            "checkpoint": work_payload["initial_checkpoint"],
            "completion_evidence": {
                "verification_results": [
                    {
                        "verification_type": "observation",
                        "name": "Indexed reverse audit",
                        "outcome": "passed",
                        "summary": "The bounded query has one exact receipt.",
                    }
                ]
            },
            "client_operation_id": operation_id,
        },
    )
    assert completed.status_code == 200, completed.text

    monkeypatch.setattr(audit, "PHASE11_AUDIT_BATCH_SIZE", 1)
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            reverse_statements = []
            original_scalar = audit._scalar

            def capture_reverse_statement(
                scalar_connection, statement, parameters=None
            ):
                if "phase11-audit-reverse-receipt-classification" in statement:
                    reverse_statements.append((statement, parameters))
                return original_scalar(scalar_connection, statement, parameters)

            monkeypatch.setattr(audit, "_scalar", capture_reverse_statement)
            assert audit._phase11_batched_evidence_receipt_violation_count(connection) == 0
            assert len(reverse_statements) == 1
            reverse_statement, reverse_parameters = reverse_statements[0]

            connection.execute(text("ALTER TABLE verification_results DISABLE TRIGGER USER"))
            connection.execute(text("ALTER TABLE artifact_references DISABLE TRIGGER USER"))
            connection.execute(text("ALTER TABLE client_operations DISABLE TRIGGER USER"))
            for table_name in ("verification_results", "artifact_references"):
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} DROP CONSTRAINT "
                        f"fk_{table_name}_work_item"
                    )
                )
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} DROP CONSTRAINT "
                        f"fk_{table_name}_completion_checkpoint"
                    )
                )

            unrelated_row_count = 4096
            connection.execute(
                text(
                    """
                    INSERT INTO verification_results (
                        id, project_id, work_item_id, completion_checkpoint_id,
                        position, verification_type, name, outcome, summary, created_at
                    )
                    SELECT gen_random_uuid(), gen_random_uuid(), gen_random_uuid(),
                           gen_random_uuid(), 0, 'observation', 'Unrelated probe row',
                           'passed', 'Unrelated to the selected checkpoint.',
                           clock_timestamp()
                    FROM generate_series(1, CAST(:row_count AS integer))
                    """
                ),
                {"row_count": unrelated_row_count},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO artifact_references (
                        id, project_id, work_item_id, completion_checkpoint_id,
                        position, artifact_type, label, reference, created_at
                    )
                    SELECT gen_random_uuid(), gen_random_uuid(), gen_random_uuid(),
                           gen_random_uuid(), 0, 'commit', 'Unrelated probe row',
                           '7ad62e4', clock_timestamp()
                    FROM generate_series(1, CAST(:row_count AS integer))
                    """
                ),
                {"row_count": unrelated_row_count},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO client_operations (
                        id, project_id, client_operation_id, operation_kind,
                        request_fingerprint_version, request_fingerprint_salt,
                        request_fingerprint, response_contract_version, state,
                        response_status, response_body, mutation_applied,
                        created_at, completed_at
                    ) OVERRIDING SYSTEM VALUE
                    SELECT -series.value, operation.project_id, gen_random_uuid(),
                           operation.operation_kind,
                           operation.request_fingerprint_version,
                           operation.request_fingerprint_salt,
                           operation.request_fingerprint,
                           operation.response_contract_version, operation.state,
                           operation.response_status,
                           pg_catalog.jsonb_set(
                               pg_catalog.jsonb_set(
                                   operation.response_body,
                                   '{checkpoint,id}',
                                   pg_catalog.to_jsonb(gen_random_uuid()::text)
                               ),
                               '{work_item,id}',
                               pg_catalog.to_jsonb(gen_random_uuid()::text)
                           ),
                           operation.mutation_applied, operation.created_at,
                           operation.completed_at
                    FROM client_operations AS operation
                    CROSS JOIN generate_series(
                        1, CAST(:row_count AS integer)
                    ) AS series(value)
                    WHERE operation.client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": operation_id, "row_count": unrelated_row_count},
            )
            connection.execute(
                text(
                    "ANALYZE verification_results, artifact_references, "
                    "client_operations"
                )
            )

            explained = connection.execute(
                text("EXPLAIN (FORMAT JSON, COSTS OFF)\n" + reverse_statement),
                reverse_parameters,
            ).scalar_one()

            def plan_nodes(node):
                yield node
                for child in node.get("Plans", ()):
                    yield from plan_nodes(child)

            nodes = list(plan_nodes(explained[0]["Plan"]))
            audited_relations = {
                "verification_results",
                "artifact_references",
                "client_operations",
            }
            assert not [
                node
                for node in nodes
                if node.get("Node Type") == "Seq Scan"
                and node.get("Relation Name") in audited_relations
            ]

            verification_index = "ix_verification_results_completion_checkpoint_id_id"
            artifact_index = "ix_artifact_references_completion_checkpoint_id_id"
            receipt_index = "ix_client_operations_completion_receipt_correspondence"

            def index_nodes(index_name):
                return [node for node in nodes if node.get("Index Name") == index_name]

            verification_nodes = index_nodes(verification_index)
            artifact_nodes = index_nodes(artifact_index)
            receipt_nodes = index_nodes(receipt_index)
            assert len(verification_nodes) >= 2
            assert artifact_nodes
            assert receipt_nodes
            assert all(
                "completion_checkpoint_id" in node.get("Index Cond", "")
                for node in verification_nodes
            )
            assert any("id <" in node.get("Index Cond", "") for node in verification_nodes)
            assert all(
                "completion_checkpoint_id" in node.get("Index Cond", "")
                for node in artifact_nodes
            )
            assert all(
                "{checkpoint,id}" in node.get("Index Cond", "")
                and "{work_item,id}" in node.get("Index Cond", "")
                for node in receipt_nodes
            )
        finally:
            transaction.rollback()
            connection.execute(
                text(
                    "ANALYZE verification_results, artifact_references, "
                    "client_operations"
                )
            )
            connection.commit()


def test_audit_phase11_receipt_rows_require_exact_bidirectional_json_and_cardinality(
    api, project, work_payload, postgres_engine
):
    audit = _audit_module()
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Audit exact completion evidence"},
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    operation_id = str(uuid4())
    completed = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete",
        json={
            "expected_version": 1,
            "checkpoint": {
                **work_payload["initial_checkpoint"],
                "prompt": "Complete with evidence for exact audit comparison.",
            },
            "completion_evidence": {
                "verification_results": [
                    {
                        "verification_type": "command",
                        "name": "Focused backend suite",
                        "outcome": "passed",
                        "summary": "The exact PostgreSQL suite passed.",
                        "command": "uv run pytest -q",
                        "exit_code": 0,
                        "observed_at": "2026-09-03T14:01:02-04:00",
                        "observed_at_commit": "7ad62e4",
                    }
                ],
                "artifact_references": [
                    {
                        "artifact_type": "commit",
                        "label": "Audited commit",
                        "reference": "7ad62e4",
                    }
                ],
            },
            "client_operation_id": operation_id,
        },
    )
    assert completed.status_code == 200, completed.text
    checkpoint_id = completed.json()["checkpoint"]["id"]

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            baseline = audit._completion_evidence_counts(connection)
            assert baseline["receipt_evidence_correspondence_violation_count"] == 0
            assert _set_based_phase11_receipt_correspondence_count(connection) == 0
            assert baseline["phase11_downgrade_blocking_count"] == 3

            changed_receipt = connection.begin_nested()
            connection.execute(
                text("DROP TRIGGER client_operation_mutation_guard ON client_operations")
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = pg_catalog.jsonb_set(
                        response_body,
                        '{completion_evidence,verification_results,0,summary}',
                        '"different summary"'::jsonb
                    )
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": operation_id},
            )
            changed_count = audit._completion_evidence_counts(connection)[
                "receipt_evidence_correspondence_violation_count"
            ]
            assert (
                changed_count
                == _set_based_phase11_receipt_correspondence_count(connection)
                > 0
            )
            changed_receipt.rollback()

            numeric_type_drift = connection.begin_nested()
            connection.execute(
                text("DROP TRIGGER client_operation_mutation_guard ON client_operations")
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = pg_catalog.jsonb_set(
                        pg_catalog.jsonb_set(
                            response_body,
                            '{completion_evidence,verification_results,0,position}',
                            '0.0'::jsonb
                        ),
                        '{completion_evidence,verification_results,0,exit_code}',
                        '0.0'::jsonb
                    )
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": operation_id},
            )
            numeric_count = audit._completion_evidence_counts(connection)[
                "receipt_evidence_correspondence_violation_count"
            ]
            assert (
                numeric_count
                == _set_based_phase11_receipt_correspondence_count(connection)
                > 0
            )
            numeric_type_drift.rollback()

            missing_receipt = connection.begin_nested()
            connection.execute(text("ALTER TABLE client_operations DISABLE TRIGGER USER"))
            connection.execute(
                text(
                    "DELETE FROM client_operations "
                    "WHERE client_operation_id = CAST(:operation_id AS uuid)"
                ),
                {"operation_id": operation_id},
            )
            duplicate_count = audit._completion_evidence_counts(connection)[
                "receipt_evidence_correspondence_violation_count"
            ]
            assert (
                duplicate_count
                == _set_based_phase11_receipt_correspondence_count(connection)
                > 0
            )
            missing_receipt.rollback()

            duplicate_receipt = connection.begin_nested()
            connection.execute(text("ALTER TABLE client_operations DISABLE TRIGGER USER"))
            connection.execute(
                text("DROP INDEX ix_client_operations_completion_receipt_correspondence")
            )
            connection.execute(
                text(
                    """
                    INSERT INTO client_operations (
                        project_id, client_operation_id, operation_kind,
                        request_fingerprint_version, request_fingerprint_salt,
                        request_fingerprint, response_contract_version, state,
                        response_status, response_body, mutation_applied,
                        created_at, completed_at
                    )
                    SELECT project_id, gen_random_uuid(), operation_kind,
                           request_fingerprint_version, request_fingerprint_salt,
                           request_fingerprint, response_contract_version, state,
                           response_status, response_body, mutation_applied,
                           created_at, completed_at
                    FROM client_operations
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": operation_id},
            )
            assert audit._completion_evidence_counts(connection)[
                "receipt_evidence_correspondence_violation_count"
            ] > 0
            duplicate_receipt.rollback()

            missing_rows = connection.begin_nested()
            connection.execute(
                text("DROP TRIGGER verification_results_immutable ON verification_results")
            )
            connection.execute(
                text("DROP TRIGGER artifact_references_immutable ON artifact_references")
            )
            connection.execute(
                text(
                    "DELETE FROM verification_results "
                    "WHERE completion_checkpoint_id = CAST(:checkpoint_id AS uuid)"
                ),
                {"checkpoint_id": checkpoint_id},
            )
            connection.execute(
                text(
                    "DELETE FROM artifact_references "
                    "WHERE completion_checkpoint_id = CAST(:checkpoint_id AS uuid)"
                ),
                {"checkpoint_id": checkpoint_id},
            )
            assert audit._completion_evidence_counts(connection)[
                "receipt_evidence_correspondence_violation_count"
            ] > 0
            missing_rows.rollback()

            null_receipt = connection.begin_nested()
            connection.execute(text("ALTER TABLE client_operations DISABLE TRIGGER USER"))
            connection.execute(
                text(
                    "ALTER TABLE client_operations "
                    "DROP CONSTRAINT ck_client_operations_state_fields_valid"
                )
            )
            connection.execute(
                text(
                    "UPDATE client_operations SET response_body = NULL "
                    "WHERE client_operation_id = CAST(:operation_id AS uuid)"
                ),
                {"operation_id": operation_id},
            )
            null_counts = audit._completion_evidence_counts(connection)
            assert null_counts["phase11_downgrade_blocking_count"] >= 3
            assert null_counts["receipt_evidence_correspondence_violation_count"] > 0
            null_receipt.rollback()
        finally:
            transaction.rollback()


@pytest.mark.parametrize(
    ("constraint_name", "receipt_tamper"),
    (
        (
            None,
            "response_body = pg_catalog.jsonb_set("
            "response_body, '{work_item,title}', '\"\"'::jsonb)",
        ),
        (None, "response_status = 201"),
        (None, "mutation_applied = false"),
        (
            "ck_client_operations_request_fingerprint_version_valid",
            "request_fingerprint_version = 2",
        ),
        (
            "ck_client_operations_response_contract_version_valid",
            "response_contract_version = 2",
        ),
        (
            "ck_client_operations_request_fingerprint_salt_length",
            "request_fingerprint_salt = pg_catalog.substring("
            "request_fingerprint_salt, 1, 31)",
        ),
        (
            "ck_client_operations_request_fingerprint_length",
            "request_fingerprint = pg_catalog.substring(request_fingerprint, 1, 31)",
        ),
    ),
    ids=(
        "body",
        "status",
        "mutation",
        "request-version",
        "response-version",
        "salt-length",
        "fingerprint-length",
    ),
)
def test_audit_and_downgrade_share_exact_malformed_receipt_corpus(
    api,
    project,
    work_payload,
    postgres_engine,
    constraint_name: str | None,
    receipt_tamper: str,
):
    audit = _audit_module()
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Shared malformed receipt corpus"},
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    operation_id = str(uuid4())
    completed = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete",
        json={
            "expected_version": 1,
            "checkpoint": {
                **work_payload["initial_checkpoint"],
                "prompt": "Complete without evidence, retaining an exact receipt.",
            },
            "client_operation_id": operation_id,
        },
    )
    assert completed.status_code == 200, completed.text

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            assert audit._phase11_downgrade_blocking_count(connection) == 0
            connection.execute(
                text(
                    "ALTER TABLE client_operations "
                    "DISABLE TRIGGER client_operation_mutation_guard"
                )
            )
            if constraint_name is not None:
                connection.execute(
                    text(f"ALTER TABLE client_operations DROP CONSTRAINT {constraint_name}")
                )
            connection.execute(
                text(
                    "UPDATE client_operations "
                    f"SET {receipt_tamper} "
                    "WHERE client_operation_id = CAST(:operation_id AS uuid)"
                ),
                {"operation_id": operation_id},
            )
            audit_count = audit._phase11_downgrade_blocking_count(connection)
            contract = audit._phase11_revision_contract()
            schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
            assert isinstance(schema, str)
            quoted_schema = connection.dialect.identifier_preparer.quote_identifier(schema)
            migration_count = contract._phase11_downgrade_blocking_count(
                connection, quoted_schema
            )
            assert audit_count == migration_count == 1
        finally:
            transaction.rollback()


def test_audit_bigint_minimum_generation_corruption_fails_closed_without_overflow(
    api, project, work_payload, postgres_engine
):
    audit = _audit_module()
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Bigint minimum generation"},
    )
    assert created.status_code == 201, created.text
    work_id = created.json()["work_item"]["id"]

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("ALTER TABLE work_items DISABLE TRIGGER USER"))
            connection.execute(
                text(
                    "ALTER TABLE work_items "
                    "DROP CONSTRAINT ck_work_items_completion_generation_range"
                )
            )
            connection.execute(
                text(
                    "UPDATE work_items SET completion_generation = -9223372036854775808 "
                    "WHERE id = CAST(:work_item_id AS uuid)"
                ),
                {"work_item_id": work_id},
            )
            counts = audit._completion_evidence_counts(connection)
            assert counts["completion_generation_violation_count"] == 1
        finally:
            transaction.rollback()
