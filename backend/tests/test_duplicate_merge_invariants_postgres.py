"""Direct-SQL tests for the immutable duplicate-merge fact system."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.postgres


def _create_project_with_work(
    connection: Connection,
    *,
    work_count: int,
) -> tuple[UUID, list[tuple[UUID, UUID]]]:
    project_id = uuid4()
    connection.execute(
        text("INSERT INTO projects (id, name, slug) VALUES (:id, :name, :slug)"),
        {
            "id": project_id,
            "name": "Duplicate merge invariant project",
            "slug": f"duplicate-merge-{project_id.hex}",
        },
    )
    work: list[tuple[UUID, UUID]] = []
    for position in range(work_count):
        work_item_id = uuid4()
        checkpoint_id = uuid4()
        created_at = connection.scalar(text("SELECT clock_timestamp()"))
        assert isinstance(created_at, datetime)
        connection.execute(
            text(
                """
                INSERT INTO work_items (
                    id, project_id, title, summary, status, priority,
                    initial_checkpoint_id, version, created_at, updated_at
                ) VALUES (
                    :id, :project_id, :title, :summary, 'pending', 0,
                    :checkpoint_id, 1, :created_at, :created_at
                )
                """
            ),
            {
                "id": work_item_id,
                "project_id": project_id,
                "title": f"Work {position}",
                "summary": f"Retained summary {position}",
                "checkpoint_id": checkpoint_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO checkpoints (
                    id, work_item_id, kind, prompt, source_client,
                    source_session_id, created_at
                ) VALUES (
                    :id, :work_item_id, 'context', :prompt, 'test-client',
                    'test-session', :created_at
                )
                """
            ),
            {
                "id": checkpoint_id,
                "work_item_id": work_item_id,
                "prompt": f"Initial context {position}",
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO work_events (
                    project_id, work_item_id, event_type, actor_kind,
                    actor_client, actor_session_id, checkpoint_id, metadata,
                    origin, created_at
                ) VALUES (
                    :project_id, :work_item_id, 'work_created', 'client',
                    'test-client', 'test-session', :checkpoint_id,
                    CAST(:metadata AS jsonb), 'live', :created_at
                )
                """
            ),
            {
                "project_id": project_id,
                "work_item_id": work_item_id,
                "checkpoint_id": checkpoint_id,
                "created_at": created_at,
                "metadata": json.dumps(
                    {
                        "initial": {
                            "title": f"Work {position}",
                            "summary": f"Retained summary {position}",
                            "status": "pending",
                            "priority": 0,
                            "version": 1,
                        }
                    }
                ),
            },
        )
        work.append((work_item_id, checkpoint_id))
    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    return project_id, work


def _revision(connection: Connection, work_item_id: UUID) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT work.version,
                   (
                       SELECT checkpoint.id
                       FROM checkpoints AS checkpoint
                       WHERE checkpoint.work_item_id = work.id
                         AND checkpoint.kind = 'context'
                       ORDER BY checkpoint.created_at DESC, checkpoint.id DESC
                       LIMIT 1
                   ) AS checkpoint_id,
                   (
                       SELECT count(*)
                       FROM work_events AS event
                       WHERE event.work_item_id = work.id
                   ) AS event_count
            FROM work_items AS work
            WHERE work.id = :work_item_id
            """
        ),
        {"work_item_id": work_item_id},
    ).mappings().one()
    return dict(row)


