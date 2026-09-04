"""Phase 7–8 migration and database-invariant coverage."""

import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api.config import Settings
from mnemonic_api.main import create_app
from mnemonic_api.schemas import ProgressEventCreate, WorkEventRead
from mnemonic_api.services.client_operations import (
    prepare_client_operation,
    request_fingerprint,
)

from .conftest import BACKEND_DIR, recreate_disposable_schema

pytestmark = pytest.mark.postgres

_ALLOWED_OPERATION_KINDS = (
    "create_work",
    "add_checkpoint",
    "append_event",
    "add_relationship",
    "update_work",
    "defer_work",
    "complete_work",
    "delete_work",
    "remove_relationship",
    "release_claim",
    "request_human_input",
    "resolve_human_input",
)


@pytest.fixture(autouse=True)
def reset_phase78_rows(postgres_engine: Engine) -> Iterator[None]:
    recreate_disposable_schema(postgres_engine)
    yield


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _seed_work(
    connection: Connection,
    *,
    status: str = "pending",
) -> tuple[UUID, UUID, UUID, datetime]:
    project_id = uuid4()
    work_item_id = uuid4()
    checkpoint_id = uuid4()
    created_at = connection.scalar(text("SELECT clock_timestamp()"))
    connection.execute(
        text(
            """
            INSERT INTO projects (id, name, slug, created_at, updated_at)
            VALUES (:project_id, 'Gate database tests', :slug, :created_at, :created_at)
            """
        ),
        {
            "project_id": project_id,
            "slug": f"gate-tests-{project_id.hex}",
            "created_at": created_at,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO work_items (
                id, project_id, title, summary, status, priority,
                initial_checkpoint_id, version, created_at, updated_at
            ) VALUES (
                :work_item_id, :project_id, 'Gate fixture',
                'Database invariant fixture.', :status, 40,
                :checkpoint_id, 1, :created_at, :created_at
            )
            """
        ),
        {
            "work_item_id": work_item_id,
            "project_id": project_id,
            "status": status,
            "checkpoint_id": checkpoint_id,
            "created_at": created_at,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO checkpoints (
                id, work_item_id, kind, prompt, source_client,
                source_session_id, source_model, created_at
            ) VALUES (
                :checkpoint_id, :work_item_id, 'context', 'Initial context.',
                'pytest', 'gate-fixture', 'fixture-model', :created_at
            )
            """
        ),
        {
            "checkpoint_id": checkpoint_id,
            "work_item_id": work_item_id,
            "created_at": created_at,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO work_events (
                project_id, work_item_id, event_type, actor_kind,
                actor_client, actor_session_id, actor_model, checkpoint_id,
                metadata, origin, created_at
            ) VALUES (
                :project_id, :work_item_id, 'work_created', 'client',
                'pytest', 'gate-fixture', 'fixture-model', :checkpoint_id,
                CAST(:metadata AS jsonb), 'live', :created_at
            )
            """
        ),
        {
            "project_id": project_id,
            "work_item_id": work_item_id,
            "checkpoint_id": checkpoint_id,
            "metadata": json.dumps(
                {
                    "initial": {
                        "title": "Gate fixture",
                        "summary": "Database invariant fixture.",
                        "status": status,
                        "priority": 40,
                        "version": 1,
                    }
                }
            ),
            "created_at": created_at,
        },
    )
    return project_id, work_item_id, checkpoint_id, created_at


def _insert_gate(
    connection: Connection,
    *,
    project_id: UUID,
    work_item_id: UUID,
    checkpoint_id: UUID,
    question: str = "Which deployment window should be used?",
) -> tuple[UUID, int, datetime]:
    gate_id = uuid4()
    created_at = connection.scalar(text("SELECT clock_timestamp()"))
    attention_sequence = connection.scalar(
        text(
            """
            INSERT INTO work_gates (
                id, project_id, work_item_id, gate_type, question,
                requested_by_client, requested_by_session_id, requested_by_model,
                requested_work_version, requested_context_checkpoint_id,
                requested_relationship_event_count, created_at
            ) VALUES (
                :gate_id, :project_id, :work_item_id, 'human', :question,
                'pytest', 'gate-request', 'fixture-model',
                1, :checkpoint_id, 0, :created_at
            )
            RETURNING attention_sequence
            """
        ),
        {
            "gate_id": gate_id,
            "project_id": project_id,
            "work_item_id": work_item_id,
            "question": question,
            "checkpoint_id": checkpoint_id,
            "created_at": created_at,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO work_events (
                project_id, work_item_id, event_type, actor_kind,
                actor_client, actor_session_id, actor_model, body,
                gate_id, metadata, origin, created_at
            ) VALUES (
                :project_id, :work_item_id, 'human_attention_requested', 'client',
                'pytest', 'gate-request', 'fixture-model', :question,
                :gate_id, CAST(:metadata AS jsonb), 'live', :created_at
            )
            """
        ),
        {
            "project_id": project_id,
            "work_item_id": work_item_id,
            "question": question,
            "gate_id": gate_id,
            "metadata": json.dumps({"gate_id": str(gate_id), "gate_type": "human"}),
            "created_at": created_at,
        },
    )
    return gate_id, attention_sequence, created_at


