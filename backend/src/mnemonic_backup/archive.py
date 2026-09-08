"""Privileged PostgreSQL project backup engine used only by the dashboard service.

Artifacts contribute their durable database metadata only. Their content belongs
to a separate backup system and is never read, written, or recovered here.
"""

from datetime import UTC, datetime
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError

from mnemonic_backup.archive_format import FORMAT, read_archive, write_archive
from mnemonic_backup.archive_integrity import validate_integrity
from mnemonic_backup.archive_schema import (
    HEAD,
    BackupError,
    advance_sequences,
    lock_tables,
    read_rows,
    reject_pending,
    replace_rows,
    schema_signature,
    validate_foreign_keys,
    validate_identities,
    validate_ownership,
)

DEFAULT_MAX_BYTES = 256 * 1024 * 1024


def export_project(
    engine: Engine, project_id: UUID, output: BinaryIO, *, max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    project = str(UUID(str(project_id)))
    try:
        with engine.begin() as connection:
            # SHARE locks quiesce writes before reading the first snapshot row and
            # serialize artifact intent transitions without accessing their files.
            lock_tables(connection, restore=False)
            signature = schema_signature(connection)
            rows = read_rows(connection, project, max_bytes=max_bytes)
            if not rows["projects"]:
                raise BackupError(404, "project_not_found", "Project not found.")
            reject_pending(rows)
            validate_identities(rows, source=True)
            header = {
                "format": FORMAT, "schema": HEAD, "schema_signature": signature,
                "project_id": project, "created_at": datetime.now(UTC).isoformat(),
                "counts": {name: len(records) for name, records in rows.items()},
            }
            write_archive(output, header, rows, max_bytes)
            return {**header, "project_name": rows["projects"][0]["name"]}
    except DBAPIError as error:
        raise _database_error(error) from error


def restore_project(
    engine: Engine, project_id: UUID, source: BinaryIO, *, max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    project = str(UUID(str(project_id)))
    header, restored = read_archive(source, max_bytes)
    if header["project_id"] != project:
        raise BackupError(409, "backup_project_mismatch", "The archive belongs to another project.")
    validate_ownership(restored, project)
    validate_identities(restored)
    reject_pending(restored)
    # A restored sequence prefix is a new stream, even when its last sequence
    # happens to equal a previously issued cursor. Other projects keep theirs.
    for head in restored["project_activity_heads"]:
        head["stream_id"] = str(uuid4())
    try:
        with engine.begin() as connection:
            lock_tables(connection, restore=True)
            signature = schema_signature(connection)
            if header["schema"] != HEAD or header["schema_signature"] != signature:
                raise BackupError(409, "backup_schema_mismatch",
                                  "The archive and database schemas do not match.")
            current = read_rows(connection, project, max_bytes=max_bytes)
            reject_pending(current)
            replace_rows(connection, current, restored)
            validate_foreign_keys(connection)
            validate_integrity(connection)
            advance_sequences(connection)
        return {**header, "project_name": restored["projects"][0]["name"]}
    except DBAPIError as error:
        raise _database_error(error) from error


def _database_error(error: DBAPIError) -> BackupError:
    sqlstate = getattr(error.orig, "sqlstate", "") or ""
    if sqlstate in {"55P03", "57014", "40P01"}:
        return BackupError(409, "backup_database_busy", "The database is busy. Retry shortly.")
    if sqlstate.startswith("23"):
        return BackupError(409, "backup_integrity_conflict",
                           "The archive conflicts with current project data or constraints.")
    if sqlstate.startswith("22"):
        return BackupError(422, "invalid_backup",
                           "The archive contains invalid typed database data.")
    return BackupError(503, "backup_database_unavailable", "The backup database is unavailable.")