def _insert_relationship_event(
    connection: Connection,
    *,
    project_id: UUID,
    endpoint_id: UUID,
    source_id: UUID,
    destination_id: UUID,
    relationship_id: UUID,
    merge_id: UUID,
    merge_time: datetime,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO work_events (
                project_id, work_item_id, event_type, actor_kind,
                actor_client, actor_session_id, relationship_id,
                relationship_source_work_item_id,
                relationship_target_work_item_id, metadata, origin, created_at,
                created_for_duplicate_merge_id
            ) VALUES (
                :project_id, :endpoint_id, 'relationship_added', 'client',
                'test-client', 'test-session', :relationship_id,
                :source_id, :destination_id, CAST(:metadata AS jsonb),
                'live', :merge_time, :merge_id
            )
            """
        ),
        {
            "project_id": project_id,
            "endpoint_id": endpoint_id,
            "relationship_id": relationship_id,
            "source_id": source_id,
            "destination_id": destination_id,
            "metadata": json.dumps({"relationship_type": "duplicate-of"}),
            "merge_time": merge_time,
            "merge_id": merge_id,
        },
    )


def _insert_merge_event(
    connection: Connection,
    *,
    project_id: UUID,
    endpoint_id: UUID,
    source_id: UUID,
    destination_id: UUID,
    source_version: int,
    destination_version: int,
    relationship_id: UUID,
    merge_id: UUID,
    merge_time: datetime,
    role: str,
) -> None:
    del relationship_id
    metadata = {
        "merge_id": str(merge_id),
        "source_work_item_id": str(source_id),
        "destination_work_item_id": str(destination_id),
        "role": role,
        "source_work_version": source_version,
        "destination_work_version": destination_version,
    }
    connection.execute(
        text(
            """
            INSERT INTO work_events (
                project_id, work_item_id, event_type, actor_kind,
                actor_client, actor_session_id, body, metadata, origin,
                created_at, work_duplicate_merge_id
            ) VALUES (
                :project_id, :endpoint_id, 'work_merged', 'client',
                'test-client', 'test-session', :rationale, CAST(:metadata AS jsonb),
                'live', :merge_time, :merge_id
            )
            """
        ),
        {
            "project_id": project_id,
            "endpoint_id": endpoint_id,
            "rationale": "Reviewed duplicate identity.",
            "metadata": json.dumps(metadata),
            "merge_time": merge_time,
            "merge_id": merge_id,
        },
    )


def _stage_merge(
    connection: Connection,
    *,
    project_id: UUID,
    source_id: UUID,
    destination_id: UUID,
    omit_merge_roles: Sequence[str] = (),
    merge_overrides: dict[str, Any] | None = None,
) -> UUID:
    source_revision = _revision(connection, source_id)
    destination_revision = _revision(connection, destination_id)
    merge_id = uuid4()
    relationship_id = uuid4()
    merge_time = connection.scalar(text("SELECT clock_timestamp()"))
    assert isinstance(merge_time, datetime)
    source_result_version = int(source_revision["version"]) + 1
    destination_result_version = int(destination_revision["version"]) + 1
    merge_values: dict[str, Any] = {
        "id": merge_id,
        "project_id": project_id,
        "source_id": source_id,
        "destination_id": destination_id,
        "relationship_id": relationship_id,
        "source_version": source_revision["version"],
        "source_checkpoint_id": source_revision["checkpoint_id"],
        "source_event_count": source_revision["event_count"],
        "destination_version": destination_revision["version"],
        "destination_checkpoint_id": destination_revision["checkpoint_id"],
        "destination_event_count": destination_revision["event_count"],
        "source_result_version": source_result_version,
        "destination_result_version": destination_result_version,
        "rationale": "Reviewed duplicate identity.",
        "merge_time": merge_time,
    }
    if merge_overrides is not None:
        merge_values.update(merge_overrides)
    connection.execute(
        text(
            """
            INSERT INTO work_relationships (
                id, project_id, relationship_type, source_work_item_id,
                target_work_item_id, created_by_client, created_by_session_id,
                created_for_duplicate_merge_id, created_at
            ) VALUES (
                :id, :project_id, 'duplicate-of', :source_id, :destination_id,
                'test-client', 'test-session', :merge_id, :merge_time
            )
            """
        ),
        {
            "id": relationship_id,
            "project_id": project_id,
            "source_id": source_id,
            "destination_id": destination_id,
            "merge_id": merge_id,
            "merge_time": merge_time,
        },
    )
    connection.execute(
        text(
            """
            UPDATE work_items
            SET version = version + 1, updated_at = :merge_time
            WHERE id IN (:source_id, :destination_id)
            """
        ),
        {
            "source_id": source_id,
            "destination_id": destination_id,
            "merge_time": merge_time,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO work_duplicate_merges (
                id, project_id, source_work_item_id, destination_work_item_id,
                duplicate_relationship_id, duplicate_relationship_type,
                reviewed_source_work_version,
                reviewed_source_context_checkpoint_id,
                reviewed_source_work_event_count,
                reviewed_destination_work_version,
                reviewed_destination_context_checkpoint_id,
                reviewed_destination_work_event_count,
                resulting_source_work_version,
                resulting_destination_work_version,
                rationale, merged_by_client, merged_by_session_id, created_at
            ) VALUES (
                :id, :project_id, :source_id, :destination_id,
                :relationship_id, 'duplicate-of', :source_version,
                :source_checkpoint_id, :source_event_count, :destination_version,
                :destination_checkpoint_id, :destination_event_count,
                :source_result_version, :destination_result_version,
                :rationale, 'test-client', 'test-session', :merge_time
            )
            """
        ),
        merge_values,
    )
    for endpoint_id in (source_id, destination_id):
        _insert_relationship_event(
            connection,
            project_id=project_id,
            endpoint_id=endpoint_id,
            source_id=source_id,
            destination_id=destination_id,
            relationship_id=relationship_id,
            merge_id=merge_id,
            merge_time=merge_time,
        )
    for endpoint_id, role in ((source_id, "source"), (destination_id, "destination")):
        if role not in omit_merge_roles:
            _insert_merge_event(
                connection,
                project_id=project_id,
                endpoint_id=endpoint_id,
                source_id=source_id,
                destination_id=destination_id,
                source_version=source_result_version,
                destination_version=destination_result_version,
                relationship_id=relationship_id,
                merge_id=merge_id,
                merge_time=merge_time,
                role=role,
            )
    return merge_id


