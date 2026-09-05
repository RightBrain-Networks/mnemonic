"""PostgreSQL REST and invariant coverage for structured completion evidence."""

import base64
import ipaddress
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

import mnemonic_api.application.mutations as mutations_module
import mnemonic_api.application.routes.work_items as work_item_routes
import mnemonic_api.services.completion_evidence as evidence_service
import mnemonic_api.services.work_items as work_item_service
from mnemonic_api.config import Settings
from mnemonic_api.main import create_app
from mnemonic_api.models import (
    ArtifactReference,
    Checkpoint,
    ClientOperation,
    VerificationResult,
    WorkEvent,
    WorkItem,
)
from mnemonic_api.schemas import (
    COMPLETION_EXPECTED_VERSION_MAX,
    WorkCompletionCreate,
    completion_evidence_text_bytes,
)
from mnemonic_api.services.client_operations import prepare_client_operation

from .conftest import BACKEND_DIR, TEST_API_KEY
from .test_duplicate_merge_invariants_postgres import _stage_merge
from .test_phase6_migration_postgres import _wait_for_relation_lock

pytestmark = pytest.mark.postgres

CORPUS_PATH = Path(__file__).resolve().parents[2] / "tests/fixtures/completion-evidence-v1.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _restore_client_operation_state_constraint_for_corruption(
    connection: Connection,
) -> None:
    """Restore the exact check while retaining an intentionally corrupt test row."""

    connection.execute(
        text(
            """
            ALTER TABLE client_operations
            ADD CONSTRAINT ck_client_operations_state_fields_valid CHECK (
                (state = 'pending' AND response_status IS NULL
                 AND response_body IS NULL AND mutation_applied IS NULL
                 AND completed_at IS NULL)
                OR
                (state = 'completed' AND response_status BETWEEN 200 AND 299
                 AND response_body IS NOT NULL
                 AND jsonb_typeof(response_body) = 'object'
                 AND octet_length(response_body::text) <= 1048576
                 AND mutation_applied IS NOT NULL AND completed_at IS NOT NULL)
            ) NOT VALID
            """
        )
    )
    connection.execute(text("SET LOCAL allow_system_table_mods = on"))
    connection.execute(
        text(
            """
            UPDATE pg_catalog.pg_constraint
            SET convalidated = true
            WHERE conrelid = 'client_operations'::regclass
              AND conname = 'ck_client_operations_state_fields_valid'
            """
        )
    )


def _collection(project: dict[str, object]) -> str:
    return f"/api/v1/projects/{project['id']}/work-items"


def _item_path(project: dict[str, object], work: dict[str, object]) -> str:
    return f"{_collection(project)}/{work['id']}"


def _create_work(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    *,
    title: str = "Capture structured completion evidence",
) -> dict[str, object]:
    response = api.post(_collection(project), json={**work_payload, "title": title})
    assert response.status_code == 201, response.text
    return response.json()["work_item"]


def _completion_payload(
    version: int,
    session: str,
    *,
    evidence: dict[str, object] | None | object = ...,
    operation_id: str | object = ...,
) -> dict[str, object]:
    payload: dict[str, object] = {
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
    if evidence is not ...:
        payload["completion_evidence"] = evidence
    if operation_id is not ...:
        payload["client_operation_id"] = operation_id
    return payload


def _mixed_evidence(marker: str = "durable-evidence-only-token") -> dict[str, object]:
    return {
        "verification_results": [
            {
                "verification_type": "command",
                "name": marker,
                "outcome": "passed",
                "summary": "The focused suite passed without skipped database tests.",
                "command": "uv run pytest -q tests/test_completion_evidence_postgres.py",
                "exit_code": 0,
                "observed_at": "2026-09-03T14:01:02-04:00",
                "observed_at_commit": "7ad62e4",
            },
            {
                "verification_type": "observation",
                "name": "Cold review",
                "outcome": "inconclusive",
                "summary": "One adversarial review item remained for the next episode.",
            },
        ],
        "artifact_references": [
            {
                "artifact_type": "commit",
                "label": "Reviewed commit",
                "reference": "7ad62e4",
            },
            {
                "artifact_type": "pull_request",
                "label": "Pull request",
                "reference": "https://example.test/mnemonic/pull/11",
            },
        ],
    }


def _maximum_escaping_completion_payload(
    operation_id: str, lease_token: str
) -> dict[str, object]:
    control = "\x01"
    results: list[dict[str, object]] = []
    for index in range(20):
        if index < 3:
            name_length, summary_length, command_length = 200, 4000, 4096
        elif index == 3:
            name_length, summary_length, command_length = 200, 3180, 4096
        else:
            name_length, summary_length, command_length = 1, 1, 1
        result: dict[str, object] = {
            "verification_type": "command",
            "name": control * name_length,
            "outcome": "failed",
            "summary": control * summary_length,
            "command": control * command_length,
            "exit_code": -2147483648,
        }
        if index == 0:
            result.update(
                observed_at="2026-09-03T18:01:02.123456+14:00",
                observed_at_commit="f" * 64,
            )
        results.append(result)
    return {
        "expected_version": COMPLETION_EXPECTED_VERSION_MAX,
        "checkpoint": {
            "prompt": control * 100000,
            "source_client": control * 80,
            "source_session_id": control * 200,
            "source_model": control * 120,
            "source_session_url": "http://a/" + control * 1991,
            "repository_branch": control * 200,
            "verified_against": "f" * 64,
            "affected_paths": [f"{index:02d}/" + "a" * 253 for index in range(64)],
            "tags": [control * 48 + f"{index:02d}" for index in range(20)],
            "source_metadata": {"x": control * 2729},
        },
        "lease_token": lease_token,
        "completion_evidence": {
            "verification_results": results,
            "artifact_references": [],
        },
        "client_operation_id": operation_id,
    }


def _complete(
    api: TestClient,
    project: dict[str, object],
    work: dict[str, object],
    version: int,
    session: str,
    *,
    evidence: dict[str, object] | None | object = ...,
    operation_id: str | object = ...,
):
    return api.post(
        f"{_item_path(project, work)}/complete",
        json=_completion_payload(
            version,
            session,
            evidence=evidence,
            operation_id=operation_id,
        ),
    )


def _reopen(
    api: TestClient,
    project: dict[str, object],
    work: dict[str, object],
    version: int,
):
    response = api.patch(
        _item_path(project, work),
        json={
            "expected_version": version,
            "status": "pending",
            "actor": {
                "actor_client": "pytest",
                "actor_session_id": f"reopen-{version}",
            },
        },
    )
    assert response.status_code == 200, response.text
    return response


def _insert_direct_completion_checkpoint(
    connection: Connection,
    work_item_id: object,
    *,
    checkpoint_id: UUID | None = None,
) -> dict[str, object]:
    row = connection.execute(
        text(
            """
            INSERT INTO checkpoints (
                id, work_item_id, kind, prompt, source_client,
                source_session_id, repository_branch, affected_paths,
                tags, source_metadata, created_at
            ) VALUES (
                CAST(:id AS uuid), CAST(:work_item_id AS uuid), 'completion',
                'Direct SQL completion fixture.', 'pytest', 'direct-sql-completion',
                'work/phase11', '{}'::varchar[], '{}'::varchar[], '{}'::jsonb,
                clock_timestamp()
            )
            RETURNING id, created_at
            """
        ),
        {
            "id": str(checkpoint_id or uuid4()),
            "work_item_id": str(work_item_id),
        },
    ).mappings().one()
    return dict(row)


def _insert_direct_observation(
    connection: Connection,
    project_id: object,
    work_item_id: object,
    checkpoint: dict[str, object],
    *,
    position: int = 0,
    summary: str = "Direct SQL observation passed.",
    created_at: object | None = None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO verification_results (
                project_id, work_item_id, completion_checkpoint_id, position,
                verification_type, name, outcome, summary, created_at
            ) VALUES (
                CAST(:project_id AS uuid), CAST(:work_item_id AS uuid),
                CAST(:checkpoint_id AS uuid), :position, 'observation',
                'Direct SQL review', 'passed', :summary, :created_at
            )
            """
        ),
        {
            "project_id": str(project_id),
            "work_item_id": str(work_item_id),
            "checkpoint_id": str(checkpoint["id"]),
            "position": position,
            "summary": summary,
            "created_at": created_at or checkpoint["created_at"],
        },
    )


def _insert_direct_artifact(
    connection: Connection,
    project_id: object,
    work_item_id: object,
    checkpoint: dict[str, object],
    *,
    position: int = 0,
    artifact_type: str = "commit",
    reference: str = "abcdef1",
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO artifact_references (
                project_id, work_item_id, completion_checkpoint_id, position,
                artifact_type, label, reference, created_at
            ) VALUES (
                CAST(:project_id AS uuid), CAST(:work_item_id AS uuid),
                CAST(:checkpoint_id AS uuid), :position, :artifact_type,
                'Direct SQL artifact', :reference, :created_at
            )
            """
        ),
        {
            "project_id": str(project_id),
            "work_item_id": str(work_item_id),
            "checkpoint_id": str(checkpoint["id"]),
            "position": position,
            "artifact_type": artifact_type,
            "reference": reference,
            "created_at": checkpoint["created_at"],
        },
    )


def _transition_direct_completion_to_done(
    connection: Connection,
    work_item_id: object,
) -> int:
    version = connection.scalar(
        text(
            """
            UPDATE work_items
            SET status = 'done', version = version + 1,
                updated_at = clock_timestamp()
            WHERE id = CAST(:work_item_id AS uuid)
            RETURNING version
            """
        ),
        {"work_item_id": str(work_item_id)},
    )
    assert isinstance(version, int)
    return version


def _insert_direct_completion_event(
    connection: Connection,
    project_id: object,
    work_item_id: object,
    checkpoint: dict[str, object],
    version: int,
    *,
    event_id: int | None = None,
) -> int:
    columns = ""
    values = ""
    override = ""
    parameters: dict[str, object] = {
        "project_id": str(project_id),
        "work_item_id": str(work_item_id),
        "checkpoint_id": str(checkpoint["id"]),
        "created_at": checkpoint["created_at"],
        "version": version,
    }
    if event_id is not None:
        columns = "id, "
        values = ":event_id, "
        override = "OVERRIDING SYSTEM VALUE"
        parameters["event_id"] = event_id
    inserted = connection.scalar(
        text(
            f"""
            INSERT INTO work_events (
                {columns}project_id, work_item_id, event_type, actor_kind,
                actor_client, actor_session_id, origin, checkpoint_id,
                metadata_version, metadata, created_at
            ) {override} VALUES (
                {values}CAST(:project_id AS uuid), CAST(:work_item_id AS uuid),
                'work_completed', 'client', 'pytest', 'direct-sql-completion',
                'live', CAST(:checkpoint_id AS uuid), 1,
                pg_catalog.jsonb_build_object(
                    'from_status', 'pending', 'to_status', 'done',
                    'work_version', CAST(:version AS integer)
                ), :created_at
            )
            RETURNING id
            """
        ),
        parameters,
    )
    assert isinstance(inserted, int)
    return inserted


def _transition_direct_reopen(
    connection: Connection,
    work_item_id: object,
) -> dict[str, object]:
    row = connection.execute(
        text(
            """
            UPDATE work_items
            SET status = 'pending', version = version + 1,
                updated_at = clock_timestamp()
            WHERE id = CAST(:work_item_id AS uuid)
            RETURNING project_id, status, version, updated_at, completion_generation
            """
        ),
        {"work_item_id": str(work_item_id)},
    ).mappings().one()
    return dict(row)


def _insert_direct_reopen_event(
    connection: Connection,
    work_item_id: object,
    transition: dict[str, object],
    *,
    from_status: str = "done",
    metadata: dict[str, object] | None = None,
    created_at: object | None = None,
    reopen_generation: int | None = None,
) -> tuple[int, int]:
    event_metadata = metadata or {
        "changes": {
            "status": {
                "before": from_status,
                "after": "pending",
            }
        },
        "from_status": from_status,
        "to_status": "pending",
        "work_version": transition["version"],
    }
    binding_column = ", reopen_generation" if reopen_generation is not None else ""
    binding_value = ", :reopen_generation" if reopen_generation is not None else ""
    row = connection.execute(
        text(
            f"""
            INSERT INTO work_events (
                project_id, work_item_id, event_type, actor_kind,
                actor_client, actor_session_id, origin, metadata_version,
                metadata, created_at{binding_column}
            ) VALUES (
                CAST(:project_id AS uuid), CAST(:work_item_id AS uuid),
                'work_reopened', 'client', 'pytest', 'direct-sql-reopen',
                'live', 1, CAST(:metadata AS jsonb), :created_at{binding_value}
            )
            RETURNING id, reopen_generation
            """
        ),
        {
            "project_id": str(transition["project_id"]),
            "work_item_id": str(work_item_id),
            "metadata": json.dumps(event_metadata),
            "created_at": created_at or transition["updated_at"],
            "reopen_generation": reopen_generation,
        },
    ).one()
    return row.id, row.reopen_generation


