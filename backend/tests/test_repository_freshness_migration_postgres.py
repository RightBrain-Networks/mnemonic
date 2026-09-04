"""Migration and direct-SQL coverage for immutable checkpoint dependency scope."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, defer
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api.config import Settings
from mnemonic_api.models import WorkDuplicateMerge, WorkEvent, WorkItem, WorkRelationship
from mnemonic_api.schemas import (
    CheckpointCreate,
    CheckpointRead,
    WorkCompletionCreate,
    WorkItemCreate,
    WorkItemRead,
    WorkMergeResult,
)
from mnemonic_api.services.client_operations import (
    PreparedOperation,
    ReplayedOperation,
    _render_registered_response,
    operation_spec,
    prepare_client_operation,
    request_fingerprint,
    reserve_client_operation,
)
from mnemonic_api.services.duplicates import _identity_pointer, _merge_read
from mnemonic_api.services.relationships import relationship_edge
from mnemonic_api.services.work_events import work_event_read

from .conftest import BACKEND_DIR
from .test_client_operations import response_vector_cases
from .test_duplicate_merge_invariants_postgres import (
    _create_project_with_work,
    _stage_merge,
)
from .test_phase6_migration_postgres import _wait_for_relation_lock
from .test_phase78_migration_postgres import _insert_gate, _insert_lease

pytestmark = pytest.mark.postgres

_SCOPE_CORPUS = json.loads(
    (
        BACKEND_DIR.parent
        / "tests"
        / "fixtures"
        / "repository-freshness-scope-v1.json"
    ).read_text(encoding="utf-8")
)
_RECEIPT_KINDS = (
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
    "merge_work",
)
_UNCHANGED_TABLES = (
    "projects",
    "project_settings",
    "work_items",
    "work_item_embeddings",
    "work_leases",
    "work_relationships",
    "work_duplicate_merges",
    "work_events",
    "work_gates",
    "client_operations",
)
_CHECKPOINT_REQUEST_FIELDS = (
    "prompt",
    "source_client",
    "source_session_id",
    "source_model",
    "source_session_url",
    "repository_branch",
    "verified_against",
    "tags",
    "source_metadata",
)


def _generated_scope(case: dict[str, object]) -> list[str]:
    count = int(case["count"])
    entry_bytes = int(case["entry_bytes"])
    prefix_width = int(case["prefix_width"])
    fill = str(case["fill"])
    extra_bytes_on_first = int(case["extra_bytes_on_first"])
    paths = []
    for index in range(count):
        prefix = f"{index:0{prefix_width}d}"
        target_bytes = entry_bytes + (extra_bytes_on_first if index == 0 else 0)
        paths.append(prefix + fill * (target_bytes - len(prefix)))
    return paths


_VALID_SCOPE_CASES = [
    *([path] for path in _SCOPE_CORPUS["valid_paths"]),
    *(
        _generated_scope(case)
        for case in _SCOPE_CORPUS["generated_scopes"]
        if case["valid"]
    ),
]
_INVALID_SCOPE_CASES = [
    *([path] for path in _SCOPE_CORPUS["invalid_paths"]),
    *(
        _generated_scope(case)
        for case in _SCOPE_CORPUS["generated_scopes"]
        if not case["valid"]
    ),
    *(case["paths"] for case in _SCOPE_CORPUS["literal_scopes"]),
]


def _row_digest(connection: Connection, table: str, *, omit_scope: bool = False) -> str:
    assert table == "checkpoints" or table in _UNCHANGED_TABLES
    row = "to_jsonb(domain_row) - 'affected_paths'" if omit_scope else "to_jsonb(domain_row)"
    return connection.scalar(
        text(
            f"SELECT md5(COALESCE(string_agg(row_value, E'\\n' ORDER BY row_value), '')) "
            f"FROM (SELECT ({row})::text AS row_value FROM {table} AS domain_row) "
            "AS serialized"
        )
    )


def _prior_catalog_digests(connection: Connection) -> dict[str, str]:
    function_digest = connection.scalar(
        text(
            """
            SELECT md5(COALESCE(string_agg(snapshot::text, E'\\n'
                                           ORDER BY snapshot::text), ''))
            FROM (
                SELECT jsonb_build_object(
                    'name', procedure.proname,
                    'arguments', pg_get_function_identity_arguments(procedure.oid),
                    'volatility', procedure.provolatile,
                    'strict', procedure.proisstrict,
                    'parallel', procedure.proparallel,
                    'configuration', procedure.proconfig,
                    'definition', pg_get_functiondef(procedure.oid)
                ) AS snapshot
                FROM pg_proc AS procedure
                JOIN pg_namespace AS namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = current_schema()
                  AND procedure.proname <> 'mnemonic_affected_paths_valid_v1'
            ) AS functions
            """
        )
    )
    trigger_digest = connection.scalar(
        text(
            """
            SELECT md5(COALESCE(string_agg(snapshot::text, E'\\n'
                                           ORDER BY snapshot::text), ''))
            FROM (
                SELECT jsonb_build_object(
                    'table', relation.relname,
                    'name', trigger.tgname,
                    'enabled', trigger.tgenabled,
                    'deferrable', trigger.tgdeferrable,
                    'initially_deferred', trigger.tginitdeferred,
                    'definition', pg_get_triggerdef(trigger.oid, true)
                ) AS snapshot
                FROM pg_trigger AS trigger
                JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
            ) AS triggers
            """
        )
    )
    index_digest = connection.scalar(
        text(
            """
            SELECT md5(COALESCE(string_agg(snapshot::text, E'\\n'
                                           ORDER BY snapshot::text), ''))
            FROM (
                SELECT jsonb_build_object(
                    'table', tablename,
                    'name', indexname,
                    'definition', indexdef
                ) AS snapshot
                FROM pg_indexes
                WHERE schemaname = current_schema()
            ) AS indexes
            """
        )
    )
    assert isinstance(function_digest, str)
    assert isinstance(trigger_digest, str)
    assert isinstance(index_digest, str)
    return {
        "functions": function_digest,
        "triggers": trigger_digest,
        "indexes": index_digest,
    }


def _insert_checkpoint_history(
    connection: Connection,
    *,
    project_id: UUID,
    progress_work_item_id: UUID,
    legacy_work_item_id: UUID,
    completion_work_item_id: UUID,
) -> tuple[UUID, UUID, UUID]:
    progress_id = uuid4()
    legacy_id = uuid4()
    completion_id = uuid4()
    legacy_record_id = uuid4()
    progress_at = connection.scalar(text("SELECT clock_timestamp()"))
    completion_at = connection.scalar(text("SELECT clock_timestamp()"))
    connection.execute(
        text(
            """
            INSERT INTO checkpoints (
                id, work_item_id, kind, prompt, source_client,
                source_session_id, source_model, source_session_url,
                repository_branch, verified_against, tags, source_metadata,
                migration_origin, legacy_record_id, created_at
            ) VALUES (
                :progress_id, :progress_work_item_id, 'progress',
                'Retain native progress provenance.', 'phase-10-fixture',
                'phase-10-progress', 'fixture-model',
                'https://sessions.example/phase-10-progress',
                'work/phase-10', 'abcdef1', ARRAY['native', 'progress']::varchar[],
                CAST(:progress_metadata AS jsonb), NULL, NULL, :progress_at
            ), (
                :legacy_id, :legacy_work_item_id, 'progress',
                'Retain migrated comment provenance.', 'legacy-import',
                'phase-10-legacy', NULL, NULL, NULL, NULL,
                ARRAY['legacy']::varchar[], CAST(:legacy_metadata AS jsonb),
                'legacy-comment', :legacy_record_id, :progress_at
            ), (
                :completion_id, :completion_work_item_id, 'completion',
                'Retain native completion provenance.', 'phase-10-fixture',
                'phase-10-completion', 'fixture-model', NULL,
                'work/phase-10', 'abcdef2', ARRAY['native', 'completion']::varchar[],
                CAST(:completion_metadata AS jsonb), NULL, NULL, :completion_at
            )
            """
        ),
        {
            "progress_id": progress_id,
            "progress_work_item_id": progress_work_item_id,
            "legacy_id": legacy_id,
            "legacy_work_item_id": legacy_work_item_id,
            "completion_id": completion_id,
            "completion_work_item_id": completion_work_item_id,
            "legacy_record_id": legacy_record_id,
            "progress_at": progress_at,
            "completion_at": completion_at,
            "progress_metadata": json.dumps(
                {
                    "opaque": {
                        "affected_paths": ["do/not/infer/**"],
                        "future_contract": True,
                    }
                }
            ),
            "legacy_metadata": json.dumps({"legacy": {"source": "comments"}}),
            "completion_metadata": json.dumps({"release": {"candidate": 10}}),
        },
    )
    connection.execute(
        text(
            """
            UPDATE work_items
            SET status = 'done', version = 2, updated_at = :completion_at
            WHERE id = :completion_work_item_id
            """
        ),
        {
            "completion_work_item_id": completion_work_item_id,
            "completion_at": completion_at,
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
                :project_id, :progress_work_item_id, 'checkpoint_added', 'client',
                'phase-10-fixture', 'phase-10-progress', 'fixture-model',
                :progress_id, '{"checkpoint_kind":"progress"}'::jsonb,
                'live', :progress_at
            ), (
                :project_id, :completion_work_item_id, 'work_completed', 'client',
                'phase-10-fixture', 'phase-10-completion', 'fixture-model',
                :completion_id, CAST(:completion_event_metadata AS jsonb),
                'live', :completion_at
            )
            """
        ),
        {
            "project_id": project_id,
            "progress_work_item_id": progress_work_item_id,
            "completion_work_item_id": completion_work_item_id,
            "progress_id": progress_id,
            "completion_id": completion_id,
            "progress_at": progress_at,
            "completion_at": completion_at,
            "completion_event_metadata": json.dumps(
                {"from_status": "pending", "to_status": "done", "work_version": 2}
            ),
        },
    )
    return progress_id, legacy_id, completion_id


