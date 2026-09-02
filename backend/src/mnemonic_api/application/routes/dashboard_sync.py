"""``/api/v1/sync``: data-free invalidation frames for dashboard browsers.

The socket carries no bearer; the browser proves its origin instead, against
the exact allowlist in ``Settings.allowed_dashboard_origins``. Every frame is
``{type, revision, scope}`` and nothing more (see ``mnemonic_api.live_sync``).
"""

from fastapi import APIRouter, WebSocket, status

from mnemonic_api.application.state import live_sync_hub_of, settings_of

router = APIRouter(prefix="/api/v1")


@router.websocket("/sync")
async def sync_dashboard(websocket: WebSocket) -> None:
    if websocket.headers.get("origin") not in settings_of(websocket).allowed_dashboard_origins:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    hub = live_sync_hub_of(websocket)
    await hub.connect(websocket)
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
    finally:
        hub.disconnect(websocket)
