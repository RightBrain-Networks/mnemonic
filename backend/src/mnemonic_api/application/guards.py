"""Which transports may carry a lease token or a client operation ID: JSON bodies.

A lease token is a capability and a client operation ID binds a receipt. Either
one in a URL, header, or cookie would reach access logs, proxies, and browser
history, so those transports get a sanitized 422 in FastAPI's own validation
shape. Nothing here reads, echoes, or logs the rejected value.
"""

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
_OPERATION_ID_BODY_ONLY = (
    "Client operation IDs are accepted only in supported JSON request bodies."
)


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
    """Reject operation IDs anywhere except a supported JSON request body."""
    transports = (
        ("query", request.query_params),
        ("header", request.headers),
        ("cookie", request.cookies),
    )
    for location, names in transports:
        if any(name.strip().casefold() in CLIENT_OPERATION_TRANSPORT_NAMES for name in names):
            raise transport_rejection(location, "client_operation_id", _OPERATION_ID_BODY_ONLY)


def reject_registered_mutation_query(request: Request) -> None:
    """Keep the twelve receipt-protected mutation routes query-free."""
    if request.query_params:
        raise transport_rejection(
            "query", None, "Query parameters are not accepted for registered mutations."
        )


def reject_lease_operation_query(request: Request) -> None:
    if request.query_params:
        raise transport_rejection(
            "query", None, "Query parameters are not accepted for lease operations."
        )
