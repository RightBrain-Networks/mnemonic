"""Per-process resource bounds for the duplicate-suggestion safe read."""

import asyncio
import json
from dataclasses import dataclass, field
from time import monotonic
from urllib.parse import parse_qsl

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mnemonic_api.config import Settings
from mnemonic_api.errors import (
    ApplicationError,
    duplicate_suggestion_busy,
    duplicate_suggestion_unavailable,
    request_body_too_large,
)

SUGGESTION_STATE_KEY = "duplicate_suggestion_inference_acquired"
SUGGESTION_DEADLINE_KEY = "duplicate_suggestion_deadline"
SEMANTIC_SEARCH_STATE_KEY = "semantic_search_inference_acquired"
_NO_STORE = (b"cache-control", b"no-store")


class _ClientDisconnected(Exception):
    pass


@dataclass(slots=True)
class DuplicateSuggestionResources:
    """Two independent semaphores; neither consumes a database connection."""

    request_slots: asyncio.Semaphore
    inference_slots: asyncio.Semaphore
    request_wait_seconds: float
    inference_wait_seconds: float
    body_max_bytes: int
    timeout_seconds: float
    draining_tasks: set[asyncio.Task[None]] = field(default_factory=set, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> DuplicateSuggestionResources:
        return cls(
            request_slots=asyncio.Semaphore(settings.duplicate_suggestion_request_slots),
            inference_slots=asyncio.Semaphore(settings.duplicate_suggestion_inference_slots),
            request_wait_seconds=settings.duplicate_suggestion_request_wait_ms / 1_000,
            inference_wait_seconds=settings.duplicate_suggestion_inference_wait_ms / 1_000,
            body_max_bytes=settings.duplicate_suggestion_body_max_bytes,
            timeout_seconds=float(settings.duplicate_suggestion_timeout_seconds),
        )

    async def acquire_request(self) -> bool:
        return await _bounded_acquire(self.request_slots, self.request_wait_seconds)

    async def acquire_inference(self) -> bool:
        return await _bounded_acquire(self.inference_slots, self.inference_wait_seconds)

    def retain_resources_until_done(
        self,
        task: asyncio.Task[None],
        inference_acquired: bool,
        *,
        request_acquired: bool = True,
    ) -> None:
        drain = asyncio.create_task(
            _release_resources_when_done(
                task,
                self,
                inference_acquired,
                request_acquired=request_acquired,
            )
        )
        self.draining_tasks.add(drain)
        drain.add_done_callback(self.draining_tasks.discard)


async def _bounded_acquire(semaphore: asyncio.Semaphore, wait_seconds: float) -> bool:
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=wait_seconds)
    except TimeoutError:
        return False
    return True


class DuplicateSuggestionControlMiddleware:
    """Authenticate outside, then bound suggestion and shared semantic resources."""

    def __init__(self, app: ASGIApp, *, resources: DuplicateSuggestionResources) -> None:
        self.app = app
        self.resources = resources

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if _is_semantic_search_request(scope):
            await _serve_semantic_search(
                self.app,
                self.resources,
                scope,
                receive,
                send,
            )
            return
        if not _is_suggestion_request(scope):
            await self.app(scope, receive, send)
            return
        send_no_store = _with_no_store(send)
        deadline = monotonic() + self.resources.timeout_seconds
        if _declared_oversize(scope, self.resources.body_max_bytes):
            await _send_error(request_body_too_large(), scope, receive, send_no_store)
            return
        if not await self.resources.acquire_request():
            await _send_error(duplicate_suggestion_busy(), scope, receive, send_no_store)
            return
        try:
            await _serve_acquired_request(
                self.app,
                self.resources,
                scope,
                receive,
                send_no_store,
                deadline,
            )
        except _ClientDisconnected:
            return


