"""Shared RabbitMQ worker and the dashboard's private backup transport."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from functools import partial
from urllib.parse import urlsplit

from fastapi import FastAPI
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session

from mnemonic_api.artifact_tika import TikaExtractor
from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine, build_session_factory
from mnemonic_api.transcript_copies import TranscriptStorage
from mnemonic_api.transcript_job_queue import (
    enqueue_transcript_jobs,
    handle_transcript_copy,
    handle_transcript_index,
)
from mnemonic_backup.config import BackupSettings
from mnemonic_backup.jobs import schedule_backups
from mnemonic_backup.service import create_app as create_backup_app
from mnemonic_jobs.runtime import WorkerState, run_worker


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MNEMONIC_", extra="ignore", hide_input_in_errors=True,
    )

    rabbitmq_url: SecretStr
    job_queue: str = Field(default="mnemonic.jobs", pattern=r"^[a-zA-Z0-9_.-]{1,120}$")

    @field_validator("rabbitmq_url")
    @classmethod
    def broker_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme not in {"amqp", "amqps"} or not parsed.hostname or not parsed.username:
            raise ValueError("RabbitMQ must use an authenticated AMQP URL")
        return value


def create_app() -> FastAPI:
    settings = Settings()
    backups = BackupSettings()  # type: ignore[call-arg]
    worker = WorkerSettings()  # type: ignore[call-arg]
    engine = build_engine(settings)
    factory = build_session_factory(engine, work_summary_max_chars=settings.work_summary_max_chars)
    state = WorkerState()

    def schedule(database: Session) -> None:
        enqueue_transcript_jobs(database, settings)
        schedule_backups(database, backups)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Only unlocked abandoned partial files older than 24 hours are removed;
        # final immutable copies and concurrent writers are never selected.
        await asyncio.to_thread(
            TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes)
            .cleanup_staging, set(),
        )
        task = asyncio.create_task(run_worker(
            factory, worker.rabbitmq_url.get_secret_value(), {
                "transcript_copy": partial(handle_transcript_copy, factory, settings),
                "transcript_index": partial(
                    handle_transcript_index, factory, settings, TikaExtractor(settings),
                ),
                "backup_create": app.state.backup_service.handle_job,
            }, schedule, state=state, queue_name=worker.job_queue,
        ))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            engine.dispose()

    return create_backup_app(backups, engine=engine, worker_lifespan=lifespan,
                             health_check=lambda: state.healthy)
