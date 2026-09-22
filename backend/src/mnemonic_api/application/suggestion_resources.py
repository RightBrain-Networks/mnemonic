"""Per-process resource bounds for the duplicate-suggestion safe read."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from urllib.parse import parse_qsl

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mnemonic_api.config import Settings
from mnemonic_api.errors import (
    ApplicationError,
    duplicate_suggestion_busy,
    duplicate_suggestion_unavailable,
    request_body_too_large,
)
from mnemonic_api.inference import INFERENCE_REQUEST_KEY, InferenceRequest, InferenceResources
from mnemonic_api.search_timing import record_timing, timed_phase

SUGGESTION_WORK_KEY = "duplicate_suggestion_owned_work"
SUGGESTION_DEADLINE_KEY = "duplicate_suggestion_deadline"
_NO_STORE = (b"cache-control", b"no-store")


class _ClientDisconnected(Exception):
    pass


@dataclass(slots=True)
class OwnedSuggestionWork:
    """Completion handles survive response expiry; workers are never cancelled by awaiters.

    At most one worker is started at a time per admitted request. Request capacity
    therefore bounds the executor population, including late SQL/native inference.
    """

    tasks: list[asyncio.Task[Any]] = field(default_factory=list)

    def start[T](self, operation: Callable[[], T]) -> asyncio.Task[T]:
        task = asyncio.create_task(asyncio.to_thread(operation))
        self.tasks.append(task)
        task.add_done_callback(_observe_worker_completion)
        return task

    @property
    def pending(self) -> bool:
        return any(not task.done() for task in self.tasks)

    async def finish(self) -> None:
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


def _observe_worker_completion(task: asyncio.Task[Any]) -> None:
    # A worker may fail just as its await budget expires. Retrieve the exception
    # even when finalization sees an already-completed handle; never log its text.
    if not task.cancelled():
        task.exception()


def suggestion_owned_work(scope: Scope) -> OwnedSuggestionWork:
    owner = scope.get("state", {}).get(SUGGESTION_WORK_KEY)
    if not isinstance(owner, OwnedSuggestionWork):
        raise RuntimeError("Duplicate suggestion work ownership is unavailable")
    return owner


@dataclass(slots=True)
class DuplicateSuggestionResources:
    """Bound suggestion workers separately from individual native model calls."""

    request_slots: asyncio.Semaphore
    request_wait_seconds: float
    body_max_bytes: int
    timeout_seconds: float
    inference: InferenceResources = field(default_factory=InferenceResources)
    draining_tasks: set[asyncio.Task[None]] = field(default_factory=set, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> DuplicateSuggestionResources:
        return cls(
            request_slots=asyncio.Semaphore(settings.duplicate_suggestion_request_slots),
            request_wait_seconds=settings.duplicate_suggestion_request_wait_ms / 1_000,
            body_max_bytes=settings.duplicate_suggestion_body_max_bytes,
            timeout_seconds=float(settings.duplicate_suggestion_timeout_seconds),
            inference=InferenceResources(
                slots=settings.duplicate_suggestion_inference_slots,
                queue_size=settings.duplicate_suggestion_inference_queue_size,
                wait_seconds=settings.duplicate_suggestion_inference_wait_ms / 1_000,
            ),
        )

    async def acquire_request(self) -> bool:
        started = monotonic()
        acquired = await _bounded_acquire(self.request_slots, self.request_wait_seconds)
        record_timing("duplicate_suggestions", "request_queue", started,
                      outcome="completed" if acquired else "capacity_exhausted")
        return acquired

    def retain_resources_until_done(
        self,
        task: asyncio.Task[None],
        *,
        request_acquired: bool = True,
        owned_work: OwnedSuggestionWork | None = None,
    ) -> None:
        drain = asyncio.create_task(
            _release_resources_when_done(
                task,
                self,
                request_acquired=request_acquired,
                owned_work=owned_work,
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
        operation = _search_operation(scope)
        if operation is None:
            await self._serve(scope, receive, send)
            return
        with timed_phase(operation, "total"):
            await self._serve(scope, receive, send)

    async def _serve(self, scope: Scope, receive: Receive, send: Send) -> None:
        if _is_artifact_search_request(scope) and _artifact_library_disabled(scope):
            await self.app(scope, receive, send)
            return
        if _is_unified_search_request(scope) or _is_artifact_search_request(scope):
            await _serve_unified_search(self.app, self.resources, scope, receive, send)
            return
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


def _search_operation(scope: Scope) -> str | None:
    if _is_unified_search_request(scope):
        return "unified_search"
    if scope.get("type") != "http":
        return None
    parts = str(scope.get("path", "")).strip("/").split("/")
    if len(parts) < 5 or parts[:3] != ["api", "v1", "projects"]:
        return None
    methods = {"search": "POST", "duplicate-suggestions": "POST", "work-items": "GET"}
    if len(parts) == 5 and scope.get("method") == methods.get(parts[4], ""):
        return {"search": "unified_search", "duplicate-suggestions": "duplicate_suggestions",
                "work-items": "work_search"}[parts[4]]
    if (len(parts) == 6 and scope.get("method") == "POST"
            and parts[4] in {"artifacts", "transcripts"} and parts[5] == "search-content"):
        return parts[4] + "_search"
    return None


async def _serve_acquired_request(
    app: ASGIApp,
    resources: DuplicateSuggestionResources,
    scope: Scope,
    receive: Receive,
    send: Send,
    deadline: float,
) -> None:
    release_resources = True
    app_task: asyncio.Task[None] | None = None
    owned_work = OwnedSuggestionWork()
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

        budget = InferenceRequest(deadline, "duplicate_suggestions")
        budget.require_time()
        state = scope.setdefault("state", {})
        state[SUGGESTION_WORK_KEY] = owned_work
        state[SUGGESTION_DEADLINE_KEY] = deadline
        state[INFERENCE_REQUEST_KEY] = budget
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
            resources.retain_resources_until_done(
                app_task, owned_work=owned_work
            )
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
            resources, app_task, owned_work
        )
        raise
    except TimeoutError:
        await _send_error(duplicate_suggestion_unavailable(), scope, receive, send)
    finally:
        _cancel_inference(scope, resources)
        if release_resources:
            _finalize_suggestion_resources(resources, app_task, owned_work)


def _finalize_suggestion_resources(
    resources: DuplicateSuggestionResources,
    app_task: asyncio.Task[None] | None,
    owned_work: OwnedSuggestionWork,
) -> None:
    if owned_work.pending and app_task is not None:
        resources.retain_resources_until_done(
            app_task, owned_work=owned_work
        )
    else:
        _release_resources(resources)


def _release_immediately_after_cancel(
    resources: DuplicateSuggestionResources,
    app_task: asyncio.Task[None] | None,
    owned_work: OwnedSuggestionWork,
) -> bool:
    if app_task is None or (app_task.done() and not owned_work.pending):
        return True
    resources.retain_resources_until_done(
        app_task, owned_work=owned_work
    )
    return False


async def _release_resources_when_done(
    task: asyncio.Task[None],
    resources: DuplicateSuggestionResources,
    *,
    request_acquired: bool,
    owned_work: OwnedSuggestionWork | None = None,
) -> None:
    try:
        await asyncio.shield(task)
    except (asyncio.CancelledError, Exception):
        pass
    finally:
        if owned_work is not None:
            await owned_work.finish()
        _release_resources(
            resources,
            request_acquired=request_acquired,
        )


def _release_resources(
    resources: DuplicateSuggestionResources,
    *,
    request_acquired: bool = True,
) -> None:
    if request_acquired:
        resources.request_slots.release()


def _remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - monotonic())


def suggestion_request_deadline(scope: Scope) -> float:
    state = scope.get("state")
    value = state.get(SUGGESTION_DEADLINE_KEY) if isinstance(state, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise RuntimeError("Duplicate suggestion request deadline is unavailable")


def _cancel_inference(scope: Scope, resources: DuplicateSuggestionResources) -> None:
    budget = scope.get("state", {}).get(INFERENCE_REQUEST_KEY)
    if isinstance(budget, InferenceRequest):
        resources.inference.cancel(budget)


async def _serve_unified_search(
    app: ASGIApp,
    resources: DuplicateSuggestionResources,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    """Bound the JSON safe read and share semantic admission with work search."""
    send = _with_no_store(send)
    maximum_bytes = 4096 if _is_artifact_search_request(scope) else 16_384
    if _declared_oversize(scope, maximum_bytes):
        await _send_error(request_body_too_large(), scope, receive, send)
        return
    try:
        body = await asyncio.wait_for(
            _read_bounded_body(receive, maximum_bytes), timeout=resources.timeout_seconds,
        )
    except _ClientDisconnected:
        return
    except TimeoutError:
        await _send_duplicate_key_error(scope, receive, send)
        return
    if body is None:
        await _send_error(request_body_too_large(), scope, receive, send)
        return
    if _preparse_rejects_json(body):
        if _is_artifact_search_request(scope):
            await app(scope, _replay_body(body), send)
        else:
            await _send_duplicate_key_error(scope, receive, send)
        return
    replay = _replay_body(body)
    if _unified_semantic_requested(
        json.loads(body), artifacts_enabled=not _artifact_library_disabled(scope),
    ):
        await _serve_semantic_search(app, resources, scope, replay, send)
    else:
        await app(scope, replay, send)


def _unified_semantic_requested(payload: object, *, artifacts_enabled: bool = True) -> bool:
    if not isinstance(payload, dict):
        return False
    filters = payload.get("filters")
    facets = ("work_items", "artifacts") if artifacts_enabled else ("work_items",)
    sources = [filters.get(key) for key in facets] \
        if isinstance(filters, dict) else []
    values = [payload.get("semantic"), *(source.get("semantic") for source in sources
                                       if isinstance(source, dict))]
    # Match Pydantic's boolean forms; invalid values remain its concern.
    return any(value is True or value == 1 or (
        isinstance(value, str) and value.lower() in {"1", "on", "t", "true", "y", "yes"}
    ) for value in values)


def _artifact_library_disabled(scope: Scope) -> bool:
    app = scope.get("app")
    settings = getattr(getattr(app, "state", None), "settings", None)
    return settings is not None and settings.artifact_max_bytes <= 0


def _is_artifact_search_request(scope: Scope) -> bool:
    if scope.get("type") != "http" or scope.get("method") != "POST":
        return False
    parts = str(scope.get("path", "")).strip("/").split("/")
    return (len(parts) == 6 and parts[:3] == ["api", "v1", "projects"]
            and parts[4:] == ["artifacts", "search-content"])


def _is_unified_search_request(scope: Scope) -> bool:
    if scope.get("type") != "http" or scope.get("method") != "POST":
        return False
    parts = str(scope.get("path", "")).strip("/").split("/")
    return parts == ["api", "v1", "search"] or (
        len(parts) == 5 and parts[:3] == ["api", "v1", "projects"] and parts[4] == "search"
    )


async def _serve_semantic_search(
    app: ASGIApp,
    resources: DuplicateSuggestionResources,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    scope.setdefault("state", {})[INFERENCE_REQUEST_KEY] = InferenceRequest(
        monotonic() + resources.timeout_seconds, _search_operation(scope) or "work_semantic",
    )

    async def run_app() -> None:
        await app(scope, receive, send)

    app_task = asyncio.create_task(run_app())
    try:
        await asyncio.shield(app_task)
    except asyncio.CancelledError:
        if not app_task.done():
            resources.retain_resources_until_done(
                app_task,
                request_acquired=False,
            )
        raise
    finally:
        _cancel_inference(scope, resources)


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
