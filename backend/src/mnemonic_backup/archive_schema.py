"""Frozen project ownership and PostgreSQL data-only restore primitives.

Archive identity columns accept only JSON integers from 1 through 2**53 - 1.
This interoperable integer range leaves PostgreSQL bigint sequences more than
9 quintillion subsequent values; uploaded data cannot exhaust a global sequence.
"""

import hashlib
import json
from typing import Any

from sqlalchemy import Connection, text

from mnemonic_api.models import Base

HEAD = "0033_transcript_imports"
TABLES = tuple(sorted(Base.metadata.tables))
MAX_ARCHIVE_IDENTITY = 2**53 - 1
IDENTITY_COLUMNS = tuple(
    (name, column.name) for name in TABLES for column in Base.metadata.tables[name].c
    if column.identity is not None
)
CHILD_OWNERS = {
    "transcripts": ("work_item_id", "work_items", "id"),
    "checkpoints": ("work_item_id", "work_items", "id"),
    "work_item_embeddings": ("work_item_id", "work_items", "id"),
    "work_leases": ("work_item_id", "work_items", "id"),
    "work_item_moves": ("work_item_id", "work_items", "id"),
    "work_report_provenance_heads": ("work_item_id", "work_items", "id"),
    "code_review_findings": ("result_id", "code_review_results", "id"),
    "artifact_work_links": ("artifact_id", "artifacts", "id"),
    "artifact_revisions": ("artifact_id", "artifacts", "id"),
    "artifact_extractions": ("artifact_id", "artifacts", "id"),
    "artifact_audit": ("artifact_id", "artifacts", "id"),
    "artifact_access_approvals": ("artifact_id", "artifacts", "id"),
}


class BackupError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def invalid(message: str = "The backup archive is invalid or incomplete.") -> BackupError:
    return BackupError(422, "invalid_backup", message)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def columns(name: str) -> list[str]:
    return [column.name for column in Base.metadata.tables[name].c if column.computed is None]


def primary_key(name: str) -> list[str]:
    return [column.name for column in Base.metadata.tables[name].primary_key]


def schema_signature(connection: Connection) -> str:
    if list(connection.scalars(text("SELECT version_num FROM alembic_version"))) != [HEAD]:
        raise BackupError(409, "backup_schema_mismatch", "The database schema is unsupported.")
    catalog = connection.execute(text("""
        SELECT table_name, column_name, udt_name, is_nullable, is_identity,
               is_generated, character_maximum_length
        FROM information_schema.columns WHERE table_schema=current_schema()
          AND table_name <> 'alembic_version'
        ORDER BY table_name, ordinal_position
    """)).all()
    if {row[0] for row in catalog} != set(TABLES):
        raise BackupError(409, "backup_schema_mismatch", "The database table catalog changed.")
    return hashlib.sha256(canonical([list(row) for row in catalog])).hexdigest()


def lock_tables(connection: Connection, *, restore: bool) -> None:
    connection.execute(text("SET LOCAL lock_timeout = '10s'"))
    connection.execute(text("SET LOCAL statement_timeout = '300s'"))
    connection.execute(text("SET LOCAL timezone = 'UTC'"))
    connection.execute(text("SET LOCAL bytea_output = 'hex'"))
    mode = "ACCESS EXCLUSIVE" if restore else "SHARE"
    connection.execute(text(f"LOCK TABLE {','.join(map(quote, TABLES))} IN {mode} MODE"))


def scope(name: str) -> str:
    if name == "projects":
        return "id = CAST(:project AS uuid)"
    if name == "transcripts":
        return "(import_project_id = CAST(:project AS uuid) OR work_item_id IN " \
            "(SELECT id FROM work_items WHERE " + scope("work_items") + "))"
    if name in CHILD_OWNERS:
        column, parent, key = CHILD_OWNERS[name]
        return f"{quote(column)} IN (SELECT {quote(key)} FROM {quote(parent)} WHERE " \
            + scope(parent) + ")"
    if "project_id" not in Base.metadata.tables[name].c:
        raise RuntimeError(f"Project ownership is undefined for {name}")
    return "project_id = CAST(:project AS uuid)"


