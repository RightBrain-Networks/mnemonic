"""Bounded FIFO admission around actual native embedding calls.

The synchronous worker owns the permit. Cancelling its HTTP awaiter can stop
queued/future calls, but never releases capacity while native code still runs.
"""

from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from threading import Condition, Event
from time import monotonic
from typing import TYPE_CHECKING

from mnemonic_api.artifact_tokenizer import PassageTokenizer, passage_tokenizer
from mnemonic_api.search_ranking import SemanticReason
from mnemonic_api.search_timing import record_timing

if TYPE_CHECKING:
    from mnemonic_api.semantic import Embedder

INFERENCE_REQUEST_KEY = "semantic_inference_request"


class InferenceCapacityError(Exception):
    """The bounded queue was full or its configured wait expired."""


def inference_failure_reason(error: Exception) -> SemanticReason:
    if isinstance(error, InferenceCapacityError):
        return "capacity_exhausted"
    return "deadline_exceeded" if isinstance(error, TimeoutError) else "model_failure"


@dataclass(slots=True)
class InferenceRequest:
    deadline: float
    operation: str = "work_semantic"
    cancelled: Event = field(default_factory=Event)

    def require_time(self) -> None:
        if self.cancelled.is_set() or monotonic() >= self.deadline:
            raise TimeoutError("Semantic request ended")


@dataclass(slots=True)
class InferenceResources:
    slots: int = 1
    queue_size: int = 8
    wait_seconds: float = 5.0
    _condition: Condition = field(default_factory=Condition, repr=False)
    _waiting: deque[object] = field(default_factory=deque, repr=False)
    _active: int = field(default=0, repr=False)

    def cancel(self, request: InferenceRequest) -> None:
        with self._condition:
            request.cancelled.set()
            self._condition.notify_all()

    @contextmanager
    def reserve(self, request: InferenceRequest) -> Iterator[None]:
        started = monotonic()
        outcome = "completed"
        try:
            self._acquire(request)
        except Exception as error:
            outcome = inference_failure_reason(error)
            raise
        finally:
            record_timing(request.operation, "inference_queue", started, outcome=outcome)
        try:
            request.require_time()
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def _acquire(self, request: InferenceRequest) -> None:
        deadline = min(request.deadline, monotonic() + self.wait_seconds)
        with self._condition:
            request.require_time()
            if self._active < self.slots and not self._waiting:
                self._active += 1
                return
            if len(self._waiting) >= self.queue_size:
                raise InferenceCapacityError("Semantic queue is full")
            ticket = object()
            self._waiting.append(ticket)
            try:
                self._wait_for_turn(ticket, request, deadline)
                self._waiting.popleft()
                self._active += 1
            finally:
                if ticket in self._waiting:
                    self._waiting.remove(ticket)
                self._condition.notify_all()

    def _wait_for_turn(self, ticket: object, request: InferenceRequest, deadline: float) -> None:
        while True:
            request.require_time()
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise InferenceCapacityError("Semantic queue wait expired")
            if self._waiting[0] is ticket and self._active < self.slots:
                return
            self._condition.wait(remaining)


@dataclass(frozen=True, slots=True)
class QueuedEmbedder:
    delegate: Embedder
    resources: InferenceResources
    request: InferenceRequest

    def _run[T](self, operation: Callable[[], T]) -> T:
        with self.resources.reserve(self.request):
            result = operation()
            self.request.require_time()
            return result

    def embed_query(self, text: str) -> list[float]:
        return self._run(lambda: self.delegate.embed_query(text))

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._run(lambda: self.delegate.embed_documents(texts))

    def passage_tokenizer(self) -> PassageTokenizer:
        return self._run(lambda: passage_tokenizer(self.delegate))


def with_inference_deadline(embedder: Embedder, deadline: float) -> Embedder:
    if isinstance(embedder, QueuedEmbedder):
        request = replace(embedder.request, deadline=min(deadline, embedder.request.deadline))
        return replace(embedder, request=request)
    return embedder