def _completion_durable_snapshot(
    engine: Engine,
    work_item_id: object,
    operation_id: object,
) -> dict[str, object]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT work.status, work.version, work.completion_generation,
                       work.updated_at,
                       (SELECT count(*) FROM checkpoints AS checkpoint
                        WHERE checkpoint.work_item_id = work.id
                          AND checkpoint.kind = 'completion') AS completion_checkpoints,
                       (SELECT count(*) FROM work_events AS event
                        WHERE event.work_item_id = work.id
                          AND event.event_type = 'work_completed') AS completion_events,
                       (SELECT count(*) FROM verification_results AS result
                        WHERE result.work_item_id = work.id) AS verification_results,
                       (SELECT count(*) FROM artifact_references AS artifact
                        WHERE artifact.work_item_id = work.id) AS artifact_references,
                       (SELECT count(*) FROM client_operations AS operation
                        WHERE operation.client_operation_id
                              = CAST(:operation_id AS uuid)) AS client_operations,
                       (SELECT count(*) FROM work_leases AS lease
                        WHERE lease.work_item_id = work.id) AS work_leases
                FROM work_items AS work
                WHERE work.id = CAST(:work_item_id AS uuid)
                """
            ),
            {
                "work_item_id": str(work_item_id),
                "operation_id": str(operation_id),
            },
        ).mappings().one()
        return dict(row)


def _fail_completion(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise RuntimeError("synthetic completion durability fault")


def _fail_after_call(
    monkeypatch: pytest.MonkeyPatch,
    target: object,
    attribute: str,
) -> None:
    original = getattr(target, attribute)

    def patched(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        _fail_completion()

    monkeypatch.setattr(target, attribute, patched)


def _install_first_result_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    original = Session.add

    def after_first_result(
        database: Session,
        instance: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        original(database, instance, *args, **kwargs)
        if isinstance(instance, VerificationResult):
            database.flush()
            _fail_completion()

    monkeypatch.setattr(Session, "add", after_first_result)


def _install_between_families_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    original = Session.add
    saw_result = False

    def before_first_artifact(
        database: Session,
        instance: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal saw_result
        if isinstance(instance, VerificationResult):
            saw_result = True
        elif isinstance(instance, ArtifactReference) and saw_result:
            database.flush()
            _fail_completion()
        original(database, instance, *args, **kwargs)

    monkeypatch.setattr(Session, "add", before_first_artifact)


def _install_event_flushed_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    original = evidence_service.hydrate_completion_evidence
    calls = 0

    def after_hydration(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 2:
            _fail_completion()
        return result

    monkeypatch.setattr(evidence_service, "hydrate_completion_evidence", after_hydration)


def _install_completion_fault(monkeypatch: pytest.MonkeyPatch, fault: str) -> None:
    installers = {
        "reservation": lambda: _fail_after_call(monkeypatch, mutations_module, "_reserve"),
        "lease": lambda: _fail_after_call(
            monkeypatch,
            work_item_service,
            "consume_lease_for_terminal_mutation",
        ),
        "checkpoint": lambda: monkeypatch.setattr(
            evidence_service,
            "insert_completion_evidence",
            _fail_completion,
        ),
        "first-result": lambda: _install_first_result_fault(monkeypatch),
        "between-families": lambda: _install_between_families_fault(monkeypatch),
        "evidence": lambda: _fail_after_call(
            monkeypatch,
            evidence_service,
            "insert_completion_evidence",
        ),
        "work-update": lambda: monkeypatch.setattr(
            work_item_service,
            "stage_work_completed",
            _fail_completion,
        ),
        "event-staged": lambda: _fail_after_call(
            monkeypatch,
            work_item_service,
            "stage_work_completed",
        ),
        "event-flushed": lambda: _install_event_flushed_fault(monkeypatch),
        "response-hydrated": lambda: _fail_after_call(
            monkeypatch,
            work_item_routes,
            "hydrate_completion_evidence",
        ),
        "response": lambda: monkeypatch.setattr(
            mutations_module,
            "complete_client_operation",
            _fail_completion,
        ),
        "receipt": lambda: _fail_after_call(
            monkeypatch,
            mutations_module,
            "complete_client_operation",
        ),
        "commit": lambda: monkeypatch.setattr(Session, "commit", _fail_completion),
    }
    try:
        installer = installers[fault]
    except KeyError as exc:
        raise AssertionError(f"Unknown completion fault: {fault}") from exc
    installer()


def _restored_schema_engine(source_engine: Engine, target_schema: str) -> Engine:
    options = f"-c search_path={target_schema} -c timezone=UTC"
    target_url = source_engine.url.update_query_dict({"options": options})
    return create_engine(
        target_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )


def _restore_complete_schema_snapshot(
    source_engine: Engine,
    target_engine: Engine,
    source_schema: str,
    target_schema: str,
) -> None:
    """Copy all table rows and exact sequence states as a logical restore fixture."""
    quote = target_engine.dialect.identifier_preparer.quote_identifier
    quoted_source = quote(source_schema)
    quoted_target = quote(target_schema)
    with target_engine.begin() as connection:
        tables = connection.scalars(
            text(
                """
                SELECT tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname = :source_schema
                  AND tablename <> 'alembic_version'
                ORDER BY tablename
                """
            ),
            {"source_schema": source_schema},
        ).all()
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        for table_name in tables:
            qualified_source = f"{quoted_source}.{quote(table_name)}"
            qualified_target = f"{quoted_target}.{quote(table_name)}"
            columns = connection.scalars(
                text(
                    """
                    SELECT attribute.attname
                    FROM pg_catalog.pg_attribute AS attribute
                    WHERE attribute.attrelid = pg_catalog.to_regclass(:qualified_table)
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                      AND attribute.attgenerated = ''
                    ORDER BY attribute.attnum
                    """
                ),
                {"qualified_table": f"{source_schema}.{table_name}"},
            ).all()
            quoted_columns = ", ".join(quote(column) for column in columns)
            connection.execute(
                text(
                    f"INSERT INTO {qualified_target} ({quoted_columns}) "
                    f"OVERRIDING SYSTEM VALUE SELECT {quoted_columns} FROM {qualified_source}"
                )
            )

        sequences = connection.scalars(
            text(
                """
                SELECT sequencename
                FROM pg_catalog.pg_sequences
                WHERE schemaname = :source_schema
                ORDER BY sequencename
                """
            ),
            {"source_schema": source_schema},
        ).all()
        for sequence_name in sequences:
            source_sequence = f"{quoted_source}.{quote(sequence_name)}"
            target_sequence = f"{quoted_target}.{quote(sequence_name)}"
            last_value, is_called = connection.execute(
                text(f"SELECT last_value, is_called FROM {source_sequence}")
            ).one()
            connection.execute(
                text(
                    "SELECT pg_catalog.setval("
                    "pg_catalog.to_regclass(:target_sequence), :last_value, :is_called)"
                ),
                {
                    "target_sequence": target_sequence,
                    "last_value": last_value,
                    "is_called": is_called,
                },
            )


def test_atomic_completion_response_storage_and_history_projection(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    evidence = _mixed_evidence()
    operation_id = str(uuid4())
    completed = _complete(
        api,
        project,
        work,
        1,
        "mixed-evidence",
        evidence=evidence,
        operation_id=operation_id,
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["work_item"]["status"] == "done"
    assert body["work_item"]["version"] == 2
    checkpoint = body["checkpoint"]
    payload = body["completion_evidence"]
    assert [row["position"] for row in payload["verification_results"]] == [0, 1]
    assert [row["position"] for row in payload["artifact_references"]] == [0, 1]
    assert payload["verification_results"][0]["observed_at"] == ("2026-09-03T18:01:02Z")
    for family in ("verification_results", "artifact_references"):
        for row in payload[family]:
            assert row["work_item_id"] == work["id"]
            assert row["completion_checkpoint_id"] == checkpoint["id"]
            assert row["created_at"] == checkpoint["created_at"]

    history_response = api.get(f"{_item_path(project, work)}/completion-evidence")
    assert history_response.status_code == 200, history_response.text
    assert history_response.headers["cache-control"] == "no-store, no-transform"
    assert history_response.headers["content-encoding"] == "identity"
    page = history_response.json()
    assert set(page) == {
        "work_item_id",
        "work_version",
        "lifecycle_status",
        "is_duplicate",
        "canonical_work_item_id",
        "current_completion_checkpoint_id",
        "as_of_completion_event_id",
        "items",
        "total",
        "structured_completion_total",
        "limit",
        "next_cursor",
    }
    assert page["work_item_id"] == page["canonical_work_item_id"] == work["id"]
    assert page["work_version"] == 2
    assert page["lifecycle_status"] == "done"
    assert page["is_duplicate"] is False
    assert page["current_completion_checkpoint_id"] == checkpoint["id"]
    assert page["total"] == page["structured_completion_total"] == 1
    assert page["limit"] == 10
    assert page["next_cursor"] is None
    assert page["as_of_completion_event_id"].isascii()
    assert page["as_of_completion_event_id"].isdecimal()
    episode = page["items"][0]
    assert episode["completion_event_id"] == page["as_of_completion_event_id"]
    assert episode["verification_results"] == payload["verification_results"]
    assert episode["artifact_references"] == payload["artifact_references"]
    assert set(episode["completion_checkpoint"]) == {
        "id",
        "work_item_id",
        "kind",
        "source_client",
        "source_session_id",
        "source_model",
        "repository_branch",
        "verified_against",
        "tags",
        "migration_origin",
        "legacy_record_id",
        "created_at",
    }
    assert episode["completion_checkpoint"]["repository_branch"] == "Work/Phase11 Exact"

    assert (
        api.get(
            _collection(project),
            params={"q": "durable-evidence-only-token", "status": "all"},
        ).json()["total"]
        == 0
    )
    context = api.get(f"{_item_path(project, work)}/context").json()
    assert "completion_evidence" not in json.dumps(context)
    events = api.get(f"{_item_path(project, work)}/events").json()["items"]
    assert "durable-evidence-only-token" not in json.dumps(events)

    with Session(postgres_engine) as database:
        stored_work = database.get(WorkItem, UUID(str(work["id"])))
        stored_checkpoint = database.get(Checkpoint, UUID(checkpoint["id"]))
        assert stored_work is not None and stored_work.completion_generation == 0
        assert stored_checkpoint is not None
        assert stored_checkpoint.completion_generation == 0
        assert database.scalar(select(func.count()).select_from(VerificationResult)) == 2
        assert database.scalar(select(func.count()).select_from(ArtifactReference)) == 2
        receipt = database.scalar(
            select(ClientOperation).where(
                ClientOperation.project_id == UUID(str(project["id"])),
                ClientOperation.client_operation_id == UUID(operation_id),
            )
        )
        assert receipt is not None
        assert receipt.response_body == body


def test_maximum_escaping_completion_representations_fit_896_kib(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work_control = "\x03"
    created = api.post(
        _collection(project),
        json={
            **work_payload,
            "title": work_control * 200,
            "summary": work_control * 1000,
            "priority": 100,
        },
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    operation_id = str(uuid4())
    lease_token = "\x02" * 200
    request_body = _maximum_escaping_completion_payload(operation_id, lease_token)
    parsed = WorkCompletionCreate.model_validate(request_body)
    assert parsed.completion_evidence is not None
    assert len(parsed.checkpoint.prompt) == 100000
    assert len(parsed.checkpoint.source_client) == 80
    assert len(parsed.checkpoint.source_session_id) == 200
    assert parsed.checkpoint.source_model is not None
    assert len(parsed.checkpoint.source_model) == 120
    assert parsed.checkpoint.source_session_url is not None
    assert len(parsed.checkpoint.source_session_url) == 2000
    assert parsed.checkpoint.repository_branch is not None
    assert len(parsed.checkpoint.repository_branch) == 200
    assert len(parsed.checkpoint.affected_paths) == 64
    assert len(parsed.checkpoint.tags) == 20
    assert parsed.lease_token is not None and len(parsed.lease_token) == 200
    assert len(parsed.completion_evidence.verification_results) == 20
    assert all(
        result.outcome == "failed" and result.exit_code == -2147483648
        for result in parsed.completion_evidence.verification_results
    )
    assert completion_evidence_text_bytes(
        parsed.completion_evidence.verification_results,
        parsed.completion_evidence.artifact_references,
    ) == 32768

    compact_request = json.dumps(
        parsed.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    prepared = prepare_client_operation(
        "complete_work",
        UUID(str(project["id"])),
        {"work_item_id": UUID(str(work["id"]))},
        parsed,
    )
    assert prepared.canonical_bytes is not None

    with postgres_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE work_items DISABLE TRIGGER completion_generation_guard")
        )
        connection.execute(
            text(
                """
                UPDATE work_items
                SET version = :version
                WHERE id = CAST(:work_item_id AS uuid)
                """
            ),
            {
                "version": COMPLETION_EXPECTED_VERSION_MAX,
                "work_item_id": work["id"],
            },
        )
        connection.execute(
            text("ALTER TABLE work_items ENABLE TRIGGER completion_generation_guard")
        )
        connection.execute(
            text(
                """
                WITH observed AS (SELECT pg_catalog.clock_timestamp() AS value)
                INSERT INTO work_leases (
                    work_item_id, holder_client, holder_session_id,
                    claim_request_id, lease_token, lease_generation_id,
                    acquired_at, renewed_at, expires_at
                )
                SELECT CAST(:work_item_id AS uuid), 'pytest', 'maximum-envelope',
                       'maximum-envelope', :lease_token, gen_random_uuid(),
                       value, value, value + interval '1 hour'
                FROM observed
                """
            ),
            {"work_item_id": work["id"], "lease_token": lease_token},
        )

    completed = api.post(
        f"{_item_path(project, work)}/complete",
        json=request_body,
    )
    assert completed.status_code == 200, completed.text

    with postgres_engine.connect() as connection:
        stored_jsonb_bytes = connection.scalar(
            text(
                """
                SELECT pg_catalog.octet_length(response_body::text)
                FROM client_operations
                WHERE client_operation_id = CAST(:operation_id AS uuid)
                """
            ),
            {"operation_id": operation_id},
        )
    measurements = {
        "compact_request": len(compact_request),
        "fingerprint_envelope": len(prepared.canonical_bytes),
        "api_response": len(completed.content),
        "postgres_jsonb": stored_jsonb_bytes,
    }
    assert all(isinstance(value, int) for value in measurements.values())
    assert all(800 * 1024 < value <= 896 * 1024 for value in measurements.values()), (
        measurements
    )


def test_sparse_empty_forms_and_nonempty_identity_requirement_are_atomic(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    for index, evidence in enumerate((..., {}, {"verification_results": []})):
        work = _create_work(api, project, work_payload, title=f"Sparse evidence {index}")
        response = _complete(
            api,
            project,
            work,
            1,
            f"sparse-{index}",
            evidence=evidence,
        )
        assert response.status_code == 200, response.text
        assert "completion_evidence" not in response.json()

    work = _create_work(api, project, work_payload, title="Missing evidence identity")
    rejected = _complete(
        api,
        project,
        work,
        1,
        "missing-operation",
        evidence=_mixed_evidence("must-not-persist-marker"),
    )
    assert rejected.status_code == 422
    assert "must-not-persist-marker" not in rejected.text
    explicit_null = _complete(
        api,
        project,
        work,
        1,
        "null-evidence",
        evidence=None,
        operation_id=str(uuid4()),
    )
    assert explicit_null.status_code == 422

    detail = api.get(_item_path(project, work)).json()["work_item"]
    assert detail["status"] == "pending"
    assert detail["version"] == 1
    with Session(postgres_engine) as database:
        work_id = UUID(str(work["id"]))
        assert (
            database.scalar(
                select(func.count())
                .select_from(Checkpoint)
                .where(
                    Checkpoint.work_item_id == work_id,
                    Checkpoint.kind == "completion",
                )
            )
            == 0
        )
        assert (
            database.scalar(
                select(func.count())
                .select_from(WorkEvent)
                .where(
                    WorkEvent.work_item_id == work_id,
                    WorkEvent.event_type == "work_completed",
                )
            )
            == 0
        )
        assert database.scalar(select(func.count()).select_from(VerificationResult)) == 0
        assert database.scalar(select(func.count()).select_from(ArtifactReference)) == 0


def test_exact_receipt_replay_survives_reopen_and_second_episode(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
):
    work = _create_work(api, project, work_payload)
    first_operation = str(uuid4())
    first_payload = _mixed_evidence("first-episode-marker")
    first = _complete(
        api,
        project,
        work,
        1,
        "first-episode",
        evidence=first_payload,
        operation_id=first_operation,
    )
    assert first.status_code == 200, first.text
    assert (
        _complete(
            api,
            project,
            work,
            1,
            "first-episode",
            evidence=first_payload,
            operation_id=first_operation,
        ).json()
        == first.json()
    )

    changed = deepcopy(first_payload)
    changed["verification_results"][0]["summary"] = "Different semantic intent."
    conflict = _complete(
        api,
        project,
        work,
        1,
        "first-episode",
        evidence=changed,
        operation_id=first_operation,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "client_operation_conflict"

    _reopen(api, project, work, 2)
    replay_after_reopen = _complete(
        api,
        project,
        work,
        1,
        "first-episode",
        evidence=first_payload,
        operation_id=first_operation,
    )
    assert replay_after_reopen.json() == first.json()

    second = _complete(
        api,
        project,
        work,
        3,
        "second-episode",
        evidence={
            "verification_results": [
                {
                    "verification_type": "observation",
                    "name": "Second cold review",
                    "outcome": "passed",
                    "summary": "The replacement completion was accepted.",
                }
            ]
        },
        operation_id=str(uuid4()),
    )
    assert second.status_code == 200, second.text
    page = api.get(f"{_item_path(project, work)}/completion-evidence").json()
    assert page["total"] == page["structured_completion_total"] == 2
    assert page["current_completion_checkpoint_id"] == second.json()["checkpoint"]["id"]
    assert [item["completion_checkpoint"]["id"] for item in page["items"]] == [
        second.json()["checkpoint"]["id"],
        first.json()["checkpoint"]["id"],
    ]
    assert page["items"][1]["verification_results"][0]["name"] == ("first-episode-marker")


def test_cursor_high_water_scope_and_totals_remain_stable(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
):
    work = _create_work(api, project, work_payload)
    checkpoints = []
    version = 1
    for episode in range(3):
        response = _complete(
            api,
            project,
            work,
            version,
            f"page-{episode}",
            evidence=(
                {
                    "artifact_references": [
                        {
                            "artifact_type": "commit",
                            "label": f"Commit {episode}",
                            "reference": f"{episode + 1:07x}",
                        }
                    ]
                }
                if episode != 1
                else ...
            ),
            operation_id=str(uuid4()) if episode != 1 else ...,
        )
        assert response.status_code == 200, response.text
        checkpoints.append(response.json()["checkpoint"]["id"])
        version += 1
        if episode < 2:
            _reopen(api, project, work, version)
            version += 1

    first_response = api.get(
        f"{_item_path(project, work)}/completion-evidence", params={"limit": 1}
    )
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    assert first["total"] == 3
    assert first["structured_completion_total"] == 2
    assert first["items"][0]["completion_checkpoint"]["id"] == checkpoints[2]
    assert first["next_cursor"]
    high_water = first["as_of_completion_event_id"]

    _reopen(api, project, work, version)
    version += 1
    newest = _complete(
        api,
        project,
        work,
        version,
        "post-high-water",
        evidence=_mixed_evidence("newer-than-high-water"),
        operation_id=str(uuid4()),
    )
    assert newest.status_code == 200, newest.text

    second_response = api.get(
        f"{_item_path(project, work)}/completion-evidence",
        params={"limit": 1, "cursor": first["next_cursor"]},
    )
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    assert second["as_of_completion_event_id"] == high_water
    assert second["total"] == 3
    assert second["structured_completion_total"] == 2
    assert second["items"][0]["completion_checkpoint"]["id"] == checkpoints[1]
    assert second["current_completion_checkpoint_id"] == newest.json()["checkpoint"]["id"]

    other = _create_work(api, project, work_payload, title="Other cursor scope")
    wrong_scope = api.get(
        f"{_item_path(project, other)}/completion-evidence",
        params={"cursor": first["next_cursor"]},
    )
    assert wrong_scope.status_code == 422
    assert wrong_scope.json()["detail"]["code"] == "invalid_cursor"
    malformed = api.get(
        f"{_item_path(project, work)}/completion-evidence",
        params={"cursor": "not+canonical/base64=="},
    )
    assert malformed.status_code == 422
    assert malformed.json()["detail"]["code"] == "invalid_cursor"


def _captured_history_statements(
    api: TestClient,
    postgres_engine: Engine,
    path: str,
) -> tuple[list[str], dict[str, object]]:
    statements: list[str] = []

    def capture_statement(connection, cursor, statement, parameters, context, executemany):
        del connection, cursor, parameters, context, executemany
        statements.append(statement)

    event.listen(postgres_engine, "before_cursor_execute", capture_statement)
    try:
        response = api.get(path)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture_statement)
    assert response.status_code == 200, response.text
    return statements, response.json()


def test_history_query_count_is_page_constant_and_checkpoint_projection_is_compact(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    one_episode = _create_work(api, project, work_payload, title="One history episode")
    first = _complete(
        api,
        project,
        one_episode,
        1,
        "single-history",
        evidence=_mixed_evidence("single-history-evidence"),
        operation_id=str(uuid4()),
    )
    assert first.status_code == 200, first.text

    three_episodes = _create_work(api, project, work_payload, title="Three history episodes")
    version = 1
    for index in range(3):
        completed = _complete(
            api,
            project,
            three_episodes,
            version,
            f"constant-query-{index}",
            evidence=_mixed_evidence(f"constant-query-evidence-{index}"),
            operation_id=str(uuid4()),
        )
        assert completed.status_code == 200, completed.text
        version += 1
        if index < 2:
            _reopen(api, project, three_episodes, version)
            version += 1

    one_sql, one_page = _captured_history_statements(
        api,
        postgres_engine,
        f"{_item_path(project, one_episode)}/completion-evidence",
    )
    three_sql, three_page = _captured_history_statements(
        api,
        postgres_engine,
        f"{_item_path(project, three_episodes)}/completion-evidence",
    )
    assert len(one_sql) == len(three_sql)
    assert len(one_page["items"]) == 1
    assert len(three_page["items"]) == 3

    page_queries = [
        statement for statement in three_sql if "mnemonic_completion_episode_is_sealed" in statement
    ]
    assert len(page_queries) == 1
    page_query = page_queries[0]
    for excluded in (
        "checkpoints.prompt",
        "checkpoints.source_metadata",
        "checkpoints.affected_paths",
    ):
        assert excluded not in page_query


def test_completion_seal_ordering_uses_bounded_neighbor_lookups(
    postgres_engine: Engine,
):
    with postgres_engine.connect() as connection:
        body = connection.scalar(
            text(
                """
                SELECT procedure.prosrc
                FROM pg_catalog.pg_proc AS procedure
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = pg_catalog.current_schema()
                  AND procedure.proname = 'mnemonic_completion_episode_is_sealed'
                  AND pg_catalog.oidvectortypes(procedure.proargtypes)
                        = 'uuid, bigint'
                """
            )
        )
    assert isinstance(body, str)
    assert body.count("ORDER BY candidate.completion_generation ASC") == 2
    assert body.count("ORDER BY candidate.completion_generation DESC") == 1
    assert body.count("LIMIT 1") == 4


def test_history_fails_closed_when_completion_event_checkpoint_is_missing(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    completed = _complete(
        api,
        project,
        work,
        1,
        "missing-checkpoint",
        evidence=_mixed_evidence(),
        operation_id=str(uuid4()),
    )
    assert completed.status_code == 200, completed.text
    with postgres_engine.begin() as connection:
        connection.execute(text("DROP TRIGGER events_immutable ON work_events"))
        connection.execute(
            text("ALTER TABLE work_events DROP CONSTRAINT fk_work_events_checkpoint")
        )
        connection.execute(
            text(
                """
                UPDATE work_events
                SET checkpoint_id = CAST(:checkpoint_id AS uuid)
                WHERE work_item_id = CAST(:work_item_id AS uuid)
                  AND event_type = 'work_completed'
                """
            ),
            {"checkpoint_id": str(uuid4()), "work_item_id": work["id"]},
        )

    response = api.get(f"{_item_path(project, work)}/completion-evidence")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "completion_evidence_unavailable"


def test_history_fails_closed_on_duplicate_completion_event_binding(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload, title="Duplicate event binding")
    completed = _complete(
        api,
        project,
        work,
        1,
        "duplicate-completion-event",
        evidence=_mixed_evidence(),
        operation_id=str(uuid4()),
    )
    assert completed.status_code == 200, completed.text

    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL allow_system_table_mods = on"))
        connection.execute(
            text(
                """
                UPDATE pg_catalog.pg_index
                SET indisunique = false
                WHERE indexrelid = 'uq_work_events_checkpoint_fact'::regclass
                """
            )
        )
        connection.execute(
            text("ALTER TABLE work_events DISABLE TRIGGER completion_lifecycle_event_insert_guard")
        )
        connection.execute(
            text(
                """
                INSERT INTO work_events (
                    project_id, work_item_id, event_type, actor_kind,
                    actor_client, actor_session_id, actor_model, body,
                    checkpoint_id, metadata_version, metadata, origin, created_at
                )
                SELECT project_id, work_item_id, event_type, actor_kind,
                       actor_client, actor_session_id, actor_model, body,
                       checkpoint_id, metadata_version, metadata, origin, created_at
                FROM work_events
                WHERE work_item_id = CAST(:work_item_id AS uuid)
                  AND event_type = 'work_completed'
                """
            ),
            {"work_item_id": work["id"]},
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        connection.execute(
            text("ALTER TABLE work_events ENABLE TRIGGER completion_lifecycle_event_insert_guard")
        )
        connection.execute(
            text(
                """
                UPDATE pg_catalog.pg_index
                SET indisunique = true
                WHERE indexrelid = 'uq_work_events_checkpoint_fact'::regclass
                """
            )
        )

    response = api.get(f"{_item_path(project, work)}/completion-evidence")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "completion_evidence_unavailable"


def _drop_event_mutation_guards(connection: Connection) -> None:
    connection.execute(text("DROP TRIGGER events_immutable ON work_events"))
    connection.execute(
        text("ALTER TABLE work_events DROP CONSTRAINT ck_work_events_metadata_v1_valid")
    )


def _two_completion_episodes(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    *,
    title: str,
) -> dict[str, object]:
    work = _create_work(api, project, work_payload, title=title)
    first = _complete(api, project, work, 1, f"{title}-first")
    assert first.status_code == 200, first.text
    _reopen(api, project, work, 2)
    second = _complete(api, project, work, 3, f"{title}-second")
    assert second.status_code == 200, second.text
    return work


def test_history_fails_closed_on_selected_completion_with_null_version_metadata(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload, title="Malformed selected completion")
    assert _complete(api, project, work, 1, "malformed-selected").status_code == 200
    with postgres_engine.begin() as connection:
        _drop_event_mutation_guards(connection)
        connection.execute(
            text(
                "UPDATE work_events SET metadata = metadata - 'work_version' "
                "WHERE work_item_id = CAST(:work_item_id AS uuid) "
                "AND event_type = 'work_completed'"
            ),
            {"work_item_id": work["id"]},
        )

    response = api.get(f"{_item_path(project, work)}/completion-evidence")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "completion_evidence_unavailable"


def test_history_fails_closed_on_malformed_immediate_prior_live_completion(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _two_completion_episodes(
        api, project, work_payload, title="Malformed prior completion"
    )
    with postgres_engine.begin() as connection:
        _drop_event_mutation_guards(connection)
        connection.execute(
            text(
                """
                UPDATE work_events
                SET metadata = metadata - 'work_version'
                WHERE id = (
                    SELECT min(id) FROM work_events
                    WHERE work_item_id = CAST(:work_item_id AS uuid)
                      AND event_type = 'work_completed'
                )
                """
            ),
            {"work_item_id": work["id"]},
        )

    response = api.get(
        f"{_item_path(project, work)}/completion-evidence", params={"limit": 1}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "completion_evidence_unavailable"


@pytest.mark.parametrize("corruption", ("same-generation", "successor"))
def test_history_fails_closed_on_malformed_reopen_lineage(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    corruption: str,
):
    work = _two_completion_episodes(api, project, work_payload, title=corruption)
    with postgres_engine.begin() as connection:
        _drop_event_mutation_guards(connection)
        if corruption == "same-generation":
            connection.execute(
                text(
                    "UPDATE work_events SET metadata = metadata - 'to_status' "
                    "WHERE work_item_id = CAST(:work_item_id AS uuid) "
                    "AND event_type = 'work_reopened'"
                ),
                {"work_item_id": work["id"]},
            )
        else:
            connection.execute(
                text(
                    """
                    UPDATE work_events
                    SET metadata = pg_catalog.jsonb_set(
                        pg_catalog.jsonb_set(metadata, '{from_status}', '"deferred"'),
                        '{changes,status,before}', '"deferred"'
                    )
                    WHERE work_item_id = CAST(:work_item_id AS uuid)
                      AND event_type = 'work_reopened'
                    """
                ),
                {"work_item_id": work["id"]},
            )

    response = api.get(
        f"{_item_path(project, work)}/completion-evidence",
        params={"limit": 1 if corruption == "same-generation" else 2},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "completion_evidence_unavailable"


def test_history_accepts_version_ordered_completion_after_higher_id_reopen(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload, title="Cross-type IDs are unordered")
    first = _complete(api, project, work, 1, "lower-first")
    assert first.status_code == 200, first.text
    with postgres_engine.begin() as connection:
        sequence = connection.scalar(
            text("SELECT pg_catalog.pg_get_serial_sequence('work_events', 'id')")
        )
        assert isinstance(sequence, str)
        connection.execute(
            text("SELECT pg_catalog.setval(CAST(:sequence AS regclass), 1000000, false)"),
            {"sequence": sequence},
        )
    _reopen(api, project, work, 2)
    with postgres_engine.begin() as connection:
        sequence = connection.scalar(
            text("SELECT pg_catalog.pg_get_serial_sequence('work_events', 'id')")
        )
        assert isinstance(sequence, str)
        connection.execute(
            text("SELECT pg_catalog.setval(CAST(:sequence AS regclass), 500000, false)"),
            {"sequence": sequence},
        )
    second = _complete(api, project, work, 3, "lower-second")
    assert second.status_code == 200, second.text

    with postgres_engine.connect() as connection:
        event_ids = {
            row.event_type: row.id
            for row in connection.execute(
                text(
                    """
                    SELECT event_type, id FROM work_events
                    WHERE work_item_id = CAST(:work_item_id AS uuid)
                      AND event_type IN ('work_reopened', 'work_completed')
                    ORDER BY id
                    """
                ),
                {"work_item_id": work["id"]},
            )
        }
    assert event_ids["work_reopened"] > event_ids["work_completed"]
    response = api.get(f"{_item_path(project, work)}/completion-evidence")
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 2


def test_history_fails_closed_when_completion_event_checkpoint_is_cross_linked(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload, title="Cross-linked history")
    other_work = _create_work(api, project, work_payload, title="Other completion")
    completed = _complete(
        api,
        project,
        work,
        1,
        "cross-linked-checkpoint",
        evidence=_mixed_evidence(),
        operation_id=str(uuid4()),
    )
    other_completed = _complete(
        api,
        project,
        other_work,
        1,
        "foreign-checkpoint",
        evidence=_mixed_evidence("foreign evidence"),
        operation_id=str(uuid4()),
    )
    assert completed.status_code == 200, completed.text
    assert other_completed.status_code == 200, other_completed.text

    with postgres_engine.begin() as connection:
        connection.execute(text("DROP TRIGGER events_immutable ON work_events"))
        connection.execute(
            text("ALTER TABLE work_events DROP CONSTRAINT fk_work_events_checkpoint")
        )
        connection.execute(
            text(
                """
                UPDATE work_events
                SET checkpoint_id = CAST(:checkpoint_id AS uuid)
                WHERE work_item_id = CAST(:work_item_id AS uuid)
                  AND event_type = 'work_completed'
                """
            ),
            {
                "checkpoint_id": other_completed.json()["checkpoint"]["id"],
                "work_item_id": work["id"],
            },
        )

    response = api.get(f"{_item_path(project, work)}/completion-evidence")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "completion_evidence_unavailable"


@pytest.mark.parametrize(
    "drift",
    (
        "child_row",
        "receipt_json",
        "receipt_uuid_spelling",
        "receipt_boolean_for_integer",
        "receipt_float_for_integer",
    ),
)
def test_history_fails_closed_on_exact_child_set_or_receipt_drift(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    drift: str,
):
    work = _create_work(api, project, work_payload)
    operation_id = str(uuid4())
    completed = _complete(
        api,
        project,
        work,
        1,
        f"history-{drift}",
        evidence=_mixed_evidence(),
        operation_id=operation_id,
    )
    assert completed.status_code == 200, completed.text
    with postgres_engine.begin() as connection:
        if drift == "child_row":
            connection.execute(
                text("DROP TRIGGER verification_results_immutable ON verification_results")
            )
            connection.execute(
                text(
                    """
                    DELETE FROM verification_results
                    WHERE completion_checkpoint_id = CAST(:checkpoint_id AS uuid)
                      AND position = 1
                    """
                ),
                {"checkpoint_id": completed.json()["checkpoint"]["id"]},
            )
        else:
            connection.execute(
                text("DROP TRIGGER client_operation_mutation_guard ON client_operations")
            )
            if drift == "receipt_json":
                path = ["completion_evidence", "verification_results", "0", "summary"]
                replacement = '"tampered receipt summary"'
            elif drift == "receipt_uuid_spelling":
                result_id = completed.json()["completion_evidence"][
                    "verification_results"
                ][0]["id"]
                path = ["completion_evidence", "verification_results", "0", "id"]
                replacement = json.dumps("{" + result_id + "}")
            else:
                path = ["work_item", "priority"]
                replacement = (
                    "true" if drift == "receipt_boolean_for_integer" else "30.0"
                )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = pg_catalog.jsonb_set(
                        response_body, CAST(:path AS text[]), CAST(:replacement AS jsonb)
                    )
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {
                    "operation_id": operation_id,
                    "path": path,
                    "replacement": replacement,
                },
            )

    response = api.get(f"{_item_path(project, work)}/completion-evidence")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "completion_evidence_unavailable"


@pytest.mark.parametrize("limit", ("0", "11", "true"))
def test_history_query_rejects_invalid_limits_and_oversized_cursors(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    limit: str,
):
    work = _create_work(api, project, work_payload)
    path = f"{_item_path(project, work)}/completion-evidence"
    assert api.get(path, params={"limit": limit}).status_code == 422
    assert api.get(path, params={"cursor": "x" * 4097}).status_code == 422
    assert api.get(path, params={"cursor": "AAAAA"}).status_code == 422

    decoded_oversized_at_outer_limit = api.get(path, params={"cursor": "A" * 4096})
    assert decoded_oversized_at_outer_limit.status_code == 422
    assert decoded_oversized_at_outer_limit.json()["detail"]["code"] == "invalid_cursor"

    oversized = base64.urlsafe_b64encode(b"{" + b" " * 2048 + b"}").rstrip(b"=")
    response = api.get(path, params={"cursor": oversized.decode("ascii")})
    assert response.status_code == 422


def test_alias_history_remains_source_owned_without_canonical_blending(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
):
    source = _create_work(api, project, work_payload, title="Alias evidence source")
    destination = _create_work(api, project, work_payload, title="Canonical evidence destination")
    source_completion = _complete(
        api,
        project,
        source,
        1,
        "source-evidence",
        evidence=_mixed_evidence("source-only-evidence"),
        operation_id=str(uuid4()),
    )
    destination_completion = _complete(
        api,
        project,
        destination,
        1,
        "destination-evidence",
        evidence=_mixed_evidence("destination-only-evidence"),
        operation_id=str(uuid4()),
    )
    assert source_completion.status_code == destination_completion.status_code == 200
    source_context = api.get(f"{_item_path(project, source)}/context").json()
    destination_context = api.get(f"{_item_path(project, destination)}/context").json()
    merged = api.post(
        f"{_item_path(project, source)}/merge",
        json={
            "destination_work_item_id": destination["id"],
            "reviewed_source_revision": source_context["merge_review_revision"],
            "reviewed_destination_revision": destination_context["merge_review_revision"],
            "rationale": "The destination is the reviewed canonical continuation.",
            "merged_by_client": "pytest",
            "merged_by_session_id": "phase11-alias-history",
            "client_operation_id": str(uuid4()),
        },
    )
    assert merged.status_code == 201, merged.text

    source_page = api.get(f"{_item_path(project, source)}/completion-evidence").json()
    destination_page = api.get(f"{_item_path(project, destination)}/completion-evidence").json()
    assert source_page["is_duplicate"] is True
    assert source_page["canonical_work_item_id"] == destination["id"]
    assert source_page["current_completion_checkpoint_id"] is None
    assert source_page["total"] == 1
    assert "source-only-evidence" in json.dumps(source_page)
    assert "destination-only-evidence" not in json.dumps(source_page)
    assert "destination-only-evidence" in json.dumps(destination_page)
    assert "source-only-evidence" not in json.dumps(destination_page)


def test_soft_deleted_history_is_concealed_by_the_evidence_route(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
):
    work = _create_work(api, project, work_payload)
    completed = _complete(
        api,
        project,
        work,
        1,
        "before-delete",
        evidence=_mixed_evidence(),
        operation_id=str(uuid4()),
    )
    assert completed.status_code == 200, completed.text
    deleted = api.post(
        f"{_item_path(project, work)}/delete",
        json={"expected_version": 2},
    )
    assert deleted.status_code == 200, deleted.text
    assert api.get(_item_path(project, work)).status_code == 404

    history = api.get(f"{_item_path(project, work)}/completion-evidence")
    assert history.status_code == 404
    assert history.json()["detail"]["code"] == "work_item_not_found"


def test_retained_deletion_tombstone_cleared_by_owner_has_no_current_pointer(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    completed = _complete(
        api,
        project,
        work,
        1,
        "before-tombstone",
        evidence=_mixed_evidence(),
        operation_id=str(uuid4()),
    )
    assert completed.status_code == 200, completed.text
    deleted = api.post(f"{_item_path(project, work)}/delete", json={"expected_version": 2})
    assert deleted.status_code == 200, deleted.text
    with postgres_engine.begin() as connection:
        connection.execute(
            text("UPDATE work_items SET deleted_at = NULL WHERE id = CAST(:id AS uuid)"),
            {"id": work["id"]},
        )

    response = api.get(f"{_item_path(project, work)}/completion-evidence")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["lifecycle_status"] == "done"
    assert page["total"] == 1
    assert page["current_completion_checkpoint_id"] is None


@pytest.mark.parametrize(
    "fault",
    (
        "reservation",
        "lease",
        "checkpoint",
        "first-result",
        "between-families",
        "evidence",
        "work-update",
        "event-staged",
        "event-flushed",
        "response-hydrated",
        "response",
        "receipt",
        "commit",
    ),
)
def test_fault_after_each_durable_completion_step_rolls_back_exactly(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
):
    work = _create_work(api, project, work_payload)
    claim = api.post(
        f"{_item_path(project, work)}/claim",
        json={
            "holder_client": "pytest",
            "holder_session_id": "phase11-fault-matrix",
            "claim_request_id": f"fault-{fault}",
        },
    )
    assert claim.status_code == 200, claim.text
    operation_id = str(uuid4())
    request_body = _completion_payload(
        1,
        f"rollback-{fault}",
        evidence=_mixed_evidence(),
        operation_id=operation_id,
    )
    request_body["lease_token"] = claim.json()["lease_token"]
    before = _completion_durable_snapshot(postgres_engine, work["id"], operation_id)

    _install_completion_fault(monkeypatch, fault)
    with pytest.raises(RuntimeError, match="synthetic completion durability fault"):
        api.post(f"{_item_path(project, work)}/complete", json=request_body)
    monkeypatch.undo()

    assert _completion_durable_snapshot(postgres_engine, work["id"], operation_id) == before
    retried = api.post(f"{_item_path(project, work)}/complete", json=request_body)
    assert retried.status_code == 200, retried.text
    after = _completion_durable_snapshot(postgres_engine, work["id"], operation_id)
    assert after["status"] == "done"
    assert after["version"] == 2
    assert after["completion_generation"] == 0
    assert after["updated_at"] != before["updated_at"]
    assert after["completion_checkpoints"] == 1
    assert after["completion_events"] == 1
    assert after["verification_results"] == 2
    assert after["artifact_references"] == 2
    assert after["client_operations"] == 1
    assert after["work_leases"] == 0


def test_unknown_outcome_after_commit_replays_exact_evidence_receipt(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    work = _create_work(api, project, work_payload)
    operation_id = str(uuid4())
    request_body = _completion_payload(
        1,
        "unknown-after-commit",
        evidence=_mixed_evidence(),
        operation_id=operation_id,
    )

    def fail_after_commit(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("synthetic response loss after commit")

    monkeypatch.setattr(mutations_module, "_record_success", fail_after_commit)
    with pytest.raises(RuntimeError, match="synthetic response loss after commit"):
        api.post(f"{_item_path(project, work)}/complete", json=request_body)
    monkeypatch.undo()

    committed = _completion_durable_snapshot(postgres_engine, work["id"], operation_id)
    assert committed["completion_checkpoints"] == 1
    assert committed["completion_events"] == 1
    assert committed["verification_results"] == 2
    assert committed["artifact_references"] == 2
    assert committed["client_operations"] == 1
    replayed = api.post(f"{_item_path(project, work)}/complete", json=request_body)
    assert replayed.status_code == 200, replayed.text
    with postgres_engine.connect() as connection:
        stored_body = connection.scalar(
            text(
                """
                SELECT response_body
                FROM client_operations
                WHERE client_operation_id = CAST(:operation_id AS uuid)
                """
            ),
            {"operation_id": operation_id},
        )
    assert replayed.json() == stored_body
    assert _completion_durable_snapshot(postgres_engine, work["id"], operation_id) == committed


def test_completion_sql_orders_children_before_state_event_and_receipt(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    statements: list[str] = []

    def capture_statement(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        statements.append(" ".join(statement.lower().split()))

    event.listen(postgres_engine, "before_cursor_execute", capture_statement)
    try:
        completed = _complete(
            api,
            project,
            work,
            1,
            "sql-write-order",
            evidence=_mixed_evidence(),
            operation_id=str(uuid4()),
        )
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture_statement)
    assert completed.status_code == 200, completed.text

    def statement_index(*needles: str) -> int:
        return next(
            index
            for index, statement in enumerate(statements)
            if all(needle in statement for needle in needles)
        )

    checkpoint_insert = statement_index("insert into checkpoints")
    verification_insert = statement_index("insert into verification_results")
    artifact_insert = statement_index("insert into artifact_references")
    work_update = statement_index("update work_items set", "status=")
    completion_event_insert = statement_index("insert into work_events", "event_type")
    receipt_update = statement_index("update client_operations set", "response_body=")
    assert checkpoint_insert < verification_insert < work_update
    assert checkpoint_insert < artifact_insert < work_update
    assert work_update < completion_event_insert < receipt_update


def test_evidence_receipt_replays_exactly_after_authoritative_merge(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    source = _create_work(api, project, work_payload, title="Replay source after merge")
    destination = _create_work(api, project, work_payload, title="Replay destination after merge")
    operation_id = str(uuid4())
    request_body = _completion_payload(
        1,
        "replay-after-merge",
        evidence=_mixed_evidence("merge-replay-evidence"),
        operation_id=operation_id,
    )
    completed = api.post(f"{_item_path(project, source)}/complete", json=request_body)
    assert completed.status_code == 200, completed.text
    source_context = api.get(f"{_item_path(project, source)}/context").json()
    destination_context = api.get(f"{_item_path(project, destination)}/context").json()
    merged = api.post(
        f"{_item_path(project, source)}/merge",
        json={
            "destination_work_item_id": destination["id"],
            "reviewed_source_revision": source_context["merge_review_revision"],
            "reviewed_destination_revision": destination_context["merge_review_revision"],
            "rationale": "The destination is the reviewed canonical continuation.",
            "merged_by_client": "pytest",
            "merged_by_session_id": "phase11-merge-replay",
            "client_operation_id": str(uuid4()),
        },
    )
    assert merged.status_code == 201, merged.text
    before_replay = _completion_durable_snapshot(
        postgres_engine,
        source["id"],
        operation_id,
    )

    replayed = api.post(f"{_item_path(project, source)}/complete", json=request_body)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == completed.json()
    assert (
        _completion_durable_snapshot(postgres_engine, source["id"], operation_id)
        == before_replay
    )


def test_evidence_receipt_replays_exactly_after_soft_deletion_and_recovery(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    operation_id = str(uuid4())
    request_body = _completion_payload(
        1,
        "replay-after-deletion",
        evidence=_mixed_evidence("deletion-replay-evidence"),
        operation_id=operation_id,
    )
    completed = api.post(f"{_item_path(project, work)}/complete", json=request_body)
    assert completed.status_code == 200, completed.text
    deleted = api.post(f"{_item_path(project, work)}/delete", json={"expected_version": 2})
    assert deleted.status_code == 200, deleted.text
    before_replay = _completion_durable_snapshot(postgres_engine, work["id"], operation_id)

    replayed = api.post(f"{_item_path(project, work)}/complete", json=request_body)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == completed.json()
    assert _completion_durable_snapshot(postgres_engine, work["id"], operation_id) == before_replay

    with postgres_engine.begin() as connection:
        connection.execute(
            text("UPDATE work_items SET deleted_at = NULL WHERE id = CAST(:work_id AS uuid)"),
            {"work_id": work["id"]},
        )
    replayed_after_recovery = api.post(
        f"{_item_path(project, work)}/complete",
        json=request_body,
    )
    assert replayed_after_recovery.status_code == 200, replayed_after_recovery.text
    assert replayed_after_recovery.json() == completed.json()
    recovered = _completion_durable_snapshot(postgres_engine, work["id"], operation_id)
    assert {key: value for key, value in recovered.items() if key != "updated_at"} == {
        key: value for key, value in before_replay.items() if key != "updated_at"
    }


def test_evidence_receipt_replays_exactly_after_complete_schema_snapshot_restore(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    operation_id = str(uuid4())
    request_body = _completion_payload(
        1,
        "replay-after-restore",
        evidence=_mixed_evidence("restore-replay-evidence"),
        operation_id=operation_id,
    )
    completed = api.post(f"{_item_path(project, work)}/complete", json=request_body)
    assert completed.status_code == 200, completed.text
    source_snapshot = _completion_durable_snapshot(postgres_engine, work["id"], operation_id)
    with postgres_engine.connect() as connection:
        source_schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
    assert isinstance(source_schema, str)

    target_schema = "mnemonic_restore_" + uuid4().hex
    with postgres_engine.begin() as connection:
        connection.execute(CreateSchema(target_schema))
    target_engine = _restored_schema_engine(postgres_engine, target_schema)
    try:
        with target_engine.begin() as connection:
            command.upgrade(_alembic_config(connection), "head")
        _restore_complete_schema_snapshot(
            postgres_engine,
            target_engine,
            source_schema,
            target_schema,
        )
        settings = Settings(
            database_url=target_engine.url.render_as_string(hide_password=False),
            api_key=TEST_API_KEY,
        )
        with TestClient(create_app(settings, engine=target_engine)) as restored_api:
            restored_api.headers["Authorization"] = f"Bearer {TEST_API_KEY}"
            replayed = restored_api.post(
                f"{_item_path(project, work)}/complete",
                json=request_body,
            )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json() == completed.json()
        assert (
            _completion_durable_snapshot(target_engine, work["id"], operation_id)
            == source_snapshot
        )
    finally:
        target_engine.dispose()
        with postgres_engine.begin() as connection:
            connection.execute(DropSchema(target_schema, cascade=True))


def _database_artifact_reference_is_valid(
    connection: Connection,
    artifact_type: str,
    reference: str,
) -> bool:
    accepted = connection.scalar(
        text(
            "SELECT mnemonic_completion_artifact_reference_v1_is_valid("
            ":artifact_type, :reference)"
        ),
        {"artifact_type": artifact_type, "reference": reference},
    )
    assert isinstance(accepted, bool)
    return accepted


def _assert_database_shared_artifact_vectors(connection: Connection) -> None:
    for case in CORPUS["cases"]:
        artifacts = (case["semantic_input"] or {}).get("artifact_references", [])
        if case["case_id"] == "duplicate_artifact":
            continue
        for artifact in artifacts:
            assert _database_artifact_reference_is_valid(
                connection,
                artifact["artifact_type"],
                artifact["reference"],
            ) is case["valid"], case["case_id"]


def _assert_database_ipv6_attack_vectors(connection: Connection) -> None:
    for valid_url in (
        "https://example.test/",
        "https://example.test:8443/path",
        "https://127.0.0.1/path",
        "https://[::1]/path",
        "https://[2001:db8::1]/runs/1",
        "https://[2001:db8::1]:8443/runs/1",
    ):
        assert _database_artifact_reference_is_valid(connection, "test_run", valid_url)

    for invalid_url in (
        "https://[::ffff:192.0.2.1]/path",
        "https://[2001:0db8::1]/runs/1",
        "https://[2001:0db8::1]:8443/runs/1",
        "https://[0:0:0:0:0:0:0:1]/runs/1",
        "https://[0000::1]/runs/1",
    ):
        assert not _database_artifact_reference_is_valid(
            connection, "external_issue", invalid_url
        )

    for hostname, expected in (
        ("::", True),
        ("::1", True),
        ("1::", True),
        ("0:1::", True),
        ("::1:0", True),
        ("2001:db8:0:1:2:3:4:5", True),
        ("2001::1:0:0:1:1", True),
        ("2001:db8::1:2:3:4:5", False),
        ("0::1", False),
        ("1::0", False),
        ("2001:0::1", False),
        ("2001::0:1", False),
        ("2001:0:0:1::1:1", False),
        ("2001:0:0:1:2:3:4:5", False),
    ):
        for port in ("", ":8443"):
            assert _database_artifact_reference_is_valid(
                connection,
                "test_run",
                f"https://[{hostname}]{port}/runs/1",
            ) is expected, (hostname, port)


def _assert_database_ipv6_mask_parity(connection: Connection) -> None:
    nonzero_groups = ("1", "2", "3", "4", "5", "6", "7", "8")
    for mask in range(256):
        expanded = ":".join(
            "0" if mask & (1 << position) else nonzero_groups[position]
            for position in range(8)
        )
        canonical = str(ipaddress.IPv6Address(expanded))
        for hostname in {expanded, canonical}:
            expected = hostname == canonical
            for port in ("", ":8443"):
                assert _database_artifact_reference_is_valid(
                    connection,
                    "test_run",
                    f"https://[{hostname}]{port}/runs/1",
                ) is expected, (mask, hostname, port)


def _assert_database_branch_vectors(connection: Connection) -> None:
    for branch, expected in (
        ("work/phase11", True),
        ("work/phase\t11", True),
        ("work/phase\x1c11", True),
        ("\ufeffwork/phase11", True),
        ("\twork/phase11", False),
        ("work/phase11\x85", False),
        ("\x1cwork/phase11", False),
    ):
        assert _database_artifact_reference_is_valid(
            connection, "branch", branch
        ) is expected


def test_database_artifact_validator_matches_shared_single_reference_vectors(
    api: TestClient,
    postgres_engine: Engine,
):
    del api
    with postgres_engine.connect() as connection:
        _assert_database_shared_artifact_vectors(connection)
        _assert_database_ipv6_attack_vectors(connection)
        _assert_database_ipv6_mask_parity(connection)
        _assert_database_branch_vectors(connection)


def test_database_rejects_post_completion_mutation_append_and_truncate(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    completed = _complete(
        api,
        project,
        work,
        1,
        "immutable",
        evidence=_mixed_evidence(),
        operation_id=str(uuid4()),
    )
    assert completed.status_code == 200, completed.text
    result = completed.json()["completion_evidence"]["verification_results"][0]
    artifact = completed.json()["completion_evidence"]["artifact_references"][0]
    checkpoint = completed.json()["checkpoint"]

    for statement, row_id in (
        ("UPDATE verification_results SET summary = 'changed' WHERE id = :id", result["id"]),
        ("DELETE FROM verification_results WHERE id = :id", result["id"]),
        ("UPDATE artifact_references SET label = 'changed' WHERE id = :id", artifact["id"]),
        ("DELETE FROM artifact_references WHERE id = :id", artifact["id"]),
    ):
        with pytest.raises(DBAPIError, match="completion evidence is immutable"):
            with postgres_engine.begin() as connection:
                connection.execute(text(statement), {"id": row_id})

    with pytest.raises(DBAPIError, match="inserted only in an open completion episode"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO artifact_references (
                        project_id, work_item_id, completion_checkpoint_id, position,
                        artifact_type, label, reference, created_at
                    ) VALUES (
                        :project_id, :work_item_id, :checkpoint_id, 2,
                        'commit', 'Late append', 'abcdef1', :created_at
                    )
                    """
                ),
                {
                    "project_id": project["id"],
                    "work_item_id": work["id"],
                    "checkpoint_id": checkpoint["id"],
                    "created_at": checkpoint["created_at"],
                },
            )

    for table in (
        "verification_results",
        "artifact_references",
        "work_events",
        "client_operations",
    ):
        with pytest.raises(DBAPIError, match="cannot be truncated"):
            with postgres_engine.begin() as connection:
                connection.execute(text(f"TRUNCATE TABLE {table}"))

    with pytest.raises(DBAPIError, match="cannot be truncated"):
        with postgres_engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE projects CASCADE"))


