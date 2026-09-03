"""Operational audit coverage for exact Phase 9 catalog objects."""

import importlib.util
from pathlib import Path
from types import ModuleType

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
