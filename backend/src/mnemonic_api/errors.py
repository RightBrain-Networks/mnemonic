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


def client_operation_conflict() -> ApplicationError:
    return ApplicationError(
        409,
        "client_operation_conflict",
        (
            "This client operation ID is already bound to a different successful request. "
            "Use a new ID only for a genuinely new intent."
        ),
    )


def client_operation_unavailable() -> ApplicationError:
    return ApplicationError(
        503,
        "client_operation_unavailable",
        "Client operation safety is unavailable. Retry the same ID with the exact same request.",
    )


def client_operation_secret_echo() -> ApplicationError:
    return ApplicationError(
        422,
        "client_operation_secret_echo",
        "Client operation or capability data cannot appear in public content fields.",
    )
