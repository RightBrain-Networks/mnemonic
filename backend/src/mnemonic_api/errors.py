"""Stable, sanitized application errors with client-safe machine-readable codes."""

from typing import Any

from fastapi import HTTPException

SAFE_ERROR_CONTEXT_KEYS = frozenset({"holder_client", "expires_at"})
SAFE_ERROR_FIELD_LOCATIONS = frozenset(
    {
        "actor.actor_client",
        "actor.actor_model",
        "actor.actor_session_id",
        "body",
        "metadata.key",
        "metadata.value",
    }
)


def _safe_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {}
    safe = {key: value for key, value in context.items() if key in SAFE_ERROR_CONTEXT_KEYS}
    fields = context.get("fields")
    if isinstance(fields, list) and all(field in SAFE_ERROR_FIELD_LOCATIONS for field in fields):
        safe["fields"] = sorted(set(fields))
    return safe


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