def _resolve_gate(
    connection: Connection,
    *,
    project_id: UUID,
    work_item_id: UUID,
    gate_id: UUID,
    checkpoint_id: UUID,
    resolution: str = "Use the Tuesday maintenance window.",
) -> datetime:
    resolved_at = connection.scalar(text("SELECT clock_timestamp()"))
    connection.execute(
        text(
            """
            UPDATE work_gates
            SET resolved_at = :resolved_at,
                resolution = :resolution,
                resolved_by_client = 'dashboard',
                resolved_by_session_id = 'human-session',
                resolved_by_model = NULL,
                resolved_work_version = 1,
                resolved_context_checkpoint_id = :checkpoint_id,
                resolved_relationship_event_count = 0
            WHERE id = :gate_id
            """
        ),
        {
            "resolved_at": resolved_at,
            "resolution": resolution,
            "checkpoint_id": checkpoint_id,
            "gate_id": gate_id,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO work_events (
                project_id, work_item_id, event_type, actor_kind,
                actor_client, actor_session_id, body, gate_id,
                metadata, origin, created_at
            ) VALUES (
                :project_id, :work_item_id, 'human_attention_resolved', 'client',
                'dashboard', 'human-session', :resolution, :gate_id,
                CAST(:metadata AS jsonb), 'live', :resolved_at
            )
            """
        ),
        {
            "project_id": project_id,
            "work_item_id": work_item_id,
            "resolution": resolution,
            "gate_id": gate_id,
            "metadata": json.dumps({"gate_id": str(gate_id), "gate_type": "human"}),
            "resolved_at": resolved_at,
        },
    )
    return resolved_at


def _insert_lease(
    connection: Connection,
    *,
    project_id: UUID,
    work_item_id: UUID,
) -> UUID:
    generation_id = uuid4()
    acquired_at = connection.scalar(text("SELECT clock_timestamp()"))
    expires_at = acquired_at + timedelta(minutes=15)
    connection.execute(
        text(
            """
            INSERT INTO work_leases (
                work_item_id, holder_client, holder_session_id,
                claim_request_id, lease_token, lease_generation_id,
                acquired_at, renewed_at, expires_at
            ) VALUES (
                :work_item_id, 'pytest', 'lease-holder', 'claim-request',
                'lease-token', :generation_id, :acquired_at, :acquired_at, :expires_at
            )
            """
        ),
        {
            "work_item_id": work_item_id,
            "generation_id": generation_id,
            "acquired_at": acquired_at,
            "expires_at": expires_at,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO work_events (
                project_id, work_item_id, event_type, actor_kind,
                actor_client, actor_session_id, lease_generation_id,
                metadata, origin, created_at
            ) VALUES (
                :project_id, :work_item_id, 'work_claimed', 'client',
                'pytest', 'lease-holder', :generation_id,
                CAST(:metadata AS jsonb), 'live', :acquired_at
            )
            """
        ),
        {
            "project_id": project_id,
            "work_item_id": work_item_id,
            "generation_id": generation_id,
            "metadata": json.dumps({"expires_at": _utc_iso(expires_at)}),
            "acquired_at": acquired_at,
        },
    )
    return generation_id


@pytest.mark.parametrize(
    ("case", "match"),
    (
        ("gate_type", "ck_work_gates_gate_type_valid"),
        ("blank_question", "ck_work_gates_question_valid"),
        ("overlong_question", "ck_work_gates_question_valid"),
        ("cross_project", "work gate requires visible pending work"),
        ("cross_work_checkpoint", "work gate request revision does not match retained state"),
        ("deferred_work", "work gate requires visible pending work"),
        ("terminal_work", "work gate requires visible pending work"),
        ("deleted_work", "work gate requires visible pending work"),
        ("pre_resolved", "work gates must be inserted unresolved"),
    ),
)
def test_gate_insert_guard_refusal_matrix(
    postgres_engine: Engine,
    case: str,
    match: str,
):
    """Each row must reach and identify the intended database guard."""
    with pytest.raises(DBAPIError, match=match):
        with postgres_engine.begin() as connection:
            project_id, work_item_id, checkpoint_id, created_at = _seed_work(
                connection,
            )
            insert_project_id = project_id
            insert_checkpoint_id = checkpoint_id
            if case in {"cross_project", "cross_work_checkpoint"}:
                other_project_id, _, other_checkpoint_id, _ = _seed_work(connection)
                if case == "cross_project":
                    insert_project_id = other_project_id
                else:
                    insert_checkpoint_id = other_checkpoint_id
            if case == "deferred_work":
                connection.execute(
                    text("UPDATE work_items SET status = 'deferred' WHERE id = :work_item_id"),
                    {"work_item_id": work_item_id},
                )
            if case == "terminal_work":
                connection.execute(
                    text(
                        "UPDATE work_items SET status = 'wont-do' "
                        "WHERE id = :work_item_id"
                    ),
                    {"work_item_id": work_item_id},
                )
            if case == "deleted_work":
                connection.execute(
                    text(
                        "UPDATE work_items SET deleted_at = clock_timestamp() "
                        "WHERE id = :work_item_id"
                    ),
                    {"work_item_id": work_item_id},
                )

            pre_resolved = case == "pre_resolved"
            connection.execute(
                text(
                    """
                    INSERT INTO work_gates (
                        id, project_id, work_item_id, gate_type, question,
                        requested_by_client, requested_by_session_id, requested_by_model,
                        requested_work_version, requested_context_checkpoint_id,
                        requested_relationship_event_count, created_at,
                        resolved_at, resolution, resolved_by_client,
                        resolved_by_session_id, resolved_work_version,
                        resolved_context_checkpoint_id,
                        resolved_relationship_event_count
                    ) VALUES (
                        :gate_id, :project_id, :work_item_id, :gate_type, :question,
                        'pytest', 'fabricated-request', 'fixture-model',
                        1, :checkpoint_id, 0, :created_at,
                        :resolved_at, :resolution, :resolved_by_client,
                        :resolved_by_session_id, :resolved_work_version,
                        :resolved_checkpoint_id, :resolved_relationship_count
                    )
                    """
                ),
                {
                    "gate_id": uuid4(),
                    "project_id": insert_project_id,
                    "work_item_id": work_item_id,
                    "gate_type": "agent" if case == "gate_type" else "human",
                    "question": (
                        "   "
                        if case == "blank_question"
                        else "x" * 4001
                        if case == "overlong_question"
                        else "Should this fabricated gate be accepted?"
                    ),
                    "checkpoint_id": insert_checkpoint_id,
                    "created_at": created_at,
                    "resolved_at": created_at if pre_resolved else None,
                    "resolution": "Already answered." if pre_resolved else None,
                    "resolved_by_client": "dashboard" if pre_resolved else None,
                    "resolved_by_session_id": "human-session" if pre_resolved else None,
                    "resolved_work_version": 1 if pre_resolved else None,
                    "resolved_checkpoint_id": checkpoint_id if pre_resolved else None,
                    "resolved_relationship_count": 0 if pre_resolved else None,
                },
            )


