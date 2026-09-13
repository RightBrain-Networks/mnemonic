"""The job ledger is also an outbox; RabbitMQ messages contain only its UUID."""

from mnemonic_api.models import BackgroundJob

__all__ = ["BackgroundJob"]
