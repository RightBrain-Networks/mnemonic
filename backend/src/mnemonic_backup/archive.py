"""Privileged PostgreSQL project backup engine used only by the dashboard service.

Artifacts contribute their durable database metadata only. Their content belongs
to a separate backup system and is never read, written, or recovered here.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from mnemonic_backup.archive_export import (
    project_counts,
    project_values,
    validate_source_snapshot,
)
from mnemonic_backup.archive_format import FORMAT, read_archive, write_stream_archive
from mnemonic_backup.archive_integrity import validate_integrity
from mnemonic_backup.archive_normalization import validate_normalized_transcripts
from mnemonic_backup.archive_recovery import validate_recovery_rows
from mnemonic_backup.archive_schema import (
    HEAD,
    TABLES,
    BackupError,
    advance_sequences,
    invalid,
    lock_tables,
    read_rows,
    reject_pending,
    replace_rows,
    reset_transcript_jobs,
    schema_signature,
    validate_foreign_keys,
    validate_identities,
    validate_ownership,
)
from mnemonic_backup.archive_spool import RestoreRows
from mnemonic_backup.schedules import BackupSchedule

DEFAULT_MAX_BYTES = 256 * 1024 * 1024


def export_project(
    engine: Engine, project_id: UUID, output: BinaryIO, *, max_bytes: int | None = None,
) -> dict:
    project = str(UUID(str(project_id)))
    try:
        with engine.begin() as connection:
            # A consistent MVCC snapshot does not block writes to any project.
            # ACCESS SHARE prevents schema changes while server-side cursors
            # stream one row at a time into the incremental compressor.
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            lock_tables(connection, restore=False)
            signature = schema_signature(connection)
            counts = project_counts(connection, project)
            if not counts["projects"]:
                raise BackupError(404, "project_not_found", "Project not found.")
            name = connection.scalar(text("SELECT name FROM projects WHERE id = :id"),
                                     {"id": project})
            header = {
                "format": FORMAT, "schema": HEAD, "schema_signature": signature,
                "project_id": project, "created_at": datetime.now(UTC).isoformat(),
                "counts": counts,
            }
            validate_source_snapshot(connection, project)
            write_stream_archive(output, project_values(connection, project, header),
                                 max_bytes=max_bytes)
            return {**header, "project_name": name}
    except DBAPIError as error:
        raise _database_error(error) from error


def restore_project(
    engine: Engine, project_id: UUID, source: BinaryIO, *, max_bytes: int = DEFAULT_MAX_BYTES,
    spool_directory: Path | None = None,
) -> dict:
    project = str(UUID(str(project_id)))
    with (RestoreRows(TABLES, directory=spool_directory) as restored,
          RestoreRows(TABLES, directory=spool_directory) as current):
        header, _ = read_archive(source, max_bytes, rows=restored)
        _validate_restore(header, restored, project)
        try:
            with engine.begin() as connection:
                lock_tables(connection, restore=True)
                signature = schema_signature(connection)
                if header["schema"] != HEAD or header["schema_signature"] != signature:
                    raise BackupError(409, "backup_schema_mismatch",
                                      "The archive and database schemas do not match.")
                read_rows(connection, project, rows=current, metadata_only=True)
                reject_pending(current)
                reset_transcript_jobs(connection, current, restored)
                replace_rows(connection, current, restored)
                validate_foreign_keys(connection)
                validate_integrity(connection)
                advance_sequences(connection)
            return {**header, "project_name": restored["projects"][0]["name"]}
        except DBAPIError as error:
            raise _database_error(error) from error


def _validate_restore(header: dict, restored: dict, project: str) -> None:
    if header["project_id"] != project:
        raise BackupError(409, "backup_project_mismatch", "The archive belongs to another project.")
    validate_ownership(restored, project)
    validate_identities(restored)
    reject_pending(restored)
    # Validate approvals against the archived generation before advancing fences.
    validate_recovery_rows(restored)
    validate_normalized_transcripts(restored)
    _validate_backup_schedules(restored)
    for index, head in enumerate(restored["project_activity_heads"]):
        head["stream_id"] = str(uuid4())
        restored["project_activity_heads"][index] = head


def _validate_backup_schedules(restored: dict) -> None:
    for row in restored["backup_settings"]:
        try:
            BackupSchedule.model_validate({key: row[key] for key in
                                           ("enabled", "time", "timezone", "retention_days")})
        except ValidationError:
            raise invalid("The backup contains invalid nightly schedule settings.") from None


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
