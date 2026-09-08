"""Control transports: JSON bodies, plus operation headers for raw artifact writes.

A lease token is a capability and a client operation ID binds a receipt. Either
one in a URL, header, or cookie would reach access logs, proxies, and browser
history, so those transports get a sanitized 422 in FastAPI's own validation
shape. The narrowly matched artifact byte routes accept their documented header.
Nothing here reads, echoes, or logs the rejected value.
"""

import re

from fastapi import HTTPException, Request

CLIENT_OPERATION_TRANSPORT_NAMES = frozenset(
    {
        "client_operation_id",
        "client-operation-id",
        "idempotency-key",
        "x-idempotency-key",
        "x-client-operation-id",
    }
)
_OPERATION_ID_BODY_ONLY = "Client operation IDs are accepted only in supported JSON request bodies."


def transport_rejection(location: str, field: str | None, message: str) -> HTTPException:
    """A 422 naming the transport (and field) but never the value."""
    loc = [location] if field is None else [location, field]
    return HTTPException(
        status_code=422,
        detail=[{"type": "extra_forbidden", "loc": loc, "msg": message}],
    )


def reject_lease_token_query(request: Request) -> None:
    # Never inspect, echo, or log a query value. Production access logging is
    # disabled as a second boundary because URLs are not secret-safe.
    if "lease_token" in request.query_params:
        raise transport_rejection(
            "query", "lease_token", "Lease tokens are accepted only in JSON request bodies."
        )


def reject_client_operation_transport(request: Request) -> None:
    """Reject operation IDs outside supported JSON bodies and artifact write headers."""
    transports = (
        ("query", request.query_params),
        ("header", request.headers),
        ("cookie", request.cookies),
    )
    for location, names in transports:
        prohibited = {
            name.strip().casefold() for name in names
            if name.strip().casefold() in CLIENT_OPERATION_TRANSPORT_NAMES
        }
        if location == "header" and _artifact_binary_mutation(request):
            prohibited.discard("x-client-operation-id")
        if prohibited:
            raise transport_rejection(location, "client_operation_id", _OPERATION_ID_BODY_ONLY)


def _artifact_binary_mutation(request: Request) -> bool:
    """Only artifact byte transports accept their documented operation UUID header."""
    prefix = r"/api/v1/projects/[0-9a-fA-F-]{36}/artifacts"
    suffixes = {"POST": "", "PUT": r"/[0-9a-fA-F-]{36}/content", "DELETE": r"/[0-9a-fA-F-]{36}"}
    suffix = suffixes.get(request.method)
    return suffix is not None and re.fullmatch(prefix + suffix, request.url.path) is not None


def reject_registered_mutation_query(request: Request) -> None:
    """Keep receipt-protected mutation routes query-free."""
    if request.query_params:
        raise transport_rejection(
            "query", None, "Query parameters are not accepted for registered mutations."
        )


def reject_lease_operation_query(request: Request) -> None:
    if request.query_params:
        raise transport_rejection(
            "query", None, "Query parameters are not accepted for lease operations."
        )


async def reject_read_body_and_duplicate_query(request: Request) -> None:
    """Strict bounded reads never silently choose among duplicate query values."""
    names = [name for name, _ in request.query_params.multi_items()]
    if len(names) != len(set(names)):
        raise transport_rejection("query", None, "Repeated query parameters are not accepted.")
    async for chunk in request.stream():
        if chunk:
            raise transport_rejection("body", None, "A request body is not accepted for this read.")


async def reject_empty_read_request(request: Request) -> None:
    if request.query_params:
        raise transport_rejection("query", None, "Query parameters are not accepted for this read.")
    await reject_read_body_and_duplicate_query(request)
