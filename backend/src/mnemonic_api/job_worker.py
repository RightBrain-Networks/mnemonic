"""Shared RabbitMQ worker and the dashboard's private backup transport."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from functools import partial
from urllib.parse import urlsplit

from fastapi import FastAPI
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session

from mnemonic_api.artifact_passage_jobs import (
    enqueue_artifact_passage_jobs,
    handle_artifact_embedding,
)
from mnemonic_api.artifact_tokenizer import passage_tokenizer
from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine, build_session_factory
from mnemonic_api.duplicate_embedding_jobs import (
    enqueue_duplicate_embedding_jobs,
    handle_duplicate_embedding,
)
from mnemonic_api.semantic import FastembedEmbedder
from mnemonic_api.transcript_copies import TranscriptStorage
from mnemonic_api.transcript_health import TranscriptHealthReporter
from mnemonic_api.transcript_job_queue import (
    enqueue_transcript_jobs,
    handle_transcript_copy,
    handle_transcript_index,
)
from mnemonic_api.transcript_upgrades import refresh_outdated_normalizations
from mnemonic_backup.config import BackupSettings
from mnemonic_backup.jobs import schedule_backups
from mnemonic_backup.service import create_app as create_backup_app
from mnemonic_jobs.ledger import RetryJob
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


def schedule_jobs(database: Session, settings: Settings, backups: BackupSettings, *,
                  embedder, health: TranscriptHealthReporter | None = None) -> None:
    # The worker supplies a fresh Session without a checked-out connection here.
    # Model/tokenizer preparation must happen before the first scheduling SQL.
    tokenizer = None
    if settings.artifact_max_bytes > 0:
        try:
            tokenizer = passage_tokenizer(embedder)
        except Exception:
            pass  # Other job kinds remain available if local model loading fails.
    refresh_outdated_normalizations(database, settings)
    if health is not None:
        health.publish(database, settings)
    enqueue_transcript_jobs(database, settings)
    if tokenizer is not None:
        enqueue_artifact_passage_jobs(database, tokenizer)
    enqueue_duplicate_embedding_jobs(database)
    schedule_backups(database, backups)


def _artifact_embedding_job(settings, factory, embedder, context):
    if settings.artifact_max_bytes <= 0:
        raise RetryJob("artifact_library_disabled", 60, consume_attempt=False)
    return handle_artifact_embedding(factory, embedder, context)


def create_app() -> FastAPI:
    settings = Settings()
    backups = BackupSettings()  # type: ignore[call-arg]
    worker = WorkerSettings()  # type: ignore[call-arg]
    engine = build_engine(settings)
    factory = build_session_factory(engine, work_summary_max_chars=settings.work_summary_max_chars)
    state = WorkerState()
    embedder = FastembedEmbedder()
    health = TranscriptHealthReporter()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Only unlocked abandoned partial files older than 24 hours are removed;
        # final immutable copies and concurrent writers are never selected.
        try:
            await asyncio.to_thread(
                TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes)
                .cleanup_staging, set(),
            )
        except (OSError, ValueError):
            logging.getLogger(__name__).warning("Transcript staging cleanup unavailable; "
                                               "worker health will report storage access")
        task = asyncio.create_task(run_worker(
            factory, worker.rabbitmq_url.get_secret_value(), {
                "transcript_copy": partial(handle_transcript_copy, factory, settings),
                "transcript_index": partial(
                    handle_transcript_index, factory, settings,
                ),
                "backup_create": app.state.backup_service.handle_job,
                "duplicate_embed": partial(handle_duplicate_embedding, factory, embedder),
                "artifact_embed": partial(
                    _artifact_embedding_job, settings, factory, embedder),
            }, partial(schedule_jobs, settings=settings, backups=backups,
                       embedder=embedder, health=health),
            state=state, queue_name=worker.job_queue,
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