def test_complete_merge_fact_and_aliases_are_immutable(postgres_engine: Engine) -> None:
    with postgres_engine.begin() as connection:
        project_id, work = _create_project_with_work(connection, work_count=2)
        source_id, _ = work[0]
        destination_id, _ = work[1]
        merge_id = _stage_merge(
            connection,
            project_id=project_id,
            source_id=source_id,
            destination_id=destination_id,
        )
        connection.execute(
            text(
                "SET CONSTRAINTS duplicate_relationship_completeness_guard, "
                "duplicate_merge_completeness_guard IMMEDIATE"
            )
        )

    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM work_duplicate_merges WHERE id = :id"),
            {"id": merge_id},
        ) == 1
        relationship_id = connection.scalar(
            text(
                "SELECT duplicate_relationship_id FROM work_duplicate_merges "
                "WHERE id = :id"
            ),
            {"id": merge_id},
        )

    guarded_statements = (
        ("UPDATE work_items SET priority = 1 WHERE id = :id", {"id": source_id}),
        (
            "UPDATE work_items SET deleted_at = clock_timestamp() WHERE id = :id",
            {"id": destination_id},
        ),
        (
            "INSERT INTO checkpoints (id, work_item_id, kind, prompt, source_client, "
            "source_session_id) VALUES (:new_id, :id, 'context', 'later', "
            "'test-client', 'test-session')",
            {"new_id": uuid4(), "id": source_id},
        ),
        (
            "UPDATE work_duplicate_merges SET rationale = rationale WHERE id = :id",
            {"id": merge_id},
        ),
        (
            "DELETE FROM work_duplicate_merges WHERE id = :id",
            {"id": merge_id},
        ),
        (
            "DELETE FROM work_relationships WHERE id = :id",
            {"id": relationship_id},
        ),
        (
            "DELETE FROM work_items WHERE id = :id",
            {"id": source_id},
        ),
        (
            "DELETE FROM work_items WHERE id = :id",
            {"id": destination_id},
        ),
        (
            "INSERT INTO work_relationships (id, project_id, relationship_type, "
            "source_work_item_id, target_work_item_id, created_by_client, "
            "created_by_session_id) VALUES (:new_id, :project_id, 'blocks', :id, "
            ":destination_id, 'test-client', 'test-session')",
            {
                "new_id": uuid4(),
                "project_id": project_id,
                "id": source_id,
                "destination_id": destination_id,
            },
        ),
        (
            "INSERT INTO work_leases (work_item_id, holder_client, holder_session_id, "
            "claim_request_id, lease_token, acquired_at, renewed_at, expires_at) "
            "VALUES (:id, 'test-client', 'test-session', 'claim', 'token', "
            "clock_timestamp(), clock_timestamp(), clock_timestamp() + interval '1 hour')",
            {"id": source_id},
        ),
        (
            "INSERT INTO work_gates (project_id, work_item_id, question, "
            "requested_by_client, requested_by_session_id, requested_work_version, "
            "requested_context_checkpoint_id, requested_relationship_event_count) "
            "VALUES (:project_id, :id, 'Question?', 'test-client', 'test-session', 2, "
            ":checkpoint_id, 2)",
            {
                "project_id": project_id,
                "id": source_id,
                "checkpoint_id": work[0][1],
            },
        ),
        (
            "INSERT INTO work_events (project_id, work_item_id, event_type, actor_kind, "
            "actor_client, actor_session_id, body, metadata, origin) VALUES "
            "(:project_id, :id, 'progress', 'client', 'test-client', 'test-session', "
            "'Unexpected progress.', '{}'::jsonb, 'live')",
            {"project_id": project_id, "id": source_id},
        ),
    )
    for statement, parameters in guarded_statements:
        with postgres_engine.connect() as connection, pytest.raises(DBAPIError):
            connection.execute(text(statement), parameters)


