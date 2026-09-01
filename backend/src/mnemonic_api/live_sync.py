"""In-process WebSocket invalidation events for dashboard live syncing."""

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from fastapi import WebSocket

UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
MUTATION_METHODS = frozenset({"POST", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class MutationEvent:
    scope: Literal["projects", "work-items"]
    project_id: str | None = None
    work_item_id: str | None = None

    def message(self, revision: int) -> dict[str, str | int | None]:
        return {
            "type": "invalidate",
            "revision": revision,
            "scope": self.scope,
            "project_id": self.project_id,
            "work_item_id": self.work_item_id,
        }


def mutation_event(method: str, path: str) -> MutationEvent | None:
    """Describe a successful REST mutation without exposing record contents."""
    if method not in MUTATION_METHODS:
        return None
    parts = path.strip("/").split("/")
    if parts[:3] != ["api", "v1", "projects"]:
        return None
    remaining = parts[3:]
    if not remaining:
        return MutationEvent("projects") if method == "POST" else None
    if not UUID_PATTERN.fullmatch(remaining[0]):
        return None
    project_id = remaining[0].lower()
    if len(remaining) == 1:
        return MutationEvent("projects", project_id=project_id) if method == "PATCH" else None
    if remaining[1] == "relationships":
        return MutationEvent("work-items", project_id=project_id)
    if remaining[1] not in {"work-items", "handoffs"}:
        return None
    work_item_id = None
    if len(remaining) >= 3 and UUID_PATTERN.fullmatch(remaining[2]):
        work_item_id = remaining[2].lower()
    return MutationEvent(
        "work-items", project_id=project_id, work_item_id=work_item_id
    )


class LiveSyncHub:
    """Fan out small invalidation messages to connected dashboard browsers."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._revision = 0

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        try:
            await websocket.send_json({"type": "ready", "revision": self._revision})
        except Exception:
            self._connections.discard(websocket)
            raise

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def publish(self, event: MutationEvent) -> None:
        self._revision += 1
        connections = tuple(self._connections)
        if not connections:
            return
        results = await asyncio.gather(
            *(
                asyncio.wait_for(connection.send_json(event.message(self._revision)), timeout=1)
                for connection in connections
            ),
            return_exceptions=True,
        )
        for connection, result in zip(connections, results, strict=True):
            if isinstance(result, BaseException):
                self._connections.discard(connection)
