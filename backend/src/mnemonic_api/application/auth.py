"""Bearer authentication: one rule, enforced twice.

``middleware.authenticate_rest_before_routing`` rejects an unauthenticated
``/api/v1`` request before FastAPI reads a body, parses a query, or runs a
dependency. The ``authenticate`` dependency repeats the check on every route as
defense in depth; the router orders it before the transport guards, so an
unauthenticated caller learns nothing from a 422.
"""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mnemonic_api.application.state import api_key_of

bearer = HTTPBearer(auto_error=False)
_UNAUTHENTICATED = "Valid bearer authentication is required"
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def presented_key_matches(request: Request, supplied: str) -> bool:
    """Compare in constant time; a missing or malformed credential compares as empty."""
    expected = api_key_of(request).encode("utf-8")
    return secrets.compare_digest(supplied.encode("utf-8"), expected)


def authenticate(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    supplied = credentials.credentials if credentials is not None else ""
    if not presented_key_matches(request, supplied):
        raise HTTPException(status_code=401, detail=_UNAUTHENTICATED, headers=_CHALLENGE)


def request_has_valid_bearer(request: Request) -> bool:
    """Read the Authorization header by hand: this runs before any dependency."""
    scheme, separator, supplied = request.headers.get("authorization", "").partition(" ")
    well_formed = bool(separator) and scheme.casefold() == "bearer" and bool(supplied)
    return presented_key_matches(request, supplied if well_formed else "")


def unauthenticated_response() -> JSONResponse:
    return JSONResponse(
        status_code=401, content={"detail": _UNAUTHENTICATED}, headers=_CHALLENGE
    )