def test_fresh_duplicate_mark_and_incomplete_merge_fail_deferred_guards(
    postgres_engine: Engine,
) -> None:
    with postgres_engine.begin() as connection:
        project_id, work = _create_project_with_work(connection, work_count=4)

    with postgres_engine.connect() as connection, connection.begin(), pytest.raises(DBAPIError):
        connection.execute(
            text(
                """
                INSERT INTO work_relationships (
                    id, project_id, relationship_type, source_work_item_id,
                    target_work_item_id, created_by_client, created_by_session_id
                ) VALUES (
                    :id, :project_id, 'duplicate-of', :source_id, :destination_id,
                    'test-client', 'test-session'
                )
                """
            ),
            {
                "id": uuid4(),
                "project_id": project_id,
                "source_id": work[0][0],
                "destination_id": work[1][0],
            },
        )
        connection.execute(
            text("SET CONSTRAINTS duplicate_relationship_completeness_guard IMMEDIATE")
        )

    with postgres_engine.connect() as connection, connection.begin(), pytest.raises(DBAPIError):
        _stage_merge(
            connection,
            project_id=project_id,
            source_id=work[2][0],
            destination_id=work[3][0],
            omit_merge_roles=("destination",),
        )
        connection.execute(
            text("SET CONSTRAINTS duplicate_merge_completeness_guard IMMEDIATE")
        )


def test_depth_boundary_allows_edge_50_and_rejects_edge_51(postgres_engine: Engine) -> None:
    with postgres_engine.begin() as connection:
        project_id, work = _create_project_with_work(connection, work_count=52)

    for position in range(50):
        with postgres_engine.begin() as connection:
            _stage_merge(
                connection,
                project_id=project_id,
                source_id=work[position][0],
                destination_id=work[position + 1][0],
            )
            connection.execute(
                text(
                    "SET CONSTRAINTS duplicate_relationship_completeness_guard, "
                    "duplicate_merge_completeness_guard IMMEDIATE"
                )
            )

    with postgres_engine.connect() as connection, connection.begin(), pytest.raises(DBAPIError):
        _stage_merge(
            connection,
            project_id=project_id,
            source_id=work[50][0],
            destination_id=work[51][0],
        )

    with postgres_engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM work_duplicate_merges WHERE project_id = :project_id"),
            {"project_id": project_id},
        ) == 50


def test_branching_component_and_incoming_source_tip_remain_valid(
    postgres_engine: Engine,
) -> None:
    with postgres_engine.begin() as connection:
        project_id, work = _create_project_with_work(connection, work_count=5)

    for source_position, destination_position in ((0, 2), (1, 2), (2, 3), (4, 3)):
        with postgres_engine.begin() as connection:
            _stage_merge(
                connection,
                project_id=project_id,
                source_id=work[source_position][0],
                destination_id=work[destination_position][0],
            )
            connection.execute(
                text(
                    "SET CONSTRAINTS duplicate_relationship_completeness_guard, "
                    "duplicate_merge_completeness_guard IMMEDIATE"
                )
            )

    with postgres_engine.connect() as connection:
        state = connection.execute(
            text(
                "SELECT reverse_depth, forward_depth, is_valid "
                "FROM mnemonic_duplicate_component_state(:project_id, :work_item_id)"
            ),
            {"project_id": project_id, "work_item_id": work[3][0]},
        ).one()
    assert state == (2, 0, True)


@pytest.mark.parametrize(
    "merge_overrides",
    (
        {"source_event_count": 2},
        {"source_checkpoint_id": uuid4()},
        {"source_version": 2, "source_result_version": 3},
        {"destination_result_version": 3},
        {"merge_time": datetime(2000, 1, 1, tzinfo=UTC)},
    ),
)
def test_merge_insert_rechecks_every_review_and_transition_witness(
    postgres_engine: Engine,
    merge_overrides: dict[str, Any],
) -> None:
    with postgres_engine.begin() as connection:
        project_id, work = _create_project_with_work(connection, work_count=2)

    with postgres_engine.connect() as connection, connection.begin(), pytest.raises(DBAPIError):
        _stage_merge(
            connection,
            project_id=project_id,
            source_id=work[0][0],
            destination_id=work[1][0],
            merge_overrides=merge_overrides,
        )