def test_database_rejects_generation_tamper_and_eventless_completion(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    with pytest.raises(DBAPIError, match="completion generation is database managed"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE work_items SET completion_generation = 7 WHERE id = CAST(:id AS uuid)"
                ),
                {"id": work["id"]},
            )

    with pytest.raises(DBAPIError, match="completion checkpoint requires one sealed episode"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO checkpoints (
                        id, work_item_id, kind, prompt, source_client,
                        source_session_id, repository_branch, affected_paths,
                        tags, source_metadata, created_at
                    ) VALUES (
                        CAST(:id AS uuid), CAST(:work_item_id AS uuid), 'completion',
                        'Unsealed completion must roll back.', 'pytest',
                        'eventless-completion', 'work/phase11', '{}'::varchar[],
                        '{}'::varchar[], '{}'::jsonb, clock_timestamp()
                    )
                    """
                ),
                {"id": str(uuid4()), "work_item_id": work["id"]},
            )

    with Session(postgres_engine) as database:
        assert (
            database.scalar(
                select(func.count())
                .select_from(Checkpoint)
                .where(
                    Checkpoint.work_item_id == UUID(str(work["id"])),
                    Checkpoint.kind == "completion",
                )
            )
            == 0
        )


def test_direct_sql_legal_aggregate_commits_with_artifacts_inserted_first(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    with postgres_engine.begin() as connection:
        checkpoint = _insert_direct_completion_checkpoint(connection, work["id"])
        _insert_direct_artifact(
            connection,
            project["id"],
            work["id"],
            checkpoint,
            artifact_type="test_run",
            reference="https://[2001::1:0:0:1:1]:8443/runs/1",
        )
        _insert_direct_observation(connection, project["id"], work["id"], checkpoint)
        version = _transition_direct_completion_to_done(connection, work["id"])
        event_id = _insert_direct_completion_event(
            connection, project["id"], work["id"], checkpoint, version
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert connection.scalar(
            text(
                "SELECT mnemonic_completion_episode_is_sealed("
                "CAST(:work_item_id AS uuid), 0)"
            ),
            {"work_item_id": work["id"]},
        ) is True

    with postgres_engine.connect() as connection:
        counts = connection.execute(
            text(
                """
                SELECT
                    (SELECT count(*) FROM verification_results
                     WHERE completion_checkpoint_id = CAST(:checkpoint_id AS uuid)),
                    (SELECT count(*) FROM artifact_references
                     WHERE completion_checkpoint_id = CAST(:checkpoint_id AS uuid)),
                    (SELECT count(*) FROM work_events WHERE id = :event_id)
                """
            ),
            {"checkpoint_id": str(checkpoint["id"]), "event_id": event_id},
        ).one()
    assert counts == (1, 1, 1)


@pytest.mark.parametrize(
    "reference",
    (
        "https://[2001:0db8::1]/runs/1",
        "https://[2001:0db8::1]:8443/runs/1",
        "https://[0:0:0:0:0:0:0:1]/runs/1",
        "https://[0000::1]/runs/1",
        "https://[2001:db8::1:2:3:4:5]/runs/1",
        "https://[2001:db8::1:2:3:4:5]:8443/runs/1",
        "https://[0::1]/runs/1",
        "https://[0::1]:8443/runs/1",
        "https://[1::0]/runs/1",
        "https://[1::0]:8443/runs/1",
        "https://[2001:0::1]/runs/1",
        "https://[2001:0::1]:8443/runs/1",
        "https://[2001:0:0:1::1:1]/runs/1",
        "https://[2001:0:0:1::1:1]:8443/runs/1",
    ),
)
def test_direct_sql_storage_rejects_noncanonical_ipv6_artifact_urls(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    reference: str,
):
    work = _create_work(api, project, work_payload)
    with pytest.raises(DBAPIError, match="ck_artifact_references_reference_valid"):
        with postgres_engine.begin() as connection:
            checkpoint = _insert_direct_completion_checkpoint(connection, work["id"])
            _insert_direct_artifact(
                connection,
                project["id"],
                work["id"],
                checkpoint,
                artifact_type="test_run",
                reference=reference,
            )


def test_direct_sql_eventless_episode_cannot_exit_pending_with_or_without_evidence(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    for target_status in ("deferred", "wont-do", "promoted"):
        for include_evidence in (False, True):
            with pytest.raises(DBAPIError, match="cannot abandon an unsealed completion"):
                with postgres_engine.begin() as connection:
                    checkpoint = _insert_direct_completion_checkpoint(connection, work["id"])
                    if include_evidence:
                        _insert_direct_observation(
                            connection, project["id"], work["id"], checkpoint
                        )
                    connection.execute(
                        text(
                            """
                            UPDATE work_items
                            SET status = :status, version = version + 1,
                                updated_at = clock_timestamp()
                            WHERE id = CAST(:work_item_id AS uuid)
                            """
                        ),
                        {"status": target_status, "work_item_id": work["id"]},
                    )

    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM checkpoints "
                "WHERE work_item_id = CAST(:work_item_id AS uuid) AND kind = 'completion'"
            ),
            {"work_item_id": work["id"]},
        ) == 0


def test_direct_sql_rejects_duplicate_pending_generation_checkpoints(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    with pytest.raises(DBAPIError, match="uq_checkpoints_completion_generation"):
        with postgres_engine.begin() as connection:
            _insert_direct_completion_checkpoint(connection, work["id"])
            _insert_direct_completion_checkpoint(connection, work["id"])

    first_id = uuid4()
    second_id = uuid4()
    with pytest.raises(DBAPIError, match="uq_checkpoints_completion_generation"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO checkpoints (
                        id, work_item_id, kind, prompt, source_client,
                        source_session_id, affected_paths, tags, source_metadata
                    ) VALUES
                    (CAST(:first_id AS uuid), CAST(:work_item_id AS uuid), 'completion',
                     'First completion.', 'pytest', 'direct-sql-completion',
                     '{}'::varchar[], '{}'::varchar[], '{}'::jsonb),
                    (CAST(:second_id AS uuid), CAST(:work_item_id AS uuid), 'completion',
                     'Second completion.', 'pytest', 'direct-sql-completion',
                     '{}'::varchar[], '{}'::varchar[], '{}'::jsonb)
                    """
                ),
                {
                    "first_id": str(first_id),
                    "second_id": str(second_id),
                    "work_item_id": work["id"],
                },
            )


