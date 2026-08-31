"""Stable ASGI entry point for the canonical Mnemonic application."""

from mnemonic_api.application import create_app

__all__ = ["create_app"]