@pytest.mark.parametrize(
    ("case", "match"),
    (
        ("duplicate_request", "uq_work_events_gate_fact"),
        ("backfill_request", "gate event source fact does not match retained state"),
        ("legacy_gate_reference", "ck_work_events_gate_reference_valid"),
        ("resolution_before_gate", "gate resolution event does not match retained state"),
        ("request_actor_mismatch", "gate request event does not match retained state"),
        ("request_timestamp_mismatch", "gate request event does not match retained state"),
    ),
)
def test_gate_event_guard_refusal_matrix(
    postgres_engine: Engine,
    case: str,
    match: str,
):
    """Gate-event provenance failures cannot be satisfied by a different guard."""
    question = "Which deployment window should be used?"
    with postgres_engine.begin() as connection:
        project_id, work_item_id, checkpoint_id, _ = _seed_work(connection)
        gate_id, _, created_at = _insert_gate(
            connection,
            project_id=project_id,
            work_item_id=work_item_id,
            checkpoint_id=checkpoint_id,
            question=question,
        )

    event_type = "human_attention_requested"
    body = question
    actor_client = "pytest"
    actor_session_id = "gate-request"
    actor_model = "fixture-model"
    metadata = {"gate_id": str(gate_id), "gate_type": "human"}
    origin = "live"
    event_created_at = created_at
    if case == "backfill_request":
        origin = "backfill"
    elif case == "legacy_gate_reference":
        event_type = "progress"
        body = "Ordinary progress."
        metadata = {}
    elif case == "resolution_before_gate":
        event_type = "human_attention_resolved"
        body = "Premature answer."
        actor_client = "dashboard"
        actor_session_id = "human-session"
        actor_model = None
    elif case == "request_actor_mismatch":
        actor_client = "imposter"
    elif case == "request_timestamp_mismatch":
        event_created_at += timedelta(seconds=1)

    with pytest.raises(DBAPIError, match=match):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO work_events (
                        project_id, work_item_id, event_type, actor_kind,
                        actor_client, actor_session_id, actor_model, body,
                        gate_id, metadata, origin, created_at
                    ) VALUES (
                        :project_id, :work_item_id, :event_type, 'client',
                        :actor_client, :actor_session_id, :actor_model, :body,
                        :gate_id, CAST(:metadata AS jsonb), :origin, :created_at
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "work_item_id": work_item_id,
                    "event_type": event_type,
                    "actor_client": actor_client,
                    "actor_session_id": actor_session_id,
                    "actor_model": actor_model,
                    "body": body,
                    "gate_id": gate_id,
                    "metadata": json.dumps(metadata),
                    "origin": origin,
                    "created_at": event_created_at,
                },
            )


