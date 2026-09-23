"""Duplicate response ceilings, independently of uncancellable worker lifetimes."""

SUGGESTION_MAX_SECONDS = 45
SUGGESTION_RESPONSE_RESERVE_SECONDS = 1.0


def suggestion_work_deadline(response_deadline: float, now: float) -> float:
    # Small configured budgets still leave time to serialize a useful fallback.
    reserve = min(SUGGESTION_RESPONSE_RESERVE_SECONDS, max(0.0, response_deadline - now) / 2)
    return response_deadline - reserve