def _insert_related_relationship(
    connection: Connection,
    *,
    project_id: UUID,
    left_id: UUID,
    right_id: UUID,
) -> UUID:
    source_id, target_id = sorted((left_id, right_id))
    relationship_id = uuid4()
    created_at = connection.scalar(text("SELECT clock_timestamp()"))
    connection.execute(
        text(
            """
            INSERT INTO work_relationships (
                id, project_id, relationship_type, source_work_item_id,
                target_work_item_id, created_by_client, created_by_session_id,
                created_by_model, created_at
            ) VALUES (
                :relationship_id, :project_id, 'related', :source_id, :target_id,
                'phase-10-fixture', 'phase-10-related', 'fixture-model', :created_at
            )
            """
        ),
        {
            "relationship_id": relationship_id,
            "project_id": project_id,
            "source_id": source_id,
            "target_id": target_id,
            "created_at": created_at,
        },
    )
    for endpoint_id in (source_id, target_id):
        connection.execute(
            text(
                """
                INSERT INTO work_events (
                    project_id, work_item_id, event_type, actor_kind,
                    actor_client, actor_session_id, actor_model, relationship_id,
                    relationship_source_work_item_id,
                    relationship_target_work_item_id, metadata, origin, created_at
                ) VALUES (
                    :project_id, :endpoint_id, 'relationship_added', 'client',
                    'phase-10-fixture', 'phase-10-related', 'fixture-model',
                    :relationship_id, :source_id, :target_id,
                    '{"relationship_type":"related"}'::jsonb, 'live', :created_at
                )
                """
            ),
            {
                "project_id": project_id,
                "endpoint_id": endpoint_id,
                "relationship_id": relationship_id,
                "source_id": source_id,
                "target_id": target_id,
                "created_at": created_at,
            },
        )
    return relationship_id