def test_gate_resolution_timestamp_order_guard(postgres_engine: Engine):
    """Resolution timestamp ordering remains independently CHECK-constrained."""
    with postgres_engine.begin() as connection:
        project_id, work_item_id, checkpoint_id, _ = _seed_work(connection)
        gate_id, _, created_at = _insert_gate(
            connection,
            project_id=project_id,
            work_item_id=work_item_id,
            checkpoint_id=checkpoint_id,
        )

    with pytest.raises(DBAPIError, match="ck_work_gates_timestamp_order"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE work_gates
                    SET resolved_at = :resolved_at,
                        resolution = 'Fabricated resolution.',
                        resolved_by_client = 'dashboard',
                        resolved_by_session_id = 'human-session',
                        resolved_work_version = 1,
                        resolved_context_checkpoint_id = :checkpoint_id,
                        resolved_relationship_event_count = 0
                    WHERE id = :gate_id
                    """
                ),
                {
                    "resolved_at": created_at - timedelta(seconds=1),
                    "checkpoint_id": checkpoint_id,
                    "gate_id": gate_id,
                },
            )


def test_gate_events_are_atomic_and_resolution_is_one_way(postgres_engine: Engine):
    with postgres_engine.begin() as connection:
        project_id, work_item_id, checkpoint_id, _ = _seed_work(connection)

    with pytest.raises(DBAPIError, match="exact retained audit events"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO work_gates (
                        project_id, work_item_id, question,
                        requested_by_client, requested_by_session_id,
                        requested_work_version, requested_context_checkpoint_id,
                        requested_relationship_event_count
                    ) VALUES (
                        :project_id, :work_item_id, 'Unpaired question',
                        'pytest', 'unpaired', 1, :checkpoint_id, 0
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "work_item_id": work_item_id,
                    "checkpoint_id": checkpoint_id,
                },
            )

    with postgres_engine.begin() as connection:
        gate_id, sequence, created_at = _insert_gate(
            connection,
            project_id=project_id,
            work_item_id=work_item_id,
            checkpoint_id=checkpoint_id,
        )
    assert sequence > 0

    with postgres_engine.connect() as connection:
        gate = connection.execute(
            text("SELECT * FROM work_gates WHERE id = :gate_id"),
            {"gate_id": gate_id},
        ).mappings().one()
        assert gate["created_at"] == created_at
        assert gate["resolved_at"] is None
        assert connection.scalar(
            text("SELECT count(*) FROM work_events WHERE gate_id = :gate_id"),
            {"gate_id": gate_id},
        ) == 1

    with pytest.raises(DBAPIError, match="mutation is not permitted"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE work_gates SET question = 'Changed' WHERE id = :gate_id"),
                {"gate_id": gate_id},
            )

    with pytest.raises(DBAPIError, match="exact retained audit events"):
        with postgres_engine.begin() as connection:
            resolved_at = connection.scalar(text("SELECT clock_timestamp()"))
            connection.execute(
                text(
                    """
                    UPDATE work_gates
                    SET resolved_at = :resolved_at,
                        resolution = 'Missing event',
                        resolved_by_client = 'dashboard',
                        resolved_by_session_id = 'human-session',
                        resolved_work_version = 1,
                        resolved_context_checkpoint_id = :checkpoint_id,
                        resolved_relationship_event_count = 0
                    WHERE id = :gate_id
                    """
                ),
                {
                    "resolved_at": resolved_at,
                    "checkpoint_id": checkpoint_id,
                    "gate_id": gate_id,
                },
            )

    with postgres_engine.begin() as connection:
        resolved_at = _resolve_gate(
            connection,
            project_id=project_id,
            work_item_id=work_item_id,
            gate_id=gate_id,
            checkpoint_id=checkpoint_id,
        )

    with postgres_engine.connect() as connection:
        gate = connection.execute(
            text("SELECT * FROM work_gates WHERE id = :gate_id"),
            {"gate_id": gate_id},
        ).mappings().one()
        assert gate["resolved_at"] == resolved_at
        assert gate["resolved_work_version"] == gate["requested_work_version"]
        assert (
            gate["resolved_context_checkpoint_id"]
            == gate["requested_context_checkpoint_id"]
        )
        assert (
            gate["resolved_relationship_event_count"]
            == gate["requested_relationship_event_count"]
        )
        assert connection.scalar(
            text("SELECT count(*) FROM work_events WHERE gate_id = :gate_id"),
            {"gate_id": gate_id},
        ) == 2

    for statement, match in (
        (
            "UPDATE work_gates SET resolution = 'Overwrite' WHERE id = :gate_id",
            "mutation is not permitted",
        ),
        (
            "UPDATE work_gates SET resolved_at = NULL WHERE id = :gate_id",
            "mutation is not permitted",
        ),
        ("DELETE FROM work_gates WHERE id = :gate_id", "cannot be deleted"),
    ):
        with pytest.raises(DBAPIError, match=match):
            with postgres_engine.begin() as connection:
                connection.execute(text(statement), {"gate_id": gate_id})


def test_gate_revision_and_event_source_guards_reject_fabrication(postgres_engine: Engine):
    with postgres_engine.begin() as connection:
        project_id, work_item_id, checkpoint_id, _ = _seed_work(connection)

    with pytest.raises(DBAPIError, match="request revision"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO work_gates (
                        project_id, work_item_id, question,
                        requested_by_client, requested_by_session_id,
                        requested_work_version, requested_context_checkpoint_id,
                        requested_relationship_event_count
                    ) VALUES (
                        :project_id, :work_item_id, 'False anchor',
                        'pytest', 'false-anchor', 2, :checkpoint_id, 0
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "work_item_id": work_item_id,
                    "checkpoint_id": checkpoint_id,
                },
            )

    with pytest.raises(DBAPIError, match="gate request event"):
        with postgres_engine.begin() as connection:
            gate_id = uuid4()
            created_at = connection.scalar(text("SELECT clock_timestamp()"))
            connection.execute(
                text(
                    """
                    INSERT INTO work_gates (
                        id, project_id, work_item_id, question,
                        requested_by_client, requested_by_session_id,
                        requested_work_version, requested_context_checkpoint_id,
                        requested_relationship_event_count, created_at
                    ) VALUES (
                        :gate_id, :project_id, :work_item_id, 'Exact question',
                        'pytest', 'source-guard', 1, :checkpoint_id, 0, :created_at
                    )
                    """
                ),
                {
                    "gate_id": gate_id,
                    "project_id": project_id,
                    "work_item_id": work_item_id,
                    "checkpoint_id": checkpoint_id,
                    "created_at": created_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO work_events (
                        project_id, work_item_id, event_type, actor_kind,
                        actor_client, actor_session_id, body, gate_id,
                        metadata, origin, created_at
                    ) VALUES (
                        :project_id, :work_item_id, 'human_attention_requested',
                        'client', 'pytest', 'source-guard', 'Different question',
                        :gate_id, CAST(:metadata AS jsonb), 'live', :created_at
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "work_item_id": work_item_id,
                    "gate_id": gate_id,
                    "metadata": json.dumps(
                        {"gate_id": str(gate_id), "gate_type": "human"}
                    ),
                    "created_at": created_at,
                },
            )

    with postgres_engine.begin() as connection:
        gate_id, _, _ = _insert_gate(
            connection,
            project_id=project_id,
            work_item_id=work_item_id,
            checkpoint_id=checkpoint_id,
        )

    with pytest.raises(DBAPIError, match="resolution revision"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE work_gates
                    SET resolved_at = clock_timestamp(),
                        resolution = 'Fabricated revision',
                        resolved_by_client = 'dashboard',
                        resolved_by_session_id = 'human-session',
                        resolved_work_version = 2,
                        resolved_context_checkpoint_id = :checkpoint_id,
                        resolved_relationship_event_count = 0
                    WHERE id = :gate_id
                    """
                ),
                {"checkpoint_id": checkpoint_id, "gate_id": gate_id},
            )


