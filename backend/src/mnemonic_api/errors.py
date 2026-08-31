"""Stable, sanitized application errors shared by canonical and compatibility routes."""

from typing import Any

from fastapi import HTTPException

SAFE_ERROR_CONTEXT_KEYS = frozenset({"holder_client", "expires_at"})


def _safe_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {}
    return {key: value for key, value in context.items() if key in SAFE_ERROR_CONTEXT_KEYS}


class ApplicationError(HTTPException):
    """An HTTP error whose machine-readable code is safe to expose to clients."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
                "context": _safe_context(context),
            },
        )


def not_found(code: str, message: str) -> ApplicationError:
    return ApplicationError(404, code, message)


def conflict(
    code: str,
    message: str,
    *,
    context: dict[str, Any] | None = None,
) -> ApplicationError:
    return ApplicationError(409, code, message, context=context)
