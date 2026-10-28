"""Durable backup scheduling and the private dashboard's job status views."""

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mnemonic_api.models import Project
from mnemonic_backup.archive import BackupError
from mnemonic_backup.config import BackupSettings
from mnemonic_jobs import enqueue_job
from mnemonic_jobs.models import BackgroundJob

ERROR_MESSAGES = {
    "backup_too_large": "This backup exceeded the worker's size limit. "
        "Confirm that the worker is up to date, then start a new backup. "
        "Current server backups are not constrained by browser upload limits.",
    "backup_schema_mismatch": "The backup worker and database schema do not match. "
        "Update the worker to the same release as the API and apply its migrations.",
    "backup_operations_pending": "Project operations are still pending. "
        "Allow uploads or recovery to finish before starting another backup.",
    "backup_database_busy": "The database remained busy. Try again when current operations finish.",
    "backup_database_unavailable": "The backup worker could not reach the database.",
    "backup_busy": "Another backup or restore is running. This backup will retry automatically.",
    "storage_full": "The backup volume is full. Delete old backups or increase its capacity.",
    "storage_unavailable": "The backup folder is unavailable. Check its mount, owner and "
        "permissions; the worker requires a private, writable folder.",
    "project_not_found": "This project no longer exists.",
    "backup_unavailable": "The backup worker could not access the database or backup storage. "
        "Check the worker logs and storage configuration.",
}


def schedule_backups(database: Session, settings: BackupSettings, *,
                     now: datetime | None = None) -> None:
    del settings  # Scheduling is explicitly enabled per project, not by process startup.
    now = now or datetime.now(UTC)
    rows = database.execute(text("SELECT project_id, time, timezone, updated_at "
                                 "FROM backup_settings WHERE enabled = true")).mappings()
    for row in rows:
        try:
            local = now.astimezone(ZoneInfo(row["timezone"]))
        except (ZoneInfoNotFoundError, ValueError):
            logging.getLogger(__name__).warning("Skipping backup schedule with invalid timezone")
            continue
        hour, minute = map(int, row["time"].split(":"))
        due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        updated = row["updated_at"]
        if isinstance(updated, str):
            updated = datetime.fromisoformat(updated)
        # A DST fold has one date identity. A skipped spring time runs at the
        # first scheduler tick after its nominal local time.
        if local < due or updated.astimezone(UTC) > due.astimezone(UTC):
            continue
        project = str(row["project_id"])
        enqueue_job(database, kind="backup_create",
                    dedupe_key=f"backup:nightly:{project}:{local.date().isoformat()}",
                    payload={"project_id": project})


def _project_jobs(project_id: UUID):
    return select(BackgroundJob).where(
        BackgroundJob.kind == "backup_create",
        BackgroundJob.payload["project_id"].as_string() == str(project_id))


def latest_backup_job(database: Session, project_id: UUID) -> dict | None:
    query = _project_jobs(project_id)
    job = database.scalar(query.where(BackgroundJob.status.in_(("pending", "running")))
                          .order_by(BackgroundJob.created_at.desc()).limit(1))
    if job is None:
        job = database.scalar(query.order_by(BackgroundJob.created_at.desc()).limit(1))
    return _job_response(job, project_id) if job is not None else None


def enqueue_backup(database: Session, project_id: UUID) -> dict:
    # Serialize enqueue per project so double-clicks/reloads cannot make an
    # unbounded queue. Existing queued/running work is safe to rediscover.
    database.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    active = database.scalar(_project_jobs(project_id).where(
        BackgroundJob.status.in_(("pending", "running"))).limit(1))
    if active is not None:
        return _job_response(active, project_id)
    job_id = enqueue_job(database, kind="backup_create",
                         dedupe_key=f"backup:manual:{uuid4()}",
                         payload={"project_id": str(project_id)})
    return {"project_id": str(project_id), "job_id": str(job_id), "state": "pending"}


def _job_response(job: BackgroundJob, project_id: UUID) -> dict:
    response = {"project_id": str(project_id), "job_id": str(job.id), "state": job.status}
    if job.status == "succeeded":
        response["result"] = job.result
    elif job.status == "failed":
        code = job.error_code or "backup_failed"
        response["error"] = {"code": code, "message": ERROR_MESSAGES.get(
            code, "The backup failed. Check the worker logs using this job's ID.")}
    return response


def backup_job(database: Session, project_id: UUID, job_id: UUID) -> dict:
    job = database.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id))
    if (job is None or job.kind != "backup_create"
            or job.payload.get("project_id") != str(project_id)):
        raise BackupError(404, "backup_job_not_found", "Backup job not found.")
    return _job_response(job, project_id)
