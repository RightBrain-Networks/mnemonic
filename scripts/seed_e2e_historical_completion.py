"""Offline, disposable-only 0018 fixture for permanent sparse receipt replay.

Mount this script read-only into the stopped E2E API image and run it after an
empty downgrade to 0018. It never runs current domain services on an old schema.
Only pure wire canonicalization is shared with the current receipt contract.
"""

import argparse
import json
import os
import secrets
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from mnemonic_api.schemas import WorkCompletionCreate, WorkCompletionRead
from mnemonic_api.services.client_operations import (
    prepare_client_operation,
    request_fingerprint,
)
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

EXPECTED_HEAD = "0018_repository_freshness"


def _checkpoint_fields(run_id: UUID, *, completion: bool) -> dict[str, Any]:
    role = "complete" if completion else "create"
    return {
        "prompt": (
            "Historical completion recorded without structured evidence before Phase 11."
            if completion
            else "Initial context for the historical completion migration fixture."
        ),
        "source_client": "playwright-api",
        "source_session_id": f"phase11-historical-{role}-{run_id}",
        "source_model": None,
        "source_session_url": None,
        "repository_branch": None,
        "verified_against": None,
        "tags": ["historical-completion", "phase-11"],
        "source_metadata": {},
    }


def _insert_checkpoint(
    connection: Connection,
    work_id: UUID,
    checkpoint_id: UUID,
    fields: dict[str, Any],
    created_at: Any,
    *,
    completion: bool,
) -> dict[str, Any]:
    parameters = {
        **fields,
        "id": checkpoint_id,
        "work_item_id": work_id,
        "kind": "completion" if completion else "context",
        "created_at": created_at,
    }
    connection.execute(
        text("""
        INSERT INTO checkpoints(id,work_item_id,kind,prompt,source_client,source_session_id,
                                tags,created_at)
        VALUES(:id,:work_item_id,:kind,:prompt,:source_client,:source_session_id,:tags,:created_at)
    """),
        parameters,
    )
    return {**parameters, "migration_origin": None, "legacy_record_id": None}


def _insert_event(
    connection: Connection,
    project_id: UUID,
    work_id: UUID,
    checkpoint: dict[str, Any],
    event_type: str,
    metadata: dict[str, Any],
) -> None:
    connection.execute(
        text("""
        INSERT INTO work_events(project_id,work_item_id,event_type,actor_kind,actor_client,
            actor_session_id,checkpoint_id,metadata,origin,created_at)
        VALUES(:project_id,:work_id,:type,'client',:client,:session,:checkpoint,
               CAST(:metadata AS jsonb),'live',:created_at)
    """),
        {
            "project_id": project_id,
            "work_id": work_id,
            "type": event_type,
            "client": checkpoint["source_client"],
            "session": checkpoint["source_session_id"],
            "checkpoint": checkpoint["id"],
            "metadata": json.dumps(metadata),
            "created_at": checkpoint["created_at"],
        },
    )


