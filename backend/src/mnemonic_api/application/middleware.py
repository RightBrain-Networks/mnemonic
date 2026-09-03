"""What wraps every request, and in which order.

Starlette nests middleware in reverse registration order: the last one added
runs outermost. ``install_middleware`` registers authentication first so it
sits inside the broadcast layer, which therefore observes every response,
including a 401, and publishes for none of the failures.
"""

import logging

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from mnemonic_api.application.auth import request_has_valid_bearer, unauthenticated_response
from mnemonic_api.application.mutations import MutationTrace, mutation_trace
from mnemonic_api.application.state import live_sync_hub_of
from mnemonic_api.live_sync import mutation_event

logger = logging.getLogger(__name__)
API_PREFIX = "/api/v1"


def install_middleware(app: FastAPI) -> None:
    """Register both layers; the second registration wraps the first."""
    app.add_middleware(BaseHTTPMiddleware, dispatch=authenticate_rest_before_routing)
    app.add_middleware(BaseHTTPMiddleware, dispatch=broadcast_successful_mutations)


async def authenticate_rest_before_routing(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Answer 401 before FastAPI reads a body, parses a query, or runs a dependency."""
    path = request.url.path
    is_api = path == API_PREFIX or path.startswith(API_PREFIX + "/")
    if is_api and not request_has_valid_bearer(request):
        return unauthenticated_response()
    return await call_next(request)


async def broadcast_successful_mutations(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """After the response: log a registered mutation's outcome, then invalidate dashboards.

    Both signals are data-free. The log line carries only the operation kind
    and outcome; the WebSocket frame carries only a scope. A replay or a no-op
    (``mutation_applied is False``) changed nothing and publishes nothing. An
    unregistered mutation leaves no trace and falls back to method and path.
    """
    response = await call_next(request)
    trace = mutation_trace(request)
    if trace is not None:
        _log_outcome(trace, response.status_code)
    event = mutation_event(request.method, request.url.path)
    succeeded = 200 <= response.status_code < 300
    changed = trace is None or (
        trace.outcome != "replayed" and trace.mutation_applied is not False
    )
    if event is not None and succeeded and changed:
        await live_sync_hub_of(request).publish(event)
    return response


def _log_outcome(trace: MutationTrace, status_code: int) -> None:
    outcome = trace.outcome
    if outcome is None and status_code >= 500:
        outcome = "unavailable"  # the route failed before the receipt could say
    if outcome is not None:
        logger.info("Client operation outcome kind=%s outcome=%s", trace.kind, outcome)
