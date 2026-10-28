"""Stream a consistent PostgreSQL project snapshot into a bounded-memory archive."""

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Connection, text

from mnemonic_backup.archive_schema import (
    IDENTITY_COLUMNS,
    TABLES,
    BackupError,
    columns,
    identity_supported,
    primary_key,
    quote,
    scope,
)


def project_counts(connection: Connection, project: str) -> dict[str, int]:
    return {name: connection.scalar(text(f"SELECT count(*) FROM {quote(name)} "
                                        f"WHERE {scope(name)}"), {"project": project}) or 0
            for name in TABLES}


def project_values(connection: Connection, project: str, header: dict) -> Iterator[dict]:
    yield header
    for name in TABLES:
        selected = ",".join(map(quote, columns(name)))
        order = ",".join(map(quote, primary_key(name)))
        statement = f"SELECT row_to_json(record) FROM (SELECT {selected} FROM {quote(name)} " \
            f"WHERE {scope(name)} ORDER BY {order}) record"
        records = connection.execute(text(statement), {"project": project},
                                     execution_options={"yield_per": 1})
        try:
            for row in records.scalars():
                validate_source_row(name, row)
                yield {"table": name, "row": row}
        finally:
            records.close()


def validate_source_row(name: str, row: dict[str, Any]) -> None:
    if name in {"artifact_operations", "client_operations"} and row["state"] != "completed":
        raise BackupError(409, "backup_operations_pending",
                          "Project operations are still pending. Retry after recovery finishes.")
    if any(not identity_supported(row[column]) for table, column in IDENTITY_COLUMNS
           if table == name):
        raise BackupError(409, "backup_identity_unsupported",
                          "Database identities exceed the supported backup range.")


def validate_source_snapshot(connection: Connection, project: str) -> None:
    for name in ("artifact_operations", "client_operations"):
        if connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {quote(name)} "
                                  f"WHERE ({scope(name)}) AND state <> 'completed')"),
                             {"project": project}):
            raise BackupError(409, "backup_operations_pending",
                              "Project operations are pending. Retry after recovery finishes.")
    for name, column in IDENTITY_COLUMNS:
        if connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {quote(name)} "
                                  f"WHERE ({scope(name)}) AND "
                                  f"({quote(column)} < 1 OR {quote(column)} > 9007199254740991))"),
                             {"project": project}):
            raise BackupError(409, "backup_identity_unsupported",
                              "Database identities exceed the supported backup range.")