async def _serve_acquired_request(
    app: ASGIApp,
    resources: DuplicateSuggestionResources,
    scope: Scope,
    receive: Receive,
    send: Send,
    deadline: float,
) -> None:
    inference_acquired = False
    release_resources = True
    app_task: asyncio.Task[None] | None = None
    try:
        body = await asyncio.wait_for(
            _read_bounded_body(receive, resources.body_max_bytes),
            timeout=_remaining_seconds(deadline),
        )
        if body is None:
            await _send_error(request_body_too_large(), scope, receive, send)
            return
        if _preparse_rejects_json(body):
            await _send_duplicate_key_error(scope, receive, send)
            return

        inference_deadline = min(
            deadline,
            monotonic() + resources.inference_wait_seconds,
        )
        inference_acquired = await _acquire_inference_before(
            resources,
            deadline,
            inference_deadline,
        )
        state = scope.setdefault("state", {})
        state[SUGGESTION_STATE_KEY] = inference_acquired
        state[SUGGESTION_DEADLINE_KEY] = deadline
        buffered: list[Message] = []

        async def buffer_response(message: Message) -> None:
            buffered.append(message)

        async def run_app() -> None:
            await app(scope, _replay_body(body), buffer_response)

        app_task = asyncio.create_task(run_app())
        done, _pending = await asyncio.wait(
            (app_task,), timeout=_remaining_seconds(deadline)
        )
        if not done:
            resources.retain_resources_until_done(app_task, inference_acquired)
            release_resources = False
            await _send_error(
                duplicate_suggestion_unavailable(), scope, receive, send
            )
            return
        await app_task
        for message in buffered:
            await send(message)
    except asyncio.CancelledError:
        release_resources = release_resources and _release_immediately_after_cancel(
            resources, app_task, inference_acquired
        )
        raise
    except TimeoutError:
        await _send_error(duplicate_suggestion_unavailable(), scope, receive, send)
    finally:
        if release_resources:
            _release_resources(resources, inference_acquired)


def _release_immediately_after_cancel(
    resources: DuplicateSuggestionResources,
    app_task: asyncio.Task[None] | None,
    inference_acquired: bool,
) -> bool:
    if app_task is None or app_task.done():
        return True
    resources.retain_resources_until_done(app_task, inference_acquired)
    return False


async def _acquire_inference_before(
    resources: DuplicateSuggestionResources,
    request_deadline: float,
    inference_deadline: float,
) -> bool:
    if _remaining_seconds(request_deadline) <= 0:
        raise TimeoutError
    budget = _remaining_seconds(inference_deadline)
    if budget <= 0:
        return False
    acquired = await _bounded_acquire(
        resources.inference_slots,
        budget,
    )
    if _remaining_seconds(request_deadline) <= 0:
        if acquired:
            resources.inference_slots.release()
        raise TimeoutError
    if acquired and _remaining_seconds(inference_deadline) <= 0:
        resources.inference_slots.release()
        return False
    return acquired


async def _release_resources_when_done(
    task: asyncio.Task[None],
    resources: DuplicateSuggestionResources,
    inference_acquired: bool,
    *,
    request_acquired: bool,
) -> None:
    try:
        await asyncio.shield(task)
    except (asyncio.CancelledError, Exception):
        pass
    finally:
        _release_resources(
            resources,
            inference_acquired,
            request_acquired=request_acquired,
        )


def _release_resources(
    resources: DuplicateSuggestionResources,
    inference_acquired: bool,
    *,
    request_acquired: bool = True,
) -> None:
    if inference_acquired:
        resources.inference_slots.release()
    if request_acquired:
        resources.request_slots.release()


def _remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - monotonic())


def suggestion_inference_acquired(scope: Scope) -> bool:
    state = scope.get("state")
    return bool(isinstance(state, dict) and state.get(SUGGESTION_STATE_KEY) is True)