@pytest.mark.parametrize("attack", ("position-gap", "combined-count", "aggregate-bytes"))
def test_direct_sql_rejects_malformed_evidence_aggregates_at_forced_check(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    attack: str,
):
    work = _create_work(api, project, work_payload)
    with pytest.raises(DBAPIError, match="sealed"):
        with postgres_engine.begin() as connection:
            checkpoint = _insert_direct_completion_checkpoint(connection, work["id"])
            if attack == "position-gap":
                _insert_direct_observation(
                    connection, project["id"], work["id"], checkpoint, position=1
                )
            elif attack == "combined-count":
                for position in range(11):
                    _insert_direct_observation(
                        connection,
                        project["id"],
                        work["id"],
                        checkpoint,
                        position=position,
                    )
                for position in range(10):
                    _insert_direct_artifact(
                        connection,
                        project["id"],
                        work["id"],
                        checkpoint,
                        position=position,
                        reference=f"{position + 1:07x}",
                    )
            else:
                for position in range(3):
                    _insert_direct_observation(
                        connection,
                        project["id"],
                        work["id"],
                        checkpoint,
                        position=position,
                        summary="\U00010000" * 4000,
                    )
            version = _transition_direct_completion_to_done(connection, work["id"])
            _insert_direct_completion_event(
                connection, project["id"], work["id"], checkpoint, version
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM checkpoints "
                "WHERE work_item_id = CAST(:work_item_id AS uuid) AND kind = 'completion'"
            ),
            {"work_item_id": work["id"]},
        ) == 0


