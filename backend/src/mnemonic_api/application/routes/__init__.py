"""The ``/api/v1`` REST surface, one module per concept a caller already knows.

- ``projects``: ``/projects``, ``/projects/{id}``, ``/projects/{id}/settings``.
- ``work_search``: ``GET /projects/{id}/work-items`` in its full, minimal, and
  roots views.
- ``work_items``: creating work; ``/work-items/{id}`` read and patch; its
  ``/defer``, ``/complete``, ``/delete``, ``/context``, and ``/children``; and
  ``/projects/{id}/ready-work``.
- ``history``: ``/work-items/{id}/checkpoints`` and ``/work-items/{id}/events``.
- ``relationships``: ``/projects/{id}/relationships[/{id}]`` and
  ``/work-items/{id}/relationships``.
- ``human_gates``: ``/projects/{id}/human-attention``, ``/work-items/{id}/gates``,
  and ``/gates/{id}/resolve`` and ``/gates/{id}/context`` beneath it.
- ``leases``: ``/claim``, ``/claim-and-recall``, ``/renew-claim``, ``/release-claim``.

Outside ``api_router``: ``dashboard_sync`` (the origin-checked WebSocket at
``/api/v1/sync``, no bearer) and ``health`` (``/healthz``, ``/readyz``).
"""

from fastapi import APIRouter, Depends

from mnemonic_api.application.auth import authenticate
from mnemonic_api.application.guards import (
    reject_client_operation_transport,
    reject_lease_token_query,
)
from mnemonic_api.application.routes import (
    duplicates,
    history,
    human_gates,
    leases,
    projects,
    relationships,
    work_items,
    work_search,
)


def api_router() -> APIRouter:
    """Every REST route behind the same three guards, in this order.

    The pre-routing middleware has already authenticated. ``authenticate`` is
    route-local defense in depth and stays first, so an unauthenticated caller
    never learns which transports the guards reject.
    """
    api = APIRouter(
        prefix="/api/v1",
        dependencies=[
            Depends(authenticate),
            Depends(reject_lease_token_query),
            Depends(reject_client_operation_transport),
        ],
    )
    for router in (
        projects.router,
        work_search.router,
        work_items.router,
        history.router,
        relationships.router,
        duplicates.router,
        human_gates.router,
        leases.router,
    ):
        api.include_router(router)
    return api
