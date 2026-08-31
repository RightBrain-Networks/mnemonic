"""Stable, sanitized application errors shared by canonical and compatibility routes."""

from typing import Any

from fastapi import HTTPException


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
                "context": context or {},
            },
        )


def not_found(code: str, message: str) -> ApplicationError:
    return ApplicationError(404, code, message)


def conflict(code: str, message: str) -> ApplicationError:
    return ApplicationError(409, code, message)