def read_rows(
    connection: Connection, project: str, *, max_bytes: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    rows = {}
    size = 0
    for name in TABLES:
        selected = ",".join(map(quote, columns(name)))
        order = ",".join(map(quote, primary_key(name)))
        statement = f"SELECT row_to_json(record) FROM (SELECT {selected} FROM {quote(name)} " \
            f"WHERE {scope(name)} ORDER BY {order}) record"
        rows[name] = []
        result = connection.execute(text(statement), {"project": project},
                                    execution_options={"yield_per": 10})
        for row in result.scalars():
            size += len(canonical(row))
            if max_bytes is not None and size > max_bytes:
                raise BackupError(413, "backup_too_large",
                                  "Project data exceeds the configured backup byte limit.")
            rows[name].append(row)
    return rows


def validate_ownership(rows: dict[str, list[dict[str, Any]]], project: str) -> None:
    if set(rows) != set(TABLES) or len(rows["projects"]) != 1:
        raise invalid()
    for name, records in rows.items():
        if any(not isinstance(row, dict) or set(row) != set(columns(name)) for row in records):
            raise invalid("The backup table columns do not match this release.")
    owners = {name: {row[key] for row in rows[parent] if isinstance(row[key], str)}
              for name, (_, parent, key) in CHILD_OWNERS.items()}
    for name, records in rows.items():
        seen = set()
        for row in records:
            key = canonical([row[column] for column in primary_key(name)])
            if key in seen or not _owned(name, row, owners, project):
                raise invalid("The backup contains duplicate or foreign project data.")
            seen.add(key)


def _owned(name: str, row: dict, owners: dict, project: str) -> bool:
    if name == "projects":
        return row["id"] == project
    if name == "transcripts" and row["kind"] == "imported":
        return (row["import_project_id"] == project and row["work_item_id"] is None
                and row["lease_generation_id"] is None and row["session_id"] is None)
    if name == "transcripts" and row["import_project_id"] is not None:
        return False
    if name in CHILD_OWNERS:
        column = CHILD_OWNERS[name][0]
        return isinstance(row[column], str) and row[column] in owners[name]
    return row["project_id"] == project


def reject_pending(rows: dict[str, list[dict[str, Any]]]) -> None:
    if any(row["state"] != "completed"
           for name in ("artifact_operations", "client_operations") for row in rows[name]):
        raise BackupError(409, "backup_operations_pending",
                          "Project operations are still pending. Retry after recovery finishes.")


def identity_supported(value: Any) -> bool:
    return type(value) is int and 1 <= value <= MAX_ARCHIVE_IDENTITY


def validate_identities(rows: dict[str, list[dict[str, Any]]], *, source: bool = False) -> None:
    if any(not identity_supported(row[column]) for name, column in IDENTITY_COLUMNS
           for row in rows[name]):
        message = f"Identity values must be JSON integers between 1 and {MAX_ARCHIVE_IDENTITY}."
        if source:
            raise BackupError(409, "backup_identity_unsupported", message)
        raise invalid(message)


def replace_rows(connection: Connection, current: dict, restored: dict) -> None:
    # Restore is privileged data loading. CHECK/NOT NULL/UNIQUE constraints still run;
    # FKs and durable semantic witnesses are explicitly checked before commit.
    connection.execute(text("SET LOCAL session_replication_role = 'replica'"))
    for name in TABLES:
        keys = primary_key(name)
        predicate = " AND ".join(f"s.{quote(key)} = r.{quote(key)}" for key in keys)
        for row in current[name]:
            connection.execute(text(
                f"DELETE FROM {quote(name)} s USING jsonb_populate_record("
                f"NULL::{quote(name)}, CAST(:row AS jsonb)) r WHERE {predicate}"
            ), {"row": canonical(row).decode()})
    for name in TABLES:
        selected = ",".join(map(quote, columns(name)))
        query = text(f"INSERT INTO {quote(name)} ({selected}) OVERRIDING SYSTEM VALUE "
                     f"SELECT {selected} FROM jsonb_populate_record(NULL::{quote(name)}, "
                     "CAST(:row AS jsonb))")
        if restored[name]:
            connection.execute(query, [{"row": canonical(row).decode()}
                                       for row in restored[name]])
    _invalidate_restored_approvals(connection, restored)
    connection.execute(text("SET LOCAL session_replication_role = 'origin'"))


def _invalidate_restored_approvals(connection: Connection, restored: dict) -> None:
    # A snapshot can predate consumption. Restoring it must never revive an old
    # permission assertion, even while its original five-minute expiry is future.
    # Keep the immutable scope, audit history, and existing consumption timestamps.
    token_hashes = [row["token_hash"] for row in restored["artifact_access_approvals"]]
    if token_hashes:
        connection.execute(text("""
            UPDATE artifact_access_approvals
            SET consumed_at = greatest(clock_timestamp(), created_at)
            WHERE token_hash = ANY(CAST(:hashes AS text[])) AND consumed_at IS NULL
        """), {"hashes": token_hashes})


def validate_foreign_keys(connection: Connection) -> None:
    constraints = connection.execute(text("""
        SELECT source.relname, target.relname,
          array_agg(source_col.attname ORDER BY keys.position),
          array_agg(target_col.attname ORDER BY keys.position)
        FROM pg_constraint constraint_row
        JOIN pg_class source ON source.oid=constraint_row.conrelid
        JOIN pg_namespace namespace ON namespace.oid=source.relnamespace
        JOIN pg_class target ON target.oid=constraint_row.confrelid
        CROSS JOIN LATERAL unnest(constraint_row.conkey, constraint_row.confkey)
            WITH ORDINALITY keys(source_number,target_number,position)
        JOIN pg_attribute source_col ON source_col.attrelid=source.oid
          AND source_col.attnum=keys.source_number
        JOIN pg_attribute target_col ON target_col.attrelid=target.oid
          AND target_col.attnum=keys.target_number
        WHERE namespace.nspname=current_schema() AND constraint_row.contype='f'
        GROUP BY constraint_row.oid, source.relname, target.relname
    """)).all()
    for source, target, local, remote in constraints:
        nonnull = " AND ".join(f"s.{quote(column)} IS NOT NULL" for column in local)
        match = " AND ".join(f"s.{quote(left)}=t.{quote(right)}"
                             for left, right in zip(local, remote, strict=True))
        query = f"SELECT EXISTS(SELECT 1 FROM {quote(source)} s WHERE {nonnull} AND " \
            f"NOT EXISTS(SELECT 1 FROM {quote(target)} t WHERE {match}))"
        if connection.scalar(text(query)):
            raise BackupError(409, "backup_dependency_conflict",
                              "Restore conflicts with current cross-project references.")


def advance_sequences(connection: Connection) -> None:
    # Validate the entire plan before the first nontransactional sequence change.
    # A late-table invalid maximum must not advance an earlier global sequence.
    plan = _sequence_plan(connection)
    for sequence, maximum in plan:
        # Never lower a global sequence, including on restore rollback, or collide
        # with another project's future events. All restored identities were also
        # checked before any database mutations began.
        connection.execute(text(
            "SELECT setval(CAST(:sequence AS regclass), greatest(:maximum, "
            "pg_sequence_last_value(CAST(:sequence AS regclass))), true)"
        ), {"sequence": sequence, "maximum": maximum})


def _sequence_plan(connection: Connection) -> list[tuple[str, int]]:
    plan = []
    for name, column in IDENTITY_COLUMNS:
        sequence = connection.scalar(text("SELECT pg_get_serial_sequence(:table,:column)"),
                                     {"table": name, "column": column})
        maximum = connection.scalar(text(f"SELECT max({quote(column)}) FROM {quote(name)}"))
        if maximum is not None and not identity_supported(maximum):
            raise BackupError(409, "backup_identity_unsupported",
                              "Database identities exceed the supported backup range.")
        if sequence and maximum is not None:
            plan.append((sequence, maximum))
    return plan