def test_gate_metadata_keys_are_reserved_for_typed_gate_events(
    postgres_engine: Engine,
):
    with postgres_engine.begin() as connection:
        project_id, work_item_id, _, _ = _seed_work(connection)

    with pytest.raises(DBAPIError, match="ck_work_events_gate_metadata_reserved"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO work_events (
                        project_id, work_item_id, event_type, actor_kind,
                        actor_client, actor_session_id, body, metadata, origin
                    ) VALUES (
                        :project_id, :work_item_id, 'progress', 'client',
                        'pytest', 'ordinary-progress', 'Not a gate event.',
                        '{"gate_id":"misleading","gate_type":"human"}'::jsonb,
                        'live'
                    )
                    """
                ),
                {"project_id": project_id, "work_item_id": work_item_id},
            )


def test_unresolved_gate_blocks_escape_and_preserves_lease_maintenance(
    postgres_engine: Engine,
):
    with postgres_engine.begin() as connection:
        project_id, work_item_id, checkpoint_id, _ = _seed_work(connection)
        generation_id = _insert_lease(
            connection,
            project_id=project_id,
            work_item_id=work_item_id,
        )
    with postgres_engine.begin() as connection:
        _insert_gate(
            connection,
            project_id=project_id,
            work_item_id=work_item_id,
            checkpoint_id=checkpoint_id,
        )

    with postgres_engine.begin() as connection:
        renewed_at = connection.scalar(text("SELECT clock_timestamp()"))
        connection.execute(
            text(
                """
                UPDATE work_leases
                SET renewed_at = :renewed_at,
                    expires_at = :expires_at
                WHERE work_item_id = :work_item_id
                """
            ),
            {
                "renewed_at": renewed_at,
                "expires_at": renewed_at + timedelta(minutes=20),
                "work_item_id": work_item_id,
            },
        )

    with pytest.raises(DBAPIError, match="prevents lease acquisition"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE work_leases
                    SET holder_session_id = 'replacement',
                        lease_generation_id = :replacement
                    WHERE work_item_id = :work_item_id
                    """
                ),
                {"replacement": uuid4(), "work_item_id": work_item_id},
            )

    for statement, error in (
        (
            "UPDATE work_items SET status = 'done' WHERE id = :work_item_id",
            "pending completion requires one live current episode",
        ),
        (
            "UPDATE work_items SET status = 'promoted' WHERE id = :work_item_id",
            "terminal or delete",
        ),
        (
            "UPDATE work_items SET deleted_at = clock_timestamp() "
            "WHERE id = :work_item_id",
            "terminal or delete",
        ),
    ):
        with pytest.raises(DBAPIError, match=error):
            with postgres_engine.begin() as connection:
                connection.execute(text(statement), {"work_item_id": work_item_id})

    with postgres_engine.begin() as connection:
        connection.execute(
            text("UPDATE work_items SET priority = 41 WHERE id = :work_item_id"),
            {"work_item_id": work_item_id},
        )
        connection.execute(
            text(
                """
                DELETE FROM work_leases
                WHERE work_item_id = :work_item_id
                  AND lease_generation_id = :generation_id
                """
            ),
            {"work_item_id": work_item_id, "generation_id": generation_id},
        )

    with pytest.raises(DBAPIError, match="prevents lease acquisition"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO work_leases (
                        work_item_id, holder_client, holder_session_id,
                        claim_request_id, lease_token, lease_generation_id,
                        acquired_at, renewed_at, expires_at
                    ) VALUES (
                        :work_item_id, 'pytest', 'fresh', 'fresh-claim',
                        'fresh-token', :generation_id,
                        clock_timestamp(), clock_timestamp(),
                        clock_timestamp() + interval '15 minutes'
                    )
                    """
                ),
                {"work_item_id": work_item_id, "generation_id": uuid4()},
            )


def test_resolution_revision_must_use_latest_context_checkpoint(postgres_engine: Engine):
    with postgres_engine.begin() as connection:
        project_id, work_item_id, checkpoint_id, _ = _seed_work(connection)
        gate_id, _, _ = _insert_gate(
            connection,
            project_id=project_id,
            work_item_id=work_item_id,
            checkpoint_id=checkpoint_id,
        )

    newer_checkpoint_id = uuid4()
    with postgres_engine.begin() as connection:
        checkpoint_at = connection.scalar(text("SELECT clock_timestamp()"))
        connection.execute(
            text(
                """
                INSERT INTO checkpoints (
                    id, work_item_id, kind, prompt, source_client,
                    source_session_id, created_at
                ) VALUES (
                    :checkpoint_id, :work_item_id, 'context', 'New reviewed context.',
                    'pytest', 'new-context', :created_at
                )
                """
            ),
            {
                "checkpoint_id": newer_checkpoint_id,
                "work_item_id": work_item_id,
                "created_at": checkpoint_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO work_events (
                    project_id, work_item_id, event_type, actor_kind,
                    actor_client, actor_session_id, checkpoint_id,
                    metadata, origin, created_at
                ) VALUES (
                    :project_id, :work_item_id, 'checkpoint_added', 'client',
                    'pytest', 'new-context', :checkpoint_id,
                    '{"checkpoint_kind":"context"}'::jsonb, 'live', :created_at
                )
                """
            ),
            {
                "project_id": project_id,
                "work_item_id": work_item_id,
                "checkpoint_id": newer_checkpoint_id,
                "created_at": checkpoint_at,
            },
        )

    with pytest.raises(DBAPIError, match="resolution revision"):
        with postgres_engine.begin() as connection:
            _resolve_gate(
                connection,
                project_id=project_id,
                work_item_id=work_item_id,
                gate_id=gate_id,
                checkpoint_id=checkpoint_id,
            )

    with postgres_engine.begin() as connection:
        _resolve_gate(
            connection,
            project_id=project_id,
            work_item_id=work_item_id,
            gate_id=gate_id,
            checkpoint_id=newer_checkpoint_id,
        )

    with postgres_engine.connect() as connection:
        gate = connection.execute(
            text("SELECT * FROM work_gates WHERE id = :gate_id"),
            {"gate_id": gate_id},
        ).mappings().one()
        assert gate["requested_context_checkpoint_id"] == checkpoint_id
        assert gate["resolved_context_checkpoint_id"] == newer_checkpoint_id