def _merge_receipt_body(connection: Connection, merge_id: UUID) -> dict[str, Any]:
    with Session(bind=connection) as database:
        merge = database.get(WorkDuplicateMerge, merge_id)
        assert merge is not None
        source = database.get(
            WorkItem,
            merge.source_work_item_id,
            options=(defer(WorkItem.completion_generation),),
        )
        destination = database.get(
            WorkItem,
            merge.destination_work_item_id,
            options=(defer(WorkItem.completion_generation),),
        )
        relationship = database.get(WorkRelationship, merge.duplicate_relationship_id)
        assert source is not None
        assert destination is not None
        assert relationship is not None
        relationship_events = database.scalars(
            select(WorkEvent)
            .options(defer(WorkEvent.reopen_generation))
            .where(WorkEvent.created_for_duplicate_merge_id == merge_id)
            .order_by(WorkEvent.id)
        ).all()
        merge_events = database.scalars(
            select(WorkEvent)
            .options(defer(WorkEvent.reopen_generation))
            .where(WorkEvent.work_duplicate_merge_id == merge_id)
            .order_by(WorkEvent.id)
        ).all()
        pointer = _identity_pointer(destination)
        result = WorkMergeResult(
            merge=_merge_read(merge),
            source_work_item=WorkItemRead.model_validate(source),
            destination_work_item=WorkItemRead.model_validate(destination),
            direct_destination=pointer,
            canonical_work_item=pointer,
            supporting_relationship_created=True,
            supporting_relationship=relationship_edge(relationship),
            relationship_events=[work_event_read(item) for item in relationship_events],
            merge_events=[work_event_read(item) for item in merge_events],
        )
    body = result.model_dump(mode="json")
    _typed, canonical, _response = _render_registered_response(
        operation_spec("merge_work"), body, stored_snapshot=True
    )
    assert canonical == body
    return body


def _historical_checkpoint_body(
    connection: Connection, checkpoint_id: UUID
) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT id, work_item_id, kind, prompt, source_client,
                   source_session_id, source_model, source_session_url,
                   repository_branch, verified_against, tags, source_metadata,
                   migration_origin, legacy_record_id, created_at
            FROM checkpoints
            WHERE id = :checkpoint_id
            """
        ),
        {"checkpoint_id": checkpoint_id},
    ).mappings().one()
    return CheckpointRead.model_validate(dict(row)).model_dump(mode="json")


def _historical_work_body(connection: Connection, work_item_id: UUID) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT id, project_id, title, summary, status, priority,
                   initial_checkpoint_id, version, created_at, updated_at
            FROM work_items
            WHERE id = :work_item_id
            """
        ),
        {"work_item_id": work_item_id},
    ).mappings().one()
    return WorkItemRead.model_validate(dict(row)).model_dump(mode="json")


def _checkpoint_request_body(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        name: checkpoint[name]
        for name in _CHECKPOINT_REQUEST_FIELDS
    }


def _coherent_historical_checkpoint_receipts(
    connection: Connection,
    project_id: UUID,
    work: list[tuple[UUID, UUID]],
    progress_id: UUID,
    completion_id: UUID,
) -> tuple[dict[str, dict[str, Any]], dict[str, PreparedOperation]]:
    create_work_id, create_checkpoint_id = work[3]
    create_work_body = _historical_work_body(connection, create_work_id)
    create_checkpoint_body = _historical_checkpoint_body(
        connection, create_checkpoint_id
    )
    create_operation_id = uuid4()
    create_payload = WorkItemCreate.model_validate(
        {
            "title": create_work_body["title"],
            "summary": create_work_body["summary"],
            "status": create_work_body["status"],
            "priority": create_work_body["priority"],
            "initial_checkpoint": _checkpoint_request_body(
                create_checkpoint_body
            ),
            "client_operation_id": create_operation_id,
        }
    )
    create_source = {
        "work_item": create_work_body,
        "initial_checkpoint": create_checkpoint_body,
        "initial_relationships": [],
    }

    progress_work_item_id = work[4][0]
    progress_body = _historical_checkpoint_body(connection, progress_id)
    progress_operation_id = uuid4()
    progress_payload = CheckpointCreate.model_validate(
        {
            **_checkpoint_request_body(progress_body),
            "kind": progress_body["kind"],
            "client_operation_id": progress_operation_id,
        }
    )

    completion_work_item_id = work[6][0]
    completion_work_body = _historical_work_body(
        connection, completion_work_item_id
    )
    completion_checkpoint_body = _historical_checkpoint_body(
        connection, completion_id
    )
    completion_operation_id = uuid4()
    completion_payload = WorkCompletionCreate.model_validate(
        {
            "expected_version": completion_work_body["version"] - 1,
            "checkpoint": _checkpoint_request_body(
                completion_checkpoint_body
            ),
            "client_operation_id": completion_operation_id,
        }
    )
    completion_source = {
        "work_item": completion_work_body,
        "checkpoint": completion_checkpoint_body,
    }

    payloads = {
        "create_work": ({}, create_payload),
        "add_checkpoint": (
            {"work_item_id": progress_work_item_id},
            progress_payload,
        ),
        "complete_work": (
            {"work_item_id": completion_work_item_id},
            completion_payload,
        ),
    }
    sources = {
        "create_work": create_source,
        "add_checkpoint": progress_body,
        "complete_work": completion_source,
    }
    bodies: dict[str, dict[str, Any]] = {}
    prepared: dict[str, PreparedOperation] = {}
    for kind, (target, payload) in payloads.items():
        spec = operation_spec(kind)
        _typed, body, _response = _render_registered_response(spec, sources[kind])
        bodies[kind] = body
        prepared[kind] = prepare_client_operation(
            kind, project_id, target, payload
        )
    return bodies, prepared


