"""Dashboard-only HTTP and scheduled PostgreSQL project backups."""

import asyncio
import errno
import logging
import os
import secrets
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import BinaryIO
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from mnemonic_backup.archive import BackupError, export_project, restore_project
from mnemonic_backup.config import BackupSettings
from mnemonic_backup.store import BackupStore

logger = logging.getLogger(__name__)


class BackupService:
    def __init__(self, settings: BackupSettings, engine: Engine):
        self.settings = settings
        self.engine = engine
        self.store = BackupStore(settings.root, settings.retention_count)
        self.last_success: float | None = None

    def require_project(self, project_id: UUID) -> None:
        with self.engine.connect() as connection:
            exists = connection.scalar(text("SELECT 1 FROM projects WHERE id = :id"),
                                       {"id": project_id})
        if exists is None:
            raise BackupError(404, "project_not_found", "Project not found.")

    def create_locked(self, project_id: UUID) -> dict:
        with self.store.staging(project_id) as (output, directory, partial):
            export_project(self.engine, project_id, output,
                           max_bytes=self.settings.expanded_max_bytes)
            if output.tell() > self.settings.max_bytes:
                raise BackupError(413, "backup_too_large",
                                  "The compressed backup exceeds the limit.")
            return self.store.publish(project_id, output, directory, partial)

    def create(self, project_id: UUID) -> dict:
        with self.store.operation():
            return self.create_locked(project_id)

    def cycle(self) -> None:
        with self.store.operation():
            with self.engine.connect() as connection:
                projects = list(connection.scalars(text("SELECT id FROM projects ORDER BY id")))
            failed = False
            for project_id in projects:
                try:
                    self.create_locked(project_id)
                except (BackupError, OSError, SQLAlchemyError) as error:
                    failed = True
                    logger.warning("Scheduled project backup failed (%s)", type(error).__name__)
            if failed:
                raise BackupError(503, "backup_incomplete", "Some scheduled backups failed.")
            self.last_success = time.monotonic()

    async def schedule(self) -> None:
        while True:
            delay = self.settings.interval_seconds
            try:
                await run_in_threadpool(self.cycle)
            except (BackupError, OSError, SQLAlchemyError) as error:
                logger.warning("Scheduled backups unavailable (%s)", type(error).__name__)
                delay = 60
            await asyncio.sleep(delay)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}},
                        headers={"Cache-Control": "no-store"})


def _download(content: BinaryIO) -> Iterator[bytes]:
    with content:
        while chunk := content.read(1024 * 1024):
            yield chunk


def _validate_upload(request: Request, project_id: UUID, limit: int) -> None:
    if request.headers.get("x-confirm-project") != str(project_id):
        raise BackupError(400, "confirmation_required", "Confirm the project before restoring.")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"application/octet-stream", "application/x-bzip2"}:
        raise BackupError(415, "invalid_archive", "Upload a bzip2 project backup file.")
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isdecimal():
            raise BackupError(400, "invalid_length", "Invalid upload length.")
        if int(length) > limit:
            raise BackupError(413, "backup_too_large", "The upload exceeds the backup size limit.")


async def _receive_upload(request: Request, output: BinaryIO, limit: int) -> None:
    total = 0
    prefix = b""
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise BackupError(413, "backup_too_large", "The upload exceeds the backup size limit.")
        if len(prefix) < 3:
            chunk = prefix + chunk
            prefix = chunk[:3]
            if len(prefix) < 3:
                continue
            if prefix != b"BZh":
                raise BackupError(400, "invalid_archive", "Upload a bzip2 project backup file.")
        await run_in_threadpool(output.write, chunk)
    if total < 3:
        raise BackupError(400, "invalid_archive", "The backup file is empty or truncated.")
    output.flush()
    output.seek(0)


def create_app(settings: BackupSettings | None = None, *, engine: Engine | None = None,
               scheduled: bool = True) -> FastAPI:
    settings = settings or BackupSettings()  # type: ignore[call-arg]
    owns_engine = engine is None
    engine = engine or create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True,
                                     hide_parameters=True, connect_args={"connect_timeout": 5})
    service = BackupService(settings, engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(service.schedule()) if scheduled else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if owns_engine:
                engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.backup_service = service
    _install_handlers(app, settings)
    _install_routes(app, service)
    return app


def _install_handlers(app: FastAPI, settings: BackupSettings) -> None:
    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path != "/healthz":
            supplied = request.headers.get("authorization", "")
            expected = "Bearer " + settings.token.get_secret_value()
            if not secrets.compare_digest(supplied.encode(), expected.encode()):
                return _error(401, "unauthorized", "Authentication required.")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(BackupError)
    async def backup_error(_request: Request, error: BackupError):
        return _error(error.status, error.code, error.message)

    @app.exception_handler(OSError)
    async def storage_error(_request: Request, error: OSError):
        if error.errno == errno.ENOSPC:
            return _error(507, "storage_full", "Backup storage is full.")
        return _error(503, "storage_unavailable", "Backup storage is unavailable.")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, _error_value: SQLAlchemyError):
        return _error(503, "database_unavailable",
                      "The database is busy or unavailable. Refresh before retrying.")


def _install_routes(app: FastAPI, service: BackupService) -> None:
    @app.get("/healthz")
    def health():
        age = None if service.last_success is None else time.monotonic() - service.last_success
        if age is None or age > service.settings.interval_seconds + 300:
            return _error(503, "backup_unhealthy", "Scheduled backups have not completed recently.")
        return {"status": "ok"}

    @app.get("/projects/{project_id}/backups")
    def list_backups(project_id: UUID):
        service.require_project(project_id)
        return {"project_id": str(project_id), "retention_count": service.settings.retention_count,
                "backups": service.store.list_archives(project_id)}

    @app.post("/projects/{project_id}/backups", status_code=201)
    def create_backup(project_id: UUID):
        return service.create(project_id)

    @app.get("/projects/{project_id}/backups/{filename}")
    def download_backup(project_id: UUID, filename: str):
        content = service.store.open(project_id, filename)
        return StreamingResponse(_download(content), media_type="application/x-bzip2", headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(os.fstat(content.fileno()).st_size),
        })

    @app.post("/projects/{project_id}/restore")
    async def restore_backup(project_id: UUID, request: Request):
        _validate_upload(request, project_id, service.settings.max_bytes)
        with service.store.operation(), service.store.staging(project_id) as (output, _, _name):
            await _receive_upload(request, output, service.settings.max_bytes)
            await run_in_threadpool(restore_project, service.engine, project_id, output,
                                    max_bytes=service.settings.expanded_max_bytes)
        return {"project_id": str(project_id), "restored": True}
