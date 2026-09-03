"""Read-only, aggregate-only preflight and integrity audit through Phase 10.

Run this with the backend environment. The default expected head is the final
Phase 10 repository-freshness head. An earlier preflight must pass
``--expected-head 0017_duplicate_suggestion_title_key``,
``--expected-head 0016_duplicate_handling`` or
``--expected-head 0015_gate_review_fixes`` explicitly.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine, text

AUDIT_VERSION = "repository-freshness-v1"
PHASE8_HEAD = "0015_gate_review_fixes"
CORE_HEAD = "0016_duplicate_handling"
ADVISORY_HEAD = "0017_duplicate_suggestion_title_key"
FINAL_HEAD = "0018_repository_freshness"
SUPPORTED_HEADS = (PHASE8_HEAD, CORE_HEAD, ADVISORY_HEAD, FINAL_HEAD)
MIB = 1024 * 1024

BASE_FUNCTIONS = frozenset(
    {
        "mnemonic_has_non_whitespace(text)",
        (
            "mnemonic_work_event_metadata_v2_is_valid("
            "text, text, uuid, uuid, uuid, uuid, uuid, uuid, uuid, uuid, uuid, smallint, jsonb)"
        ),
        "mnemonic_phase6_progress_metadata_is_valid(jsonb)",
    }
)
CORE_FUNCTIONS = frozenset(
    {
        "mnemonic_duplicate_component_state(uuid, uuid)",
        "mnemonic_duplicate_merge_is_complete(uuid, uuid)",
        "mnemonic_guard_duplicate_merge_insert()",
        "mnemonic_work_merged_metadata_v1_is_valid(uuid, uuid, smallint, jsonb)",
    }
)
ADVISORY_FUNCTIONS = frozenset({"mnemonic_duplicate_title_key_v1(text)"})
ADVISORY_INDEXES = frozenset({"ix_work_items_duplicate_title_key_v1"})
REPOSITORY_FRESHNESS_FUNCTIONS = frozenset(
    {"mnemonic_affected_paths_valid_v1(character varying[])"}
)
REPOSITORY_FRESHNESS_FUNCTION_BODY_MD5 = "288c0a1062044becfc340ac31b5c51ad"
CHECKPOINT_IMMUTABILITY_FUNCTION_BODY_MD5 = "b02dbd2b85e18d4acc4825d4926a2bb0"
CLIENT_OPERATION_INSERT_GUARD_BODY_MD5 = "e7e31ffb446b98fbb48972703b9d3d17"
CLIENT_OPERATION_MUTATION_GUARD_BODY_MD5 = "5fd0008ae172dbf4a8e62d0e703d0342"
CLIENT_OPERATION_COMPLETION_GUARD_BODY_MD5 = "ae5c0b2536afe86d361fd08f92b09267"
CORE_TABLES = frozenset({"work_duplicate_merges"})


def _local_settings() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip("\"'")
    return {**values, **os.environ}


def _database_url(raw: str) -> str:
    if raw.startswith("postgres://"):
        return "postgresql+psycopg://" + raw.removeprefix("postgres://")
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg://" + raw.removeprefix("postgresql://")
    return raw


def _scalar(
    connection: Connection,
    statement: str,
    parameters: Mapping[str, Any] | None = None,
) -> int:
    value = connection.scalar(text(statement), parameters or {})
    return int(value or 0)


def _ordinary_table_exists(
    connection: Connection, name: str, *, schema: str | None = None
) -> bool:
    selected_schema = schema or connection.scalar(
        text("SELECT pg_catalog.current_schema()")
    )
    if not isinstance(selected_schema, str):
        raise TypeError("Could not establish the audited application schema")
    return bool(
        connection.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_class AS relation
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname OPERATOR(pg_catalog.=)
                              CAST(:schema AS text)
                      AND relation.relname = CAST(:name AS text)
                      AND relation.relkind IN ('r', 'p')
                )
                """
            ),
            {"name": name, "schema": selected_schema},
        )
    )


