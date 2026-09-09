"""Deployment policy for fresh work summaries, independent of retained history."""

from mnemonic_api.errors import ApplicationError

DEFAULT_WORK_SUMMARY_MAX_CHARS = 2048


def require_work_summary_length(summary: str, maximum: int) -> None:
    if len(summary) > maximum:
        raise ApplicationError(
            422,
            "work_summary_too_long",
            f"Work summary exceeds the configured maximum of {maximum} characters.",
            context={"max_chars": maximum},
        )