def seed_historical_completion(connection: Connection, run_id: UUID) -> dict[str, Any]:
    """Seed an empty exact-0018 disposable schema in the caller's single transaction."""
    if connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalars().all() != [EXPECTED_HEAD]:
        raise RuntimeError(
            "Historical fixture requires the exact 0018 migration boundary"
        )
    if connection.scalar(text("SELECT count(*) FROM projects")) != 0:
        raise RuntimeError("Historical fixture requires an empty disposable schema")
    connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    project_id, work_id, initial_id, completion_id, operation_id = [
        uuid4() for _ in range(5)
    ]
    project_name = f"E2E Phase 1 {run_id}"
    title = f"Phase 11 migrated 0018 completion {str(run_id)[:8]}"
    summary = (
        "A receipt-backed evidence-free completion carried through migration 0019."
    )
    created_at = connection.scalar(text("SELECT clock_timestamp()"))
    completed_at = created_at + timedelta(microseconds=1)
    connection.execute(
        text(
            "INSERT INTO projects(id,name,slug,description) VALUES(:id,:name,:slug,:description)"
        ),
        {
            "id": project_id,
            "name": project_name,
            "slug": "e2e-" + str(run_id),
            "description": "Disposable historical completion acceptance fixture.",
        },
    )
    connection.execute(
        text("""
        INSERT INTO work_items(id,project_id,title,summary,status,priority,initial_checkpoint_id,
                               created_at,updated_at)
        VALUES(:id,:project,:title,:summary,'pending',31,:checkpoint,:created,:created)
    """),
        {
            "id": work_id,
            "project": project_id,
            "title": title,
            "summary": summary,
            "checkpoint": initial_id,
            "created": created_at,
        },
    )
    initial = _insert_checkpoint(
        connection,
        work_id,
        initial_id,
        _checkpoint_fields(run_id, completion=False),
        created_at,
        completion=False,
    )
    _insert_event(
        connection,
        project_id,
        work_id,
        initial,
        "work_created",
        {
            "initial": {
                "title": title,
                "summary": summary,
                "status": "pending",
                "priority": 31,
                "version": 1,
            }
        },
    )
    completion_fields = _checkpoint_fields(run_id, completion=True)
    checkpoint = _insert_checkpoint(
        connection,
        work_id,
        completion_id,
        completion_fields,
        completed_at,
        completion=True,
    )
    connection.execute(
        text(
            "UPDATE work_items SET status='done',version=2,updated_at=:time WHERE id=:id"
        ),
        {"id": work_id, "time": completed_at},
    )
    _insert_event(
        connection,
        project_id,
        work_id,
        checkpoint,
        "work_completed",
        {
            "from_status": "pending",
            "to_status": "done",
            "work_version": 2,
        },
    )
    request_body = {
        "expected_version": 1,
        "checkpoint": completion_fields,
        "client_operation_id": str(operation_id),
    }
    payload = WorkCompletionCreate.model_validate(request_body)
    prepared = prepare_client_operation(
        "complete_work", project_id, {"work_item_id": work_id}, payload
    )
    assert prepared.canonical_bytes is not None
    response = WorkCompletionRead.model_validate(
        {
            "work_item": {
                "id": work_id,
                "project_id": project_id,
                "title": title,
                "summary": summary,
                "status": "done",
                "priority": 31,
                "initial_checkpoint_id": initial_id,
                "version": 2,
                "created_at": created_at,
                "updated_at": completed_at,
            },
            "checkpoint": checkpoint,
        }
    ).model_dump(mode="json")
    response_body = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    salt = secrets.token_bytes(32)
    connection.execute(
        text("""
        INSERT INTO client_operations(project_id,client_operation_id,operation_kind,
                                      request_fingerprint_salt,request_fingerprint)
        VALUES(:project,:operation,'complete_work',:salt,:fingerprint)
    """),
        {
            "project": project_id,
            "operation": operation_id,
            "salt": salt,
            "fingerprint": request_fingerprint(salt, prepared.canonical_bytes),
        },
    )
    connection.execute(
        text("""
        UPDATE client_operations SET state='completed',response_status=200,
            response_body=CAST(:body AS jsonb),mutation_applied=true,completed_at=clock_timestamp()
        WHERE project_id=:project AND client_operation_id=:operation
    """),
        {"body": response_body, "project": project_id, "operation": operation_id},
    )
    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return {
        "projectId": str(project_id),
        "projectName": project_name,
        "runId": str(run_id),
        "historicalCompletion": {
            "title": title,
            "workItemId": str(work_id),
            "completionCheckpointId": str(completion_id),
            "clientOperationId": str(operation_id),
            "requestBody": request_body,
            "responseBody": response_body,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=UUID, required=True)
    args = parser.parse_args()
    url = make_url(os.environ["DATABASE_URL"])
    if (url.database, url.username, url.host) != (
        "mnemonic_e2e",
        "mnemonic_e2e",
        "postgres",
    ):
        raise RuntimeError("Refusing to seed outside the disposable E2E database")
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            fixture = seed_historical_completion(connection, args.run_id)
        print(json.dumps(fixture, ensure_ascii=False, separators=(",", ":")))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
