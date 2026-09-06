"""The Mnemonic REST application, assembled from one module per concern.

Read the package from a request's point of view:

- ``middleware``  what wraps every request, and in which order.
- ``auth``        the single bearer rule, checked before routing and again per route.
- ``guards``      which transports may carry lease tokens and client operation IDs.
- ``validation``  how request validation failures are sanitized before they leave.
- ``mutations``   the one lifecycle shared by the thirteen receipt-protected writes.
- ``handlers``    the two failure classes that escape routes, and their envelopes.
- ``routes``      one module per domain concept; its docstring maps paths to modules.
- ``state``       typed access to what ``create_app`` stores on ``app.state``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI
from sqlalchemy.engine import Engine

from mnemonic_api.application.handlers import install_exception_handlers
from mnemonic_api.application.middleware import install_middleware
from mnemonic_api.application.routes import api_router
from mnemonic_api.application.routes.dashboard_sync import router as sync_router
from mnemonic_api.application.routes.health import router as health_router
from mnemonic_api.application.suggestion_resources import DuplicateSuggestionResources
from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine, build_session_factory
from mnemonic_api.live_sync import LiveSyncHub
from mnemonic_api.schemas import COMPLETION_EVENT_ID_MAX
from mnemonic_api.semantic import Embedder, FastembedEmbedder

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
        yield
        if engine is None:
            # Only an engine this factory built is this factory's to dispose.
            connection_pool.dispose()

    app = FastAPI(
        title="Mnemonic API",
        version="0.10.0",
        description="Durable project-scoped work with immutable agent checkpoints.",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.session_factory = build_session_factory(connection_pool)
    app.state.semantic_embedder = semantic_embedder or FastembedEmbedder()
    app.state.duplicate_suggestion_resources = DuplicateSuggestionResources.from_settings(config)
    app.state.live_sync_hub = LiveSyncHub()

    app.include_router(api_router())
    app.include_router(sync_router)
    app.include_router(health_router)
    install_middleware(app, app.state.duplicate_suggestion_resources)
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
