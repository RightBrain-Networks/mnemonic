"""Per-checkout deadlines without mutating a shared SQLAlchemy pool timeout."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Any

from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.pool import QueuePool
from sqlalchemy.util.queue import Queue

_checkout_deadline: ContextVar[float | None] = ContextVar("mnemonic_pool_deadline", default=None)


class _DeadlineQueue(Queue[Any]):
    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        deadline = _checkout_deadline.get()
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise PoolTimeoutError("Database checkout deadline expired")
            timeout = remaining if timeout is None else min(timeout, remaining)
        return super().get(block, timeout)


class DeadlineQueuePool(QueuePool):
    """Preserve QueuePool overflow/accounting; bound only this worker's queue wait."""

    _queue_class = _DeadlineQueue


@contextmanager
def pool_checkout_deadline(deadline: float) -> Iterator[None]:
    token = _checkout_deadline.set(deadline)
    try:
        yield
    finally:
        _checkout_deadline.reset(token)