def _insert_receipt_corpus(
    connection: Connection,
    project_id: UUID,
    merge_id: UUID,
    work: list[tuple[UUID, UUID]],
    progress_id: UUID,
    completion_id: UUID,
) -> tuple[dict[str, int], dict[str, PreparedOperation]]:
    bodies = {
        kind: expected_body
        for kind, _source, expected_body in response_vector_cases()
    }
    assert tuple(bodies) == _RECEIPT_KINDS
    checkpoint_bodies, prepared = _coherent_historical_checkpoint_receipts(
        connection,
        project_id,
        work,
        progress_id,
        completion_id,
    )
    bodies.update(checkpoint_bodies)
    bodies["merge_work"] = _merge_receipt_body(connection, merge_id)
    assert UUID(bodies["merge_work"]["merge"]["id"]) == merge_id
    receipt_ids: dict[str, int] = {}
    for position, kind in enumerate(_RECEIPT_KINDS, start=1):
        spec = operation_spec(kind)
        body = bodies[kind]
        _typed, canonical, _response = _render_registered_response(
            spec, body, stored_snapshot=True
        )
        assert canonical == body
        mutation_applied = (
            True
            if spec.mutation_applied_field is None
            else body[spec.mutation_applied_field]
        )
        salt = bytes([position]) * 32
        prepared_operation = prepared.get(kind)
        if prepared_operation is None:
            operation_id = uuid4()
            fingerprint = bytes([position + 32]) * 32
        else:
            assert prepared_operation.identity is not None
            assert prepared_operation.canonical_bytes is not None
            operation_id = prepared_operation.identity.client_operation_id
            fingerprint = request_fingerprint(
                salt, prepared_operation.canonical_bytes
            )
        receipt_id = connection.scalar(
            text(
                """
                INSERT INTO client_operations (
                    project_id, client_operation_id, operation_kind,
                    request_fingerprint_version, request_fingerprint_salt,
                    request_fingerprint, response_contract_version
                ) VALUES (
                    :project_id, :operation_id, :kind, 1, :salt, :fingerprint, 1
                )
                RETURNING id
                """
            ),
            {
                "project_id": project_id,
                "operation_id": operation_id,
                "kind": kind,
                "salt": salt,
                "fingerprint": fingerprint,
            },
        )
        assert isinstance(receipt_id, int)
        receipt_ids[kind] = receipt_id
        connection.execute(
            text(
                """
                UPDATE client_operations
                SET state = 'completed',
                    response_status = :response_status,
                    response_body = CAST(:response_body AS jsonb),
                    mutation_applied = :mutation_applied,
                    completed_at = created_at
                WHERE id = :receipt_id
                """
            ),
            {
                "receipt_id": receipt_id,
                "response_status": spec.status_code,
                "response_body": json.dumps(body, separators=(",", ":")),
                "mutation_applied": mutation_applied,
            },
        )
    return receipt_ids, prepared


def _assert_populated_fixture(
    connection: Connection,
    *,
    merge_id: UUID,
) -> None:
    counts = connection.execute(
        text(
            """
            SELECT
                (SELECT count(*) FROM project_settings) AS settings,
                (SELECT count(*) FROM work_items) AS work_items,
                (SELECT count(*) FROM work_item_embeddings) AS embeddings,
                (SELECT count(*) FROM work_leases) AS leases,
                (SELECT count(*) FROM work_relationships) AS relationships,
                (SELECT count(*) FROM work_duplicate_merges) AS merges,
                (SELECT count(*) FROM work_events) AS events,
                (SELECT count(*) FROM work_gates) AS gates,
                (SELECT count(*) FROM work_gates WHERE resolved_at IS NULL)
                    AS unresolved_gates,
                (SELECT count(*) FROM client_operations) AS receipts,
                (SELECT count(DISTINCT operation_kind) FROM client_operations)
                    AS receipt_kinds,
                (SELECT count(*) FROM work_items WHERE status = 'done') AS done_work,
                (SELECT count(*) FROM checkpoints WHERE migration_origin IS NOT NULL)
                    AS legacy_checkpoints,
                (SELECT count(*) FROM checkpoints WHERE migration_origin IS NULL)
                    AS native_checkpoints
            """
        )
    ).mappings().one()
    assert dict(counts) == {
        "settings": 1,
        "work_items": 7,
        "embeddings": 2,
        "leases": 1,
        "relationships": 2,
        "merges": 1,
        "events": 17,
        "gates": 1,
        "unresolved_gates": 1,
        "receipts": 13,
        "receipt_kinds": 13,
        "done_work": 1,
        "legacy_checkpoints": 1,
        "native_checkpoints": 9,
    }
    checkpoint_kinds = dict(
        connection.execute(
            text("SELECT kind, count(*) FROM checkpoints GROUP BY kind ORDER BY kind")
        ).all()
    )
    assert checkpoint_kinds == {"completion": 1, "context": 7, "progress": 2}
    assert connection.scalar(
        text(
            """
            SELECT count(*)
            FROM work_events
            WHERE created_for_duplicate_merge_id = :merge_id
            """
        ),
        {"merge_id": merge_id},
    ) == 2
    assert connection.scalar(
        text(
            """
            SELECT count(*)
            FROM work_events
            WHERE work_duplicate_merge_id = :merge_id
            """
        ),
        {"merge_id": merge_id},
    ) == 2
    assert connection.scalar(
        text(
            """
            SELECT response_body -> 'merge' ->> 'id'
            FROM client_operations
            WHERE operation_kind = 'merge_work'
            """
        )
    ) == str(merge_id)


def _assert_receipts_replay(
    connection: Connection,
    prepared_checkpoint_receipts: dict[str, PreparedOperation],
) -> None:
    rows = connection.execute(
        text(
            """
            SELECT operation_kind, response_body
            FROM client_operations
            ORDER BY operation_kind
            """
        )
    ).all()
    assert {kind for kind, _body in rows} == set(_RECEIPT_KINDS)
    bodies_by_kind = dict(rows)
    for kind, body in rows:
        assert isinstance(body, dict)
        _typed, replayed, _response = _render_registered_response(
            operation_spec(kind), body, stored_snapshot=True
        )
        assert replayed == body
        if kind == "create_work":
            assert "affected_paths" not in body["initial_checkpoint"]
        elif kind == "add_checkpoint":
            assert "affected_paths" not in body
        elif kind == "complete_work":
            assert "affected_paths" not in body["checkpoint"]

    before_replay = {
        table: _row_digest(connection, table)
        for table in (*_UNCHANGED_TABLES, "checkpoints")
    }
    with Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    ) as database:
        for kind, prepared in prepared_checkpoint_receipts.items():
            replay = reserve_client_operation(database, prepared, wait_seconds=2)
            assert isinstance(replay, ReplayedOperation)
            assert replay.status == operation_spec(kind).status_code
            assert replay.mutation_applied is True
            assert json.loads(replay.response.body) == bodies_by_kind[kind]
        database.commit()
    assert {
        table: _row_digest(connection, table)
        for table in (*_UNCHANGED_TABLES, "checkpoints")
    } == before_replay