def suggestion_request_deadline(scope: Scope) -> float:
    state = scope.get("state")
    value = state.get(SUGGESTION_DEADLINE_KEY) if isinstance(state, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise RuntimeError("Duplicate suggestion request deadline is unavailable")


def semantic_search_inference_acquired(scope: Scope) -> bool:
    state = scope.get("state")
    return bool(isinstance(state, dict) and state.get(SEMANTIC_SEARCH_STATE_KEY) is True)


async def _serve_semantic_search(
    app: ASGIApp,
    resources: DuplicateSuggestionResources,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    inference_acquired = await resources.acquire_inference()
    scope.setdefault("state", {})[SEMANTIC_SEARCH_STATE_KEY] = inference_acquired
    if not inference_acquired:
        await app(scope, receive, send)
        return
    release_resources = True

    async def run_app() -> None:
        await app(scope, receive, send)

    app_task = asyncio.create_task(run_app())
    try:
        await asyncio.shield(app_task)
    except asyncio.CancelledError:
        if not app_task.done():
            resources.retain_resources_until_done(
                app_task,
                True,
                request_acquired=False,
            )
            release_resources = False
        raise
    finally:
        if release_resources:
            _release_resources(resources, True, request_acquired=False)


def _is_suggestion_request(scope: Scope) -> bool:
    if scope.get("type") != "http" or scope.get("method") != "POST":
        return False
    parts = str(scope.get("path", "")).strip("/").split("/")
    return len(parts) == 5 and parts[:3] == ["api", "v1", "projects"] and parts[4] == (
        "duplicate-suggestions"
    )


def _is_semantic_search_request(scope: Scope) -> bool:
    if scope.get("type") != "http" or scope.get("method") != "GET":
        return False
    parts = str(scope.get("path", "")).strip("/").split("/")
    if len(parts) != 5 or parts[:3] != ["api", "v1", "projects"]:
        return False
    if parts[4] != "work-items":
        return False
    values = [
        value
        for name, value in parse_qsl(
            bytes(scope.get("query_string", b"")).decode("latin-1"),
            keep_blank_values=True,
        )
        if name == "semantic"
    ]
    return bool(values) and values[-1].lower() in {
        "1",
        "on",
        "t",
        "true",
        "y",
        "yes",
    }


def _declared_oversize(scope: Scope, limit: int) -> bool:
    for name, raw_value in scope.get("headers", ()):
        if name.lower() != b"content-length":
            continue
        try:
            if int(raw_value) > limit:
                return True
        except ValueError:
            continue
    return False


async def _read_bounded_body(receive: Receive, limit: int) -> bytes | None:
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise _ClientDisconnected
        chunk = message.get("body", b"")
        if len(body) + len(chunk) > limit:
            return None
        body.extend(chunk)
        if not message.get("more_body", False):
            return bytes(body)


def _replay_body(body: bytes) -> Receive:
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


class _DuplicateJSONKey(ValueError):
    pass


def _preparse_rejects_json(body: bytes) -> bool:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJSONKey
            result[key] = value
        return result

    try:
        json.loads(body, object_pairs_hook=unique_object)
    except _DuplicateJSONKey:
        return True
    except (RecursionError, ValueError):
        return True
    return False


def _with_no_store(send: Send) -> Send:
    async def send_no_store(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = [
                (name, value)
                for name, value in message.get("headers", [])
                if name.lower() != b"cache-control"
            ]
            message = {**message, "headers": [*headers, _NO_STORE]}
        await send(message)

    return send_no_store


async def _send_error(
    error: ApplicationError,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    from starlette.responses import JSONResponse

    response = JSONResponse(
        status_code=error.status_code,
        content={"detail": error.detail},
        headers=error.headers,
    )
    await response(scope, receive, send)


async def _send_duplicate_key_error(scope: Scope, receive: Receive, send: Send) -> None:
    from starlette.responses import JSONResponse

    response = JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "type": "value_error",
                    "loc": ["body"],
                    "msg": "Value is invalid.",
                }
            ]
        },
    )
    await response(scope, receive, send)