def test_client_operation_constraint_accepts_exactly_twelve_kinds(
    postgres_engine: Engine,
):
    project_id = uuid4()
    with postgres_engine.begin() as connection:
        for operation_kind in _ALLOWED_OPERATION_KINDS:
            operation_id = uuid4()
            receipt_id = connection.scalar(
                text(
                    """
                    INSERT INTO client_operations (
                        project_id, client_operation_id, operation_kind,
                        request_fingerprint_salt, request_fingerprint
                    ) VALUES (
                        :project_id, :operation_id, :operation_kind,
                        decode(repeat('a1', 32), 'hex'),
                        decode(repeat('b2', 32), 'hex')
                    )
                    RETURNING id
                    """
                ),
                {
                    "project_id": project_id,
                    "operation_id": operation_id,
                    "operation_kind": operation_kind,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET state = 'completed',
                        response_status = 200,
                        response_body = '{}'::jsonb,
                        mutation_applied = false,
                        completed_at = clock_timestamp()
                    WHERE id = :receipt_id
                    """
                ),
                {"receipt_id": receipt_id},
            )

    with pytest.raises(DBAPIError, match="ck_client_operations_operation_kind_valid"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO client_operations (
                        project_id, client_operation_id, operation_kind,
                        request_fingerprint_salt, request_fingerprint
                    ) VALUES (
                        :project_id, :operation_id, 'future_gate_kind',
                        decode(repeat('a1', 32), 'hex'),
                        decode(repeat('b2', 32), 'hex')
                    )
                    """
                ),
                {"project_id": project_id, "operation_id": uuid4()},
            )

    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM client_operations")) == 12


def _complete_gate_request_receipt(
    connection: Connection,
    *,
    project_id: UUID,
    operation_id: UUID,
) -> None:
    receipt_id = connection.scalar(
        text(
            """
            INSERT INTO client_operations (
                project_id, client_operation_id, operation_kind,
                request_fingerprint_salt, request_fingerprint
            ) VALUES (
                :project_id, :operation_id, 'request_human_input',
                decode(repeat('a1', 32), 'hex'),
                decode(repeat('b2', 32), 'hex')
            )
            RETURNING id
            """
        ),
        {"project_id": project_id, "operation_id": operation_id},
    )
    connection.execute(
        text(
            """
            UPDATE client_operations
            SET state = 'completed',
                response_status = 201,
                response_body = '{"synthetic":"gate-request"}'::jsonb,
                mutation_applied = true,
                completed_at = clock_timestamp()
            WHERE id = :receipt_id
            """
        ),
        {"receipt_id": receipt_id},
    )


def _wait_for_relation_lock(
    engine: Engine,
    *,
    waiting_pid: int,
    blocking_pid: int,
    relation_oid: int,
    mode: str,
) -> None:
    deadline = time.monotonic() + 3
    with engine.connect() as observer:
        while time.monotonic() < deadline:
            waiting, blocked = observer.execute(
                text(
                    """
                    SELECT
                        EXISTS (
                            SELECT 1
                            FROM pg_locks
                            WHERE pid = :waiting_pid
                              AND relation = :relation_oid
                              AND mode = :mode
                              AND NOT granted
                        ),
                        :blocking_pid = ANY(pg_blocking_pids(:waiting_pid))
                    """
                ),
                {
                    "waiting_pid": waiting_pid,
                    "blocking_pid": blocking_pid,
                    "relation_oid": relation_oid,
                    "mode": mode,
                },
            ).one()
            if waiting and blocked:
                return
    raise AssertionError(
        f"backend {waiting_pid} did not wait for {mode} on relation "
        f"{relation_oid} behind backend {blocking_pid}"
    )


def _metadata_v1_definition(connection: Connection) -> str:
    return connection.scalar(
        text(
            """
            SELECT pg_get_functiondef(procedure.oid)
            FROM pg_proc AS procedure
            JOIN pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = current_schema()
              AND procedure.proname = 'mnemonic_work_event_metadata_v1_is_valid'
            """
        )
    )


