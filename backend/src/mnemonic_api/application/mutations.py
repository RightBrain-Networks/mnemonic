"""One lifecycle for the sixteen receipt-protected REST mutations.

Every route that accepts an optional ``client_operation_id`` runs the same
sequence, so it is written once here and each route contributes only its
domain work through ``execute``:

1. **Reserve.** Canonicalize the request and reserve its receipt, or find the
   receipt an earlier identical request already completed.
2. **Replay.** A completed receipt answers with its stored response; no domain
   code runs. The transaction still commits to release the reservation.
3. **Execute.** Otherwise ``execute`` runs the route's domain services inside
   the same transaction and returns the public result.
4. **Complete.** The registry checks that result against the request and for
   echoed secrets, then stores it on the receipt.
5. **Commit.** One commit covers the domain change, its authoritative events,
   and the receipt, so no client ever sees a receipt without its effect.
6. **Trace.** The outcome stays on ``request.state`` for
   ``middleware.broadcast_successful_mutations``, which logs it and decides
   whether dashboards need an invalidation.

Unregistered mutations (project administration, claims, renewals) never pass
through here. Their missing trace is what keeps the middleware's method/path
fallback for them.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from mnemonic_api.application.guards import reject_registered_mutation_query
from mnemonic_api.application.state import settings_of
from mnemonic_api.errors import ApplicationError
from mnemonic_api.schemas import APIModel
from mnemonic_api.services.client_operations import (
    CompletedOperation,
    OperationKind,
    OperationSpec,
    ReplayedOperation,
    ReservationOutcome,
    complete_client_operation,
    prepare_client_operation,
    reserve_client_operation,
)
from mnemonic_api.services.project_mutations import project_mutation

type Outcome = Literal["executed", "replayed", "no_op", "conflict", "unavailable"]

_TRACE_STATE = "mnemonic_mutation_trace"
_RESERVATION_FAILURE_OUTCOMES: dict[object, Outcome] = {
    "client_operation_conflict": "conflict",
    "client_operation_unavailable": "unavailable",
}


@dataclass(slots=True)
class MutationTrace:
    """What the post-response middleware may learn about a registered mutation.

    ``outcome`` stays ``None`` when the route failed for a reason that is not
    the receipt's own (validation, a version conflict, a lost database); the
    middleware then reports ``unavailable`` for a 5xx and nothing otherwise.
    ``mutation_applied`` is ``False`` after a replay or a no-op, which
    suppresses the live-sync invalidation a 2xx would otherwise imply.
    """

    kind: OperationKind
    outcome: Outcome | None = None
    mutation_applied: bool | None = None


def mutation_trace(request: Request) -> MutationTrace | None:
    return getattr(request.state, _TRACE_STATE, None)


def record_mutation_trace(request: Request, trace: MutationTrace) -> None:
    """Make ``trace`` the request's outcome; the lifecycle below does this for its routes."""
    setattr(request.state, _TRACE_STATE, trace)


def run_registered_mutation[Payload: APIModel, Result: APIModel](
    kind: OperationKind,
    *,
    request: Request,
    database: Session,
    project_id: UUID,
    target: Mapping[str, UUID],
    payload: Payload,
    execute: Callable[[Payload], Result],
    additional_project_ids: Iterable[UUID] = (),
    before_commit: Callable[[], None] | None = None,
) -> JSONResponse:
    """Run ``execute`` under the lifecycle described in the module docstring.

    ``execute`` receives the domain payload: the validated request with its
    control field removed, so ``client_operation_id`` never crosses into a
    service.
    """
    reject_registered_mutation_query(request)
    trace = MutationTrace(kind)
    record_mutation_trace(request, trace)
    operation = _reserve(trace, request, database, project_id, target, payload)
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_success(trace, operation)
        return operation.response
    database.info["client_operation_keyed"] = (
        getattr(payload, "client_operation_id", None) is not None
    )
    with project_mutation(
        database,
        project_id,
        additional_project_ids=additional_project_ids,
        protected=True,
    ):
        result = execute(cast(Payload, operation.domain_payload))
        completed = complete_client_operation(
            database,
            operation,
            result,
            mutation_applied=_mutation_applied(operation.spec, result),
        )
        if before_commit is not None:
            before_commit()
        database.commit()
    _record_success(trace, completed)
    return completed.response


def _reserve(
    trace: MutationTrace,
    request: Request,
    database: Session,
    project_id: UUID,
    target: Mapping[str, UUID],
    payload: APIModel,
) -> ReservationOutcome:
    settings = settings_of(request)
    try:
        prepared = prepare_client_operation(
            trace.kind,
            project_id,
            target,
            payload,
            known_secret_values=(settings.api_key.get_secret_value(),),
        )
        return reserve_client_operation(
            database, prepared, wait_seconds=settings.client_operation_wait_seconds
        )
    except ApplicationError as exc:
        trace.outcome = _reservation_failure_outcome(exc)
        raise


def _reservation_failure_outcome(exc: ApplicationError) -> Outcome | None:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    return _RESERVATION_FAILURE_OUTCOMES.get(detail.get("code"))


def _mutation_applied(spec: OperationSpec, result: APIModel) -> bool:
    """Whether a fresh execution changed anything.

    Most registered writes always do. Adding an edge that exists, removing one
    that is gone, and releasing a lease that is not held succeed without
    applying anything; the registry names the result field (``created``,
    ``removed``, ``released``) that says which happened, and it re-checks this
    value against the stored result before completing the receipt.
    """
    if spec.mutation_applied_field is None:
        return True
    applied: bool = getattr(result, spec.mutation_applied_field)
    return applied


def _record_success(
    trace: MutationTrace, operation: CompletedOperation | ReplayedOperation
) -> None:
    trace.mutation_applied = operation.mutation_applied
    if not operation.mutation_applied:
        trace.outcome = "no_op"
    elif isinstance(operation, ReplayedOperation):
        trace.outcome = "replayed"
    else:
        trace.outcome = "executed"