def test_direct_sql_witness_free_recompletion_rolls_back_entire_episode(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    completed = _complete(api, project, work, 1, "initial-direct-attack")
    assert completed.status_code == 200, completed.text

    with pytest.raises(DBAPIError, match="reopen"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE work_items
                    SET status = 'pending', version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = CAST(:work_item_id AS uuid)
                    """
                ),
                {"work_item_id": work["id"]},
            )
            checkpoint = _insert_direct_completion_checkpoint(connection, work["id"])
            _insert_direct_observation(connection, project["id"], work["id"], checkpoint)
            version = _transition_direct_completion_to_done(connection, work["id"])
            _insert_direct_completion_event(
                connection, project["id"], work["id"], checkpoint, version
            )

    with postgres_engine.connect() as connection:
        retained = connection.execute(
            text(
                """
                SELECT status, version, completion_generation,
                       (SELECT count(*) FROM checkpoints AS checkpoint
                        WHERE checkpoint.work_item_id = work.id
                          AND checkpoint.kind = 'completion')
                FROM work_items AS work
                WHERE id = CAST(:work_item_id AS uuid)
                """
            ),
            {"work_item_id": work["id"]},
        ).one()
    assert retained == ("done", 2, 0, 1)


def test_direct_sql_rejects_nonmonotonic_and_out_of_range_completion_event_ids(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    with postgres_engine.begin() as connection:
        unused_lower_id = connection.scalar(text("SELECT nextval('work_events_id_seq')"))
    assert isinstance(unused_lower_id, int)
    completed = _complete(api, project, work, 1, "completion-after-id-gap")
    assert completed.status_code == 200, completed.text
    reopened = _reopen(api, project, work, 2)
    assert reopened.json()["version"] == 3

    for event_id, message in (
        (unused_lower_id, "current episode"),
        (0, "supported range"),
        (-1, "supported range"),
        (9223372036854775807, "supported range"),
    ):
        with pytest.raises(DBAPIError, match=message):
            with postgres_engine.begin() as connection:
                checkpoint = _insert_direct_completion_checkpoint(connection, work["id"])
                version = _transition_direct_completion_to_done(connection, work["id"])
                _insert_direct_completion_event(
                    connection,
                    project["id"],
                    work["id"],
                    checkpoint,
                    version,
                    event_id=event_id,
                )

    retried = _complete(api, project, work, 3, "completion-after-id-attacks")
    assert retried.status_code == 200, retried.text


@pytest.mark.parametrize(
    ("attack", "message"),
    (
        ("missing", "reopen transition requires its exact bound event"),
        ("malformed", "reopen event does not match its guarded transition"),
        ("future", "reopen event does not match its guarded transition"),
        ("caller-binding", "reopen generation is database managed"),
    ),
)
def test_direct_sql_rejects_missing_malformed_future_and_caller_bound_reopen_witnesses(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    attack: str,
    message: str,
):
    work = _create_work(api, project, work_payload)
    completed = _complete(api, project, work, 1, f"reopen-witness-{attack}")
    assert completed.status_code == 200, completed.text

    with pytest.raises(DBAPIError, match=message):
        with postgres_engine.begin() as connection:
            if attack == "future":
                transition = dict(
                    connection.execute(
                        text(
                            """
                            SELECT project_id, status, version, updated_at,
                                   completion_generation
                            FROM work_items
                            WHERE id = CAST(:work_item_id AS uuid)
                            """
                        ),
                        {"work_item_id": work["id"]},
                    ).mappings().one()
                )
                _insert_direct_reopen_event(connection, work["id"], transition)
            else:
                transition = _transition_direct_reopen(connection, work["id"])
                if attack == "missing":
                    connection.execute(
                        text("SET CONSTRAINTS completion_generation_reopen_guard IMMEDIATE")
                    )
                elif attack == "malformed":
                    _insert_direct_reopen_event(
                        connection,
                        work["id"],
                        transition,
                        metadata={
                            "changes": {
                                "status": {"before": "done", "after": "pending"}
                            },
                            "from_status": "done",
                            "to_status": "done",
                            "work_version": transition["version"],
                        },
                    )
                else:
                    _insert_direct_reopen_event(
                        connection,
                        work["id"],
                        transition,
                        reopen_generation=1,
                    )

    with postgres_engine.connect() as connection:
        retained = connection.execute(
            text(
                """
                SELECT status, version, completion_generation,
                       (SELECT count(*) FROM work_events AS event
                        WHERE event.work_item_id = work.id
                          AND event.event_type = 'work_reopened')
                FROM work_items AS work
                WHERE id = CAST(:work_item_id AS uuid)
                """
            ),
            {"work_item_id": work["id"]},
        ).one()
    assert retained == ("done", 2, 0, 0)


def test_direct_sql_rejects_duplicate_reopen_witness_after_committed_transition(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    assert _complete(api, project, work, 1, "duplicate-reopen-witness").status_code == 200
    reopened = _reopen(api, project, work, 2)
    assert reopened.json()["version"] == 3

    with pytest.raises(DBAPIError, match="uq_work_events_reopen_generation"):
        with postgres_engine.begin() as connection:
            transition = dict(
                connection.execute(
                    text(
                        """
                        SELECT project_id, status, version, updated_at,
                               completion_generation
                        FROM work_items
                        WHERE id = CAST(:work_item_id AS uuid)
                        """
                    ),
                    {"work_item_id": work["id"]},
                ).mappings().one()
            )
            _insert_direct_reopen_event(connection, work["id"], transition)


def test_direct_sql_rejects_stale_reopen_witness_reused_for_later_generation(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    assert _complete(api, project, work, 1, "stale-reopen-first").status_code == 200
    _reopen(api, project, work, 2)
    assert _complete(api, project, work, 3, "stale-reopen-second").status_code == 200

    with pytest.raises(DBAPIError, match="reopen event does not match its guarded transition"):
        with postgres_engine.begin() as connection:
            stale = connection.execute(
                text(
                    """
                    SELECT metadata, created_at
                    FROM work_events
                    WHERE work_item_id = CAST(:work_item_id AS uuid)
                      AND event_type = 'work_reopened'
                    ORDER BY reopen_generation
                    LIMIT 1
                    """
                ),
                {"work_item_id": work["id"]},
            ).mappings().one()
            transition = _transition_direct_reopen(connection, work["id"])
            assert transition["completion_generation"] == 2
            _insert_direct_reopen_event(
                connection,
                work["id"],
                transition,
                metadata=stale["metadata"],
                created_at=stale["created_at"],
            )


@pytest.mark.parametrize("version_delta", (0, -1, 2), ids=("unchanged", "decrement", "jump"))
def test_direct_sql_rejects_invalid_pending_to_done_version_change(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    version_delta: int,
):
    work = _create_work(api, project, work_payload)
    with pytest.raises(DBAPIError, match="pending completion|work version"):
        with postgres_engine.begin() as connection:
            _insert_direct_completion_checkpoint(connection, work["id"])
            connection.execute(
                text(
                    """
                    UPDATE work_items
                    SET status = 'done', version = version + :version_delta,
                        updated_at = clock_timestamp()
                    WHERE id = CAST(:work_item_id AS uuid)
                    """
                ),
                {"version_delta": version_delta, "work_item_id": work["id"]},
            )


def test_direct_sql_rejects_pending_to_done_at_integer_version_ceiling(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    with pytest.raises(DBAPIError, match="pending completion requires one live current episode"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE work_items DISABLE TRIGGER completion_generation_guard")
            )
            connection.execute(
                text(
                    "UPDATE work_items SET version = 2147483647 "
                    "WHERE id = CAST(:work_item_id AS uuid)"
                ),
                {"work_item_id": work["id"]},
            )
            connection.execute(
                text("ALTER TABLE work_items ENABLE TRIGGER completion_generation_guard")
            )
            _insert_direct_completion_checkpoint(connection, work["id"])
            connection.execute(
                text(
                    """
                    UPDATE work_items
                    SET status = 'done', version = 2147483647,
                        updated_at = clock_timestamp()
                    WHERE id = CAST(:work_item_id AS uuid)
                    """
                ),
                {"work_item_id": work["id"]},
            )


@pytest.mark.parametrize("prior_status", ("deferred", "wont-do", "promoted"))
def test_direct_sql_rejects_nonpending_to_done_transition(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    prior_status: str,
):
    work = _create_work(api, project, work_payload)
    with pytest.raises(DBAPIError, match="only pending work can become done"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE work_items
                    SET status = :prior_status, version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = CAST(:work_item_id AS uuid)
                    """
                ),
                {"prior_status": prior_status, "work_item_id": work["id"]},
            )
            connection.execute(
                text(
                    """
                    UPDATE work_items
                    SET status = 'done', version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = CAST(:work_item_id AS uuid)
                    """
                ),
                {"work_item_id": work["id"]},
            )


@pytest.mark.parametrize(
    ("initial_state", "version_expression"),
    (
        ("pending", "0"),
        ("pending", "version + 2"),
        ("done", "version - 1"),
    ),
    ids=("pending-reset", "pending-jump", "done-decrement"),
)
def test_direct_sql_rejects_status_preserving_version_reset_decrement_and_jump(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    initial_state: str,
    version_expression: str,
):
    work = _create_work(api, project, work_payload)
    if initial_state == "done":
        completed = _complete(api, project, work, 1, "done-version-decrement")
        assert completed.status_code == 200, completed.text

    with pytest.raises(DBAPIError, match="work version may only remain stable or advance by one"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE work_items SET version = {version_expression} "
                    "WHERE id = CAST(:work_item_id AS uuid)"
                ),
                {"work_item_id": work["id"]},
            )