def _assert_prior_immutable_guards(
    engine,
    *,
    checkpoint_id: UUID,
    receipt_id: int,
) -> None:
    checkpoint_statements = (
        "UPDATE checkpoints SET prompt = prompt WHERE id = :id",
        "DELETE FROM checkpoints WHERE id = :id",
    )
    for statement in checkpoint_statements:
        with pytest.raises(DBAPIError, match="checkpoints are immutable") as captured:
            with engine.begin() as connection:
                connection.execute(text(statement), {"id": checkpoint_id})
        assert getattr(captured.value.orig, "sqlstate", None) == "55000"

    receipt_statements = (
        "UPDATE client_operations SET response_body = response_body WHERE id = :id",
        "DELETE FROM client_operations WHERE id = :id",
    )
    for statement in receipt_statements:
        with pytest.raises(DBAPIError) as captured:
            with engine.begin() as connection:
                connection.execute(text(statement), {"id": receipt_id})
        assert getattr(captured.value.orig, "sqlstate", None) == "55000"


def _migration_engine():
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(database_url=raw_url, api_key="phase-10-migration-key-is-long-enough")
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_freshness_0018_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(
        url.update_query_dict({"options": f"-c search_path={schema} -c timezone=UTC"}),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    return admin, engine, schema


def test_0018_upgrade_binds_commit_guard_to_pg_catalog_builtins():
    admin, engine, schema = _migration_engine()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0017_duplicate_suggestion_title_key")
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            _project_id, work = _create_project_with_work(connection, work_count=1)
            connection.execute(
                text(
                    """
                    CREATE FUNCTION cardinality(value varchar[])
                    RETURNS integer
                    LANGUAGE sql
                    IMMUTABLE
                    STRICT
                    PARALLEL SAFE
                    SET search_path = pg_catalog
                    AS 'SELECT 0'
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_integer_equal(integer, integer)
                    RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT true';
                    CREATE OPERATOR = (
                        LEFTARG = integer,
                        RIGHTARG = integer,
                        FUNCTION = mnemonic_integer_equal
                    );
                    CREATE FUNCTION mnemonic_integer_greater(integer, integer)
                    RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT false';
                    CREATE OPERATOR > (
                        LEFTARG = integer,
                        RIGHTARG = integer,
                        FUNCTION = mnemonic_integer_greater
                    );
                    CREATE FUNCTION current_schema()
                    RETURNS name LANGUAGE sql STABLE PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT ''pg_catalog''::name'
                    """
                )
            )
            connection.exec_driver_sql(
                f'SET LOCAL search_path = "{schema}", pg_catalog'
            )
            assert connection.execute(
                text(
                    "SELECT cardinality(ARRAY['src/**']::varchar[]), "
                    "1 = 0, 1 > 0, current_schema()"
                )
            ).one() == (0, True, False, "pg_catalog")

        with engine.begin() as connection:
            connection.exec_driver_sql(
                f'SET LOCAL search_path = "{schema}", pg_catalog'
            )
            config.attributes["connection"] = connection
            command.upgrade(config, "0018_repository_freshness")

        with engine.connect() as connection:
            captured_dependencies = connection.execute(
                text(
                    """
                    SELECT dependency.refclassid::pg_catalog.regclass::text,
                           dependency.refobjid
                    FROM pg_catalog.pg_constraint AS constraint_value
                    JOIN pg_catalog.pg_depend AS dependency
                      ON dependency.classid =
                             'pg_catalog.pg_constraint'::pg_catalog.regclass
                     AND dependency.objid = constraint_value.oid
                    WHERE constraint_value.conrelid = 'checkpoints'::pg_catalog.regclass
                      AND constraint_value.conname =
                              'ck_checkpoints_affected_paths_require_commit'
                      AND dependency.refclassid IN (
                          'pg_catalog.pg_proc'::pg_catalog.regclass,
                          'pg_catalog.pg_operator'::pg_catalog.regclass
                      )
                    """
                )
            ).all()
            # Pinned pg_catalog functions/operators intentionally have no
            # pg_depend row; a schema-local overload does.
            assert captured_dependencies == []

        with pytest.raises(DBAPIError) as captured:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    f'SET LOCAL search_path = "{schema}", pg_catalog'
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO checkpoints (
                            id, work_item_id, kind, prompt, source_client,
                            source_session_id, verified_against, affected_paths
                        ) VALUES (
                            :id, :work_item_id, 'progress', 'Reject commitless scope',
                            'pytest', 'phase-10-overload', NULL,
                            ARRAY['src/**']::varchar[]
                        )
                        """
                    ),
                    {"id": uuid4(), "work_item_id": work[0][0]},
                )
        assert captured.value.orig.diag.constraint_name == (
            "ck_checkpoints_affected_paths_require_commit"
        )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_0018_populated_upgrade_preserves_all_prior_facts_and_receipts():
    admin, engine, schema = _migration_engine()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0017_duplicate_suggestion_title_key")

        with engine.begin() as connection:
            project_id, work = _create_project_with_work(connection, work_count=7)
            connection.execute(
                text(
                    """
                    INSERT INTO project_settings (project_id, recall_pointer_template)
                    VALUES (:project_id, 'Recall {{work_item.id}} from {{checkpoint.id}}')
                    """
                ),
                {"project_id": project_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO work_item_embeddings (work_item_id, model, digest, vector)
                    VALUES
                        (:first_id, 'retained-model-v1', :first_digest,
                         ARRAY[0.25, 0.75]::real[]),
                        (:second_id, 'retained-model-v1', :second_digest,
                         ARRAY[-0.5, 0.125, 0.875]::real[])
                    """
                ),
                {
                    "first_id": work[3][0],
                    "second_id": work[4][0],
                    "first_digest": "a" * 64,
                    "second_digest": "b" * 64,
                },
            )
            progress_id, _legacy_id, completion_id = _insert_checkpoint_history(
                connection,
                project_id=project_id,
                progress_work_item_id=work[4][0],
                legacy_work_item_id=work[5][0],
                completion_work_item_id=work[6][0],
            )
            _insert_gate(
                connection,
                project_id=project_id,
                work_item_id=work[2][0],
                checkpoint_id=work[2][1],
                question="Retain the unresolved production approval gate?",
            )
            _insert_lease(
                connection,
                project_id=project_id,
                work_item_id=work[3][0],
            )
            _insert_related_relationship(
                connection,
                project_id=project_id,
                left_id=work[4][0],
                right_id=work[5][0],
            )
            merge_id = _stage_merge(
                connection,
                project_id=project_id,
                source_id=work[0][0],
                destination_id=work[1][0],
            )
            receipt_ids, prepared_checkpoint_receipts = _insert_receipt_corpus(
                connection,
                project_id,
                merge_id,
                work,
                progress_id,
                completion_id,
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            _assert_populated_fixture(connection, merge_id=merge_id)
            _assert_receipts_replay(connection, prepared_checkpoint_receipts)
            before = {
                table: _row_digest(connection, table) for table in _UNCHANGED_TABLES
            }
            checkpoint_before = _row_digest(connection, "checkpoints", omit_scope=True)
            catalog_before = _prior_catalog_digests(connection)
            receipts_before = connection.execute(
                text(
                    """
                    SELECT operation_kind, request_fingerprint_version,
                           request_fingerprint_salt, request_fingerprint,
                           response_contract_version, state, response_status,
                           response_body, mutation_applied, created_at, completed_at
                    FROM client_operations
                    ORDER BY operation_kind
                    """
                )
            ).all()

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0018_repository_freshness")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_repository_freshness"
            )
            assert {
                table: _row_digest(connection, table) for table in _UNCHANGED_TABLES
            } == before
            assert _row_digest(connection, "checkpoints", omit_scope=True) == checkpoint_before
            assert _prior_catalog_digests(connection) == catalog_before
            assert connection.scalar(
                text("SELECT count(*) FROM checkpoints WHERE affected_paths <> '{}'")
            ) == 0
            assert connection.execute(
                text(
                    """
                    SELECT operation_kind, request_fingerprint_version,
                           request_fingerprint_salt, request_fingerprint,
                           response_contract_version, state, response_status,
                           response_body, mutation_applied, created_at, completed_at
                    FROM client_operations
                    ORDER BY operation_kind
                    """
                )
            ).all() == receipts_before
            _assert_populated_fixture(connection, merge_id=merge_id)
            _assert_receipts_replay(connection, prepared_checkpoint_receipts)
            function = connection.execute(
                text(
                    """
                    SELECT procedure.provolatile, procedure.proisstrict,
                           procedure.proparallel, procedure.proconfig,
                           pg_get_functiondef(procedure.oid) AS definition
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = current_schema()
                      AND procedure.proname = 'mnemonic_affected_paths_valid_v1'
                    """
                )
            ).mappings().one()
            assert function["provolatile"] == "i"
            assert function["proisstrict"] is True
            assert function["proparallel"] == "s"
            assert function["proconfig"] == ["search_path=pg_catalog"]
            assert "CREATE OR REPLACE FUNCTION" in function["definition"]
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND indexdef ILIKE '%affected_paths%'
                    """
                )
            ) == 0

        _assert_prior_immutable_guards(
            engine,
            checkpoint_id=progress_id,
            receipt_id=receipt_ids["merge_work"],
        )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0017_duplicate_suggestion_title_key")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0017_duplicate_suggestion_title_key"
            )
            assert {
                table: _row_digest(connection, table) for table in _UNCHANGED_TABLES
            } == before
            assert _row_digest(connection, "checkpoints") == checkpoint_before
            assert _prior_catalog_digests(connection) == catalog_before
            assert connection.execute(
                text(
                    """
                    SELECT operation_kind, request_fingerprint_version,
                           request_fingerprint_salt, request_fingerprint,
                           response_contract_version, state, response_status,
                           response_body, mutation_applied, created_at, completed_at
                    FROM client_operations
                    ORDER BY operation_kind
                    """
                )
            ).all() == receipts_before
            _assert_populated_fixture(connection, merge_id=merge_id)
            _assert_receipts_replay(connection, prepared_checkpoint_receipts)
            assert connection.scalar(
                text("SELECT to_regprocedure('mnemonic_affected_paths_valid_v1(varchar[])')")
            ) is None

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0018_repository_freshness")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_repository_freshness"
            )
            assert {
                table: _row_digest(connection, table) for table in _UNCHANGED_TABLES
            } == before
            assert _row_digest(connection, "checkpoints", omit_scope=True) == checkpoint_before
            assert _prior_catalog_digests(connection) == catalog_before
            assert connection.scalar(
                text("SELECT count(*) FROM checkpoints WHERE affected_paths <> '{}'")
            ) == 0
            assert connection.execute(
                text(
                    """
                    SELECT operation_kind, request_fingerprint_version,
                           request_fingerprint_salt, request_fingerprint,
                           response_contract_version, state, response_status,
                           response_body, mutation_applied, created_at, completed_at
                    FROM client_operations
                    ORDER BY operation_kind
                    """
                )
            ).all() == receipts_before
            _assert_populated_fixture(connection, merge_id=merge_id)
            _assert_receipts_replay(connection, prepared_checkpoint_receipts)
            assert connection.scalar(
                text("SELECT to_regprocedure('mnemonic_affected_paths_valid_v1(varchar[])')")
            ) is not None
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.mark.parametrize(
    "paths",
    _VALID_SCOPE_CASES,
)
def test_affected_path_sql_validator_accepts_exact_public_corpus(
    postgres_engine, paths
):
    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT mnemonic_affected_paths_valid_v1("
                "CAST(:paths AS varchar[]))"
            ),
            {"paths": paths},
        ) is True


@pytest.mark.parametrize(
    "paths",
    _INVALID_SCOPE_CASES,
)
def test_affected_path_sql_validator_rejects_exact_invalid_corpus(
    postgres_engine, paths
):
    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT mnemonic_affected_paths_valid_v1("
                "CAST(:paths AS varchar[]))"
            ),
            {"paths": paths},
        ) is False


@pytest.mark.parametrize(
    "array_expression",
    [
        "ARRAY[NULL]::varchar[]",
        "ARRAY[['src/**']]::varchar[]",
        "'[0:0]={src/**}'::varchar[]",
    ],
)
def test_affected_path_sql_validator_rejects_null_multidimensional_and_lower_bound(
    postgres_engine, array_expression
):
    assert array_expression in {
        "ARRAY[NULL]::varchar[]",
        "ARRAY[['src/**']]::varchar[]",
        "'[0:0]={src/**}'::varchar[]",
    }
    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text(f"SELECT mnemonic_affected_paths_valid_v1({array_expression})")
        ) is False


def test_database_constraints_and_immutability_protect_affected_paths(
    api, project, work_payload, postgres_engine
):
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items", json=work_payload
    )
    assert created.status_code == 201, created.text
    work_item_id = created.json()["work_item"]["id"]

    statement = text(
        """
        INSERT INTO checkpoints (
            id, work_item_id, kind, prompt, source_client, source_session_id,
            verified_against, affected_paths
        ) VALUES (
            :id, :work_item_id, 'progress', 'Direct SQL scope', 'pytest',
            'phase-10-sql', :verified_against, CAST(:paths AS varchar[])
        )
        """
    )
    with pytest.raises(DBAPIError):
        with postgres_engine.begin() as connection:
            connection.execute(
                statement,
                {
                    "id": uuid4(),
                    "work_item_id": work_item_id,
                    "verified_against": "abcdef1",
                    "paths": ["bad path"],
                },
            )
    with pytest.raises(DBAPIError):
        with postgres_engine.begin() as connection:
            connection.execute(
                statement,
                {
                    "id": uuid4(),
                    "work_item_id": work_item_id,
                    "verified_against": None,
                    "paths": ["src/**"],
                },
            )

    checkpoint_id = uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(
            statement,
            {
                "id": checkpoint_id,
                "work_item_id": work_item_id,
                "verified_against": "abcdef1",
                "paths": ["src/**"],
            },
        )
    with pytest.raises(DBAPIError, match="checkpoints are immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE checkpoints SET affected_paths = ARRAY['tests/**']::varchar[] "
                    "WHERE id = :id"
                ),
                {"id": checkpoint_id},
            )


def test_0018_downgrade_refuses_to_erase_nonempty_scope():
    admin, engine, schema = _migration_engine()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0018_repository_freshness")
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            _project_id, work = _create_project_with_work(connection, work_count=1)
            scoped_id = uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO checkpoints (
                        id, work_item_id, kind, prompt, source_client,
                        source_session_id, verified_against, affected_paths
                    ) VALUES (
                        :id, :work_item_id, 'progress', 'Retain this scope',
                        'pytest', 'phase-10-downgrade', 'abcdef1',
                        ARRAY['src/**']::varchar[]
                    )
                    """
                ),
                {"id": scoped_id, "work_item_id": work[0][0]},
            )
            connection.execute(
                text(
                    """
                    CREATE FUNCTION mnemonic_integer_greater(integer, integer)
                    RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT false';
                    CREATE OPERATOR > (
                        LEFTARG = integer,
                        RIGHTARG = integer,
                        FUNCTION = mnemonic_integer_greater
                    )
                    """
                )
            )

        with pytest.raises(RuntimeError, match="Cannot downgrade repository freshness"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    f'SET LOCAL search_path = "{schema}", pg_catalog'
                )
                assert connection.scalar(text("SELECT 1 > 0")) is False
                config.attributes["connection"] = connection
                command.downgrade(config, "0017_duplicate_suggestion_title_key")

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_repository_freshness"
            )
            assert connection.scalar(
                text("SELECT affected_paths FROM checkpoints WHERE id = :id"),
                {"id": scoped_id},
            ) == ["src/**"]
            assert connection.scalar(
                text("SELECT to_regprocedure('mnemonic_affected_paths_valid_v1(varchar[])')")
            ) is not None
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_0018_downgrade_rejects_repeatable_read_before_destructive_work():
    admin, engine, schema = _migration_engine()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0018_repository_freshness")
            connection.execute(
                text(
                    """
                    CREATE FUNCTION current_setting(value text)
                    RETURNS text LANGUAGE sql STABLE PARALLEL SAFE
                    SET search_path = pg_catalog AS 'SELECT ''read committed''::text'
                    """
                )
            )

        with engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.exec_driver_sql(
                    f'SET LOCAL search_path = "{schema}", pg_catalog'
                )
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "0018_repository_freshness"
                )
                assert connection.scalar(
                    text("SELECT current_setting('transaction_isolation')")
                ) == "read committed"
                config.attributes["connection"] = connection
                with pytest.raises(RuntimeError, match="requires READ COMMITTED isolation"):
                    command.downgrade(config, "0017_duplicate_suggestion_title_key")

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_repository_freshness"
            )
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'checkpoints'
                      AND column_name = 'affected_paths'
                    """
                )
            ) == 1
            assert connection.scalar(
                text("SELECT to_regprocedure('mnemonic_affected_paths_valid_v1(varchar[])')")
            ) is not None
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_0018_downgrade_sees_scope_committed_while_waiting_for_table_lock():
    admin, engine, schema = _migration_engine()
    downgrade_pid_ready = Event()
    downgrade_pid: list[int] = []
    writer_connection = None
    writer_transaction = None
    scoped_id = uuid4()
    try:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0018_repository_freshness")
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            _project_id, work = _create_project_with_work(connection, work_count=1)

        with engine.connect() as connection:
            relation_oid = connection.scalar(text("SELECT 'checkpoints'::regclass::oid"))

        writer_connection = engine.connect()
        writer_transaction = writer_connection.begin()
        writer_connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        writer_pid = writer_connection.scalar(text("SELECT pg_backend_pid()"))
        writer_connection.execute(
            text(
                """
                INSERT INTO checkpoints (
                    id, work_item_id, kind, prompt, source_client,
                    source_session_id, verified_against, affected_paths
                ) VALUES (
                    :id, :work_item_id, 'progress', 'Writer-first racing scope',
                    'pytest', 'phase-10-writer-first-race', 'abcdef1',
                    ARRAY['src/**']::varchar[]
                )
                """
            ),
            {"id": scoped_id, "work_item_id": work[0][0]},
        )

        def downgrade() -> None:
            downgrade_config = Config(str(BACKEND_DIR / "alembic.ini"))
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout = '5s'"))
                downgrade_pid.append(connection.scalar(text("SELECT pg_backend_pid()")))
                downgrade_pid_ready.set()
                downgrade_config.attributes["connection"] = connection
                command.downgrade(
                    downgrade_config, "0017_duplicate_suggestion_title_key"
                )

        with ThreadPoolExecutor(max_workers=1) as executor:
            downgrade_future = executor.submit(downgrade)
            assert downgrade_pid_ready.wait(timeout=2)
            _wait_for_relation_lock(
                engine,
                waiting_pid=downgrade_pid[0],
                blocking_pid=writer_pid,
                relation_oid=relation_oid,
                mode="AccessExclusiveLock",
            )
            writer_transaction.commit()
            with pytest.raises(RuntimeError, match="Cannot downgrade repository freshness"):
                downgrade_future.result(timeout=5)

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_repository_freshness"
            )
            assert connection.scalar(
                text("SELECT affected_paths FROM checkpoints WHERE id = :id"),
                {"id": scoped_id},
            ) == ["src/**"]
            assert connection.scalar(
                text("SELECT to_regprocedure('mnemonic_affected_paths_valid_v1(varchar[])')")
            ) is not None
    finally:
        if writer_transaction is not None and writer_transaction.is_active:
            writer_transaction.rollback()
        if writer_connection is not None:
            writer_connection.close()
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_0018_downgrade_lock_prevents_scope_insert_after_empty_check():
    admin, engine, schema = _migration_engine()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    allow_downgrade = Event()
    empty_check_complete = Event()
    downgrade_pid_ready = Event()
    writer_pid_ready = Event()
    downgrade_pid: list[int] = []
    writer_pid: list[int] = []

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0018_repository_freshness")
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            _project_id, work = _create_project_with_work(connection, work_count=1)

        with engine.connect() as connection:
            relation_oid = connection.scalar(text("SELECT 'checkpoints'::regclass::oid"))

        def pause_after_empty_check(
            connection,
            cursor,
            statement,
            parameters,
            context,
            executemany,
        ) -> None:
            normalized = " ".join(statement.lower().split())
            if (
                "select exists" not in normalized
                or "checkpoints" not in normalized
                or "cardinality(affected_paths)" not in normalized
            ):
                return
            empty_check_complete.set()
            assert allow_downgrade.wait(timeout=5), (
                "test did not release the downgrade after its empty-scope check"
            )

        def downgrade() -> None:
            downgrade_config = Config(str(BACKEND_DIR / "alembic.ini"))
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout = '5s'"))
                downgrade_pid.append(connection.scalar(text("SELECT pg_backend_pid()")))
                downgrade_pid_ready.set()
                downgrade_config.attributes["connection"] = connection
                command.downgrade(
                    downgrade_config, "0017_duplicate_suggestion_title_key"
                )

        def insert_scope() -> None:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout = '5s'"))
                writer_pid.append(connection.scalar(text("SELECT pg_backend_pid()")))
                writer_pid_ready.set()
                connection.execute(
                    text(
                        """
                        INSERT INTO checkpoints (
                            id, work_item_id, kind, prompt, source_client,
                            source_session_id, verified_against, affected_paths
                        ) VALUES (
                            :id, :work_item_id, 'progress', 'Racing scope',
                            'pytest', 'phase-10-race', 'abcdef1',
                            ARRAY['src/**']::varchar[]
                        )
                        """
                    ),
                    {"id": uuid4(), "work_item_id": work[0][0]},
                )

        event.listen(engine, "after_cursor_execute", pause_after_empty_check)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                downgrade_future = executor.submit(downgrade)
                assert downgrade_pid_ready.wait(timeout=2)
                assert empty_check_complete.wait(timeout=2)

                writer_future = executor.submit(insert_scope)
                assert writer_pid_ready.wait(timeout=2)
                try:
                    _wait_for_relation_lock(
                        engine,
                        waiting_pid=writer_pid[0],
                        blocking_pid=downgrade_pid[0],
                        relation_oid=relation_oid,
                        mode="RowExclusiveLock",
                    )
                finally:
                    allow_downgrade.set()

                downgrade_future.result(timeout=5)
                with pytest.raises(DBAPIError) as blocked_writer:
                    writer_future.result(timeout=5)
                assert getattr(blocked_writer.value.orig, "sqlstate", None) == "42703"
        finally:
            allow_downgrade.set()
            event.remove(engine, "after_cursor_execute", pause_after_empty_check)

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0017_duplicate_suggestion_title_key"
            )
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'checkpoints'
                      AND column_name = 'affected_paths'
                    """
                )
            ) == 0
    finally:
        allow_downgrade.set()
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
