"""Bounded metadata-only health checks, observed by the actual API and copy worker."""

import os
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import ArtifactSearchIndex
from mnemonic_api.config import Settings
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Transcript, TranscriptWorkerHealth, WorkItem
from mnemonic_api.services.transcripts import transcript_project_id
from mnemonic_api.services.work_items import require_project
from mnemonic_api.transcript_access import (
    RECHECK_SECONDS,
    RECOVERABLE_COPY_ERRORS,
    TranscriptAccessError,
    access_error,
    access_instruction,
)
from mnemonic_api.transcript_recovery_sources import effective_copy_path
from mnemonic_api.transcript_storage import _open_source
from mnemonic_api.transcript_usage import TranscriptUsage, database_usage, directory_usage

MAX_WARNINGS = 50
HEALTH_INTERVAL_SECONDS = 30
HEALTH_STALE_SECONDS = 120


class TranscriptWarning(BaseModel):
    code: str
    path: str | None = None
    service: str
    message: str
    action: str
    affected: int = 0
    retry_at: datetime | None = None
    uid: int | None = None
    gid: int | None = None
    owner_uid: int | None = None
    owner_gid: int | None = None
    mode: str | None = None


class TranscriptHealthRead(BaseModel):
    project_id: UUID
    checked_at: datetime
    worker_checked_at: datetime | None
    warnings: list[TranscriptWarning]
    warnings_omitted: int
    affected_transcripts: int
    recheck_seconds: int = RECHECK_SECONDS
    storage: TranscriptUsage | None = None


def warning_for(code: str, details: dict, service: str, *, affected: int = 0,
                retry_at: datetime | None = None) -> TranscriptWarning:
    return TranscriptWarning(
        code=code, service=service,
        message=code.removeprefix("transcript_").replace("_", " ").capitalize(),
        action=access_instruction(code, details), affected=affected, retry_at=retry_at,
        **{key: details.get(key) for key in (
            "path", "uid", "gid", "owner_uid", "owner_gid", "mode")},
    )


def probe_source(path: Path, roots: list[Path]) -> dict | None:
    try:
        descriptor = _open_source(str(path), roots, directory=True)
        os.close(descriptor)
    except TranscriptAccessError as error:
        return {"code": error.code, "details": error.details}
    except OSError as error:
        failure = access_error(error, str(path), operation="list_directory")
        return {"code": failure.code, "details": failure.details}
    return None


def _storage_write_probe(descriptor: int) -> None:
    name = ".health-" + uuid4().hex
    file = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                   0o600, dir_fd=descriptor)
    try:
        os.write(file, b"health\n")
        os.fsync(file)
    finally:
        os.close(file)
        os.unlink(name, dir_fd=descriptor)
    os.fsync(descriptor)


def probe_storage(path: Path) -> dict | None:
    descriptor = None
    try:
        descriptor = _open_source(str(path), [path], directory=True)
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise TranscriptAccessError("transcript_copy_unavailable", str(path),
                                        operation="write_storage", info=info)
        _storage_write_probe(descriptor)
    except TranscriptAccessError as error:
        return {"code": error.code, "details": error.details | {"operation": "write_storage"}}
    except OSError as error:
        failure = access_error(error, str(path), operation="write_storage")
        return {"code": failure.code, "details": failure.details}
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return None


def environment_report(settings: Settings, *, worker: bool) -> dict:
    roots = settings.transcript_allowed_roots
    issues = [issue for root in roots[:100] if (issue := probe_source(root, roots))]
    if worker and (issue := probe_storage(settings.transcript_root)):
        issues.append(issue)
    return {"uid": os.geteuid(), "gid": os.getegid(), "roots": [str(root) for root in roots],
            "storage": str(settings.transcript_root), "issues": issues}


class TranscriptHealthReporter:
    def __init__(self) -> None:
        self.worker_id = uuid4()
        self.next_check = 0.0

    def publish(self, database: Session, settings: Settings) -> None:
        if monotonic() < self.next_check:
            return
        report = environment_report(settings, worker=True)
        report["native_storage"] = directory_usage(settings.transcript_root).model_dump(mode="json")
        now = database.execute(select(func.clock_timestamp())).scalar_one()
        statement = insert(TranscriptWorkerHealth).values(
            worker_id=self.worker_id, checked_at=now, report=report)
        database.execute(statement.on_conflict_do_update(
            index_elements=[TranscriptWorkerHealth.worker_id],
            set_={"checked_at": now, "report": report}))
        database.execute(delete(TranscriptWorkerHealth).where(
            TranscriptWorkerHealth.checked_at < now - timedelta(days=1)))
        self.next_check = monotonic() + HEALTH_INTERVAL_SECONDS


