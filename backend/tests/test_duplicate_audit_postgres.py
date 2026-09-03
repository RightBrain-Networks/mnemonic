"""Operational audit coverage for exact Phase 9 and Phase 10 catalog objects."""

import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.postgres

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _audit_module() -> ModuleType:
    path = REPOSITORY_ROOT / "scripts" / "audit_duplicate_handling.py"
    spec = importlib.util.spec_from_file_location("phase9_duplicate_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
                "duplicate_title_key_contract_failures": 1
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
            assert baseline["required_table_count"] == 1
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
