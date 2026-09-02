"""Focused PostgreSQL idempotency and fault acceptance for human gates."""

from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from threading import Barrier, Event
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import mnemonic_api.application as application_module
import mnemonic_api.services.client_operations as client_operations_module
from mnemonic_api.schemas import HumanGateRequestCreate, HumanGateResolutionCreate
from mnemonic_api.services.client_operations import (
    ReservedOperation,
    complete_client_operation,
    prepare_client_operation,
    reserve_client_operation,
)
from mnemonic_api.services.gates import request_human_gate, resolve_human_gate

pytestmark = pytest.mark.postgres
GateMutation = Literal["request", "resolve"]


@dataclass(frozen=True)
class GateCase:
    mutation: GateMutation
    project_id: UUID
    work_item_id: UUID
    gate_id: UUID | None
    path: str
    body: dict[str, Any]


@pytest.fixture
def gate_api(api: TestClient) -> TestClient:
    api.app.state.settings.human_gate_requests_enabled = True
    return api


def work_collection(project_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/work-items"


def request_body(operation_id: UUID | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "question": "Which atomic gate outcome should be retained?",
        "requested_by_client": "pytest-agent",
        "requested_by_session_id": "phase78-idempotency",
        "requested_by_model": "test-model",
    }
    if operation_id is not None:
        body["client_operation_id"] = str(operation_id)
    return body


def resolution_body(operation_id: UUID) -> dict[str, Any]:
    return {
        "resolution": "Use the one transactionally retained decision.",
        "resolved_by_client": "dashboard",
        "resolved_by_session_id": "phase78-idempotency-human",
        "resolved_by_model": "human-ui",
        "acknowledge_context_change": False,
        "client_operation_id": str(operation_id),
    }


def make_case(
    api: TestClient,
    project: dict[str, Any],
    work_payload: dict[str, Any],
    mutation: GateMutation,
    operation_id: UUID,
) -> GateCase:
    project_id = UUID(project["id"])
    created = api.post(
        work_collection(project_id),
        json={**work_payload, "title": f"Gate {mutation} idempotency target"},
    )
    assert created.status_code == 201, created.text
    work_item_id = UUID(created.json()["work_item"]["id"])
    gates_path = f"{work_collection(project_id)}/{work_item_id}/gates"
    if mutation == "request":
        return GateCase(
            mutation,
            project_id,
            work_item_id,
            None,
            gates_path,
            request_body(operation_id),
        )

    requested = api.post(gates_path, json=request_body())
    assert requested.status_code == 201, requested.text
    gate_id = UUID(requested.json()["id"])
    return GateCase(
        mutation,
        project_id,
        work_item_id,
        gate_id,
        f"{gates_path}/{gate_id}/resolve",
        resolution_body(operation_id),
    )


def prepared_case(case: GateCase):
    if case.mutation == "request":
        return prepare_client_operation(
            "request_human_input",
            case.project_id,
            {"work_item_id": case.work_item_id},
            HumanGateRequestCreate.model_validate(case.body),
        )
    assert case.gate_id is not None
    return prepare_client_operation(
        "resolve_human_input",
        case.project_id,
        {"work_item_id": case.work_item_id, "gate_id": case.gate_id},
        HumanGateResolutionCreate.model_validate(case.body),
    )


def execute_reserved_case(
    database: Session,
    case: GateCase,
    operation: ReservedOperation,
) -> dict[str, Any]:
    if case.mutation == "request":
        result = request_human_gate(
            database,
            case.project_id,
            case.work_item_id,
            operation.domain_payload,
        )
    else:
        assert case.gate_id is not None
        result = resolve_human_gate(
            database,
            case.project_id,
            case.work_item_id,
            case.gate_id,
            operation.domain_payload,
        )
    completed = complete_client_operation(
        database,
        operation,
        result,
        mutation_applied=True,
    )
    return json.loads(completed.response.body)


def durable_rows(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    tables = ("work_items", "work_gates", "work_events", "client_operations")
    with engine.connect() as connection:
        return {
            table: [
                dict(row)
                for row in connection.execute(
                    text(f"SELECT * FROM {table} ORDER BY id")
                ).mappings()
            ]
            for table in tables
        }


def assert_applied_exactly_once(
    engine: Engine,
    case: GateCase,
    response_body: dict[str, Any],
) -> None:
    state = durable_rows(engine)
    assert len(state["work_items"]) == len(state["work_gates"]) == 1
    work = state["work_items"][0]
    gate = state["work_gates"][0]
    assert work["id"] == case.work_item_id
    assert work["version"] == 1
    assert work["status"] == "pending"
    assert work["deleted_at"] is None
    assert gate["id"] == UUID(response_body["id"])
    assert gate["work_item_id"] == case.work_item_id

    gate_events = [
        event for event in state["work_events"] if event["gate_id"] is not None
    ]
    assert all(event["gate_id"] == gate["id"] for event in gate_events)
    assert len(state["client_operations"]) == 1
    receipt = state["client_operations"][0]
    assert receipt["state"] == "completed"
    assert receipt["mutation_applied"] is True
    assert receipt["response_body"] == response_body
    assert len(receipt["request_fingerprint_salt"]) == 32
    assert len(receipt["request_fingerprint"]) == 32

    if case.mutation == "request":
        assert len(state["work_events"]) == 2
        assert [event["event_type"] for event in gate_events] == [
            "human_attention_requested"
        ]
        assert gate["resolved_at"] is None
        assert gate_events[0]["body"] == case.body["question"]
        assert work["updated_at"] == gate["created_at"]
        assert gate_events[0]["created_at"] == gate["created_at"]
        assert receipt["operation_kind"] == "request_human_input"
        assert receipt["response_status"] == 201
        assert response_body["status"] == "unresolved"
    else:
        assert len(state["work_events"]) == 3
        assert [event["event_type"] for event in gate_events] == [
            "human_attention_requested",
            "human_attention_resolved",
        ]
        assert gate["resolved_at"] is not None
        assert gate["resolution"] == case.body["resolution"]
        assert work["updated_at"] == gate["resolved_at"]
        assert gate_events[1]["created_at"] == gate["resolved_at"]
        assert gate_events[1]["body"] == case.body["resolution"]
        assert receipt["operation_kind"] == "resolve_human_input"
        assert receipt["response_status"] == 200
        assert response_body["status"] == "resolved"


def wait_for_receipt_lock(
    engine: Engine,
    application_name: str,
    future: Future[dict[str, Any]],
) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if future.done():
            pytest.fail(f"The receipt waiter finished before rollback: {future.result()!r}")
        with engine.connect() as observer:
            blocked = bool(
                observer.scalar(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM pg_stat_activity
                            WHERE application_name = :application_name
                              AND wait_event_type = 'Lock'
                        )
                        """
                    ),
                    {"application_name": application_name},
                )
            )
        if blocked:
            return
        time.sleep(0.01)
    pytest.fail("The same-key waiter never blocked on the owner's receipt.")


@pytest.mark.parametrize("mutation", ["request", "resolve"])
def test_same_key_gate_mutation_concurrency_executes_exactly_once(
    gate_api: TestClient,
    project: dict[str, Any],
    work_payload: dict[str, Any],
    postgres_engine: Engine,
    mutation: GateMutation,
) -> None:
    case = make_case(gate_api, project, work_payload, mutation, uuid4())
    authorization = gate_api.headers["Authorization"]
    start = Barrier(3)

    def mutate():
        with TestClient(gate_api.app) as client:
            start.wait(timeout=5)
            return client.post(
                case.path,
                json=case.body,
                headers={"Authorization": authorization},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(mutate) for _ in range(2)]
        start.wait(timeout=5)
        responses = [future.result(timeout=10) for future in futures]

    expected_status = 201 if mutation == "request" else 200
    assert [response.status_code for response in responses] == [
        expected_status,
        expected_status,
    ]
    assert responses[0].content == responses[1].content
    assert_applied_exactly_once(postgres_engine, case, responses[0].json())


@pytest.mark.parametrize("mutation", ["request", "resolve"])
def test_waiter_executes_after_same_key_owner_reservation_rolls_back(
    gate_api: TestClient,
    project: dict[str, Any],
    work_payload: dict[str, Any],
    postgres_engine: Engine,
    mutation: GateMutation,
) -> None:
    case = make_case(gate_api, project, work_payload, mutation, uuid4())
    prepared = prepared_case(case)
    waiter_ready = Event()
    application_name = "mnemonic_gate_waiter_" + uuid4().hex

    def wait_then_execute() -> dict[str, Any]:
        with Session(postgres_engine, expire_on_commit=False) as database:
            database.execute(
                text("SELECT set_config('application_name', :name, true)"),
                {"name": application_name},
            )
            waiter_ready.set()
            operation = reserve_client_operation(database, prepared, wait_seconds=5)
            assert isinstance(operation, ReservedOperation)
            body = execute_reserved_case(database, case, operation)
            database.commit()
            return body

    with Session(postgres_engine, expire_on_commit=False) as owner:
        first = reserve_client_operation(owner, prepared, wait_seconds=5)
        assert isinstance(first, ReservedOperation)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(wait_then_execute)
            assert waiter_ready.wait(timeout=5)
            wait_for_receipt_lock(postgres_engine, application_name, future)
            owner.rollback()
            body = future.result(timeout=5)

    replay = gate_api.post(case.path, json=case.body)
    assert replay.status_code == (201 if mutation == "request" else 200)
    assert replay.json() == body
    assert_applied_exactly_once(postgres_engine, case, body)


@pytest.mark.parametrize("mutation", ["request", "resolve"])
@pytest.mark.parametrize("fault", ["response_render", "receipt_completion", "commit"])
def test_gate_mutation_fault_rolls_back_domain_activity_event_and_receipt(
    gate_api: TestClient,
    project: dict[str, Any],
    work_payload: dict[str, Any],
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    mutation: GateMutation,
    fault: str,
) -> None:
    case = make_case(gate_api, project, work_payload, mutation, uuid4())
    before = durable_rows(postgres_engine)

    if fault == "response_render":

        def fail_render(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise RuntimeError("private synthetic response render failure")

        monkeypatch.setattr(
            client_operations_module,
            "_render_registered_response",
            fail_render,
        )
        expected_code = "client_operation_unavailable"
    elif fault == "receipt_completion":
        original_complete = application_module.complete_client_operation

        def miss_pending_receipt(
            database: Session,
            operation: object,
            public_result: object,
            *,
            mutation_applied: bool,
        ) -> object:
            assert isinstance(operation, ReservedOperation)
            poisoned = replace(operation, receipt_id=operation.receipt_id + 1)
            return original_complete(
                database,
                poisoned,
                public_result,
                mutation_applied=mutation_applied,
            )

        monkeypatch.setattr(
            application_module,
            "complete_client_operation",
            miss_pending_receipt,
        )
        expected_code = "client_operation_unavailable"
    else:

        def fail_commit(self: Session) -> None:
            del self
            raise SQLAlchemyError("private synthetic commit failure")

        monkeypatch.setattr(Session, "commit", fail_commit)
        expected_code = "database_unavailable"

    response = gate_api.post(case.path, json=case.body)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == expected_code
    assert case.body["client_operation_id"] not in response.text
    assert durable_rows(postgres_engine) == before
