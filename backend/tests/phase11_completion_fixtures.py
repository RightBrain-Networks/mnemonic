"""Offline exact-0019 fixtures for historical migration and audit tests.

These helpers write SQL and use only pure wire/canonical receipt models. They do
not run the current application, ORM writers, or current lifecycle services on a
historical schema. Sparse historical receipts intentionally have no job report.
"""

import json
import secrets
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import Connection, Engine, text

from mnemonic_api.schemas import (
    APIModel,
    CheckpointCreate,
    CheckpointRead,
    WorkCompletionCreate,
    WorkCompletionRead,
    WorkCreation,
    WorkItemCreate,
)
from mnemonic_api.services.client_operations import (
    OperationKind,
    prepare_client_operation,
    request_fingerprint,
)

HEAD = "0019_structured_completion_evidence"
WORK_COLUMNS = (
    "id, project_id, title, summary, status, priority, initial_checkpoint_id, "
    "version, created_at, updated_at"
)


def _require_boundary(connection: Connection) -> None:
    assert connection.scalars(text("SELECT version_num FROM alembic_version")).all() == [HEAD]


def _insert(connection: Connection, table: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Insert caller-owned fixture columns; identifiers never come from application input."""
    columns = ", ".join(fields)
    values = ", ".join(
        f"CAST(:{key} AS jsonb)" if key in {"metadata", "source_metadata"} else f":{key}"
        for key in fields
    )
    parameters = {
        key: json.dumps(value) if key in {"metadata", "source_metadata"} else value
        for key, value in fields.items()
    }
    return dict(
        connection.execute(
            text(f"INSERT INTO {table} ({columns}) VALUES ({values}) RETURNING *"), parameters
        )
        .mappings()
        .one()
    )


def _event(
    connection: Connection,
    project: dict[str, Any],
    work: dict[str, Any],
    event_type: str,
    metadata: dict[str, Any],
    *,
    checkpoint: dict[str, Any] | None = None,
) -> None:
    _insert(
        connection,
        "work_events",
        {
            "project_id": UUID(str(project["id"])),
            "work_item_id": UUID(str(work["id"])),
            "event_type": event_type,
            "actor_kind": "client",
            "origin": "live",
            "actor_client": checkpoint["source_client"] if checkpoint else "pytest",
            "actor_session_id": checkpoint["source_session_id"]
            if checkpoint
            else "legacy-transition",
            "actor_model": checkpoint["source_model"] if checkpoint else None,
            "checkpoint_id": checkpoint["id"] if checkpoint else None,
            "metadata": metadata,
            "created_at": checkpoint["created_at"] if checkpoint else work["updated_at"],
        },
    )


def create_work(
    engine: Engine,
    project: dict[str, Any] | None = None,
    *,
    payload: dict[str, Any] | None = None,
    title: str = "Historical completion evidence fixture",
    summary: str = "Retain the Phase 11 migration and audit contract.",
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = payload or {}
    with engine.begin() as connection:
        _require_boundary(connection)
        if project is None:
            project = _insert(
                connection,
                "projects",
                {
                    "id": uuid4(),
                    "name": "Historical Phase 11 fixture",
                    "slug": uuid4().hex,
                },
            )
        initial_id = uuid4()
        work = _insert(
            connection,
            "work_items",
            {
                "id": uuid4(),
                "project_id": project["id"],
                "title": payload.get("title", title),
                "summary": payload.get("summary", summary),
                "status": payload.get("status", "pending"),
                "priority": payload.get("priority", 50),
                "initial_checkpoint_id": initial_id,
            },
        )
        checkpoint = _insert(
            connection,
            "checkpoints",
            {
                "id": initial_id,
                "work_item_id": work["id"],
                "kind": "context",
                "prompt": "Retain this historical work fixture.",
                "source_client": "pytest",
                "source_session_id": "legacy-create",
                **payload.get("initial_checkpoint", {}),
                "created_at": work["created_at"],
            },
        )
        _event(
            connection,
            project,
            work,
            "work_created",
            {
                "initial": {
                    key: work[key] for key in ("title", "summary", "status", "priority", "version")
                }
            },
            checkpoint=checkpoint,
        )
        if payload.get("client_operation_id") is not None:
            _other_receipt(
                connection,
                "create_work",
                project,
                {},
                WorkItemCreate.model_validate(payload),
                _creation_body(work, checkpoint),
                201,
            )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return project, work


def _evidence(
    connection: Connection,
    project: dict[str, Any],
    work: dict[str, Any],
    checkpoint: dict[str, Any],
    payload: WorkCompletionCreate,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "verification_results": [],
        "artifact_references": [],
    }
    if payload.completion_evidence is None:
        return result
    for table in result:
        for position, child in enumerate(getattr(payload.completion_evidence, table)):
            fields = child.model_dump(exclude_none=True)
            row = _insert(
                connection,
                table,
                {
                    **fields,
                    "id": uuid4(),
                    "project_id": project["id"],
                    "work_item_id": work["id"],
                    "completion_checkpoint_id": checkpoint["id"],
                    "position": position,
                    "created_at": checkpoint["created_at"],
                },
            )
            result[table].append(
                {
                    **fields,
                    **{
                        key: row[key]
                        for key in (
                            "id",
                            "work_item_id",
                            "completion_checkpoint_id",
                            "position",
                            "created_at",
                        )
                    },
                }
            )
    return result


def _receipt(
    connection: Connection,
    project: dict[str, Any],
    work: dict[str, Any],
    payload: WorkCompletionCreate,
    body: dict[str, Any],
) -> None:
    if payload.client_operation_id is None:
        return
    prepared = prepare_client_operation(
        "complete_work", UUID(str(project["id"])), {"work_item_id": UUID(str(work["id"]))}, payload
    )
    assert prepared.canonical_bytes is not None
    salt = secrets.token_bytes(32)
    _insert(
        connection,
        "client_operations",
        {
            "project_id": project["id"],
            "client_operation_id": payload.client_operation_id,
            "operation_kind": "complete_work",
            "request_fingerprint_salt": salt,
            "request_fingerprint": request_fingerprint(salt, prepared.canonical_bytes),
        },
    )
    connection.execute(
        text("""
        UPDATE client_operations SET state='completed', response_status=200,
            response_body=CAST(:body AS jsonb), mutation_applied=true,
            completed_at=clock_timestamp()
        WHERE project_id=:project_id AND client_operation_id=:operation_id
    """),
        {
            "body": json.dumps(body),
            "project_id": project["id"],
            "operation_id": payload.client_operation_id,
        },
    )


def complete(
    engine: Engine,
    project: dict[str, Any],
    work: dict[str, Any],
    version: int,
    session: str,
    *,
    evidence: dict[str, Any] | None | object = ...,
    operation_id: str | object = ...,
    checkpoint: dict[str, Any] | None = None,
) -> httpx.Response:
    request: dict[str, Any] = {
        "expected_version": version,
        "checkpoint": {
            "prompt": f"Completed and reviewed in {session}.",
            "source_client": "pytest",
            "source_session_id": session,
            "source_model": "test-model",
            "repository_branch": "Work/Phase11 Exact",
            "verified_against": "7ad62e4",
            "tags": ["evidence", "backend"],
            "source_metadata": {"suite": "postgres"},
        },
    }
    if checkpoint is not None:
        request["checkpoint"] = checkpoint
    if operation_id is not ...:
        request["client_operation_id"] = operation_id
    if evidence is not ...:
        request["completion_evidence"] = evidence
    payload = WorkCompletionCreate.model_validate(request)
    with engine.begin() as connection:
        _require_boundary(connection)
        checkpoint = _insert(
            connection,
            "checkpoints",
            {
                **payload.checkpoint.model_dump(),
                "id": uuid4(),
                "work_item_id": work["id"],
                "kind": "completion",
            },
        )
        children = _evidence(connection, project, work, checkpoint, payload)
        completed_work = dict(
            connection.execute(
                text(f"""
            UPDATE work_items SET status='done', version=version+1, updated_at=:created_at
            WHERE id=:work_id AND status='pending' AND version=:version
            RETURNING {WORK_COLUMNS}
        """),
                {"created_at": checkpoint["created_at"], "work_id": work["id"], "version": version},
            )
            .mappings()
            .one()
        )
        _event(
            connection,
            project,
            completed_work,
            "work_completed",
            {
                "from_status": "pending",
                "to_status": "done",
                "work_version": version + 1,
            },
            checkpoint=checkpoint,
        )
        response: dict[str, Any] = {
            "work_item": completed_work,
            "checkpoint": {key: checkpoint[key] for key in CheckpointRead.model_fields},
        }
        if any(children.values()):
            response["completion_evidence"] = children
        body = WorkCompletionRead.model_validate(response).model_dump(mode="json")
        _receipt(connection, project, work, payload, body)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return httpx.Response(200, json=body)


def _transition(
    engine: Engine,
    project: dict[str, Any],
    work: dict[str, Any],
    version: int,
    status: str,
) -> httpx.Response:
    with engine.begin() as connection:
        _require_boundary(connection)
        old_status = connection.scalar(text("SELECT status FROM work_items WHERE id=:id"), work)
        updated = dict(
            connection.execute(
                text(f"""
            UPDATE work_items SET status=:status, version=version+1, updated_at=clock_timestamp()
            WHERE id=:work_id AND version=:version RETURNING {WORK_COLUMNS}
        """),
                {"status": status, "work_id": work["id"], "version": version},
            )
            .mappings()
            .one()
        )
        event_type = "work_reopened" if status == "pending" else "work_status_changed"
        _event(
            connection,
            project,
            updated,
            event_type,
            {
                "changes": {"status": {"before": old_status, "after": status}},
                "from_status": old_status,
                "to_status": status,
                "work_version": version + 1,
            },
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return httpx.Response(200, json={"status": status, "version": version + 1})


def reopen(
    engine: Engine,
    project: dict[str, Any],
    work: dict[str, Any],
    version: int,
) -> httpx.Response:
    return _transition(engine, project, work, version, "pending")


def set_terminal(
    engine: Engine,
    project: dict[str, Any],
    work: dict[str, Any],
    status: str,
) -> httpx.Response:
    return _transition(engine, project, work, 1, status)


def _creation_body(work: dict[str, Any], checkpoint: dict[str, Any]) -> dict[str, Any]:
    return WorkCreation.model_validate(
        {
            "work_item": {key: work[key] for key in WORK_COLUMNS.split(", ")},
            "initial_checkpoint": {key: checkpoint[key] for key in CheckpointRead.model_fields},
        }
    ).model_dump(mode="json")


def _other_receipt(
    connection: Connection,
    kind: OperationKind,
    project: dict[str, Any],
    target: dict[str, UUID],
    payload: APIModel,
    body: dict[str, Any],
    status: int,
) -> None:
    operation_id = getattr(payload, "client_operation_id", None)
    if operation_id is None:
        return
    prepared = prepare_client_operation(kind, UUID(str(project["id"])), target, payload)
    assert prepared.canonical_bytes is not None
    salt = secrets.token_bytes(32)
    _insert(
        connection,
        "client_operations",
        {
            "project_id": project["id"],
            "client_operation_id": operation_id,
            "operation_kind": kind,
            "request_fingerprint_salt": salt,
            "request_fingerprint": request_fingerprint(salt, prepared.canonical_bytes),
        },
    )
    connection.execute(
        text("""
        UPDATE client_operations SET state='completed',response_status=:status,
            response_body=CAST(:body AS jsonb),mutation_applied=true,completed_at=clock_timestamp()
        WHERE project_id=:project AND client_operation_id=:operation
    """),
        {
            "status": status,
            "body": json.dumps(body),
            "project": project["id"],
            "operation": operation_id,
        },
    )


def create_fixture(
    engine: Engine, project: dict[str, Any], payload: dict[str, Any]
) -> httpx.Response:
    project, work = create_work(engine, project, payload=payload)
    with engine.connect() as connection:
        checkpoint = dict(
            connection.execute(
                text("SELECT * FROM checkpoints WHERE id=:id"),
                {"id": work["initial_checkpoint_id"]},
            )
            .mappings()
            .one()
        )
    return httpx.Response(201, json=_creation_body(work, checkpoint))


def checkpoint_fixture(
    engine: Engine,
    project: dict[str, Any],
    work: dict[str, Any],
    request: dict[str, Any],
) -> httpx.Response:
    payload = CheckpointCreate.model_validate(request)
    with engine.begin() as connection:
        _require_boundary(connection)
        checkpoint = _insert(
            connection,
            "checkpoints",
            {
                **payload.model_dump(exclude={"lease_token", "client_operation_id"}),
                "id": uuid4(),
                "work_item_id": work["id"],
            },
        )
        _event(
            connection,
            project,
            work,
            "checkpoint_added",
            {"checkpoint_kind": payload.kind},
            checkpoint=checkpoint,
        )
        body = CheckpointRead.model_validate(
            {key: checkpoint[key] for key in CheckpointRead.model_fields}
        ).model_dump(mode="json")
        _other_receipt(
            connection,
            "add_checkpoint",
            project,
            {"work_item_id": UUID(str(work["id"]))},
            payload,
            body,
            201,
        )
    return httpx.Response(201, json=body)


def complete_fixture(
    engine: Engine,
    project: dict[str, Any],
    work: dict[str, Any],
    request: dict[str, Any],
) -> httpx.Response:
    return complete(
        engine,
        project,
        work,
        request["expected_version"],
        request["checkpoint"]["source_session_id"],
        checkpoint=request["checkpoint"],
        evidence=request.get("completion_evidence", ...),
        operation_id=request.get("client_operation_id", ...),
    )


def reopen_fixture(
    engine: Engine,
    project: dict[str, Any],
    work: dict[str, Any],
    request: dict[str, Any],
) -> httpx.Response:
    assert request["status"] == "pending"
    return reopen(engine, project, work, request["expected_version"])
