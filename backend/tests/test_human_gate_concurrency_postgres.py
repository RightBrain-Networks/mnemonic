"""Deterministic Phase 7–8 human-gate lock-order and revision races.

Same-key and different-key resolution races remain covered by
``test_human_gates_postgres.py``; this module covers the rest of plan §12.5.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import WorkGate
from mnemonic_api.schemas import (
    CheckpointCreate,
    CompletionCheckpointCreate,
    HumanGateRequestCreate,
    HumanGateResolutionCreate,
    MutationActor,
    ProgressEventCreate,
    RelationshipCreate,
    WorkClaimCreate,
    WorkItemPatch,
)
from mnemonic_api.services.gates import request_human_gate, resolve_human_gate
from mnemonic_api.services.leases import claim_lease_record
from mnemonic_api.services.relationships import add_relationship_record
from mnemonic_api.services.work_events import append_progress_event
from mnemonic_api.services.work_items import (
    append_checkpoint_record,
    complete_work_record,
    delete_work_record,
    require_work_item,
    update_work_record,
)

pytestmark = pytest.mark.postgres


@dataclass(frozen=True)
class Outcome:
    value: object | None = None
    code: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.code is None


@contextmanager
def held_database(engine: Engine) -> Iterator[tuple[Session, Connection, int]]:
    """Yield a session whose outer transaction is committed explicitly by the test."""
    with engine.connect() as connection:
        transaction = connection.begin()
        database = Session(bind=connection, expire_on_commit=False)
        holder_pid = int(connection.scalar(text("SELECT pg_backend_pid()")))
        try:
            yield database, connection, holder_pid
        finally:
            database.close()
            if transaction.is_active:
                transaction.rollback()


def launch_operation(
    executor: ThreadPoolExecutor,
    engine: Engine,
    operation: Callable[[Session], object],
) -> tuple[Future[Outcome], int]:
    ready = Event()
    state: dict[str, int] = {}

    def run() -> Outcome:
        with Session(engine, expire_on_commit=False) as database:
            state["pid"] = int(database.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            try:
                value = operation(database)
                database.commit()
                return Outcome(value=value)
            except ApplicationError as error:
                database.rollback()
                return Outcome(code=str(error.detail["code"]))

    future = executor.submit(run)
    assert ready.wait(timeout=5), "The competing PostgreSQL session did not start."
    return future, state["pid"]


def wait_until_blocked(
    engine: Engine,
    *,
    holder_pid: int,
    waiter_pid: int | None,
    future: Future[Any],
) -> None:
    """Observe a real lock wait without using wall-clock sleeps for synchronization."""
    deadline = monotonic() + 5
    with engine.connect() as observer:
        while monotonic() < deadline:
            if future.done():
                pytest.fail(
                    "Competing operation finished before the held lock: "
                    f"{future.result()!r}"
                )
            if waiter_pid is None:
                blocked = bool(
                    observer.scalar(
                        text(
                            """
                            SELECT EXISTS (
                                SELECT 1
                                FROM pg_stat_activity AS waiting
                                WHERE CAST(:holder_pid AS integer)
                                    = ANY(pg_blocking_pids(waiting.pid))
                            )
                            """
                        ),
                        {"holder_pid": holder_pid},
                    )
                )
            else:
                blocked = bool(
                    observer.scalar(
                        text(
                            "SELECT CAST(:holder_pid AS integer) "
                            "= ANY(pg_blocking_pids(CAST(:waiter_pid AS integer)))"
                        ),
                        {"holder_pid": holder_pid, "waiter_pid": waiter_pid},
                    )
                )
            if blocked:
                return
    pytest.fail("The competing PostgreSQL session never waited on the held transaction.")


def application_code(error: ApplicationError) -> str:
    return str(error.detail["code"])


def collection(project_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/work-items"


def create_work(api: TestClient, project_id: UUID, work_payload: dict, title: str) -> dict:
    response = api.post(collection(project_id), json={**work_payload, "title": title})
    assert response.status_code == 201, response.text
    return response.json()


def request_payload(label: str, *, operation_id: UUID | None = None) -> HumanGateRequestCreate:
    return HumanGateRequestCreate(
        question=f"Which deterministic outcome applies to {label}?",
        requested_by_client="pytest-agent",
        requested_by_session_id="phase78-concurrency",
        requested_by_model="test-model",
        client_operation_id=operation_id,
    )


def resolution_payload(
    label: str,
    *,
    revision: dict[str, object] | None = None,
    operation_id: UUID | None = None,
) -> HumanGateResolutionCreate:
    return HumanGateResolutionCreate(
        resolution=f"Use the deterministic result for {label}.",
        resolved_by_client="dashboard",
        resolved_by_session_id="phase78-human",
        resolved_by_model="human-ui",
        acknowledge_context_change=revision is not None,
        reviewed_context_revision=revision,
        client_operation_id=operation_id,
    )


def actor(label: str) -> MutationActor:
    return MutationActor(
        actor_client="pytest",
        actor_session_id=f"phase78-{label}",
        actor_model=None,
    )


def checkpoint_payload(work_payload: dict, label: str) -> CheckpointCreate:
    return CheckpointCreate.model_validate(
        {
            **work_payload["initial_checkpoint"],
            "kind": "context",
            "prompt": f"Deterministic context mutation {label}.",
            "source_session_id": f"phase78-{label}",
        }
    )


def completion_payload(work_payload: dict, label: str) -> CompletionCheckpointCreate:
    return CompletionCheckpointCreate.model_validate(
        {
            **work_payload["initial_checkpoint"],
            "prompt": f"Deterministic completion {label}.",
            "source_session_id": f"phase78-{label}",
        }
    )


def request_gate(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    label: str,
) -> object:
    return request_human_gate(
        database,
        project_id,
        work_item_id,
        request_payload(label),
    )


def resolve_gate(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    gate_id: UUID,
    label: str,
    *,
    revision: dict[str, object] | None = None,
    operation_id: UUID | None = None,
) -> object:
    return resolve_human_gate(
        database,
        project_id,
        work_item_id,
        gate_id,
        resolution_payload(label, revision=revision, operation_id=operation_id),
    )


def claim_work(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    request_id: str,
) -> object:
    work = require_work_item(database, project_id, work_item_id, lock=True)
    return claim_lease_record(
        database,
        work,
        WorkClaimCreate(
            holder_client="pytest-agent",
            holder_session_id="phase78-claim",
            claim_request_id=request_id,
        ),
        ttl_seconds=300,
    )


def checkpoint_work(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    work_payload: dict,
    label: str,
) -> object:
    work = require_work_item(database, project_id, work_item_id, lock=True)
    return append_checkpoint_record(database, work, checkpoint_payload(work_payload, label))


def relationship_work(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    other_work_item_id: UUID,
    label: str,
) -> object:
    return add_relationship_record(
        database,
        project_id,
        RelationshipCreate(
            relationship_type="blocks",
            source_work_item_id=work_item_id,
            target_work_item_id=other_work_item_id,
            created_by_client="pytest",
            created_by_session_id=f"phase78-{label}",
        ),
    )


def terminal_work(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    work_payload: dict,
    action: str,
) -> object:
    work = require_work_item(database, project_id, work_item_id, lock=True)
    if action == "complete":
        return complete_work_record(
            database,
            work,
            expected_version=1,
            payload=completion_payload(work_payload, action),
        )
    if action in {"wont-do", "promoted"}:
        update_work_record(
            database,
            work,
            WorkItemPatch(
                expected_version=1,
                status=action,
                actor=actor(action),
            ),
        )
        return work
    if action == "delete":
        delete_work_record(database, work, expected_version=1, actor=actor(action))
        return work
    raise AssertionError(f"Unknown terminal action {action}")


def progress_work(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    label: str,
) -> object:
    return append_progress_event(
        database,
        project_id,
        work_item_id,
        ProgressEventCreate(
            body=f"Unrelated progress {label}.",
            actor=actor(label),
        ),
        bearer_key="mnemonic-integration-test-key-32-characters",
    )


def create_gate(api: TestClient, project_id: UUID, work_item_id: UUID, label: str) -> dict:
    response = api.post(
        f"{collection(project_id)}/{work_item_id}/gates",
        json=request_payload(label, operation_id=uuid4()).model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def gate_history(api: TestClient, project_id: UUID, work_item_id: UUID) -> list[dict]:
    response = api.get(f"{collection(project_id)}/{work_item_id}/gates?limit=100")
    assert response.status_code == 200, response.text
    return response.json()["items"]


@pytest.mark.parametrize("claim_kind", ["fresh", "replacement"])
@pytest.mark.parametrize("gate_first", [True, False], ids=["gate-first", "claim-first"])
def test_request_vs_fresh_or_replacement_claim_in_both_orders(
    api: TestClient,
    project: dict,
    work_payload: dict,
    postgres_engine: Engine,
    claim_kind: str,
    gate_first: bool,
) -> None:
    api.app.state.settings.human_gate_requests_enabled = True
    project_id = UUID(project["id"])
    created = create_work(api, project_id, work_payload, f"{claim_kind} claim race")
    work_item_id = UUID(created["work_item"]["id"])
    old_request_id = "expired-retained-claim"
    if claim_kind == "replacement":
        claim = api.post(
            f"{collection(project_id)}/{work_item_id}/claim",
            json={
                "holder_client": "old-agent",
                "holder_session_id": "old-session",
                "claim_request_id": old_request_id,
            },
        )
        assert claim.status_code == 200, claim.text
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE work_leases SET "
                    "acquired_at = clock_timestamp() - interval '3 seconds', "
                    "renewed_at = clock_timestamp() - interval '2 seconds', "
                    "expires_at = clock_timestamp() - interval '1 second' "
                    "WHERE work_item_id = :work_item_id"
                ),
                {"work_item_id": work_item_id},
            )

    new_request_id = f"{claim_kind}-winning-claim"
    with held_database(postgres_engine) as (holder, connection, holder_pid):
        transaction = connection.get_transaction()
        assert transaction is not None
        if gate_first:
            request_gate(holder, project_id, work_item_id, "gate-first-claim")
            def competing(database: Session) -> object:
                return claim_work(database, project_id, work_item_id, new_request_id)
        else:
            claim_work(holder, project_id, work_item_id, new_request_id)
            def competing(database: Session) -> object:
                return request_gate(
                    database, project_id, work_item_id, "claim-first-gate"
                )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future, waiter_pid = launch_operation(executor, postgres_engine, competing)
            wait_until_blocked(
                postgres_engine,
                holder_pid=holder_pid,
                waiter_pid=waiter_pid,
                future=future,
            )
            transaction.commit()
            outcome = future.result(timeout=10)

    assert outcome.code == "work_gated" if gate_first else outcome.succeeded
    with postgres_engine.connect() as connection:
        gate_count = connection.scalar(
            select(text("count(*)")).select_from(WorkGate).where(
                WorkGate.work_item_id == work_item_id
            )
        )
        retained_request = connection.scalar(
            text(
                "SELECT claim_request_id FROM work_leases "
                "WHERE work_item_id = :work_item_id"
            ),
            {"work_item_id": work_item_id},
        )
    assert gate_count == 1
    if gate_first:
        assert retained_request == (old_request_id if claim_kind == "replacement" else None)
    else:
        assert retained_request == new_request_id


@pytest.mark.parametrize("action", ["complete", "wont-do", "promoted", "delete"])
@pytest.mark.parametrize("gate_first", [True, False], ids=["gate-first", "terminal-first"])
def test_request_vs_every_terminal_or_delete_path_in_both_orders(
    api: TestClient,
    project: dict,
    work_payload: dict,
    postgres_engine: Engine,
    action: str,
    gate_first: bool,
) -> None:
    api.app.state.settings.human_gate_requests_enabled = True
    project_id = UUID(project["id"])
    created = create_work(api, project_id, work_payload, f"{action} race")
    work_item_id = UUID(created["work_item"]["id"])

    with held_database(postgres_engine) as (holder, connection, holder_pid):
        transaction = connection.get_transaction()
        assert transaction is not None
        if gate_first:
            request_gate(holder, project_id, work_item_id, f"before-{action}")
            def competing(database: Session) -> object:
                return terminal_work(
                    database, project_id, work_item_id, work_payload, action
                )
        else:
            terminal_work(holder, project_id, work_item_id, work_payload, action)
            def competing(database: Session) -> object:
                return request_gate(
                    database, project_id, work_item_id, f"after-{action}"
                )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future, waiter_pid = launch_operation(executor, postgres_engine, competing)
            wait_until_blocked(
                postgres_engine,
                holder_pid=holder_pid,
                waiter_pid=waiter_pid,
                future=future,
            )
            transaction.commit()
            outcome = future.result(timeout=10)

    if gate_first:
        assert outcome.code == "work_gated"
        assert len(gate_history(api, project_id, work_item_id)) == 1
        current = api.get(f"{collection(project_id)}/{work_item_id}")
        assert current.status_code == 200
        assert current.json()["status"] == "pending"
    else:
        assert outcome.code == ("work_item_not_found" if action == "delete" else "work_not_pending")
        with postgres_engine.connect() as connection:
            assert connection.scalar(
                select(text("count(*)")).select_from(WorkGate).where(
                    WorkGate.work_item_id == work_item_id
                )
            ) == 0


@pytest.mark.parametrize("action", ["fresh-claim", "complete", "new-gate"])
@pytest.mark.parametrize(
    "resolution_first", [True, False], ids=["resolution-first", "competing-first"]
)
def test_resolution_vs_claim_completion_or_new_gate_in_both_orders(
    api: TestClient,
    project: dict,
    work_payload: dict,
    postgres_engine: Engine,
    action: str,
    resolution_first: bool,
) -> None:
    api.app.state.settings.human_gate_requests_enabled = True
    project_id = UUID(project["id"])
    created = create_work(api, project_id, work_payload, f"resolution {action} race")
    work_item_id = UUID(created["work_item"]["id"])
    gate = create_gate(api, project_id, work_item_id, f"resolve-vs-{action}")
    gate_id = UUID(gate["id"])

    def competing(database: Session) -> object:
        if action == "fresh-claim":
            return claim_work(database, project_id, work_item_id, "resolution-race-claim")
        if action == "complete":
            return terminal_work(database, project_id, work_item_id, work_payload, "complete")
        return request_gate(database, project_id, work_item_id, "second-gate")

    with held_database(postgres_engine) as (holder, connection, holder_pid):
        transaction = connection.get_transaction()
        assert transaction is not None
        if resolution_first:
            resolve_gate(holder, project_id, work_item_id, gate_id, action)
            waiter_operation = competing
            holder_succeeded = True
        else:
            try:
                competing(holder)
                holder_succeeded = True
            except ApplicationError as error:
                holder_succeeded = False
                assert application_code(error) == "work_gated"
            def waiter_operation(database: Session) -> object:
                return resolve_gate(
                    database, project_id, work_item_id, gate_id, action
                )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future, waiter_pid = launch_operation(
                executor, postgres_engine, waiter_operation
            )
            wait_until_blocked(
                postgres_engine,
                holder_pid=holder_pid,
                waiter_pid=waiter_pid,
                future=future,
            )
            if holder_succeeded:
                transaction.commit()
            else:
                transaction.rollback()
            outcome = future.result(timeout=10)

    assert outcome.succeeded
    history = gate_history(api, project_id, work_item_id)
    assert sum(item["status"] == "resolved" for item in history) == 1
    assert sum(item["status"] == "unresolved" for item in history) == (
        1 if action == "new-gate" else 0
    )
    if resolution_first and action == "fresh-claim":
        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT claim_request_id FROM work_leases "
                    "WHERE work_item_id = :work_item_id"
                ),
                {"work_item_id": work_item_id},
            ) == "resolution-race-claim"
    if resolution_first and action == "complete":
        assert api.get(f"{collection(project_id)}/{work_item_id}").json()["status"] == "done"


@pytest.mark.parametrize("mutation", ["checkpoint", "relationship"])
@pytest.mark.parametrize("gate_operation", ["request", "resolution"])
@pytest.mark.parametrize(
    "gate_first", [True, False], ids=["gate-operation-first", "mutation-first"]
)
def test_gate_request_or_resolution_vs_tokenless_context_and_graph_mutations(
    api: TestClient,
    project: dict,
    work_payload: dict,
    postgres_engine: Engine,
    mutation: str,
    gate_operation: str,
    gate_first: bool,
) -> None:
    api.app.state.settings.human_gate_requests_enabled = True
    project_id = UUID(project["id"])
    created = create_work(api, project_id, work_payload, f"{gate_operation} {mutation} race")
    work_item_id = UUID(created["work_item"]["id"])
    other = create_work(api, project_id, work_payload, f"other endpoint {uuid4()}")
    other_work_item_id = UUID(other["work_item"]["id"])
    existing_gate = (
        create_gate(api, project_id, work_item_id, f"resolve-vs-{mutation}")
        if gate_operation == "resolution"
        else None
    )
    gate_id = UUID(existing_gate["id"]) if existing_gate is not None else None

    def mutate(database: Session) -> object:
        if mutation == "checkpoint":
            return checkpoint_work(
                database, project_id, work_item_id, work_payload, gate_operation
            )
        return relationship_work(
            database,
            project_id,
            work_item_id,
            other_work_item_id,
            gate_operation,
        )

    def gate_mutation(database: Session) -> object:
        if gate_operation == "request":
            return request_gate(database, project_id, work_item_id, mutation)
        assert gate_id is not None
        return resolve_gate(database, project_id, work_item_id, gate_id, mutation)

    with held_database(postgres_engine) as (holder, connection, holder_pid):
        transaction = connection.get_transaction()
        assert transaction is not None
        first = gate_mutation if gate_first else mutate
        second = mutate if gate_first else gate_mutation
        first(holder)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future, waiter_pid = launch_operation(executor, postgres_engine, second)
            wait_until_blocked(
                postgres_engine,
                holder_pid=holder_pid,
                waiter_pid=waiter_pid,
                future=future,
            )
            transaction.commit()
            outcome = future.result(timeout=10)

    if gate_operation == "resolution" and not gate_first:
        assert outcome.code == "gate_context_changed"
        current = gate_history(api, project_id, work_item_id)[0]
        fresh = api.post(
            f"{collection(project_id)}/{work_item_id}/gates/{gate_id}/resolve",
            json=resolution_payload(
                f"fresh-{mutation}",
                revision=current["current_context_revision"],
                operation_id=uuid4(),
            ).model_dump(mode="json"),
        )
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["context_changed_at_resolution"] is True
    else:
        assert outcome.succeeded

    history = gate_history(api, project_id, work_item_id)
    focal = next(item for item in history if gate_id is None or item["id"] == str(gate_id))
    if gate_operation == "request":
        assert focal["context_changed_since_request"] is gate_first
    elif gate_first:
        assert focal["status"] == "resolved"
        assert focal["context_changed_at_resolution"] is False
        assert focal["context_changed_since_request"] is True
    else:
        assert focal["status"] == "resolved"
        assert focal["context_changed_at_resolution"] is True


def test_reviewed_revision_b_is_rejected_after_locked_revision_c_then_fresh_c_succeeds(
    api: TestClient,
    project: dict,
    work_payload: dict,
    postgres_engine: Engine,
) -> None:
    api.app.state.settings.human_gate_requests_enabled = True
    project_id = UUID(project["id"])
    created = create_work(api, project_id, work_payload, "reviewed B to C")
    work_item_id = UUID(created["work_item"]["id"])
    other = create_work(api, project_id, work_payload, "reviewed C endpoint")
    other_work_item_id = UUID(other["work_item"]["id"])
    gate = create_gate(api, project_id, work_item_id, "reviewed-B")
    gate_id = UUID(gate["id"])

    checkpoint = api.post(
        f"{collection(project_id)}/{work_item_id}/checkpoints",
        json=checkpoint_payload(work_payload, "revision-b").model_dump(mode="json"),
    )
    assert checkpoint.status_code == 201, checkpoint.text
    review_b_response = api.get(
        f"{collection(project_id)}/{work_item_id}/gates/{gate_id}/context"
    )
    assert review_b_response.status_code == 200, review_b_response.text
    review_b = next(
        item
        for item in review_b_response.json()["unresolved_gates"]
        if item["id"] == str(gate_id)
    )["current_context_revision"]
    stale_operation_id = uuid4()

    with held_database(postgres_engine) as (holder, connection, holder_pid):
        transaction = connection.get_transaction()
        assert transaction is not None
        relationship_work(
            holder,
            project_id,
            work_item_id,
            other_work_item_id,
            "revision-c",
        )

        def post_stale_resolution() -> object:
            with TestClient(api.app) as client:
                client.headers["Authorization"] = api.headers["Authorization"]
                return client.post(
                    f"{collection(project_id)}/{work_item_id}/gates/{gate_id}/resolve",
                    json=resolution_payload(
                        "stale-b",
                        revision=review_b,
                        operation_id=stale_operation_id,
                    ).model_dump(mode="json"),
                )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(post_stale_resolution)
            wait_until_blocked(
                postgres_engine,
                holder_pid=holder_pid,
                waiter_pid=None,
                future=future,
            )
            transaction.commit()
            stale = future.result(timeout=10)

    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "gate_context_changed"
    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM client_operations "
                "WHERE client_operation_id = :operation_id"
            ),
            {"operation_id": stale_operation_id},
        ) == 0

    review_c_response = api.get(
        f"{collection(project_id)}/{work_item_id}/gates/{gate_id}/context"
    )
    assert review_c_response.status_code == 200, review_c_response.text
    review_c = next(
        item
        for item in review_c_response.json()["unresolved_gates"]
        if item["id"] == str(gate_id)
    )["current_context_revision"]
    assert review_c != review_b
    fresh_operation_id = uuid4()
    fresh = api.post(
        f"{collection(project_id)}/{work_item_id}/gates/{gate_id}/resolve",
        json=resolution_payload(
            "fresh-c",
            revision=review_c,
            operation_id=fresh_operation_id,
        ).model_dump(mode="json"),
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["resolved_context_revision"] == review_c
    assert fresh.json()["context_change_acknowledged"] is True
    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM client_operations "
                "WHERE client_operation_id = :operation_id"
            ),
            {"operation_id": fresh_operation_id},
        ) == 1


def test_unrelated_progress_commits_while_focal_gate_work_is_contended(
    api: TestClient,
    project: dict,
    work_payload: dict,
    postgres_engine: Engine,
) -> None:
    api.app.state.settings.human_gate_requests_enabled = True
    project_id = UUID(project["id"])
    focal = create_work(api, project_id, work_payload, "contended focal work")
    unrelated = create_work(api, project_id, work_payload, "independent progress work")
    focal_id = UUID(focal["work_item"]["id"])
    unrelated_id = UUID(unrelated["work_item"]["id"])
    gate = create_gate(api, project_id, focal_id, "contended-gate")
    gate_id = UUID(gate["id"])

    with held_database(postgres_engine) as (holder, connection, holder_pid):
        transaction = connection.get_transaction()
        assert transaction is not None
        resolve_gate(holder, project_id, focal_id, gate_id, "held-resolution")
        with ThreadPoolExecutor(max_workers=2) as executor:
            focal_future, focal_pid = launch_operation(
                executor,
                postgres_engine,
                lambda database: request_gate(
                    database, project_id, focal_id, "queued-focal-request"
                ),
            )
            wait_until_blocked(
                postgres_engine,
                holder_pid=holder_pid,
                waiter_pid=focal_pid,
                future=focal_future,
            )
            unrelated_future, _ = launch_operation(
                executor,
                postgres_engine,
                lambda database: progress_work(
                    database, project_id, unrelated_id, "during-contention"
                ),
            )
            unrelated_outcome = unrelated_future.result(timeout=5)
            assert unrelated_outcome.succeeded
            with postgres_engine.connect() as observer:
                assert observer.scalar(
                    text(
                        "SELECT count(*) FROM work_events "
                        "WHERE work_item_id = :work_item_id AND event_type = 'progress'"
                    ),
                    {"work_item_id": unrelated_id},
                ) == 1
            transaction.commit()
            focal_outcome = focal_future.result(timeout=10)

    assert focal_outcome.succeeded
    history = gate_history(api, project_id, focal_id)
    assert [item["status"] for item in history].count("resolved") == 1
    assert [item["status"] for item in history].count("unresolved") == 1
