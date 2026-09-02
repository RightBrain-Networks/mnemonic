"""Typed access to what ``create_app`` stores on ``app.state``.

Starlette leaves ``app.state`` untyped. Everything the application carries at
runtime is listed here once, so a reader sees it in one place and a type
checker can follow every use.
"""

from starlette.requests import HTTPConnection

from mnemonic_api.config import Settings
from mnemonic_api.live_sync import LiveSyncHub
from mnemonic_api.semantic import Embedder


def settings_of(connection: HTTPConnection) -> Settings:
    return connection.app.state.settings


def api_key_of(connection: HTTPConnection) -> str:
    """The shared bearer secret, for constant-time comparison and echo rejection."""
    return settings_of(connection).api_key.get_secret_value()


def embedder_of(connection: HTTPConnection) -> Embedder:
    return connection.app.state.semantic_embedder


def live_sync_hub_of(connection: HTTPConnection) -> LiveSyncHub:
    return connection.app.state.live_sync_hub