def test_transition_captured_version_rejects_bump_before_event_but_allows_later_edit(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    with pytest.raises(DBAPIError, match="completion event version does not match transition"):
        with postgres_engine.begin() as connection:
            checkpoint = _insert_direct_completion_checkpoint(connection, work["id"])
            transition_version = _transition_direct_completion_to_done(connection, work["id"])
            assert transition_version == 2
            connection.execute(
                text(
                    "UPDATE work_items SET version = version + 1 "
                    "WHERE id = CAST(:work_item_id AS uuid)"
                ),
                {"work_item_id": work["id"]},
            )
            _insert_direct_completion_event(
                connection,
                project["id"],
                work["id"],
                checkpoint,
                3,
            )
            connection.execute(text("SET CONSTRAINTS completion_state_episode_guard IMMEDIATE"))

    completed = _complete(api, project, work, 1, "legal-before-done-edit")
    assert completed.status_code == 200, completed.text
    edited = api.patch(
        _item_path(project, work),
        json={"expected_version": 2, "title": "Legal edit after sealed completion"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 3


def test_completion_rejects_reset_event_sequence_then_succeeds_after_operator_reseed(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    assert _complete(api, project, work, 1, "sequence-first").status_code == 200
    _reopen(api, project, work, 2)
    with postgres_engine.begin() as connection:
        sequence = connection.scalar(text("SELECT pg_get_serial_sequence('work_events', 'id')"))
        assert isinstance(sequence, str)
        connection.execute(
            text("SELECT pg_catalog.setval(CAST(:sequence AS regclass), 1, false)"),
            {"sequence": sequence},
        )

    failed = _complete(api, project, work, 3, "sequence-reset-rejected")
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "database_unavailable"

    with postgres_engine.begin() as connection:
        maximum = connection.scalar(text("SELECT max(id) FROM work_events"))
        assert isinstance(maximum, int)
        connection.execute(
            text("SELECT pg_catalog.setval(CAST(:sequence AS regclass), :maximum, true)"),
            {"sequence": sequence, "maximum": maximum},
        )
    retried = _complete(api, project, work, 3, "sequence-reset-rejected")
    assert retried.status_code == 200, retried.text


def test_three_direct_completion_generations_commit_across_forced_constraint_modes(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    checkpoint_ids: list[object] = []
    with postgres_engine.begin() as connection:
        first = _insert_direct_completion_checkpoint(connection, work["id"])
        checkpoint_ids.append(first["id"])
        version = _transition_direct_completion_to_done(connection, work["id"])
        _insert_direct_completion_event(connection, project["id"], work["id"], first, version)
        connection.execute(
            text(
                "SET CONSTRAINTS completion_checkpoint_episode_guard, "
                "completion_state_episode_guard IMMEDIATE"
            )
        )
        connection.execute(
            text(
                "SET CONSTRAINTS completion_checkpoint_episode_guard, "
                "completion_state_episode_guard DEFERRED"
            )
        )

        for expected_generation in (1, 2):
            transition = _transition_direct_reopen(connection, work["id"])
            assert transition["completion_generation"] == expected_generation
            _insert_direct_reopen_event(connection, work["id"], transition)
            checkpoint = _insert_direct_completion_checkpoint(connection, work["id"])
            checkpoint_ids.append(checkpoint["id"])
            version = _transition_direct_completion_to_done(connection, work["id"])
            _insert_direct_completion_event(
                connection,
                project["id"],
                work["id"],
                checkpoint,
                version,
            )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    page = api.get(f"{_item_path(project, work)}/completion-evidence")
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["total"] == 3
    assert body["current_completion_checkpoint_id"] == str(checkpoint_ids[-1])
    assert [item["completion_checkpoint"]["id"] for item in body["items"]] == [
        str(value) for value in reversed(checkpoint_ids)
    ]
    with postgres_engine.connect() as connection:
        checkpoint_generations = connection.scalars(
            text(
                """
                SELECT completion_generation
                FROM checkpoints
                WHERE work_item_id = CAST(:work_item_id AS uuid)
                  AND kind = 'completion'
                ORDER BY completion_generation
                """
            ),
            {"work_item_id": work["id"]},
        ).all()
        assert checkpoint_generations == [0, 1, 2]
        reopen_generations = connection.scalars(
            text(
                """
                SELECT reopen_generation
                FROM work_events
                WHERE work_item_id = CAST(:work_item_id AS uuid)
                  AND event_type = 'work_reopened'
                ORDER BY reopen_generation
                """
            ),
            {"work_item_id": work["id"]},
        ).all()
        assert reopen_generations == [1, 2]
        for generation in (0, 1, 2):
            assert connection.scalar(
                text(
                    "SELECT mnemonic_completion_episode_is_sealed("
                    "CAST(:work_item_id AS uuid), :generation)"
                ),
                {"work_item_id": work["id"], "generation": generation},
            ) is True


@pytest.mark.parametrize("phase", ("before-done", "after-done"))
def test_direct_sql_rejects_soft_delete_during_unsealed_completion_intervals(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    phase: str,
):
    work = _create_work(api, project, work_payload)
    with pytest.raises(DBAPIError, match="work cannot be deleted with an unsealed completion"):
        with postgres_engine.begin() as connection:
            checkpoint = _insert_direct_completion_checkpoint(connection, work["id"])
            _insert_direct_observation(connection, project["id"], work["id"], checkpoint)
            if phase == "after-done":
                _transition_direct_completion_to_done(connection, work["id"])
            connection.execute(
                text(
                    "UPDATE work_items SET deleted_at = clock_timestamp() "
                    "WHERE id = CAST(:work_item_id AS uuid)"
                ),
                {"work_item_id": work["id"]},
            )
            connection.execute(
                text(
                    "UPDATE work_items SET deleted_at = NULL "
                    "WHERE id = CAST(:work_item_id AS uuid)"
                ),
                {"work_item_id": work["id"]},
            )

    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM checkpoints "
                "WHERE work_item_id = CAST(:work_item_id AS uuid) AND kind = 'completion'"
            ),
            {"work_item_id": work["id"]},
        ) == 0


@pytest.mark.parametrize(
    ("attack", "message"),
    (
        ("checkpoint", "completion checkpoints require live canonical pending work"),
        ("child", "evidence can be inserted only in an open completion episode"),
        ("transition", "pending completion requires one live current episode"),
        ("event", "lifecycle event requires live canonical work"),
    ),
)
def test_retained_deletion_event_blocks_completion_after_deleted_at_is_cleared(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    attack: str,
    message: str,
):
    work = _create_work(api, project, work_payload)
    deleted = api.post(f"{_item_path(project, work)}/delete", json={"expected_version": 1})
    assert deleted.status_code == 200, deleted.text
    with postgres_engine.begin() as connection:
        connection.execute(
            text("UPDATE work_items SET deleted_at = NULL WHERE id = CAST(:work_id AS uuid)"),
            {"work_id": work["id"]},
        )

    with pytest.raises(DBAPIError, match=message):
        with postgres_engine.begin() as connection:
            initial_checkpoint = dict(
                connection.execute(
                    text(
                        """
                        SELECT checkpoint.id, checkpoint.created_at
                        FROM checkpoints AS checkpoint
                        JOIN work_items AS work
                          ON work.initial_checkpoint_id = checkpoint.id
                        WHERE work.id = CAST(:work_item_id AS uuid)
                        """
                    ),
                    {"work_item_id": work["id"]},
                ).mappings().one()
            )
            if attack == "checkpoint":
                _insert_direct_completion_checkpoint(connection, work["id"])
            elif attack == "child":
                _insert_direct_observation(
                    connection,
                    project["id"],
                    work["id"],
                    initial_checkpoint,
                )
            elif attack == "transition":
                connection.execute(
                    text(
                        """
                        UPDATE work_items
                        SET status = 'done', version = version + 1,
                            updated_at = clock_timestamp()
                        WHERE id = CAST(:work_item_id AS uuid)
                        """
                    ),
                    {"work_item_id": work["id"]},
                )
            else:
                _insert_direct_completion_event(
                    connection,
                    project["id"],
                    work["id"],
                    initial_checkpoint,
                    3,
                )


@pytest.mark.parametrize("phase", ("before-done", "after-done"))
def test_direct_sql_rejects_authoritative_aliasing_during_unsealed_completion(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    phase: str,
):
    source = _create_work(api, project, work_payload, title=f"Alias source {phase}")
    destination = _create_work(api, project, work_payload, title=f"Alias target {phase}")
    expected_message = (
        "pending completion requires one live current episode"
        if phase == "before-done"
        else "lifecycle event requires live canonical work"
    )
    with pytest.raises(DBAPIError, match=expected_message):
        with postgres_engine.begin() as connection:
            checkpoint = _insert_direct_completion_checkpoint(connection, source["id"])
            _insert_direct_observation(
                connection,
                project["id"],
                source["id"],
                checkpoint,
            )
            if phase == "after-done":
                _transition_direct_completion_to_done(connection, source["id"])
            _stage_merge(
                connection,
                project_id=UUID(str(project["id"])),
                source_id=UUID(str(source["id"])),
                destination_id=UUID(str(destination["id"])),
            )
            if phase == "before-done":
                _transition_direct_completion_to_done(connection, source["id"])
            else:
                _insert_direct_completion_event(
                    connection,
                    project["id"],
                    source["id"],
                    checkpoint,
                    3,
                )

    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM work_duplicate_merges "
                "WHERE source_work_item_id = CAST(:source_id AS uuid)"
            ),
            {"source_id": source["id"]},
        ) == 0
        assert connection.scalar(
            text(
                "SELECT count(*) FROM checkpoints "
                "WHERE work_item_id = CAST(:source_id AS uuid) AND kind = 'completion'"
            ),
            {"source_id": source["id"]},
        ) == 0


def test_concurrent_structured_completions_retain_one_exact_atomic_episode(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    authorization = api.headers["Authorization"]
    barrier = Barrier(3)

    def complete(marker: str):
        with TestClient(api.app) as client:
            barrier.wait(timeout=5)
            return client.post(
                f"{_item_path(project, work)}/complete",
                json=_completion_payload(
                    1,
                    marker,
                    evidence=_mixed_evidence(marker),
                    operation_id=str(uuid4()),
                ),
                headers={"Authorization": authorization},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(complete, marker) for marker in ("race-a", "race-b")]
        barrier.wait(timeout=5)
        responses = [future.result(timeout=10) for future in futures]

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    with postgres_engine.connect() as connection:
        counts = connection.execute(
            text(
                """
                SELECT
                    (SELECT count(*) FROM checkpoints
                     WHERE work_item_id = CAST(:work_item_id AS uuid)
                       AND kind = 'completion'),
                    (SELECT count(*) FROM work_events
                     WHERE work_item_id = CAST(:work_item_id AS uuid)
                       AND event_type = 'work_completed'),
                    (SELECT count(*) FROM verification_results
                     WHERE work_item_id = CAST(:work_item_id AS uuid)),
                    (SELECT count(*) FROM artifact_references
                     WHERE work_item_id = CAST(:work_item_id AS uuid)),
                    (SELECT count(*) FROM client_operations
                     WHERE project_id = CAST(:project_id AS uuid)
                       AND operation_kind = 'complete_work')
                """
            ),
            {"project_id": project["id"], "work_item_id": work["id"]},
        ).one()
    assert counts == (1, 1, 2, 2, 1)
    assert winner["completion_evidence"]["verification_results"][0]["name"] in {
        "race-a",
        "race-b",
    }


def _alembic_config(connection) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def _exact_schema_catalog_snapshot(connection) -> dict[str, list[tuple[object, ...]]]:
    """Capture user-schema catalog state without unstable object identifiers."""
    statements = {
        "relations": """
            SELECT relation.relname, relation.relkind, relation.relpersistence,
                   relation.relispartition, relation.relrowsecurity,
                   relation.relforcerowsecurity, relation.relreplident,
                   COALESCE(relation.reloptions, ARRAY[]::text[]),
                   COALESCE(access_method.amname, ''),
                   COALESCE(tablespace.spcname, '')
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            LEFT JOIN pg_catalog.pg_am AS access_method
              ON access_method.oid = relation.relam
            LEFT JOIN pg_catalog.pg_tablespace AS tablespace
              ON tablespace.oid = relation.reltablespace
            WHERE namespace.nspname = pg_catalog.current_schema()
              AND relation.relkind IN ('r', 'p', 'S', 'v', 'm')
            ORDER BY relation.relname
        """,
        "columns": """
            SELECT relation.relname, attribute.attname,
                   pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
                   attribute.attnotnull,
                   COALESCE(pg_catalog.pg_get_expr(
                       default_value.adbin, default_value.adrelid, true
                   ), ''),
                   attribute.attidentity, attribute.attgenerated,
                   COALESCE(collation_namespace.nspname, ''),
                   COALESCE(collation_value.collname, ''),
                   COALESCE(collation_value.collprovider::text, ''),
                   collation_value.collisdeterministic,
                   collation_value.collencoding,
                   COALESCE(collation_value.collcollate, ''),
                   COALESCE(collation_value.collctype, ''),
                   COALESCE(collation_value.colllocale, ''),
                   COALESCE(collation_value.collicurules, ''),
                   COALESCE(collation_value.collversion, '')
            FROM pg_catalog.pg_attribute AS attribute
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = attribute.attrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            LEFT JOIN pg_catalog.pg_attrdef AS default_value
              ON default_value.adrelid = relation.oid
             AND default_value.adnum = attribute.attnum
            LEFT JOIN pg_catalog.pg_collation AS collation_value
              ON collation_value.oid = attribute.attcollation
            LEFT JOIN pg_catalog.pg_namespace AS collation_namespace
              ON collation_namespace.oid = collation_value.collnamespace
            WHERE namespace.nspname = pg_catalog.current_schema()
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
            ORDER BY relation.relname, attribute.attnum
        """,
        "constraints": """
            SELECT relation.relname, constraint_value.conname,
                   constraint_value.contype, constraint_value.condeferrable,
                   constraint_value.condeferred, constraint_value.convalidated,
                   constraint_value.connoinherit,
                   pg_catalog.pg_get_constraintdef(constraint_value.oid, true)
            FROM pg_catalog.pg_constraint AS constraint_value
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = constraint_value.conrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = pg_catalog.current_schema()
            ORDER BY relation.relname, constraint_value.conname
        """,
        "indexes": """
            SELECT table_relation.relname, index_relation.relname,
                   access_method.amname, index_relation.relpersistence,
                   index_relation.relispartition,
                   COALESCE(index_relation.reloptions, ARRAY[]::text[]),
                   COALESCE(tablespace.spcname, ''),
                   index_value.indisunique, index_value.indisprimary,
                   index_value.indisexclusion, index_value.indimmediate,
                   index_value.indisvalid, index_value.indisready,
                   index_value.indislive, index_value.indisclustered,
                   index_value.indisreplident,
                   index_value.indnullsnotdistinct,
                   index_value.indnkeyatts, index_value.indnatts,
                   pg_catalog.pg_get_indexdef(index_relation.oid),
                   COALESCE(pg_catalog.pg_get_expr(
                       index_value.indpred, index_value.indrelid, true
                   ), '')
            FROM pg_catalog.pg_index AS index_value
            JOIN pg_catalog.pg_class AS index_relation
              ON index_relation.oid = index_value.indexrelid
            JOIN pg_catalog.pg_class AS table_relation
              ON table_relation.oid = index_value.indrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = index_relation.relnamespace
            JOIN pg_catalog.pg_am AS access_method
              ON access_method.oid = index_relation.relam
            LEFT JOIN pg_catalog.pg_tablespace AS tablespace
              ON tablespace.oid = index_relation.reltablespace
            WHERE namespace.nspname = pg_catalog.current_schema()
            ORDER BY index_relation.relname
        """,
        "triggers": """
            SELECT relation.relname, trigger_value.tgname,
                   trigger_value.tgenabled, trigger_value.tgtype,
                   trigger_value.tgisinternal, trigger_value.tgdeferrable,
                   trigger_value.tginitdeferred, trigger_value.tgnargs,
                   pg_catalog.encode(trigger_value.tgargs, 'hex'),
                   trigger_value.tgqual IS NOT NULL,
                   trigger_value.tgparentid = 0,
                   procedure_namespace.nspname, procedure.proname,
                   pg_catalog.oidvectortypes(procedure.proargtypes),
                   pg_catalog.pg_get_triggerdef(trigger_value.oid, true)
            FROM pg_catalog.pg_trigger AS trigger_value
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = trigger_value.tgrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_proc AS procedure
              ON procedure.oid = trigger_value.tgfoid
            JOIN pg_catalog.pg_namespace AS procedure_namespace
              ON procedure_namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = pg_catalog.current_schema()
            ORDER BY relation.relname, trigger_value.tgname, trigger_value.tgtype
        """,
        "functions": """
            SELECT procedure.proname,
                   pg_catalog.oidvectortypes(procedure.proargtypes),
                   pg_catalog.format_type(procedure.prorettype, NULL),
                   procedure.prokind, procedure.pronargs,
                   procedure.pronargdefaults, procedure.proretset,
                   procedure.provolatile, procedure.proisstrict,
                   procedure.proparallel, procedure.prosecdef,
                   procedure.proleakproof, procedure.provariadic,
                   procedure.procost, procedure.prorows,
                   procedure.prosupport::pg_catalog.regproc::text,
                   COALESCE(procedure.proargnames, ARRAY[]::text[]),
                   COALESCE(procedure.proargmodes::text, ''),
                   COALESCE(procedure.proallargtypes::text, ''),
                   COALESCE(procedure.proconfig, ARRAY[]::text[]),
                   language.lanname, procedure.prosrc, procedure.probin,
                   COALESCE(procedure.prosqlbody::text, '')
            FROM pg_catalog.pg_proc AS procedure
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            JOIN pg_catalog.pg_language AS language
              ON language.oid = procedure.prolang
            WHERE namespace.nspname = pg_catalog.current_schema()
            ORDER BY procedure.proname,
                     pg_catalog.oidvectortypes(procedure.proargtypes)
        """,
        "sequences": """
            SELECT sequence_relation.relname, sequence_value.seqtypid::regtype::text,
                   sequence_value.seqstart, sequence_value.seqincrement,
                   sequence_value.seqmax, sequence_value.seqmin,
                   sequence_value.seqcache, sequence_value.seqcycle,
                   sequence_view.last_value,
                   COALESCE(owned_relation.relname, ''),
                   COALESCE(owned_attribute.attname, '')
            FROM pg_catalog.pg_sequence AS sequence_value
            JOIN pg_catalog.pg_class AS sequence_relation
              ON sequence_relation.oid = sequence_value.seqrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = sequence_relation.relnamespace
            LEFT JOIN pg_catalog.pg_sequences AS sequence_view
              ON sequence_view.schemaname = namespace.nspname
             AND sequence_view.sequencename = sequence_relation.relname
            LEFT JOIN pg_catalog.pg_depend AS dependency
              ON dependency.classid = 'pg_catalog.pg_class'::regclass
             AND dependency.objid = sequence_relation.oid
             AND dependency.deptype IN ('a', 'i')
            LEFT JOIN pg_catalog.pg_class AS owned_relation
              ON owned_relation.oid = dependency.refobjid
            LEFT JOIN pg_catalog.pg_attribute AS owned_attribute
              ON owned_attribute.attrelid = dependency.refobjid
             AND owned_attribute.attnum = dependency.refobjsubid
            WHERE namespace.nspname = pg_catalog.current_schema()
            ORDER BY sequence_relation.relname
        """,
        "work_event_sequence_state": """
            SELECT last_value, is_called FROM work_events_id_seq
        """,
    }
    return {
        category: [tuple(row) for row in connection.execute(text(statement))]
        for category, statement in statements.items()
    }


def _downgrade_phase11_in_new_transaction(
    engine: Engine,
    *,
    backend_pids: list[int],
    ready: Event,
) -> None:
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '10s'"))
        backend_pids.append(connection.scalar(text("SELECT pg_backend_pid()")))
        ready.set()
        command.downgrade(_alembic_config(connection), "0018_repository_freshness")


def _ordinary_completion_snapshot(connection, work_item_id: str) -> dict[str, object]:
    snapshot = connection.scalar(
        text(
            """
            SELECT pg_catalog.jsonb_build_object(
                'work_item', (
                    SELECT pg_catalog.to_jsonb(work) - 'completion_generation'
                    FROM work_items AS work
                    WHERE work.id = CAST(:work_item_id AS uuid)
                ),
                'checkpoints', COALESCE((
                    SELECT pg_catalog.jsonb_agg(
                        pg_catalog.to_jsonb(checkpoint) - 'completion_generation'
                        ORDER BY checkpoint.id
                    )
                    FROM checkpoints AS checkpoint
                    WHERE checkpoint.work_item_id = CAST(:work_item_id AS uuid)
                ), '[]'::jsonb),
                'events', COALESCE((
                    SELECT pg_catalog.jsonb_agg(
                        pg_catalog.to_jsonb(event) - 'reopen_generation'
                        ORDER BY event.id
                    )
                    FROM work_events AS event
                    WHERE event.work_item_id = CAST(:work_item_id AS uuid)
                ), '[]'::jsonb),
                'receipts', COALESCE((
                    SELECT pg_catalog.jsonb_agg(
                        pg_catalog.to_jsonb(operation) ORDER BY operation.id
                    )
                    FROM client_operations AS operation
                    WHERE operation.response_body #>> '{work_item,id}'
                        = CAST(:work_item_id AS text)
                ), '[]'::jsonb)
            )
            """
        ),
        {"work_item_id": work_item_id},
    )
    assert isinstance(snapshot, dict)
    return snapshot


def test_phase11_upgrade_downgrade_restores_exact_0018_catalog(
    api: TestClient,
    postgres_engine: Engine,
):
    del api
    with postgres_engine.begin() as connection:
        config = _alembic_config(connection)
        command.downgrade(config, "0018_repository_freshness")
        expected = _exact_schema_catalog_snapshot(connection)

        command.upgrade(config, "0019_structured_completion_evidence")
        command.downgrade(config, "0018_repository_freshness")

        assert _exact_schema_catalog_snapshot(connection) == expected
        command.upgrade(config, "head")


def test_phase11_migration_roundtrip_supports_apostrophe_schema(
    postgres_engine: Engine,
):
    schema = "mnemonic_test_quote_'_" + uuid4().hex
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(CreateSchema(schema))
            quoted_schema = connection.dialect.identifier_preparer.quote_identifier(schema)
            connection.execute(
                text("SELECT pg_catalog.set_config('search_path', :path, true)"),
                {"path": quoted_schema},
            )
            config = _alembic_config(connection)
            command.upgrade(config, "0018_repository_freshness")
            command.upgrade(config, "0019_structured_completion_evidence")
            command.downgrade(config, "0018_repository_freshness")
            command.upgrade(config, "0019_structured_completion_evidence")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0019_structured_completion_evidence"
            )
        finally:
            transaction.rollback()


def test_phase11_fresh_catalog_hash_is_independent_of_database_owner(
    postgres_engine: Engine,
):
    """Match a fresh deployment whose application role owns the public catalog."""

    role = "mnemonic_phase11_owner_" + uuid4().hex
    schema = "mnemonic_phase11_owner_schema_" + uuid4().hex
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        quoted_role = connection.dialect.identifier_preparer.quote_identifier(role)
        quoted_schema = connection.dialect.identifier_preparer.quote_identifier(schema)
        try:
            connection.exec_driver_sql(f"CREATE ROLE {quoted_role} NOLOGIN")
            connection.exec_driver_sql(
                f"CREATE SCHEMA {quoted_schema} AUTHORIZATION {quoted_role}"
            )
            connection.exec_driver_sql(f"SET LOCAL ROLE {quoted_role}")
            connection.execute(
                text("SELECT pg_catalog.set_config('search_path', :path, true)"),
                {"path": quoted_schema},
            )

            command.upgrade(_alembic_config(connection), "0019_structured_completion_evidence")

            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0019_structured_completion_evidence"
            )
            assert connection.scalar(
                text(
                    """
                    SELECT pg_catalog.bool_and(owner.rolname = CAST(:role AS text))
                    FROM (
                        SELECT relation.relowner AS owner_oid
                        FROM pg_catalog.pg_class AS relation
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = CAST(:schema AS text)
                          AND relation.relname IN (
                              'verification_results', 'artifact_references'
                          )
                        UNION ALL
                        SELECT procedure.proowner AS owner_oid
                        FROM pg_catalog.pg_proc AS procedure
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        WHERE namespace.nspname = CAST(:schema AS text)
                          AND procedure.proname =
                              'mnemonic_completion_artifact_reference_v1_is_valid'
                    ) AS phase11_object
                    JOIN pg_catalog.pg_roles AS owner
                      ON owner.oid = phase11_object.owner_oid
                    """
                ),
                {"role": role, "schema": schema},
            ) is True
        finally:
            transaction.rollback()


