"""Durable backup scheduling and the private dashboard's job status views."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mnemonic_backup.archive import BackupError
from mnemonic_backup.config import BackupSettings
from mnemonic_jobs import enqueue_job
from mnemonic_jobs.models import BackgroundJob


def schedule_backups(database: Session, settings: BackupSettings) -> None:
    # Epoch slots are persistent dedupe identities; restarting or running another
    # worker does not enqueue a fresh startup backup for the same interval.
    slot = int(datetime.now(UTC).timestamp()) // settings.interval_seconds
    projects = database.scalars(text("SELECT id FROM projects ORDER BY id"))
    for project in projects:
        enqueue_job(database, kind="backup_create",
                    dedupe_key=f"backup:scheduled:{project}:{settings.interval_seconds}:{slot}",
                    payload={"project_id": str(project)})


def enqueue_backup(database: Session, project_id: UUID) -> dict:
    job_id = enqueue_job(database, kind="backup_create",
                         dedupe_key=f"backup:manual:{uuid4()}",
                         payload={"project_id": str(project_id)})
    return {"project_id": str(project_id), "job_id": str(job_id), "state": "pending"}


def backup_job(database: Session, project_id: UUID, job_id: UUID) -> dict:
    job = database.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id))
    if (job is None or job.kind != "backup_create"
            or job.payload.get("project_id") != str(project_id)):
        raise BackupError(404, "backup_job_not_found", "Backup job not found.")
    response = {"project_id": str(project_id), "job_id": str(job.id), "state": job.status}
    if job.status == "succeeded":
        response["result"] = job.result
    elif job.status == "failed":
        response["error"] = {"code": job.error_code or "backup_failed",
                             "message": "The backup could not be completed. Try a new backup."}
    return response