def _base_counts(connection: Connection, audit_schema: str) -> dict[str, int]:
    has_merge_ledger = _ordinary_table_exists(
        connection, "work_duplicate_merges", schema=audit_schema
    )
    counts = {
        "pending_receipts": _scalar(
            connection, "SELECT count(*) FROM client_operations WHERE state = 'pending'"
        ),
        "invalid_receipts": _scalar(
            connection,
            """
            SELECT count(*)
            FROM client_operations
            WHERE request_fingerprint_version <> 1
               OR octet_length(request_fingerprint_salt) <> 32
               OR octet_length(request_fingerprint) <> 32
               OR response_contract_version <> 1
               OR state NOT IN ('pending', 'completed')
               OR (state = 'pending' AND (
                    response_status IS NOT NULL OR response_body IS NOT NULL
                    OR mutation_applied IS NOT NULL OR completed_at IS NOT NULL
               ))
               OR (state = 'completed' AND (
                    response_status NOT BETWEEN 200 AND 299
                    OR response_body IS NULL
                    OR jsonb_typeof(response_body) <> 'object'
                    OR mutation_applied IS NULL OR completed_at IS NULL
                    OR completed_at < created_at
               ))
            """,
        ),
        "checkpoint_owner_violations": _scalar(
            connection,
            """
            SELECT count(*)
            FROM checkpoints AS checkpoint
            LEFT JOIN work_items AS work ON work.id = checkpoint.work_item_id
            WHERE work.id IS NULL
            """,
        ),
        "event_owner_violations": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_events AS event
            LEFT JOIN work_items AS work
              ON work.id = event.work_item_id AND work.project_id = event.project_id
            WHERE work.id IS NULL
            """,
        ),
        "gate_owner_violations": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_gates AS gate
            LEFT JOIN work_items AS work
              ON work.id = gate.work_item_id AND work.project_id = gate.project_id
            WHERE work.id IS NULL
            """,
        ),
        "live_gate_state_violations": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_gates AS gate
            JOIN work_items AS work ON work.id = gate.work_item_id
            WHERE gate.resolved_at IS NULL
              AND (work.deleted_at IS NOT NULL OR work.status <> 'pending')
            """,
        ),
        "relationship_scope_violations": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_relationships AS relationship
            LEFT JOIN work_items AS source
              ON source.id = relationship.source_work_item_id
             AND source.project_id = relationship.project_id
            LEFT JOIN work_items AS destination
              ON destination.id = relationship.target_work_item_id
             AND destination.project_id = relationship.project_id
            WHERE source.id IS NULL OR destination.id IS NULL
            """,
        ),
        "weak_duplicate_marks": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_relationships
            WHERE relationship_type = 'duplicate-of'
              AND (
                NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = CAST(:audit_schema AS text)
                      AND table_name = 'work_relationships'
                      AND column_name = 'created_for_duplicate_merge_id'
                )
                OR created_for_duplicate_merge_id IS NULL
              )
            """
            if has_merge_ledger
            else "SELECT count(*) FROM work_relationships WHERE relationship_type = 'duplicate-of'",
            {"audit_schema": audit_schema} if has_merge_ledger else None,
        ),
        "weak_duplicate_multi_targets": _scalar(
            connection,
            """
            SELECT count(*)
            FROM (
                SELECT project_id, source_work_item_id
                FROM work_relationships
                WHERE relationship_type = 'duplicate-of'
                GROUP BY project_id, source_work_item_id
                HAVING count(DISTINCT target_work_item_id) > 1
            ) AS conflicts
            """,
        ),
        "weak_duplicate_cycle_paths": _scalar(
            connection,
            """
            WITH RECURSIVE walk(project_id, origin, node, visited, depth, cycle) AS (
                SELECT project_id, source_work_item_id, target_work_item_id,
                       ARRAY[source_work_item_id, target_work_item_id]::uuid[], 1,
                       source_work_item_id = target_work_item_id
                FROM work_relationships
                WHERE relationship_type = 'duplicate-of'
                UNION ALL
                SELECT walk.project_id, walk.origin, edge.target_work_item_id,
                       walk.visited || edge.target_work_item_id, walk.depth + 1,
                       edge.target_work_item_id = ANY(walk.visited)
                FROM walk
                JOIN work_relationships AS edge
                  ON edge.project_id = walk.project_id
                 AND edge.relationship_type = 'duplicate-of'
                 AND edge.source_work_item_id = walk.node
                WHERE NOT walk.cycle AND walk.depth < 51
            )
            SELECT count(*) FROM walk WHERE cycle
            """,
        ),
        "weak_duplicate_max_depth": _scalar(
            connection,
            """
            WITH RECURSIVE walk(project_id, node, visited, depth) AS (
                SELECT project_id, target_work_item_id,
                       ARRAY[source_work_item_id, target_work_item_id]::uuid[], 1
                FROM work_relationships
                WHERE relationship_type = 'duplicate-of'
                UNION ALL
                SELECT walk.project_id, edge.target_work_item_id,
                       walk.visited || edge.target_work_item_id, walk.depth + 1
                FROM walk
                JOIN work_relationships AS edge
                  ON edge.project_id = walk.project_id
                 AND edge.relationship_type = 'duplicate-of'
                 AND edge.source_work_item_id = walk.node
                WHERE walk.depth < 51
                  AND NOT edge.target_work_item_id = ANY(walk.visited)
            )
            SELECT coalesce(max(depth), 0) FROM walk
            """,
        ),
        "weak_mark_deleted_or_cross_project_endpoints": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_relationships AS relationship
            LEFT JOIN work_items AS source ON source.id = relationship.source_work_item_id
            LEFT JOIN work_items AS destination ON destination.id = relationship.target_work_item_id
            WHERE relationship.relationship_type = 'duplicate-of'
              AND (source.id IS NULL OR destination.id IS NULL
                   OR source.project_id <> relationship.project_id
                   OR destination.project_id <> relationship.project_id
                   OR source.deleted_at IS NOT NULL OR destination.deleted_at IS NOT NULL)
            """,
        ),
        "weak_mark_source_leases": _scalar(
            connection,
            """
            SELECT count(DISTINCT lease.work_item_id)
            FROM work_leases AS lease
            JOIN work_relationships AS relationship
              ON relationship.source_work_item_id = lease.work_item_id
             AND relationship.relationship_type = 'duplicate-of'
            """,
        ),
        "weak_mark_source_unresolved_gates": _scalar(
            connection,
            """
            SELECT count(DISTINCT gate.work_item_id)
            FROM work_gates AS gate
            JOIN work_relationships AS relationship
              ON relationship.source_work_item_id = gate.work_item_id
             AND relationship.relationship_type = 'duplicate-of'
            WHERE gate.resolved_at IS NULL
            """,
        ),
        "weak_mark_source_structural_adjacencies": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_relationships AS mark
            JOIN work_relationships AS structural
              ON structural.project_id = mark.project_id
             AND structural.relationship_type IN ('blocks', 'parent-child')
             AND mark.source_work_item_id IN (
                 structural.source_work_item_id, structural.target_work_item_id
             )
            WHERE mark.relationship_type = 'duplicate-of'
            """,
        ),
    }
    return counts


def _repository_freshness_counts(connection: Connection) -> dict[str, int]:
    schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
    original_search_path = connection.scalar(
        text("SELECT pg_catalog.current_setting('search_path')")
    )
    if not isinstance(schema, str) or not isinstance(original_search_path, str):
        raise TypeError("Could not establish a safe repository-freshness audit schema")
    quoted_schema = connection.dialect.identifier_preparer.quote_identifier(schema)
    connection.scalar(
        text("SELECT pg_catalog.set_config('search_path', :search_path, true)"),
        {"search_path": f"pg_catalog, {quoted_schema}"},
    )
    try:
        return _repository_freshness_counts_on_safe_search_path(connection)
    finally:
        connection.scalar(
            text("SELECT pg_catalog.set_config('search_path', :search_path, true)"),
            {"search_path": original_search_path},
        )


def _repository_freshness_counts_on_safe_search_path(
    connection: Connection,
) -> dict[str, int]:
    return {
        "invalid_affected_paths_count": _scalar(
            connection,
            """
            SELECT count(*)
            FROM checkpoints
            WHERE NOT mnemonic_affected_paths_valid_v1(affected_paths)
            """,
        ),
        "commitless_affected_paths_count": _scalar(
            connection,
            """
            SELECT count(*)
            FROM checkpoints
            WHERE pg_catalog.cardinality(affected_paths) OPERATOR(pg_catalog.>) 0
              AND verified_against IS NULL
            """,
        ),
        "scoped_checkpoint_count": _scalar(
            connection,
            """
            SELECT count(*)
            FROM checkpoints
            WHERE pg_catalog.cardinality(affected_paths) OPERATOR(pg_catalog.>) 0
            """,
        ),
        "checkpoint_receipt_scope_violation_count": _scalar(
            connection,
            """
            WITH RECURSIVE completed_operations AS (
                SELECT operation.id AS operation_id,
                       operation.project_id,
                       operation.operation_kind,
                       operation.response_body
                FROM client_operations AS operation
                WHERE operation.state OPERATOR(pg_catalog.=) 'completed'
            ),
            receipt_nodes (
                operation_id, operation_kind, node_path, node_value
            ) AS (
                SELECT operation_id,
                       operation_kind,
                       ARRAY[]::text[],
                       response_body
                FROM completed_operations
                UNION ALL
                SELECT node.operation_id,
                       node.operation_kind,
                       node.node_path OPERATOR(pg_catalog.||) child.component,
                       child.value
                FROM receipt_nodes AS node
                CROSS JOIN LATERAL (
                    SELECT object_entry.key AS component,
                           object_entry.value
                    FROM pg_catalog.jsonb_each(
                        CASE
                            WHEN pg_catalog.jsonb_typeof(node.node_value)
                                OPERATOR(pg_catalog.=) 'object'
                                THEN node.node_value
                            ELSE '{}'::jsonb
                        END
                    ) AS object_entry
                    UNION ALL
                    SELECT array_entry.ordinality::text AS component,
                           array_entry.value
                    FROM pg_catalog.jsonb_array_elements(
                        CASE
                            WHEN pg_catalog.jsonb_typeof(node.node_value)
                                OPERATOR(pg_catalog.=) 'array'
                                THEN node.node_value
                            ELSE '[]'::jsonb
                        END
                    ) WITH ORDINALITY AS array_entry(value, ordinality)
                ) AS child
                WHERE pg_catalog.cardinality(node.node_path) OPERATOR(pg_catalog.=) 0
                   OR NOT (
                       node.node_path[pg_catalog.cardinality(node.node_path)]
                           OPERATOR(pg_catalog.=) 'metadata'
                       OR node.node_path[pg_catalog.cardinality(node.node_path)]
                           OPERATOR(pg_catalog.=) 'source_metadata'
                   )
            ),
            forbidden_scope_operations AS (
                SELECT DISTINCT operation_id
                FROM receipt_nodes
                WHERE node_path[pg_catalog.cardinality(node_path)]
                          OPERATOR(pg_catalog.=) 'affected_paths'
                  AND NOT (
                      (
                          operation_kind OPERATOR(pg_catalog.=) 'create_work'
                          AND node_path OPERATOR(pg_catalog.=)
                              ARRAY['initial_checkpoint', 'affected_paths']::text[]
                      )
                      OR (
                          operation_kind OPERATOR(pg_catalog.=) 'add_checkpoint'
                          AND node_path OPERATOR(pg_catalog.=)
                              ARRAY['affected_paths']::text[]
                      )
                      OR (
                          operation_kind OPERATOR(pg_catalog.=) 'complete_work'
                          AND node_path OPERATOR(pg_catalog.=)
                              ARRAY['checkpoint', 'affected_paths']::text[]
                      )
                  )
            ),
            receipt_checkpoints AS (
                SELECT operation.operation_id,
                       operation.project_id,
                       CASE
                           WHEN operation.operation_kind
                               OPERATOR(pg_catalog.=) 'create_work'
                               THEN operation.response_body -> 'initial_checkpoint'
                           WHEN operation.operation_kind
                               OPERATOR(pg_catalog.=) 'add_checkpoint'
                               THEN operation.response_body
                           WHEN operation.operation_kind
                               OPERATOR(pg_catalog.=) 'complete_work'
                               THEN operation.response_body -> 'checkpoint'
                       END AS checkpoint_body
                FROM completed_operations AS operation
                WHERE operation.operation_kind OPERATOR(pg_catalog.=) 'create_work'
                   OR operation.operation_kind OPERATOR(pg_catalog.=) 'add_checkpoint'
                   OR operation.operation_kind OPERATOR(pg_catalog.=) 'complete_work'
            ),
            checkpoint_scope_violations AS (
                SELECT receipt.operation_id
                FROM receipt_checkpoints AS receipt
                LEFT JOIN checkpoints AS checkpoint
                  ON checkpoint.id::text = receipt.checkpoint_body ->> 'id'
                 AND EXISTS (
                     SELECT 1
                     FROM work_items AS work
                     WHERE work.id = checkpoint.work_item_id
                       AND work.project_id = receipt.project_id
                 )
                WHERE (
                       pg_catalog.jsonb_typeof(receipt.checkpoint_body)
                           OPERATOR(pg_catalog.=) 'object'
                      ) IS NOT TRUE
                   OR checkpoint.id IS NULL
                   OR NOT receipt.checkpoint_body ? 'verified_against'
                   OR NOT (
                       pg_catalog.jsonb_typeof(
                           receipt.checkpoint_body -> 'verified_against'
                       ) OPERATOR(pg_catalog.=) 'string'
                       OR pg_catalog.jsonb_typeof(
                           receipt.checkpoint_body -> 'verified_against'
                       ) OPERATOR(pg_catalog.=) 'null'
                   )
                   OR NOT COALESCE(
                       checkpoint.verified_against::text OPERATOR(pg_catalog.=)
                           (receipt.checkpoint_body ->> 'verified_against'),
                       checkpoint.verified_against IS NULL
                           AND receipt.checkpoint_body ->> 'verified_against' IS NULL
                   )
                   OR (
                       receipt.checkpoint_body ? 'affected_paths'
                       AND (
                           CASE
                               WHEN pg_catalog.jsonb_typeof(
                                   receipt.checkpoint_body -> 'affected_paths'
                               ) OPERATOR(pg_catalog.=) 'array'
                               THEN pg_catalog.jsonb_array_length(
                                   receipt.checkpoint_body -> 'affected_paths'
                               ) OPERATOR(pg_catalog.=) 0
                               ELSE true
                           END
                       )
                   )
                   OR (
                       pg_catalog.cardinality(checkpoint.affected_paths)
                           OPERATOR(pg_catalog.>) 0
                       AND (
                           NOT receipt.checkpoint_body ? 'affected_paths'
                           OR NOT (
                               receipt.checkpoint_body -> 'affected_paths'
                                   OPERATOR(pg_catalog.=)
                                       pg_catalog.to_jsonb(checkpoint.affected_paths)
                           )
                       )
                   )
                   OR (
                       pg_catalog.cardinality(checkpoint.affected_paths)
                           OPERATOR(pg_catalog.=) 0
                       AND receipt.checkpoint_body ? 'affected_paths'
                   )
            )
            SELECT pg_catalog.count(*)
            FROM (
                SELECT operation_id
                FROM forbidden_scope_operations
                UNION
                SELECT operation_id
                FROM checkpoint_scope_violations
            ) AS violating_operations
            """,
        ),
    }


def _core_counts(connection: Connection) -> dict[str, int]:
    return {
        "authoritative_merges": _scalar(
            connection, "SELECT count(*) FROM work_duplicate_merges"
        ),
        "authoritative_graph_invalid": _scalar(
            connection,
            """
            WITH RECURSIVE walk(project_id, origin, node, visited, depth, cycle) AS (
                SELECT project_id, source_work_item_id, destination_work_item_id,
                       ARRAY[source_work_item_id, destination_work_item_id]::uuid[], 1,
                       source_work_item_id = destination_work_item_id
                FROM work_duplicate_merges
                UNION ALL
                SELECT walk.project_id, walk.origin, merge.destination_work_item_id,
                       walk.visited || merge.destination_work_item_id, walk.depth + 1,
                       merge.destination_work_item_id = ANY(walk.visited)
                FROM walk
                JOIN work_duplicate_merges AS merge
                  ON merge.project_id = walk.project_id
                 AND merge.source_work_item_id = walk.node
                WHERE NOT walk.cycle AND walk.depth <= 50
            )
            SELECT count(*) FROM walk WHERE cycle OR depth > 50
            """,
        ),
        "merge_endpoint_revision_violations": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_duplicate_merges AS merge
            LEFT JOIN work_items AS source
              ON source.id = merge.source_work_item_id
             AND source.project_id = merge.project_id
            LEFT JOIN work_items AS destination
              ON destination.id = merge.destination_work_item_id
             AND destination.project_id = merge.project_id
            WHERE source.id IS NULL OR destination.id IS NULL
               OR source.deleted_at IS NOT NULL OR destination.deleted_at IS NOT NULL
               OR source.version <> merge.resulting_source_work_version
               OR source.updated_at <> merge.created_at
               OR destination.version < merge.resulting_destination_work_version
               OR destination.updated_at < merge.created_at
            """,
        ),
        "merge_relationship_violations": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_duplicate_merges AS merge
            LEFT JOIN work_relationships AS relationship
              ON relationship.project_id = merge.project_id
             AND relationship.id = merge.duplicate_relationship_id
             AND relationship.relationship_type = 'duplicate-of'
             AND relationship.source_work_item_id = merge.source_work_item_id
             AND relationship.target_work_item_id = merge.destination_work_item_id
            WHERE relationship.id IS NULL
               OR relationship.created_for_duplicate_merge_id IS DISTINCT FROM NULL
                  AND relationship.created_for_duplicate_merge_id <> merge.id
            """,
        ),
        "merge_event_pair_violations": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_duplicate_merges AS merge
            LEFT JOIN LATERAL (
                SELECT count(*) AS total,
                       count(*) FILTER (
                           WHERE event.work_item_id = merge.source_work_item_id
                             AND event.metadata ->> 'role' = 'source'
                       ) AS sources,
                       count(*) FILTER (
                           WHERE event.work_item_id = merge.destination_work_item_id
                             AND event.metadata ->> 'role' = 'destination'
                       ) AS destinations
                FROM work_events AS event
                WHERE event.project_id = merge.project_id
                  AND event.work_duplicate_merge_id = merge.id
                  AND event.event_type = 'work_merged'
                  AND event.actor_kind = 'client'
                  AND event.actor_client = merge.merged_by_client
                  AND event.actor_session_id = merge.merged_by_session_id
                  AND event.actor_model IS NOT DISTINCT FROM merge.merged_by_model
                  AND event.body = merge.rationale
                  AND event.origin = 'live'
                  AND event.created_at = merge.created_at
                  AND event.metadata ->> 'merge_id' = merge.id::text
                  AND event.metadata ->> 'source_work_item_id'
                      = merge.source_work_item_id::text
                  AND event.metadata ->> 'destination_work_item_id'
                      = merge.destination_work_item_id::text
                  AND event.metadata ->> 'source_work_version'
                      = merge.resulting_source_work_version::text
                  AND event.metadata ->> 'destination_work_version'
                      = merge.resulting_destination_work_version::text
            ) AS evidence ON true
            WHERE evidence.total <> 2 OR evidence.sources <> 1 OR evidence.destinations <> 1
            """,
        ),
        "merge_witness_event_violations": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_duplicate_merges AS merge
            JOIN work_relationships AS relationship
              ON relationship.id = merge.duplicate_relationship_id
            LEFT JOIN LATERAL (
                SELECT count(*) AS total,
                       count(DISTINCT event.work_item_id) AS endpoints
                FROM work_events AS event
                WHERE event.project_id = merge.project_id
                  AND event.created_for_duplicate_merge_id = merge.id
                  AND event.event_type = 'relationship_added'
                  AND event.relationship_id = relationship.id
                  AND event.created_at = merge.created_at
            ) AS evidence ON true
            WHERE (relationship.created_for_duplicate_merge_id = merge.id
                   AND (evidence.total <> 2 OR evidence.endpoints <> 2))
               OR (relationship.created_for_duplicate_merge_id IS NULL
                   AND evidence.total <> 0)
            """,
        ),
        "alias_domain_state_violations": _scalar(
            connection,
            """
            SELECT count(*)
            FROM work_duplicate_merges AS merge
            WHERE EXISTS (
                    SELECT 1 FROM work_leases
                    WHERE work_item_id = merge.source_work_item_id
                  )
               OR EXISTS (
                    SELECT 1 FROM work_gates
                    WHERE work_item_id = merge.source_work_item_id
                      AND resolved_at IS NULL
                  )
               OR EXISTS (
                    SELECT 1 FROM work_relationships
                    WHERE relationship_type IN ('blocks', 'parent-child')
                      AND merge.source_work_item_id IN (
                          source_work_item_id, target_work_item_id
                      )
                  )
            """,
        ),
        "invalid_completed_merge_receipts": _scalar(
            connection,
            """
            SELECT count(*)
            FROM client_operations AS operation
            WHERE operation.operation_kind = 'merge_work'
              AND operation.state = 'completed'
              AND NOT EXISTS (
                  SELECT 1
                  FROM work_duplicate_merges AS merge
                  WHERE merge.project_id = operation.project_id
                    AND operation.response_body #>> '{merge,id}' = merge.id::text
                    AND operation.response_body #>> '{merge,source_work_item_id}'
                        = merge.source_work_item_id::text
                    AND operation.response_body #>> '{merge,destination_work_item_id}'
                        = merge.destination_work_item_id::text
              )
            """,
        ),
    }


def _required_functions(expected_head: str) -> frozenset[str]:
    required = set(BASE_FUNCTIONS)
    if expected_head in {CORE_HEAD, ADVISORY_HEAD, FINAL_HEAD}:
        required.update(CORE_FUNCTIONS)
    if expected_head in {ADVISORY_HEAD, FINAL_HEAD}:
        required.update(ADVISORY_FUNCTIONS)
    if expected_head == FINAL_HEAD:
        required.update(REPOSITORY_FRESHNESS_FUNCTIONS)
    return frozenset(required)


def _required_tables(expected_head: str) -> frozenset[str]:
    if expected_head in {CORE_HEAD, ADVISORY_HEAD, FINAL_HEAD}:
        return CORE_TABLES
    return frozenset()


def _migration_head_status(connection: Connection, expected_head: str) -> tuple[bool, int]:
    head_count = _scalar(connection, "SELECT count(*) FROM alembic_version")
    expected_count = int(
        connection.scalar(
            text("SELECT count(*) FROM alembic_version WHERE version_num = :expected_head"),
            {"expected_head": expected_head},
        )
        or 0
    )
    return head_count == 1 and expected_count == 1, head_count


def _checkpoint_immutability_catalog_failure_count(
    connection: Connection, schema: str
) -> int:
    valid_trigger_count = int(
        connection.scalar(
            text(
                """
                SELECT count(*)
                FROM pg_catalog.pg_trigger AS trigger_value
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid = trigger_value.tgrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                JOIN pg_catalog.pg_proc AS procedure
                  ON procedure.oid = trigger_value.tgfoid
                JOIN pg_catalog.pg_namespace AS procedure_namespace
                  ON procedure_namespace.oid = procedure.pronamespace
                JOIN pg_catalog.pg_language AS language
                  ON language.oid = procedure.prolang
                WHERE namespace.nspname = CAST(:audit_schema AS text)
                  AND relation.relname = 'checkpoints'
                  AND trigger_value.tgname = 'checkpoints_immutable'
                  AND NOT trigger_value.tgisinternal
                  AND trigger_value.tgenabled = 'O'
                  AND trigger_value.tgtype = 27
                  AND trigger_value.tgconstraint = 0
                  AND NOT trigger_value.tgdeferrable
                  AND NOT trigger_value.tginitdeferred
                  AND trigger_value.tgnargs = 0
                  AND octet_length(trigger_value.tgargs) = 0
                  AND trigger_value.tgqual IS NULL
                  AND trigger_value.tgparentid = 0
                  AND procedure_namespace.nspname = CAST(:audit_schema AS text)
                  AND procedure.proname = 'mnemonic_reject_checkpoint_mutation'
                  AND pg_catalog.oidvectortypes(procedure.proargtypes) = ''
                  AND procedure.prorettype OPERATOR(pg_catalog.=)
                      'trigger'::pg_catalog.regtype
                  AND procedure.prokind = 'f'
                  AND procedure.pronargs = 0
                  AND procedure.pronargdefaults = 0
                  AND NOT procedure.proretset
                  AND procedure.provolatile = 'v'
                  AND NOT procedure.proisstrict
                  AND procedure.proparallel = 'u'
                  AND NOT procedure.prosecdef
                  AND NOT procedure.proleakproof
                  AND procedure.provariadic = 0
                  AND procedure.proconfig IS NULL
                  AND language.lanname = 'plpgsql'
                  AND pg_catalog.md5(procedure.prosrc) = :body_md5
                """
            ),
            {
                "audit_schema": schema,
                "body_md5": CHECKPOINT_IMMUTABILITY_FUNCTION_BODY_MD5,
            },
        )
        or 0
    )
    return int(valid_trigger_count != 1)


def _catalog(connection: Connection, expected_head: str) -> dict[str, int]:
    schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
    original_search_path = connection.scalar(
        text("SELECT pg_catalog.current_setting('search_path')")
    )
    if not isinstance(schema, str) or not isinstance(original_search_path, str):
        raise TypeError("Could not establish a safe catalog audit schema")
    quoted_schema = connection.dialect.identifier_preparer.quote_identifier(schema)
    connection.scalar(
        text("SELECT pg_catalog.set_config('search_path', :search_path, true)"),
        {"search_path": f"pg_catalog, {quoted_schema}"},
    )
    try:
        return _catalog_on_safe_search_path(connection, expected_head, schema)
    finally:
        connection.scalar(
            text("SELECT pg_catalog.set_config('search_path', :search_path, true)"),
            {"search_path": original_search_path},
        )


def _catalog_on_safe_search_path(
    connection: Connection, expected_head: str, audit_schema: str
) -> dict[str, int]:
    original_search_path = connection.scalar(
        text("SELECT pg_catalog.current_setting('search_path')")
    )
    if not isinstance(original_search_path, str):
        raise TypeError("Could not capture the catalog audit search path")
    connection.scalar(
        text("SELECT pg_catalog.set_config('search_path', 'pg_catalog', true)")
    )
    try:
        return _catalog_on_pg_catalog_path(connection, expected_head, audit_schema)
    finally:
        connection.scalar(
            text("SELECT pg_catalog.set_config('search_path', :search_path, true)"),
            {"search_path": original_search_path},
        )


def _catalog_on_pg_catalog_path(
    connection: Connection, expected_head: str, audit_schema: str
) -> dict[str, int]:
    quoted_schema = connection.dialect.identifier_preparer.quote_identifier(
        audit_schema
    )
    required = _required_functions(expected_head)
    required_tables = _required_tables(expected_head)
    required_names = {signature.partition("(")[0] for signature in required}
    present = set(
        connection.scalars(
            text(
                """
                SELECT procedure.proname || '('
                       || pg_catalog.oidvectortypes(procedure.proargtypes) || ')'
                FROM pg_catalog.pg_proc AS procedure
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = procedure.pronamespace
                JOIN pg_catalog.pg_language AS language
                  ON language.oid = procedure.prolang
                WHERE namespace.nspname = CAST(:audit_schema AS text)
                  AND procedure.proname = ANY(:names)
                  AND (
                      procedure.proname NOT IN (
                          'mnemonic_duplicate_title_key_v1',
                          'mnemonic_affected_paths_valid_v1'
                      )
                      OR (
                          procedure.proname = 'mnemonic_duplicate_title_key_v1'
                          AND
                          procedure.provolatile = 'i'
                          AND procedure.proparallel = 's'
                          AND procedure.proisstrict
                          AND procedure.prorettype OPERATOR(pg_catalog.=)
                              'text'::pg_catalog.regtype
                          AND procedure.prokind = 'f'
                          AND procedure.pronargs = 1
                          AND procedure.pronargdefaults = 0
                          AND NOT procedure.proretset
                          AND NOT procedure.prosecdef
                          AND NOT procedure.proleakproof
                          AND procedure.provariadic = 0
                          AND language.lanname = 'sql'
                          AND procedure.proconfig OPERATOR(pg_catalog.=)
                              ARRAY['search_path=pg_catalog']
                      )
                      OR (
                          procedure.proname = 'mnemonic_affected_paths_valid_v1'
                          AND procedure.provolatile = 'i'
                          AND procedure.proparallel = 's'
                          AND procedure.proisstrict
                          AND procedure.prorettype OPERATOR(pg_catalog.=)
                              'boolean'::pg_catalog.regtype
                          AND procedure.prokind = 'f'
                          AND procedure.pronargs = 1
                          AND procedure.pronargdefaults = 0
                          AND NOT procedure.proretset
                          AND NOT procedure.prosecdef
                          AND NOT procedure.proleakproof
                          AND procedure.provariadic = 0
                          AND language.lanname = 'plpgsql'
                          AND procedure.proconfig OPERATOR(pg_catalog.=)
                              ARRAY['search_path=pg_catalog']
                      )
                  )
                """
            ),
            {"audit_schema": audit_schema, "names": list(required_names)},
        )
    )
    title_key_contract_failure_count = 0
    title_key_signature = "mnemonic_duplicate_title_key_v1(text)"
    if expected_head in {ADVISORY_HEAD, FINAL_HEAD} and title_key_signature in present:
        title_key_contract_valid = connection.scalar(
            text(
                f"""
                SELECT {quoted_schema}.mnemonic_duplicate_title_key_v1(:wide)
                           OPERATOR(pg_catalog.=) 'cache repair'
                   AND {quoted_schema}.mnemonic_duplicate_title_key_v1(:spacing)
                           OPERATOR(pg_catalog.=) 'alpha beta'
                   AND {quoted_schema}.mnemonic_duplicate_title_key_v1(:dotted_i)
                           OPERATOR(pg_catalog.=) :dotted_i
                   AND {quoted_schema}.mnemonic_duplicate_title_key_v1(:sharp_s)
                           OPERATOR(pg_catalog.=) :sharp_s
                   AND {quoted_schema}.mnemonic_duplicate_title_key_v1(:line_separator)
                           OPERATOR(pg_catalog.=) :line_separator
                   AND {quoted_schema}.mnemonic_duplicate_title_key_v1(NULL) IS NULL
                """
            ),
            {
                "wide": "  ＣＡＣＨＥ\t Repair ",
                "spacing": "\tALPHA\n  BETA\r",
                "dotted_i": "İ",
                "sharp_s": "ß",
                "line_separator": "alpha\u2028beta",
            },
        )
        title_key_contract_failure_count = int(title_key_contract_valid is not True)
    required_indexes = (
        ADVISORY_INDEXES
        if expected_head in {ADVISORY_HEAD, FINAL_HEAD}
        else frozenset()
    )
    present_indexes: set[str] = set()
    if required_indexes:
        present_indexes.update(
            connection.scalars(
                text(
                    """
                    SELECT index_relation.relname
                    FROM pg_catalog.pg_index AS index
                    JOIN pg_catalog.pg_class AS index_relation
                      ON index_relation.oid = index.indexrelid
                    JOIN pg_catalog.pg_class AS table_relation
                      ON table_relation.oid = index.indrelid
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = index_relation.relnamespace
                    JOIN pg_catalog.pg_am AS access_method
                      ON access_method.oid = index_relation.relam
                    WHERE namespace.nspname = CAST(:audit_schema AS text)
                      AND index_relation.relname = ANY(:names)
                      AND table_relation.relname = 'work_items'
                      AND access_method.amname = 'btree'
                      AND index.indisvalid
                      AND index.indisready
                      AND NOT index.indisunique
                      AND index.indnkeyatts = 3
                      AND pg_catalog.pg_get_indexdef(index.indexrelid, 1, true)
                          = 'project_id'
                      AND pg_catalog.replace(
                          pg_catalog.pg_get_indexdef(index.indexrelid, 2, true),
                          pg_catalog.quote_ident(CAST(:audit_schema AS text))
                              OPERATOR(pg_catalog.||) '.',
                          ''
                      ) = 'mnemonic_duplicate_title_key_v1(title::text)'
                      AND pg_catalog.pg_get_indexdef(index.indexrelid, 3, true)
                          = 'id'
                      AND pg_catalog.pg_get_expr(
                          index.indpred, index.indrelid, true
                      ) IN ('deleted_at IS NULL', '(deleted_at IS NULL)')
                    """
                ),
                {"audit_schema": audit_schema, "names": list(required_indexes)},
            )
        )

    missing_repository_freshness_function_count = 0
    repository_freshness_definition_failure_count = 0
    repository_freshness_contract_failure_count = 0
    checkpoint_affected_paths_column_failure_count = 0
    checkpoint_affected_paths_constraint_failure_count = 0
    unexpected_affected_paths_index_count = 0
    checkpoint_immutability_trigger_failure_count = (
        _checkpoint_immutability_catalog_failure_count(connection, audit_schema)
    )
    valid_client_operation_guard_count = _scalar(
        connection,
        f"""
        WITH expected(
            trigger_name, function_name, trigger_type, is_constraint,
            is_deferrable, is_initially_deferred, body_md5
        ) AS (
            VALUES
                (
                    'client_operation_insert_guard',
                    'mnemonic_guard_client_operation_insert',
                    7, false, false, false,
                    '{CLIENT_OPERATION_INSERT_GUARD_BODY_MD5}'
                ),
                (
                    'client_operation_mutation_guard',
                    'mnemonic_guard_client_operation_mutation',
                    27, false, false, false,
                    '{CLIENT_OPERATION_MUTATION_GUARD_BODY_MD5}'
                ),
                (
                    'client_operation_completion_guard',
                    'mnemonic_require_completed_client_operation',
                    5, true, true, true,
                    '{CLIENT_OPERATION_COMPLETION_GUARD_BODY_MD5}'
                )
        )
        SELECT count(*)
        FROM expected
        JOIN pg_catalog.pg_trigger AS trigger_value
          ON trigger_value.tgname = expected.trigger_name
        JOIN pg_catalog.pg_class AS relation
          ON relation.oid = trigger_value.tgrelid
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        JOIN pg_catalog.pg_proc AS procedure
          ON procedure.oid = trigger_value.tgfoid
        JOIN pg_catalog.pg_namespace AS procedure_namespace
          ON procedure_namespace.oid = procedure.pronamespace
        JOIN pg_catalog.pg_language AS language
          ON language.oid = procedure.prolang
        LEFT JOIN pg_catalog.pg_constraint AS trigger_constraint
          ON trigger_constraint.oid = trigger_value.tgconstraint
        WHERE namespace.nspname = CAST(:audit_schema AS text)
          AND relation.relname = 'client_operations'
          AND NOT trigger_value.tgisinternal
          AND trigger_value.tgenabled = 'O'
          AND trigger_value.tgtype = expected.trigger_type
          AND (trigger_value.tgconstraint <> 0) = expected.is_constraint
          AND trigger_value.tgdeferrable = expected.is_deferrable
          AND trigger_value.tginitdeferred = expected.is_initially_deferred
          AND (
              (
                  NOT expected.is_constraint
                  AND trigger_value.tgconstraint = 0
                  AND trigger_constraint.oid IS NULL
              )
              OR (
                  expected.is_constraint
                  AND trigger_constraint.contype = 't'
                  AND trigger_constraint.condeferrable
                  AND trigger_constraint.condeferred
              )
          )
          AND trigger_value.tgnargs = 0
          AND octet_length(trigger_value.tgargs) = 0
          AND trigger_value.tgqual IS NULL
          AND trigger_value.tgparentid = 0
          AND procedure_namespace.nspname = CAST(:audit_schema AS text)
          AND procedure.proname = expected.function_name
          AND pg_catalog.oidvectortypes(procedure.proargtypes) = ''
          AND procedure.prorettype OPERATOR(pg_catalog.=)
              'trigger'::pg_catalog.regtype
          AND procedure.prokind = 'f'
          AND procedure.pronargs = 0
          AND procedure.pronargdefaults = 0
          AND NOT procedure.proretset
          AND procedure.provolatile = 'v'
          AND NOT procedure.proisstrict
          AND procedure.proparallel = 'u'
          AND NOT procedure.prosecdef
          AND NOT procedure.proleakproof
          AND procedure.provariadic = 0
          AND procedure.proconfig OPERATOR(pg_catalog.=)
              ARRAY['search_path=pg_catalog']
          AND language.lanname = 'plpgsql'
          AND pg_catalog.md5(
              replace(
                  procedure.prosrc,
                  '"' || pg_catalog.replace(
                      CAST(:audit_schema AS text), '"', '""'
                  ) || '".',
                  '<schema>.'
              )
          ) = expected.body_md5
        """,
        {"audit_schema": audit_schema},
    )
    client_operation_guard_failure_count = 3 - valid_client_operation_guard_count
    if expected_head == FINAL_HEAD:
        missing_repository_freshness_function_count = len(
            REPOSITORY_FRESHNESS_FUNCTIONS - present
        )
        if not missing_repository_freshness_function_count:
            body_digest = connection.scalar(
                text(
                    """
                    SELECT pg_catalog.md5(procedure.prosrc)
                    FROM pg_catalog.pg_proc AS procedure
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid OPERATOR(pg_catalog.=)
                         procedure.pronamespace
                    WHERE namespace.nspname OPERATOR(pg_catalog.=)
                              CAST(:audit_schema AS text)
                      AND procedure.proname OPERATOR(pg_catalog.=)
                          'mnemonic_affected_paths_valid_v1'
                      AND pg_catalog.oidvectortypes(procedure.proargtypes)
                              OPERATOR(pg_catalog.=)
                          'character varying[]'
                    """
                ),
                {"audit_schema": audit_schema},
            )
            repository_freshness_definition_failure_count = int(
                body_digest != REPOSITORY_FRESHNESS_FUNCTION_BODY_MD5
            )
            repository_freshness_contract_valid = connection.scalar(
                text(
                    f"""
                    SELECT {quoted_schema}.mnemonic_affected_paths_valid_v1(
                               '{{}}'::varchar[]
                           )
                       AND {quoted_schema}.mnemonic_affected_paths_valid_v1(
                           ARRAY['src/**', 'tests/test_*.py', 'a*b*c']::varchar[]
                       )
                       AND {quoted_schema}.mnemonic_affected_paths_valid_v1(
                           ARRAY[pg_catalog.repeat('A', 512)]::varchar[]
                       )
                       AND NOT {quoted_schema}.mnemonic_affected_paths_valid_v1(
                           ARRAY['/absolute']::varchar[]
                       )
                       AND NOT {quoted_schema}.mnemonic_affected_paths_valid_v1(
                           ARRAY['bad**component']::varchar[]
                       )
                       AND NOT {quoted_schema}.mnemonic_affected_paths_valid_v1(
                           ARRAY[
                               'caf' OPERATOR(pg_catalog.||) pg_catalog.chr(233)
                               OPERATOR(pg_catalog.||) '.py'
                           ]::varchar[]
                       )
                       AND NOT {quoted_schema}.mnemonic_affected_paths_valid_v1(
                           ARRAY[pg_catalog.repeat('A', 513)]::varchar[]
                       )
                       AND NOT {quoted_schema}.mnemonic_affected_paths_valid_v1(
                           ARRAY['duplicate', 'duplicate']::varchar[]
                       )
                       AND NOT {quoted_schema}.mnemonic_affected_paths_valid_v1(
                           ARRAY(
                               SELECT pg_catalog.lpad(value::text, 2, '0')
                                      OPERATOR(pg_catalog.||)
                                      pg_catalog.repeat('a', 255)
                               FROM pg_catalog.generate_series(0, 63) AS value
                           )::varchar[]
                       )
                       AND {quoted_schema}.mnemonic_affected_paths_valid_v1(
                           NULL::varchar[]
                       ) IS NULL
                    """
                )
            )
            repository_freshness_contract_failure_count = int(
                repository_freshness_contract_valid is not True
            )

        valid_column_count = _scalar(
            connection,
            """
            SELECT count(*)
            FROM pg_catalog.pg_attribute AS attribute
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid OPERATOR(pg_catalog.=) attribute.attrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid OPERATOR(pg_catalog.=) relation.relnamespace
            LEFT JOIN pg_catalog.pg_attrdef AS default_value
              ON default_value.adrelid OPERATOR(pg_catalog.=) relation.oid
             AND default_value.adnum OPERATOR(pg_catalog.=) attribute.attnum
            WHERE namespace.nspname OPERATOR(pg_catalog.=)
                      CAST(:audit_schema AS text)
              AND relation.relname OPERATOR(pg_catalog.=) 'checkpoints'
              AND relation.relkind OPERATOR(pg_catalog.=) ANY (
                  ARRAY['r', 'p']::"char"[]
              )
              AND attribute.attname OPERATOR(pg_catalog.=) 'affected_paths'
              AND NOT attribute.attisdropped
              AND attribute.attnotnull
              AND pg_catalog.format_type(
                  attribute.atttypid, attribute.atttypmod
              ) OPERATOR(pg_catalog.=) 'character varying(512)[]'
              AND (
                  pg_catalog.pg_get_expr(
                      default_value.adbin, default_value.adrelid, true
                  ) OPERATOR(pg_catalog.=) '''{}''::character varying[]'
                  OR pg_catalog.pg_get_expr(
                      default_value.adbin, default_value.adrelid, true
                  ) OPERATOR(pg_catalog.=) '''{}''::varchar[]'
              )
            """,
            {"audit_schema": audit_schema},
        )
        checkpoint_affected_paths_column_failure_count = int(valid_column_count != 1)

        valid_constraint_count = _scalar(
            connection,
            """
            SELECT count(*)
            FROM pg_catalog.pg_constraint AS constraint_value
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid OPERATOR(pg_catalog.=) constraint_value.conrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid OPERATOR(pg_catalog.=) relation.relnamespace
            WHERE namespace.nspname OPERATOR(pg_catalog.=)
                      CAST(:audit_schema AS text)
              AND relation.relname OPERATOR(pg_catalog.=) 'checkpoints'
              AND constraint_value.contype OPERATOR(pg_catalog.=) 'c'
              AND constraint_value.convalidated
              AND (
                  (
                      constraint_value.conname OPERATOR(pg_catalog.=)
                          'ck_checkpoints_affected_paths_valid_v1'
                      AND pg_catalog.lower(
                          pg_catalog.regexp_replace(
                              pg_catalog.replace(
                                  pg_catalog.pg_get_expr(
                                      constraint_value.conbin,
                                      constraint_value.conrelid,
                                      true
                                  ),
                                  pg_catalog.quote_ident(
                                      CAST(:audit_schema AS text)
                                  ) OPERATOR(pg_catalog.||) '.',
                                  ''
                              ),
                              '[[:space:]()]',
                              '',
                              'g'
                          )
                      ) OPERATOR(pg_catalog.=)
                          'mnemonic_affected_paths_valid_v1affected_paths'
                      AND EXISTS (
                          SELECT 1
                          FROM pg_catalog.pg_depend AS dependency
                          JOIN pg_catalog.pg_proc AS validator
                            ON validator.oid OPERATOR(pg_catalog.=)
                               dependency.refobjid
                          JOIN pg_catalog.pg_namespace AS validator_namespace
                            ON validator_namespace.oid OPERATOR(pg_catalog.=)
                               validator.pronamespace
                          WHERE dependency.classid OPERATOR(pg_catalog.=)
                                    'pg_catalog.pg_constraint'::pg_catalog.regclass
                            AND dependency.objid OPERATOR(pg_catalog.=)
                                    constraint_value.oid
                            AND dependency.refclassid OPERATOR(pg_catalog.=)
                                    'pg_catalog.pg_proc'::pg_catalog.regclass
                            AND validator_namespace.nspname OPERATOR(pg_catalog.=)
                                    CAST(:audit_schema AS text)
                            AND validator.proname OPERATOR(pg_catalog.=)
                                    'mnemonic_affected_paths_valid_v1'
                            AND pg_catalog.oidvectortypes(validator.proargtypes)
                                    OPERATOR(pg_catalog.=) 'character varying[]'
                      )
                  )
                  OR (
                      constraint_value.conname OPERATOR(pg_catalog.=)
                          'ck_checkpoints_affected_paths_require_commit'
                      AND pg_catalog.lower(
                          pg_catalog.regexp_replace(
                              pg_catalog.pg_get_expr(
                                  constraint_value.conbin,
                                  constraint_value.conrelid,
                                  true
                              ),
                              '[[:space:]()]',
                              '',
                              'g'
                          )
                      ) OPERATOR(pg_catalog.=)
                          'cardinalityaffected_paths=0orverified_againstisnotnull'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM pg_catalog.pg_depend AS dependency
                          WHERE dependency.classid OPERATOR(pg_catalog.=)
                                    'pg_catalog.pg_constraint'::pg_catalog.regclass
                            AND dependency.objid OPERATOR(pg_catalog.=)
                                    constraint_value.oid
                            AND (
                                dependency.refclassid OPERATOR(pg_catalog.=)
                                    'pg_catalog.pg_proc'::pg_catalog.regclass
                                OR dependency.refclassid OPERATOR(pg_catalog.=)
                                    'pg_catalog.pg_operator'::pg_catalog.regclass
                            )
                      )
                  )
              )
            """,
            {"audit_schema": audit_schema},
        )
        checkpoint_affected_paths_constraint_failure_count = 2 - valid_constraint_count

        unexpected_affected_paths_index_count = _scalar(
            connection,
            """
            SELECT count(*)
            FROM pg_catalog.pg_indexes
            WHERE schemaname OPERATOR(pg_catalog.=) CAST(:audit_schema AS text)
              AND tablename OPERATOR(pg_catalog.=) 'checkpoints'
              AND indexdef OPERATOR(pg_catalog.~~*) '%affected_paths%'
            """,
            {"audit_schema": audit_schema},
        )
    return {
        "server_version_num": _scalar(
            connection,
            "SELECT pg_catalog.current_setting('server_version_num')",
        ),
        "required_table_count": len(required_tables),
        "missing_table_count": sum(
            not _ordinary_table_exists(connection, table_name, schema=audit_schema)
            for table_name in required_tables
        ),
        "required_function_count": len(required),
        "missing_function_count": len(required - present),
        "title_key_contract_failure_count": title_key_contract_failure_count,
        "required_index_count": len(required_indexes),
        "missing_index_count": len(required_indexes - present_indexes),
        "missing_repository_freshness_function_count": (
            missing_repository_freshness_function_count
        ),
        "repository_freshness_definition_failure_count": (
            repository_freshness_definition_failure_count
        ),
        "repository_freshness_contract_failure_count": (
            repository_freshness_contract_failure_count
        ),
        "checkpoint_affected_paths_column_failure_count": (
            checkpoint_affected_paths_column_failure_count
        ),
        "checkpoint_affected_paths_constraint_failure_count": (
            checkpoint_affected_paths_constraint_failure_count
        ),
        "unexpected_affected_paths_index_count": unexpected_affected_paths_index_count,
        "checkpoint_immutability_trigger_failure_count": (
            checkpoint_immutability_trigger_failure_count
        ),
        "client_operation_guard_failure_count": client_operation_guard_failure_count,
        "database_bytes": _scalar(
            connection,
            "SELECT pg_catalog.pg_database_size(pg_catalog.current_database())",
        ),
    }


def _blocking_counts(
    counts: Mapping[str, int],
    *,
    require_empty_scope: bool = False,
) -> dict[str, int]:
    informational = {
        "authoritative_merges",
        "weak_duplicate_marks",
        "weak_duplicate_multi_targets",
        "weak_duplicate_cycle_paths",
        "weak_duplicate_max_depth",
        "weak_mark_deleted_or_cross_project_endpoints",
        "weak_mark_source_leases",
        "weak_mark_source_unresolved_gates",
        "weak_mark_source_structural_adjacencies",
        "scoped_checkpoint_count",
    }
    blocking = {
        name: value for name, value in counts.items() if name not in informational and value
    }
    if require_empty_scope and counts.get("scoped_checkpoint_count", 0):
        blocking["unexpected_pre_enablement_scoped_checkpoint_count"] = counts[
            "scoped_checkpoint_count"
        ]
    return blocking


def _catalog_blocking_counts(catalog: Mapping[str, int]) -> dict[str, int]:
    blocking: dict[str, int] = {}
    if catalog["server_version_num"] < 170000:
        blocking["postgres_version_too_old"] = 1
    finding_keys = {
        "missing_table_count": "missing_required_tables",
        "missing_function_count": "missing_required_functions",
        "missing_index_count": "missing_required_indexes",
        "title_key_contract_failure_count": "duplicate_title_key_contract_failures",
        "missing_repository_freshness_function_count": (
            "missing_repository_freshness_function"
        ),
        "repository_freshness_definition_failure_count": (
            "repository_freshness_definition_failures"
        ),
        "repository_freshness_contract_failure_count": (
            "repository_freshness_contract_failures"
        ),
        "checkpoint_affected_paths_column_failure_count": (
            "checkpoint_affected_paths_column_failures"
        ),
        "checkpoint_affected_paths_constraint_failure_count": (
            "checkpoint_affected_paths_constraint_failures"
        ),
        "unexpected_affected_paths_index_count": "unexpected_affected_paths_indexes",
        "checkpoint_immutability_trigger_failure_count": (
            "checkpoint_immutability_trigger_failures"
        ),
        "client_operation_guard_failure_count": "client_operation_guard_failures",
    }
    for catalog_key, finding_key in finding_keys.items():
        if value := catalog[catalog_key]:
            blocking[finding_key] = value
    return blocking


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def _parse_args(settings: Mapping[str, str]) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    configured_backup = Path(settings.get("MNEMONIC_BACKUP_DIR", "./backups"))
    if not configured_backup.is_absolute():
        configured_backup = repository_root / configured_backup
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=settings.get("DATABASE_URL"))
    parser.add_argument("--expected-head", choices=SUPPORTED_HEADS, default=FINAL_HEAD)
    parser.add_argument(
        "--backup-directory",
        type=Path,
        default=configured_backup,
    )
    parser.add_argument(
        "--minimum-backup-free-bytes",
        type=_nonnegative_int,
        default=0,
        help="Override the default of max(2x database size, 512 MiB).",
    )
    parser.add_argument(
        "--require-empty-scope",
        action="store_true",
        help=(
            "Pre-enablement gate: treat any non-empty checkpoint repository scope as blocking. "
            "Do not use for steady-state audits after scoped writes are enabled."
        ),
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url is required")
    if args.require_empty_scope and args.expected_head != FINAL_HEAD:
        parser.error("--require-empty-scope requires --expected-head 0018_repository_freshness")
    return args


def _database_audit_snapshot(
    connection: Connection, expected_head: str
) -> tuple[bool, int, dict[str, int], dict[str, int]]:
    """Collect every database fact under one trusted, transaction-local path."""
    audit_schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
    original_search_path = connection.scalar(
        text("SELECT pg_catalog.current_setting('search_path')")
    )
    if not isinstance(audit_schema, str) or not isinstance(
        original_search_path, str
    ):
        raise TypeError("Could not establish a safe database audit schema")
    quoted_schema = connection.dialect.identifier_preparer.quote_identifier(
        audit_schema
    )
    connection.scalar(
        text("SELECT pg_catalog.set_config('search_path', :search_path, true)"),
        {"search_path": f"pg_catalog, {quoted_schema}"},
    )
    try:
        head_matches, head_count = _migration_head_status(connection, expected_head)
        counts = _base_counts(connection, audit_schema)
        if _ordinary_table_exists(
            connection, "work_duplicate_merges", schema=audit_schema
        ):
            counts.update(_core_counts(connection))
        if expected_head == FINAL_HEAD:
            counts.update(_repository_freshness_counts_on_safe_search_path(connection))
        catalog = _catalog_on_safe_search_path(
            connection, expected_head, audit_schema
        )
        return head_matches, head_count, counts, catalog
    finally:
        connection.scalar(
            text("SELECT pg_catalog.set_config('search_path', :search_path, true)"),
            {"search_path": original_search_path},
        )


def main() -> int:
    settings = _local_settings()
    args = _parse_args(settings)
    report: dict[str, Any] = {"audit_version": AUDIT_VERSION}
    try:
        engine = create_engine(
            _database_url(args.database_url),
            hide_parameters=True,
            connect_args={"connect_timeout": 5},
        )
        with engine.connect() as connection:
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            connection.execute(text("SET LOCAL statement_timeout = '60s'"))
            head_matches, head_count, counts, catalog = _database_audit_snapshot(
                connection,
                args.expected_head,
            )
            connection.rollback()
        engine.dispose()

        if not args.backup_directory.is_dir():
            raise RuntimeError("Configured backup directory is unavailable")
        free_bytes = shutil.disk_usage(args.backup_directory).free
        required_free = args.minimum_backup_free_bytes or max(
            catalog["database_bytes"] * 2, 512 * MIB
        )
        blocking = _blocking_counts(
            counts,
            require_empty_scope=args.require_empty_scope,
        )
        blocking.update(_catalog_blocking_counts(catalog))
        if not head_matches:
            blocking["migration_head_mismatch"] = 1
        if free_bytes < required_free:
            blocking["backup_capacity_insufficient"] = 1
        report.update(
            {
                "expected_head": args.expected_head,
                "require_empty_scope": args.require_empty_scope,
                "migration_head_count": head_count,
                "migration_head_matches": head_matches,
                "counts": counts,
                "catalog": catalog,
                "backup_capacity": {
                    "free_bytes": free_bytes,
                    "required_free_bytes": required_free,
                    "sufficient": free_bytes >= required_free,
                },
                "blocking_findings": blocking,
                "result": "pass" if not blocking else "blocked",
            }
        )
    except Exception:  # noqa: BLE001 - audits fail closed without exposing database details.
        report.update({"result": "blocked", "audit_runtime_failure": True})
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
