"""Offline schema-0023 completion receipts using only pure wire canonicalization."""

import json
import secrets
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from mnemonic_api.phase12_schemas import JobCompletionReportRead
from mnemonic_api.schemas import (
    CompletionCheckpointRead,
    WorkCompletionCreate,
    WorkCompletionRead,
    WorkItemRead,
)
from mnemonic_api.services.client_operations import prepare_client_operation, request_fingerprint


def _row(connection: Connection, table: str, record_id: object) -> dict:
    return connection.scalar(
        text(f"SELECT to_jsonb(row) FROM {table} row WHERE id=:id"), {"id": record_id}
    )


def _wire_evidence(row: dict) -> dict:
    body = {k: v for k, v in row.items() if k != "project_id" and v is not None}
    for field in ("created_at", "observed_at"):
        if field in body:
            body[field] = datetime.fromisoformat(body[field]).isoformat().replace("+00:00", "Z")
    return body


def seal_historical_receipt(connection: Connection, work_id: object, checkpoint_id: object) -> int:
    work = _row(connection, "work_items", work_id)
    checkpoint = _row(connection, "checkpoints", checkpoint_id)
    report = connection.scalar(
        text(
            "SELECT to_jsonb(row) FROM job_completion_reports row "
            "WHERE completion_checkpoint_id=:id"
        ),
        {"id": checkpoint_id},
    )
    report["prompt_revision"] = str(report["prompt_revision"])
    report["closeout_event_id"] = str(report["closeout_event_id"])
    evidence = {
        name: [
            _wire_evidence(row)
            for row in connection.scalars(
                text(
                    f"SELECT to_jsonb(row) FROM {table} row "
                    "WHERE completion_checkpoint_id=:id "
                    "ORDER BY position"
                ),
                {"id": checkpoint_id},
            )
        ]
        for name, table in (
            ("verification_results", "verification_results"),
            ("artifact_references", "artifact_references"),
        )
    }
    response = WorkCompletionRead.model_validate(
        {
            "work_item": {k: v for k, v in work.items() if k in WorkItemRead.model_fields},
            "checkpoint": {
                k: v for k, v in checkpoint.items() if k in CompletionCheckpointRead.model_fields
            },
            "job_completion_report": {
                k: v for k, v in report.items() if k in JobCompletionReportRead.model_fields
            },
            "completion_evidence": evidence,
        }
    ).model_dump(mode="json")
    operation_id = uuid4()
    payload = WorkCompletionCreate.model_validate(
        {
            "expected_version": work["version"] - 1,
            "client_operation_id": operation_id,
            "checkpoint": {
                k: checkpoint[k]
                for k in (
                    "prompt",
                    "source_client",
                    "source_session_id",
                    "repository_branch",
                    "affected_paths",
                )
            },
            "job_completion_report": {
                k: report[k] for k in ("summary", "fyi_items", "prompt_revision")
            },
            "completion_evidence": {
                key: [
                    {
                        k: v
                        for k, v in row.items()
                        if k
                        not in (
                            "id",
                            "work_item_id",
                            "completion_checkpoint_id",
                            "position",
                            "created_at",
                        )
                    }
                    for row in rows
                ]
                for key, rows in evidence.items()
            },
        }
    )
    prepared = prepare_client_operation(
        "complete_work", UUID(work["project_id"]), {"work_item_id": UUID(work["id"])}, payload
    )
    assert prepared.canonical_bytes is not None
    salt = secrets.token_bytes(32)
    receipt_id = connection.scalar(
        text(
            "INSERT INTO client_operations(project_id,client_operation_id,operation_kind,"
            "request_fingerprint_salt,request_fingerprint) "
            "VALUES(:project,:operation,'complete_work',:salt,:fingerprint) RETURNING id"
        ),
        {
            "project": work["project_id"],
            "operation": operation_id,
            "salt": salt,
            "fingerprint": request_fingerprint(salt, prepared.canonical_bytes),
        },
    )
    connection.execute(
        text(
            "UPDATE client_operations SET state='completed',response_status=200,"
            "response_body=CAST(:body AS jsonb),mutation_applied=true,"
            "completed_at=clock_timestamp() "
            "WHERE id=:id"
        ),
        {"id": receipt_id, "body": json.dumps(response, ensure_ascii=False, separators=(",", ":"))},
    )
    return receipt_id
