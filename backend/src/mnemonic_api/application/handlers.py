"""The two failure classes that escape routes, and the bodies they become.

Everything else a route raises is already an ``HTTPException`` whose detail is
a sanitized envelope (see ``mnemonic_api.errors``), which FastAPI renders as is.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from mnemonic_api.application.validation import public_validation_errors

logger = logging.getLogger(__name__)


def install_exception_handlers(app: FastAPI) -> None:
    # The decorator API, unlike add_exception_handler, accepts handlers typed
    # for the exact exception class they are registered to receive.
    app.exception_handler(SQLAlchemyError)(database_failure)
    app.exception_handler(RequestValidationError)(invalid_request)


async def database_failure(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    """A failing database is a 503 with a fixed envelope; the log names only the type."""
    logger.error("Database operation failed (%s)", type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "database_unavailable",
                "message": "Database operation unavailable.",
                "context": {},
            }
        },
    )


async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"detail": public_validation_errors(exc.errors())}
    )
