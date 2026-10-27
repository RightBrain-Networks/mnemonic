"""The Mnemonic REST application, assembled from one module per concern.

Read the package from a request's point of view:

- ``middleware``  what wraps every request, and in which order.
- ``auth``        the single bearer rule, checked before routing and again per route.
- ``guards``      which transports may carry lease tokens and client operation IDs.
- ``validation``  how request validation failures are sanitized before they leave.
- ``mutations``   the one lifecycle shared by the sixteen receipt-protected writes.
- ``handlers``    the two failure classes that escape routes, and their envelopes.
- ``routes``      one module per domain concept; its docstring maps paths to modules.
- ``state``       typed access to what ``create_app`` stores on ``app.state``.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any, cast

import httpx
from fastapi import FastAPI
from sqlalchemy.engine import Engine

from mnemonic_api.application.artifact_maintenance import artifact_maintenance_loop
from mnemonic_api.application.browser_artifacts import (
    ArtifactBrowserMiddleware,
    artifact_browser_router,
)
from mnemonic_api.application.browser_backups import BackupBrowserMiddleware
from mnemonic_api.application.browser_boundary import BrowserBoundaryMiddleware
from mnemonic_api.application.browser_routes import browser_router
from mnemonic_api.application.browser_transcripts import (
    TranscriptBrowserMiddleware,
    transcript_browser_router,
)
from mnemonic_api.application.handlers import install_exception_handlers
from mnemonic_api.application.middleware import install_middleware
from mnemonic_api.application.routes import api_router
from mnemonic_api.application.routes.dashboard_sync import router as sync_router
from mnemonic_api.application.routes.health import router as health_router
from mnemonic_api.application.suggestion_resources import DuplicateSuggestionResources
from mnemonic_api.application.transcript_startup import start_transcript_services
from mnemonic_api.artifact_extraction import artifact_extraction_loop
from mnemonic_api.artifact_storage import ArtifactStorage
from mnemonic_api.artifact_tika import TikaExtractor
from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine, build_session_factory
from mnemonic_api.live_sync import LiveSyncHub
from mnemonic_api.schemas import COMPLETION_EVENT_ID_MAX
from mnemonic_api.semantic import Embedder, FastembedEmbedder
from mnemonic_api.services.artifact_search import ArtifactSearchIndex
from mnemonic_api.transcript_search_index import TranscriptSearchIndex

__all__ = ["create_app"]


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    semantic_embedder: Embedder | None = None,
) -> FastAPI:
    """Build the application; tests supply their own settings, engine, and embedder."""
    config = settings if settings is not None else Settings()
    connection_pool = engine if engine is not None else build_engine(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        start_transcript_services(config, app.state.transcript_search_index)
        maintenance = None
        extraction = None
        if config.artifact_max_bytes > 0:
            maintenance = asyncio.create_task(artifact_maintenance_loop(
                app.state.session_factory, app.state.artifact_storage
            ))
            extraction = asyncio.create_task(artifact_extraction_loop(
                app.state.session_factory, app.state.artifact_storage,
                TikaExtractor(config), config.artifact_extraction_timeout_seconds,
            ))
        try:
            yield
        finally:
            # Browser timeouts may leave writes or read cleanup in flight.
            # Finish those tasks before closing their shared resources.
            await asyncio.gather(*app.state.browser_pending_tasks, return_exceptions=True)
            await app.state.backup_client.aclose()
            for task in (maintenance, extraction):
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            app.state.transcript_search_index.close()
            if engine is None:
                # Only an engine this factory built is this factory's to dispose.
                connection_pool.dispose()

    app = FastAPI(
        title="Mnemonic API",
        version="0.112.0",
        description="Durable project-scoped work with immutable agent checkpoints.",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.artifact_storage = (
        ArtifactStorage(config.artifact_root, config.artifact_max_bytes)
        if config.artifact_max_bytes > 0
        else None
    )
    app.state.artifact_upload_slots = asyncio.Semaphore(4)
    app.state.artifact_search_index = ArtifactSearchIndex()
    app.state.transcript_search_index = TranscriptSearchIndex(config.transcript_index_dir)
    app.state.session_factory = build_session_factory(
        connection_pool, work_summary_max_chars=config.work_summary_max_chars
    )
    app.state.semantic_embedder = semantic_embedder or FastembedEmbedder(
        workers=config.duplicate_suggestion_inference_slots,
        threads=config.duplicate_suggestion_inference_threads,
    )
    app.state.duplicate_suggestion_resources = DuplicateSuggestionResources.from_settings(config)
    app.state.live_sync_hub = LiveSyncHub()
    app.state.browser_pending_tasks = set()
    # The gateway owns the whole-operation deadline and never retries a create
    # or restore. Environment proxies must not receive the private worker token.
    app.state.backup_client = httpx.AsyncClient(
        follow_redirects=False, timeout=None, trust_env=False,
        transport=httpx.AsyncHTTPTransport(retries=0),
    )

    app.include_router(api_router())
    app.include_router(browser_router())
    app.include_router(artifact_browser_router())
    app.include_router(transcript_browser_router())
    app.include_router(sync_router)
    app.include_router(health_router)
    install_middleware(app, app.state.duplicate_suggestion_resources)
    app.add_middleware(BrowserBoundaryMiddleware, pending_tasks=app.state.browser_pending_tasks)
    app.add_middleware(ArtifactBrowserMiddleware, pending_tasks=app.state.browser_pending_tasks)
    app.add_middleware(TranscriptBrowserMiddleware, pending_tasks=app.state.browser_pending_tasks)
    app.add_middleware(BackupBrowserMiddleware)
    install_exception_handlers(app)

    default_openapi = app.openapi

    def exact_integer_openapi() -> dict[str, Any]:
        document = default_openapi()
        page = document["components"]["schemas"]["CompletionEvidencePage"]
        for field in ("total", "structured_completion_total"):
            page["properties"][field]["maximum"] = COMPLETION_EVENT_ID_MAX
        return document

    app.openapi = cast(Any, exact_integer_openapi)
    return app