@pytest.mark.parametrize(
    "function_default_privilege",
    (
        "GRANT ALL PRIVILEGES ON FUNCTIONS TO PUBLIC",
        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
    ),
    ids=("permissive", "restrictive"),
)
def test_phase11_upgrade_normalizes_hostile_default_privileges(
    postgres_engine: Engine,
    function_default_privilege: str,
):
    with postgres_engine.begin() as connection:
        config = _alembic_config(connection)
        command.downgrade(config, "0018_repository_freshness")
        schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
        assert isinstance(schema, str)
        prior_functions = list(
            connection.scalars(
                text(
                    """
                    SELECT procedure.proname || '(' ||
                           pg_catalog.oidvectortypes(procedure.proargtypes) || ')'
                    FROM pg_catalog.pg_proc AS procedure
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = CAST(:audit_schema AS text)
                    """
                ),
                {"audit_schema": schema},
            )
        )
        quoted_schema = connection.dialect.identifier_preparer.quote_identifier(schema)
        connection.exec_driver_sql(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
            "GRANT ALL PRIVILEGES ON TABLES TO PUBLIC"
        )
        connection.exec_driver_sql(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
            f"{function_default_privilege}"
        )

        command.upgrade(config, "0019_structured_completion_evidence")

        public_table_grants = connection.scalar(
            text(
                """
                SELECT pg_catalog.count(*)
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(
                        relation.relacl,
                        pg_catalog.acldefault('r', relation.relowner)
                    )
                ) AS privilege
                WHERE namespace.nspname = CAST(:audit_schema AS text)
                  AND relation.relname IN (
                      'verification_results', 'artifact_references'
                  )
                  AND privilege.grantee = 0
                """
            ),
            {"audit_schema": schema},
        )
        public_function_grants = connection.scalar(
            text(
                """
                SELECT pg_catalog.count(*)
                FROM pg_catalog.pg_proc AS procedure
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = procedure.pronamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(
                        procedure.proacl,
                        pg_catalog.acldefault('f', procedure.proowner)
                    )
                ) AS privilege
                WHERE namespace.nspname = CAST(:audit_schema AS text)
                  AND procedure.proname || '(' ||
                      pg_catalog.oidvectortypes(procedure.proargtypes) || ')'
                      <> ALL(:prior_functions)
                  AND privilege.grantee = 0
                """
            ),
            {"audit_schema": schema, "prior_functions": prior_functions},
        )
        assert public_table_grants == 0
        assert public_function_grants == 0


@pytest.mark.parametrize(
    "sequence_tamper",
    (
        "ALTER TABLE work_events ALTER COLUMN id DROP IDENTITY",
        "ALTER SEQUENCE work_events_id_seq CACHE 2",
        "SELECT pg_catalog.setval('work_events_id_seq'::regclass, 9223372036854775807, false)",
    ),
    ids=("detached", "wrong-cache", "exhausted"),
)
def test_phase11_upgrade_rejects_invalid_empty_history_identity_sequence(
    api: TestClient,
    postgres_engine: Engine,
    sequence_tamper: str,
):
    del api
    with pytest.raises(RuntimeError, match="work_events identity sequence"):
        with postgres_engine.begin() as connection:
            config = _alembic_config(connection)
            command.downgrade(config, "0018_repository_freshness")
            connection.execute(text(sequence_tamper))
            command.upgrade(config, "0019_structured_completion_evidence")

    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0019_structured_completion_evidence"
        )