def test_0015_populated_upgrade_preserves_gate_history_and_changes_defaults():
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(database_url=raw_url, api_key="phase-78-migration-key-is-long-enough")
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_phase78_0015_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(
        url.update_query_dict({"options": f"-c search_path={schema} -c timezone=UTC"}),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    config = Config(str(BACKEND_DIR / "alembic.ini"))

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0014_human_gates")

        with engine.begin() as connection:
            project_id, work_item_id, checkpoint_id, checkpoint_created_at = _seed_work(
                connection
            )
            gate_id, attention_sequence, gate_created_at = _insert_gate(
                connection,
                project_id=project_id,
                work_item_id=work_item_id,
                checkpoint_id=checkpoint_id,
            )
            resolved_at = connection.scalar(text("SELECT clock_timestamp()"))
            resolution = "Keep the retained 0014 decision."
            connection.execute(
                text(
                    """
                    UPDATE work_gates
                    SET resolved_at = :resolved_at,
                        resolution = :resolution,
                        resolved_by_client = 'dashboard',
                        resolved_by_session_id = 'migration-human',
                        resolved_work_version = 1,
                        resolved_context_checkpoint_id = :checkpoint_id,
                        resolved_relationship_event_count = 0,
                        context_changed_at_resolution = false,
                        context_change_acknowledged = false
                    WHERE id = :gate_id
                    """
                ),
                {
                    "resolved_at": resolved_at,
                    "resolution": resolution,
                    "checkpoint_id": checkpoint_id,
                    "gate_id": gate_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO work_events (
                        project_id, work_item_id, event_type, actor_kind,
                        actor_client, actor_session_id, body, gate_id,
                        metadata, origin, created_at
                    ) VALUES (
                        :project_id, :work_item_id, 'human_attention_resolved',
                        'client', 'dashboard', 'migration-human', :resolution,
                        :gate_id, CAST(:metadata AS jsonb), 'live', :resolved_at
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "work_item_id": work_item_id,
                    "resolution": resolution,
                    "gate_id": gate_id,
                    "metadata": json.dumps(
                        {"gate_id": str(gate_id), "gate_type": "human"}
                    ),
                    "resolved_at": resolved_at,
                },
            )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0015_gate_review_fixes")

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0015_gate_review_fixes"
            columns = set(
                connection.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'work_gates'
                        """
                    )
                ).scalars()
            )
            assert "context_change_acknowledged" not in columns
            assert "context_changed_at_resolution" not in columns
            gate = connection.execute(
                text(
                    """
                    SELECT attention_sequence, created_at, resolved_at, resolution,
                           resolved_work_version, resolved_context_checkpoint_id,
                           resolved_relationship_event_count
                    FROM work_gates
                    WHERE id = :gate_id
                    """
                ),
                {"gate_id": gate_id},
            ).mappings().one()
            assert dict(gate) == {
                "attention_sequence": attention_sequence,
                "created_at": gate_created_at,
                "resolved_at": resolved_at,
                "resolution": resolution,
                "resolved_work_version": 1,
                "resolved_context_checkpoint_id": checkpoint_id,
                "resolved_relationship_event_count": 0,
            }
            assert connection.scalar(
                text("SELECT created_at FROM checkpoints WHERE id = :checkpoint_id"),
                {"checkpoint_id": checkpoint_id},
            ) == checkpoint_created_at
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM work_events "
                    "WHERE gate_id = :gate_id AND event_type LIKE 'human_attention_%'"
                ),
                {"gate_id": gate_id},
            ) == 2
            defaults = dict(
                connection.execute(
                    text(
                        """
                        SELECT table_name, column_default
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND column_name = 'created_at'
                          AND table_name IN ('checkpoints', 'work_relationships')
                        """
                    )
                ).tuples().all()
            )
            assert defaults == {
                "checkpoints": "clock_timestamp()",
                "work_relationships": "clock_timestamp()",
            }
        with pytest.raises(
            RuntimeError,
            match="Downgrade from 0015_gate_review_fixes is unsupported",
        ):
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "0014_human_gates")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0015_gate_review_fixes"
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_0014_populated_upgrade_preserves_history_and_empty_downgrade():
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(database_url=raw_url, api_key="phase-78-migration-key-is-long-enough")
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_phase78_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(
        url.update_query_dict({"options": f"-c search_path={schema} -c timezone=UTC"}),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    config = Config(str(BACKEND_DIR / "alembic.ini"))

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0013_idempotent_mutations")

        with engine.begin() as connection:
            project_id, work_item_id, _, _ = _seed_work(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO work_events (
                        project_id, work_item_id, event_type, actor_kind,
                        actor_client, actor_session_id, body, metadata, origin
                    ) VALUES (
                        :project_id, :work_item_id, 'progress', 'client',
                        'pytest', 'historical-progress', 'Historical gate-like metadata.',
                        '{"gate_id":"historical","gate_type":"historical"}'::jsonb,
                        'live'
                    )
                    """
                ),
                {"project_id": project_id, "work_item_id": work_item_id},
            )
            operation_id = uuid4()
            receipt_id = connection.scalar(
                text(
                    """
                    INSERT INTO client_operations (
                        project_id, client_operation_id, operation_kind,
                        request_fingerprint_salt, request_fingerprint
                    ) VALUES (
                        :project_id, :operation_id, 'append_event',
                        decode(repeat('a1', 32), 'hex'),
                        decode(repeat('b2', 32), 'hex')
                    )
                    RETURNING id
                    """
                ),
                {"project_id": project_id, "operation_id": operation_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET state = 'completed',
                        response_status = 201,
                        response_body = '{"legacy":"unchanged"}'::jsonb,
                        mutation_applied = true,
                        completed_at = clock_timestamp()
                    WHERE id = :receipt_id
                    """
                ),
                {"receipt_id": receipt_id},
            )
            legacy_definition = _metadata_v1_definition(connection)

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0014_human_gates")

        with engine.connect() as connection:
            assert _metadata_v1_definition(connection) == legacy_definition
            assert connection.scalar(text("SELECT count(*) FROM work_gates")) == 0
            assert connection.scalar(
                text("SELECT count(*) FROM work_events WHERE gate_id IS NOT NULL")
            ) == 0
            assert connection.scalar(
                text(
                    "SELECT response_body FROM client_operations "
                    "WHERE client_operation_id = :operation_id"
                ),
                {"operation_id": operation_id},
            ) == {"legacy": "unchanged"}
            assert connection.scalar(
                text(
                    "SELECT gate_id FROM work_events "
                    "WHERE work_item_id = :work_item_id AND event_type = 'work_created'"
                ),
                {"work_item_id": work_item_id},
            ) is None
            assert connection.scalar(
                text(
                    "SELECT metadata FROM work_events "
                    "WHERE body = 'Historical gate-like metadata.'"
                )
            ) == {"gate_id": "historical", "gate_type": "historical"}

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0013_idempotent_mutations")

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT to_regclass('work_gates')")) is None
            columns = connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'work_events'
                    """
                )
            ).scalars().all()
            assert "gate_id" not in columns
            assert _metadata_v1_definition(connection) == legacy_definition
            assert connection.scalar(
                text(
                    "SELECT response_body FROM client_operations "
                    "WHERE client_operation_id = :operation_id"
                ),
                {"operation_id": operation_id},
            ) == {"legacy": "unchanged"}
            assert connection.scalar(
                text(
                    "SELECT metadata FROM work_events "
                    "WHERE body = 'Historical gate-like metadata.'"
                )
            ) == {"gate_id": "historical", "gate_type": "historical"}

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0014_human_gates")

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0014_human_gates"
            assert connection.scalar(text("SELECT count(*) FROM work_gates")) == 0
            assert connection.scalar(
                text(
                    "SELECT metadata FROM work_events "
                    "WHERE body = 'Historical gate-like metadata.'"
                )
            ) == {"gate_id": "historical", "gate_type": "historical"}
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_0014_replays_canonical_phase6_append_event_bytes_after_reupgrade():
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(database_url=raw_url, api_key="phase-78-migration-key-is-long-enough")
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_phase6_replay_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(
        url.update_query_dict({"options": f"-c search_path={schema} -c timezone=UTC"}),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    config = Config(str(BACKEND_DIR / "alembic.ini"))

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0013_idempotent_mutations")

        operation_id = uuid4()
        wire_body = {
            "event_type": "progress",
            "body": "Canonical Phase 6 progress survives the migration boundary.",
            "metadata": {
                "phase": 6,
                "nested": {"verified": True, "values": [1, "two", None]},
            },
            "actor": {
                "actor_client": "phase6-client",
                "actor_session_id": "phase6-session",
                "actor_model": "phase6-model",
            },
            "client_operation_id": str(operation_id),
        }
        payload = ProgressEventCreate.model_validate(wire_body)
        salt = bytes.fromhex("a1" * 32)

        with engine.begin() as connection:
            project_id, work_item_id, _, _ = _seed_work(connection)
            prepared = prepare_client_operation(
                "append_event",
                project_id,
                {"work_item_id": work_item_id},
                payload,
            )
            assert prepared.canonical_bytes is not None
            fingerprint = request_fingerprint(salt, prepared.canonical_bytes)
            created_at = connection.scalar(text("SELECT clock_timestamp()"))
            event_id = connection.scalar(
                text(
                    """
                    INSERT INTO work_events (
                        project_id, work_item_id, event_type, actor_kind,
                        actor_client, actor_session_id, actor_model, body,
                        metadata, origin, created_at
                    ) VALUES (
                        :project_id, :work_item_id, 'progress', 'client',
                        :actor_client, :actor_session_id, :actor_model, :body,
                        CAST(:metadata AS jsonb), 'live', :created_at
                    )
                    RETURNING id
                    """
                ),
                {
                    "project_id": project_id,
                    "work_item_id": work_item_id,
                    "actor_client": payload.actor.actor_client,
                    "actor_session_id": payload.actor.actor_session_id,
                    "actor_model": payload.actor.actor_model,
                    "body": payload.body,
                    "metadata": json.dumps(payload.metadata),
                    "created_at": created_at,
                },
            )
            connection.execute(
                text(
                    "UPDATE work_items SET updated_at=:created_at "
                    "WHERE id=:work_item_id"
                ),
                {"created_at": created_at, "work_item_id": work_item_id},
            )
            canonical_response = WorkEventRead.model_validate(
                {
                    "id": event_id,
                    "project_id": project_id,
                    "work_item_id": work_item_id,
                    "event_type": "progress",
                    "actor_kind": "client",
                    "actor_client": payload.actor.actor_client,
                    "actor_session_id": payload.actor.actor_session_id,
                    "actor_model": payload.actor.actor_model,
                    "body": payload.body,
                    "checkpoint_id": None,
                    "lease_generation_id": None,
                    "lease_release_id": None,
                    "relationship_id": None,
                    "relationship_source_work_item_id": None,
                    "relationship_target_work_item_id": None,
                    "relationship_context_checkpoint_work_item_id": None,
                    "relationship_context_checkpoint_id": None,
                    "relationship_direction": None,
                    "counterpart_work_item_id": None,
                    "metadata_version": 1,
                    "metadata": payload.metadata,
                    "origin": "live",
                    "created_at": created_at,
                }
            ).model_dump(mode="json")
            connection.execute(
                text(
                    """
                    INSERT INTO client_operations (
                        project_id, client_operation_id, operation_kind,
                        request_fingerprint_version, request_fingerprint_salt,
                        request_fingerprint, response_contract_version,
                        created_at
                    ) VALUES (
                        :project_id, :operation_id, 'append_event', 1,
                        :salt, :fingerprint, 1, :created_at
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "operation_id": operation_id,
                    "salt": salt,
                    "fingerprint": fingerprint,
                    "created_at": created_at,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET state = 'completed',
                        response_status = 201,
                        response_body = CAST(:response_body AS jsonb),
                        mutation_applied = true,
                        completed_at = :created_at
                    WHERE project_id = :project_id
                      AND client_operation_id = :operation_id
                    """
                ),
                {
                    "project_id": project_id,
                    "operation_id": operation_id,
                    "response_body": json.dumps(canonical_response),
                    "created_at": created_at,
                },
            )

        def migrate(revision: str, *, downgrade: bool = False) -> None:
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                if downgrade:
                    command.downgrade(config, revision)
                else:
                    command.upgrade(config, revision)

        def replay() -> bytes:
            app_settings = Settings(
                database_url=engine.url.render_as_string(hide_password=False),
                api_key="phase-78-migration-key-is-long-enough",
            )
            with TestClient(
                create_app(app_settings, engine=engine, semantic_embedder=object())
            ) as api:
                api.headers["Authorization"] = (
                    "Bearer phase-78-migration-key-is-long-enough"
                )
                response = api.post(
                    f"/api/v1/projects/{project_id}/work-items/{work_item_id}/events",
                    json=wire_body,
                )
                assert response.status_code == 201
                assert response.json() == canonical_response
                return response.content

        expected_bytes = replay()
        with engine.connect() as connection:
            phase6_counts = (
                connection.scalar(text("SELECT count(*) FROM work_events")),
                connection.scalar(text("SELECT count(*) FROM client_operations")),
                connection.scalar(
                    text("SELECT updated_at FROM work_items WHERE id=:work_item_id"),
                    {"work_item_id": work_item_id},
                ),
            )
            assert phase6_counts[:2] == (2, 1)

        migrate("0014_human_gates")
        assert replay() == expected_bytes
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT count(*) FROM work_events")),
                connection.scalar(text("SELECT count(*) FROM client_operations")),
                connection.scalar(
                    text("SELECT updated_at FROM work_items WHERE id=:work_item_id"),
                    {"work_item_id": work_item_id},
                ),
            ) == phase6_counts
            assert connection.scalar(text("SELECT count(*) FROM work_gates")) == 0

        migrate("0013_idempotent_mutations", downgrade=True)
        migrate("0014_human_gates")
        assert replay() == expected_bytes
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT count(*) FROM work_events")),
                connection.scalar(text("SELECT count(*) FROM client_operations")),
                connection.scalar(
                    text("SELECT updated_at FROM work_items WHERE id=:work_item_id"),
                    {"work_item_id": work_item_id},
                ),
            ) == phase6_counts
            assert connection.scalar(text("SELECT count(*) FROM work_gates")) == 0
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
