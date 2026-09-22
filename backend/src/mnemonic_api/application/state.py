"""Typed access to what ``create_app`` stores on ``app.state``.

Starlette leaves ``app.state`` untyped. Everything the application carries at
runtime is listed here once, so a reader sees it in one place and a type
checker can follow every use.
"""

from time import monotonic

from starlette.requests import HTTPConnection

from mnemonic_api.config import Settings
from mnemonic_api.inference import INFERENCE_REQUEST_KEY, InferenceRequest, QueuedEmbedder
from mnemonic_api.live_sync import LiveSyncHub
from mnemonic_api.semantic import Embedder


def settings_of(connection: HTTPConnection) -> Settings:
    return connection.app.state.settings


def api_key_of(connection: HTTPConnection) -> str:
    """The shared bearer secret, for constant-time comparison and echo rejection."""
    return settings_of(connection).api_key.get_secret_value()


def embedder_of(connection: HTTPConnection) -> Embedder:
    resources = connection.app.state.duplicate_suggestion_resources
    state = connection.scope.setdefault("state", {})
    budget = state.setdefault(INFERENCE_REQUEST_KEY,
                             InferenceRequest(monotonic() + resources.timeout_seconds))
    return QueuedEmbedder(connection.app.state.semantic_embedder, resources.inference, budget)


def live_sync_hub_of(connection: HTTPConnection) -> LiveSyncHub:
    return connection.app.state.live_sync_hub