def test_phase11_upgrade_preflight_rejects_oversized_work_version_without_overflow(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload, title="Oversized legacy work version")
    completed = _complete(api, project, work, 1, "oversized-upgrade-version")
    assert completed.status_code == 200, completed.text

    with pytest.raises(RuntimeError, match="live_completion_version_ordering"):
        with postgres_engine.begin() as connection:
            config = _alembic_config(connection)
            command.downgrade(config, "0018_repository_freshness")
            connection.execute(text("DROP TRIGGER events_immutable ON work_events"))
            connection.execute(
                text(
                    "ALTER TABLE work_events "
                    "DROP CONSTRAINT ck_work_events_metadata_v1_valid"
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE work_events
                    SET metadata = pg_catalog.jsonb_set(
                        metadata, '{work_version}',
                        '9999999999999999999999999999999999999999'::jsonb
                    )
                    WHERE work_item_id = CAST(:work_item_id AS uuid)
                      AND event_type = 'work_completed'
                    """
                ),
                {"work_item_id": work["id"]},
            )
            command.upgrade(config, "0019_structured_completion_evidence")


def test_evidence_free_downgrade_reupgrade_preserves_completion_history(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    completed = _complete(
        api,
        project,
        work,
        1,
        "empty-before-downgrade",
        operation_id=str(uuid4()),
    )
    assert completed.status_code == 200, completed.text

    with postgres_engine.begin() as connection:
        expected = _ordinary_completion_snapshot(connection, str(work["id"]))
        for _cycle in range(2):
            command.downgrade(_alembic_config(connection), "0018_repository_freshness")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_repository_freshness"
            )
            assert _ordinary_completion_snapshot(connection, str(work["id"])) == expected
            command.upgrade(_alembic_config(connection), "head")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0019_structured_completion_evidence"
            )
            assert _ordinary_completion_snapshot(connection, str(work["id"])) == expected
        count = connection.scalar(
            text(
                "SELECT count(*) FROM work_events "
                "WHERE work_item_id = :work_item_id AND event_type = 'work_completed'"
            ),
            {"work_item_id": work["id"]},
        )
        assert count == 1


def test_two_completion_cycle_downgrade_reupgrade_maps_exact_legacy_generations(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload, title="Two retained completions")
    first = _complete(api, project, work, 1, "legacy-cycle-first", operation_id=str(uuid4()))
    assert first.status_code == 200, first.text
    _reopen(api, project, work, 2)
    second = _complete(api, project, work, 3, "legacy-cycle-second", operation_id=str(uuid4()))
    assert second.status_code == 200, second.text

    with postgres_engine.begin() as connection:
        config = _alembic_config(connection)
        command.downgrade(config, "0018_repository_freshness")
        command.upgrade(config, "0019_structured_completion_evidence")

        generations = connection.execute(
            text(
                """
                SELECT checkpoint.completion_generation, event.id
                FROM checkpoints AS checkpoint
                JOIN work_events AS event
                  ON event.work_item_id = checkpoint.work_item_id
                 AND event.checkpoint_id = checkpoint.id
                 AND event.event_type = 'work_completed'
                WHERE checkpoint.work_item_id = CAST(:work_item_id AS uuid)
                ORDER BY event.id
                """
            ),
            {"work_item_id": work["id"]},
        ).all()
        assert len(generations) == 2
        assert [generation for generation, _event_id in generations] == [
            -event_id for _generation, event_id in generations
        ]
        assert (
            connection.scalar(
                text(
                    "SELECT completion_generation FROM work_items "
                    "WHERE id = CAST(:work_item_id AS uuid)"
                ),
                {"work_item_id": work["id"]},
            )
            == -generations[-1].id
        )

        command.downgrade(config, "0018_repository_freshness")
        command.upgrade(config, "head")


def test_downgrade_rejects_wrong_current_legacy_completion_pointer(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload, title="Wrong legacy current")
    assert _complete(api, project, work, 1, "older").status_code == 200
    _reopen(api, project, work, 2)
    assert _complete(api, project, work, 3, "newer").status_code == 200

    with postgres_engine.begin() as connection:
        config = _alembic_config(connection)
        command.downgrade(config, "0018_repository_freshness")
        command.upgrade(config, "0019_structured_completion_evidence")

    with pytest.raises(RuntimeError, match="invalid completion generation mapping"):
        with postgres_engine.begin() as connection:
            older_generation = connection.scalar(
                text(
                    """
                    SELECT checkpoint.completion_generation
                    FROM checkpoints AS checkpoint
                    JOIN work_events AS event
                      ON event.checkpoint_id = checkpoint.id
                     AND event.work_item_id = checkpoint.work_item_id
                    WHERE checkpoint.work_item_id = CAST(:work_item_id AS uuid)
                      AND checkpoint.kind = 'completion'
                    ORDER BY event.id
                    LIMIT 1
                    """
                ),
                {"work_item_id": work["id"]},
            )
            connection.execute(text("ALTER TABLE work_items DISABLE TRIGGER USER"))
            connection.execute(
                text(
                    "UPDATE work_items SET completion_generation = :generation "
                    "WHERE id = CAST(:work_item_id AS uuid)"
                ),
                {"generation": older_generation, "work_item_id": work["id"]},
            )
            connection.execute(text("ALTER TABLE work_items ENABLE TRIGGER USER"))
            command.downgrade(_alembic_config(connection), "0018_repository_freshness")


@pytest.mark.parametrize("terminal_status", ("deferred", "wont-do", "promoted"))
def test_downgrade_accepts_each_legal_terminal_reopen_without_completion(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    terminal_status: str,
):
    work = _create_work(api, project, work_payload, title=f"Reopen {terminal_status}")
    if terminal_status == "deferred":
        terminal = api.post(
            f"{_item_path(project, work)}/defer",
            json={"expected_version": 1},
        )
    else:
        terminal = api.patch(
            _item_path(project, work),
            json={"expected_version": 1, "status": terminal_status},
        )
    assert terminal.status_code == 200, terminal.text
    reopened = _reopen(api, project, work, 2)
    assert reopened.json()["status"] == "pending"

    with postgres_engine.begin() as connection:
        config = _alembic_config(connection)
        command.downgrade(config, "0018_repository_freshness")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0018_repository_freshness"
        )
        command.upgrade(config, "head")


def test_downgrade_refuses_after_nonempty_evidence_use(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    completed = _complete(
        api,
        project,
        work,
        1,
        "used-before-downgrade",
        evidence=_mixed_evidence(),
        operation_id=str(uuid4()),
    )
    assert completed.status_code == 200, completed.text

    with pytest.raises(RuntimeError, match="after evidence was used"):
        with postgres_engine.begin() as connection:
            command.downgrade(_alembic_config(connection), "0018_repository_freshness")
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0019_structured_completion_evidence"
        )


def test_downgrade_refuses_on_evidence_bearing_receipt_without_child_rows(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    operation_id = str(uuid4())
    completed = _complete(
        api,
        project,
        work,
        1,
        "receipt-only-downgrade",
        operation_id=operation_id,
    )
    assert completed.status_code == 200, completed.text

    with pytest.raises(RuntimeError, match="after evidence was used"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE client_operations DISABLE TRIGGER client_operation_mutation_guard"
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = response_body ||
                        '{"completion_evidence": {}}'::jsonb
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"operation_id": operation_id},
            )
            connection.execute(
                text("ALTER TABLE client_operations ENABLE TRIGGER client_operation_mutation_guard")
            )
            command.downgrade(_alembic_config(connection), "0018_repository_freshness")

    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0019_structured_completion_evidence"
        )


@pytest.mark.parametrize("isolation_level", ("REPEATABLE READ", "SERIALIZABLE"))
def test_downgrade_rejects_non_read_committed_isolation_before_ddl(
    api: TestClient,
    postgres_engine: Engine,
    isolation_level: str,
):
    del api
    with postgres_engine.connect().execution_options(isolation_level=isolation_level) as connection:
        with connection.begin():
            with pytest.raises(RuntimeError, match="requires READ COMMITTED isolation"):
                command.downgrade(_alembic_config(connection), "0018_repository_freshness")

    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0019_structured_completion_evidence"
        )
        assert connection.scalar(text("SELECT to_regclass('verification_results')")) is not None


def test_downgrade_sees_evidence_writer_committed_while_waiting_for_first_lock(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload, title="Writer-first downgrade race")
    operation_id = str(uuid4())
    writer_paused = Event()
    allow_writer_commit = Event()
    downgrade_ready = Event()
    writer_pids: list[int] = []
    downgrade_pids: list[int] = []

    def pause_completed_receipt(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        del cursor, parameters, context, executemany
        normalized = " ".join(statement.lower().split())
        if (
            "update client_operations set" not in normalized
            or "response_body" not in normalized
            or writer_pids
        ):
            return
        driver_connection = connection.connection.driver_connection
        writer_pids.append(int(driver_connection.info.backend_pid))
        writer_paused.set()
        assert allow_writer_commit.wait(timeout=8), (
            "test did not release the Phase 11 writer transaction"
        )

    event.listen(postgres_engine, "after_cursor_execute", pause_completed_receipt)
    try:
        with postgres_engine.connect() as connection:
            relation_oid = connection.scalar(text("SELECT 'client_operations'::regclass::oid"))
        with ThreadPoolExecutor(max_workers=2) as executor:
            writer_future = executor.submit(
                _complete,
                api,
                project,
                work,
                1,
                "writer-first-race",
                evidence=_mixed_evidence("committed-racing-evidence"),
                operation_id=operation_id,
            )
            assert writer_paused.wait(timeout=3)
            downgrade_future = executor.submit(
                _downgrade_phase11_in_new_transaction,
                postgres_engine,
                backend_pids=downgrade_pids,
                ready=downgrade_ready,
            )
            assert downgrade_ready.wait(timeout=3)
            try:
                _wait_for_relation_lock(
                    postgres_engine,
                    waiting_pid=downgrade_pids[0],
                    blocking_pid=writer_pids[0],
                    relation_oid=relation_oid,
                    mode="AccessExclusiveLock",
                )
            finally:
                allow_writer_commit.set()

            completed = writer_future.result(timeout=8)
            assert completed.status_code == 200, completed.text
            with pytest.raises(RuntimeError, match="after evidence was used"):
                downgrade_future.result(timeout=8)
    finally:
        allow_writer_commit.set()
        event.remove(postgres_engine, "after_cursor_execute", pause_completed_receipt)

    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0019_structured_completion_evidence"
        )
        assert connection.scalar(text("SELECT count(*) FROM verification_results")) == 2
        assert (
            connection.scalar(
                text(
                    """
                SELECT response_body ? 'completion_evidence'
                FROM client_operations
                WHERE client_operation_id = CAST(:operation_id AS uuid)
                """
                ),
                {"operation_id": operation_id},
            )
            is True
        )


def test_downgrade_waits_for_harmless_receipt_first_holder_then_succeeds(
    api: TestClient,
    postgres_engine: Engine,
):
    del api
    downgrade_ready = Event()
    downgrade_pids: list[int] = []
    holder = postgres_engine.connect()
    holder_transaction = holder.begin()
    try:
        holder.execute(text("LOCK TABLE client_operations IN ROW EXCLUSIVE MODE"))
        holder.execute(text("LOCK TABLE work_items IN ROW SHARE MODE"))
        holder_pid = holder.scalar(text("SELECT pg_backend_pid()"))
        relation_oid = holder.scalar(text("SELECT 'client_operations'::regclass::oid"))
        with ThreadPoolExecutor(max_workers=1) as executor:
            downgrade_future = executor.submit(
                _downgrade_phase11_in_new_transaction,
                postgres_engine,
                backend_pids=downgrade_pids,
                ready=downgrade_ready,
            )
            assert downgrade_ready.wait(timeout=3)
            _wait_for_relation_lock(
                postgres_engine,
                waiting_pid=downgrade_pids[0],
                blocking_pid=holder_pid,
                relation_oid=relation_oid,
                mode="AccessExclusiveLock",
            )
            holder_transaction.commit()
            downgrade_future.result(timeout=8)
    finally:
        if holder_transaction.is_active:
            holder_transaction.rollback()
        holder.close()

    with postgres_engine.begin() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0018_repository_freshness"
        )
        command.upgrade(_alembic_config(connection), "head")


@pytest.mark.parametrize("target_relation", ("verification_results", "work_events"))
def test_downgrade_inverse_lock_order_times_out_without_ddl_then_retries(
    api: TestClient,
    postgres_engine: Engine,
    target_relation: str,
):
    del api
    downgrade_ready = Event()
    downgrade_pids: list[int] = []
    holder = postgres_engine.connect()
    holder_transaction = holder.begin()
    try:
        holder.execute(text(f"LOCK TABLE {target_relation} IN ROW EXCLUSIVE MODE"))
        holder.execute(text("LOCK TABLE work_items IN ROW SHARE MODE"))
        holder_pid = holder.scalar(text("SELECT pg_backend_pid()"))
        work_items_oid = holder.scalar(text("SELECT 'work_items'::regclass::oid"))

        with ThreadPoolExecutor(max_workers=1) as executor:
            downgrade_future = executor.submit(
                _downgrade_phase11_in_new_transaction,
                postgres_engine,
                backend_pids=downgrade_pids,
                ready=downgrade_ready,
            )
            assert downgrade_ready.wait(timeout=3)
            _wait_for_relation_lock(
                postgres_engine,
                waiting_pid=downgrade_pids[0],
                blocking_pid=holder_pid,
                relation_oid=work_items_oid,
                mode="AccessExclusiveLock",
            )
            with pytest.raises(DBAPIError) as timed_out:
                downgrade_future.result(timeout=8)
            assert getattr(timed_out.value.orig, "sqlstate", None) == "55P03"

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0019_structured_completion_evidence"
            )
            assert connection.scalar(text("SELECT to_regclass('verification_results')")) is not None
            assert (
                connection.scalar(
                    text("SELECT to_regprocedure('mnemonic_guard_completion_generation()')")
                )
                is not None
            )
    finally:
        if holder_transaction.is_active:
            holder_transaction.rollback()
        holder.close()

    with postgres_engine.begin() as connection:
        command.downgrade(_alembic_config(connection), "0018_repository_freshness")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0018_repository_freshness"
        )
        command.upgrade(_alembic_config(connection), "head")


@pytest.mark.parametrize(
    "tamper_statement",
    (
        """
            CREATE OR REPLACE FUNCTION mnemonic_guard_completion_generation()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog
            AS $function$
            BEGIN
                RETURN NEW;
            END
            $function$
        """,
        "ALTER FUNCTION mnemonic_guard_completion_generation() COST 7",
        "GRANT EXECUTE ON FUNCTION mnemonic_guard_completion_generation() TO PUBLIC",
        "GRANT SELECT ON verification_results TO PUBLIC",
        "GRANT SELECT (summary) ON verification_results TO PUBLIC",
    ),
    ids=("body", "attribute", "function-acl", "relation-acl", "column-acl"),
)
def test_downgrade_rejects_exact_phase11_catalog_tamper_before_ddl(
    api: TestClient,
    postgres_engine: Engine,
    tamper_statement: str,
):
    del api
    with pytest.raises(RuntimeError, match="indeterminate Phase 11 catalog"):
        with postgres_engine.begin() as connection:
            connection.execute(text(tamper_statement))
            command.downgrade(_alembic_config(connection), "0018_repository_freshness")

    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0019_structured_completion_evidence"
        )
        assert connection.scalar(text("SELECT to_regclass('verification_results')")) is not None


@pytest.mark.parametrize(
    "tamper_statement",
    (
        "ALTER FUNCTION mnemonic_affected_paths_valid_v1(varchar[]) COST 101",
        "ALTER INDEX ix_work_items_duplicate_title_key_v1 SET (fillfactor = 75)",
        "GRANT SELECT ON work_items TO PUBLIC",
        "GRANT SELECT (title) ON work_items TO PUBLIC",
        (
            "ALTER TABLE projects ADD CONSTRAINT "
            "completion_state_episode_guard CHECK (true)"
        ),
    ),
    ids=(
        "function-attribute",
        "index-attribute",
        "relation-acl",
        "column-acl",
        "phase11-constraint-name-collision",
    ),
)
def test_downgrade_rejects_phase10_survivor_catalog_tamper_before_ddl(
    api: TestClient,
    postgres_engine: Engine,
    tamper_statement: str,
):
    del api
    with pytest.raises(RuntimeError, match="Phase 10 survivor catalog"):
        with postgres_engine.begin() as connection:
            connection.execute(text(tamper_statement))
            command.downgrade(_alembic_config(connection), "0018_repository_freshness")

    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0019_structured_completion_evidence"
        )
        assert connection.scalar(text("SELECT to_regclass('verification_results')")) is not None


def test_downgrade_independently_rejects_corrupt_generation_history(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    with pytest.raises(RuntimeError, match="invalid completion generation mapping"):
        with postgres_engine.begin() as connection:
            connection.execute(text("ALTER TABLE work_items DISABLE TRIGGER USER"))
            connection.execute(
                text(
                    "UPDATE work_items SET completion_generation = 1 "
                    "WHERE id = CAST(:work_item_id AS uuid)"
                ),
                {"work_item_id": work["id"]},
            )
            connection.execute(text("ALTER TABLE work_items ENABLE TRIGGER USER"))
            command.downgrade(_alembic_config(connection), "0018_repository_freshness")

    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0019_structured_completion_evidence"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT completion_generation FROM work_items "
                    "WHERE id = CAST(:work_item_id AS uuid)"
                ),
                {"work_item_id": work["id"]},
            )
            == 0
        )


def test_downgrade_rejects_current_completion_on_non_done_work(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    completed = _complete(api, project, work, 1, "non-done-current-completion")
    assert completed.status_code == 200, completed.text

    with pytest.raises(RuntimeError, match="invalid completion generation mapping"):
        with postgres_engine.begin() as connection:
            connection.execute(text("ALTER TABLE work_items DISABLE TRIGGER USER"))
            connection.execute(
                text(
                    "UPDATE work_items SET status = 'pending' "
                    "WHERE id = CAST(:work_item_id AS uuid)"
                ),
                {"work_item_id": work["id"]},
            )
            connection.execute(text("ALTER TABLE work_items ENABLE TRIGGER USER"))
            command.downgrade(_alembic_config(connection), "0018_repository_freshness")

    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT status FROM work_items WHERE id = CAST(:work_item_id AS uuid)"),
                {"work_item_id": work["id"]},
            )
            == "done"
        )


def test_downgrade_rejects_identity_sequence_behind_retained_history(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
):
    work = _create_work(api, project, work_payload)
    completed = _complete(api, project, work, 1, "sequence-preflight")
    assert completed.status_code == 200, completed.text

    with pytest.raises(RuntimeError, match="invalid work_events identity sequence"):
        with postgres_engine.begin() as connection:
            maximum = connection.scalar(text("SELECT max(id) FROM work_events"))
            assert isinstance(maximum, int) and maximum > 1
            sequence = connection.scalar(text("SELECT pg_get_serial_sequence('work_events', 'id')"))
            assert isinstance(sequence, str)
            connection.execute(
                text("SELECT pg_catalog.setval(CAST(:sequence AS regclass), 1, false)"),
                {"sequence": sequence},
            )
            command.downgrade(_alembic_config(connection), "0018_repository_freshness")

    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0019_structured_completion_evidence"
        )


@pytest.mark.parametrize("receipt_body", (None, "null", "[]", "1", "{}"))
def test_downgrade_rejects_sql_null_and_malformed_completion_receipts(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    receipt_body: str | None,
):
    work = _create_work(api, project, work_payload)
    operation_id = str(uuid4())
    completed = _complete(
        api,
        project,
        work,
        1,
        "malformed-receipt",
        operation_id=operation_id,
    )
    assert completed.status_code == 200, completed.text

    with pytest.raises(RuntimeError, match="receipt state is indeterminate"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE client_operations DISABLE TRIGGER client_operation_mutation_guard"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE client_operations "
                    "DROP CONSTRAINT ck_client_operations_state_fields_valid"
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = CAST(:receipt_body AS jsonb)
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {"receipt_body": receipt_body, "operation_id": operation_id},
            )
            _restore_client_operation_state_constraint_for_corruption(connection)
            connection.execute(
                text("ALTER TABLE client_operations ENABLE TRIGGER client_operation_mutation_guard")
            )
            command.downgrade(_alembic_config(connection), "0018_repository_freshness")

    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0019_structured_completion_evidence"
        )
        assert connection.scalar(text("SELECT to_regclass('verification_results')")) is not None


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (("work_item", "title"), "null"),
        (("work_item", "version"), '"2"'),
        (("work_item", "unexpected"), "true"),
        (("checkpoint", "prompt"), '"tampered prompt"'),
    ),
    ids=("null-title", "string-version", "extra-key", "checkpoint-drift"),
)
def test_downgrade_rejects_malformed_nested_completion_receipt_fields(
    api: TestClient,
    project: dict[str, object],
    work_payload: dict[str, object],
    postgres_engine: Engine,
    path: tuple[str, ...],
    replacement: str,
):
    work = _create_work(api, project, work_payload)
    operation_id = str(uuid4())
    completed = _complete(
        api,
        project,
        work,
        1,
        "malformed-nested-receipt",
        operation_id=operation_id,
    )
    assert completed.status_code == 200, completed.text

    with pytest.raises(RuntimeError, match="receipt state is indeterminate"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE client_operations DISABLE TRIGGER client_operation_mutation_guard"
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE client_operations
                    SET response_body = pg_catalog.jsonb_set(
                        response_body, CAST(:path AS text[]), CAST(:replacement AS jsonb)
                    )
                    WHERE client_operation_id = CAST(:operation_id AS uuid)
                    """
                ),
                {
                    "path": list(path),
                    "replacement": replacement,
                    "operation_id": operation_id,
                },
            )
            connection.execute(
                text("ALTER TABLE client_operations ENABLE TRIGGER client_operation_mutation_guard")
            )
            command.downgrade(_alembic_config(connection), "0018_repository_freshness")

    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0019_structured_completion_evidence"
        )
