"""Resume unattended intents and remove stale, unreferenced upload staging files."""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from mnemonic_api.artifact_storage import ArtifactStorage, UnsafeArtifactPath
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import ArtifactOperation
from mnemonic_api.services.artifacts import recover_all_artifacts

logger = logging.getLogger(__name__)


def maintain_artifacts(factory: sessionmaker[Session], storage: ArtifactStorage) -> None:
    with factory() as database:
        try:
            recover_all_artifacts(database, storage)
        except (SQLAlchemyError, OSError, UnsafeArtifactPath, ApplicationError) as error:
            database.rollback()
            logger.warning("Artifact recovery unavailable (%s)", type(error).__name__)
        # Cleanup needs a readable journal, but not successful publication of
        # every intent. Still-pending descriptors remain protected from deletion.
        intents = database.scalars(
            select(ArtifactOperation.intent).where(ArtifactOperation.state == "pending")
        ).all()
        active = {intent["staged"]["temporary_path"] for intent in intents if intent.get("staged")}
        storage.cleanup_staging(active)


async def artifact_maintenance_loop(
    factory: sessionmaker[Session], storage: ArtifactStorage
) -> None:
    while True:
        # Reads and mutations recover their own project immediately. Background
        # recovery handles projects that nobody accesses after a process restart.
        await asyncio.sleep(60)
        try:
            await run_in_threadpool(maintain_artifacts, factory, storage)
        except (SQLAlchemyError, OSError, UnsafeArtifactPath, ApplicationError) as error:
            logger.warning("Artifact maintenance unavailable (%s)", type(error).__name__)