def _worker_warnings(database: Session, api_report: dict, now: datetime):
    workers = database.scalars(select(TranscriptWorkerHealth).order_by(
        TranscriptWorkerHealth.checked_at.desc()).limit(20)).all()
    last_check = workers[0].checked_at if workers else None
    fresh = [row for row in workers if
             (now - row.checked_at).total_seconds() <= HEALTH_STALE_SECONDS]
    warnings = []
    if not fresh:
        warnings.append(TranscriptWarning(
            code="transcript_worker_unavailable", service="worker",
            message="Transcript worker health is unavailable or stale.",
            action="Check that the worker and RabbitMQ are running the current release. "
            "Copy progress cannot be confirmed until the worker reports again."))
    for row in fresh:
        warnings.extend(warning_for(item["code"], item["details"], "worker")
                        for item in row.report["issues"])
        if (set(row.report["roots"]) != set(api_report["roots"])
                or row.report["uid"] != api_report["uid"]
                or row.report["gid"] != api_report["gid"]):
            warnings.append(TranscriptWarning(
                code="transcript_worker_configuration_mismatch", service="worker",
                message="API and worker use different source roots or service identities.",
                action=f"Rebuild and recreate both services with the same source settings. "
                f"Worker UID/GID: {row.report['uid']}:{row.report['gid']}; "
                f"worker roots: {', '.join(row.report['roots']) or '(none)'}."))
    return warnings, last_check


def _copy_warnings(database: Session, project_id: UUID):
    source = func.coalesce(Transcript.copy_error_details["path"].astext, effective_copy_path())
    grouped = (select(Transcript.copy_error_code, Transcript.copy_error_details, source,
                      func.count(), func.min(Transcript.copy_next_attempt_at))
        .outerjoin(WorkItem, WorkItem.id == Transcript.work_item_id)
        .where(transcript_project_id() == project_id, Transcript.copy_status != "ready",
               Transcript.copy_error_code.is_not(None))
        .group_by(Transcript.copy_error_code, Transcript.copy_error_details, source)).subquery()
    # One statement keeps counts consistent with rows while copies finish concurrently.
    rows = database.execute(select(grouped, func.count().over(), func.sum(grouped.c[3]).over())
                            .order_by(grouped.c[0], grouped.c[2]).limit(MAX_WARNINGS)).all()
    warnings = [warning_for(code, details or {"path": path}, "worker", affected=count,
                retry_at=due if code in RECOVERABLE_COPY_ERRORS else None)
                for code, details, path, count, due, _, _ in rows]
    return warnings, rows[0][-2] - len(rows) if rows else 0, rows[0][-1] if rows else 0


def transcript_health(database: Session, project_id: UUID,
                      settings: Settings,
                      index: ArtifactSearchIndex | None = None) -> TranscriptHealthRead:
    require_project(database, project_id)
    report = environment_report(settings, worker=False)
    now = database.execute(select(func.clock_timestamp())).scalar_one()
    warnings = [warning_for(item["code"], item["details"], "api") for item in report["issues"]]
    workers, checked = _worker_warnings(database, report, now)
    copies, omitted, affected = _copy_warnings(database, project_id)
    if not report["roots"]:
        warnings.append(TranscriptWarning(code="transcript_sources_unconfigured", service="api",
            message="No shared transcript source directories are configured.",
            action="Configure dedicated transcript source directories and their read-only mounts "
            "for both API and worker. Other machines need an explicitly shared filesystem."))
    warnings.extend(index_warnings(index, settings))
    # Deduplicate identical findings from multiple workers, not distinct paths.
    unique = {item.model_dump_json(): item for item in [*warnings, *workers, *copies]}
    worker = database.scalar(select(TranscriptWorkerHealth).order_by(
        TranscriptWorkerHealth.checked_at.desc()).limit(1))
    storage = TranscriptUsage(
        transcripts=worker.report.get("native_storage") if worker else None,
        index=directory_usage(settings.transcript_index_dir)
            if settings.transcript_index_dir is not None else None,
        database_bytes=database_usage(database))
    return TranscriptHealthRead(project_id=project_id, checked_at=now, worker_checked_at=checked,
        warnings=list(unique.values())[:MAX_WARNINGS],
        warnings_omitted=omitted + max(0, len(unique) - MAX_WARNINGS),
        affected_transcripts=affected, storage=storage)


def index_warnings(index: ArtifactSearchIndex | None,
                   settings: Settings) -> list[TranscriptWarning]:
    if settings.transcript_index_dir is None:
        return []
    issue = probe_storage(settings.transcript_index_dir)
    if issue is not None:
        details = issue["details"]
        warning = warning_for(issue["code"], details, "api")
        warning.action = (f"Transcript search index: API UID {os.geteuid()} / GID {os.getegid()} "
                          "needs an existing private directory owned by this UID, mode 0700, "
                          "mounted read-write. Check free space and quota. "
                          "Fix this path and refresh; retained transcripts remain intact.")
        return [warning]
    if index is not None:
        try:
            index.start()
        except ApplicationError as error:
            if not isinstance(error.detail, dict) or (
                error.detail.get("code") != "transcript_search_busy"
            ):
                return [TranscriptWarning(code="transcript_index_unavailable", service="api",
                    path=str(settings.transcript_index_dir),
                    message="Transcript search index could not be opened.",
                    action="Check private index ownership, permissions and available space. "
                    "Only one API process may own this index directory. Refresh after fixing it.")]
    return []
