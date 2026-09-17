"""Content-free timing events for diagnosing search latency and resource pressure."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import monotonic
from typing import Literal

from mnemonic_api.search_ranking import CacheRefresh

logger = logging.getLogger(__name__)
Phase = Literal[
    "request_queue", "inference_queue", "query_embedding", "candidate_selection",
    "document_inference", "cache_refresh", "total",
]


def record_timing(operation: str, phase: Phase, started: float, *, outcome: str = "completed"):
    elapsed = round(max(0, monotonic() - started) * 1000, 3)
    logger.info("Search timing operation=%s phase=%s duration_ms=%.3f outcome=%s",
                operation, phase, elapsed, outcome, extra={
        "search_operation": operation, "search_phase": phase,
        "duration_ms": elapsed,
        "search_outcome": outcome,
    })


@contextmanager
def timed_phase(operation: str, phase: Phase) -> Iterator[None]:
    started = monotonic()
    outcome = "completed"
    try:
        yield
    except BaseException:
        outcome = "failed"
        raise
    finally:
        record_timing(operation, phase, started, outcome=outcome)


def refresh_cache(operation: str, needed: bool, persist: Callable[[], object]) -> CacheRefresh:
    if not needed:
        return CacheRefresh()
    try:
        with timed_phase(operation, "cache_refresh"):
            if persist() is False:
                raise RuntimeError("Disposable cache refresh did not complete")
    except Exception as error:
        logger.warning("Search cache refresh failed operation=%s error_type=%s",
                       operation, type(error).__name__)
        return CacheRefresh(status="failed", reason="cache_refresh_failed")
    return CacheRefresh(status="completed")
