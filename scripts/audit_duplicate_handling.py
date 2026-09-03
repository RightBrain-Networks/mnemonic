"""Read-only, aggregate-only preflight and integrity audit for Phase 9.

Run this with the backend environment. The default expected head is the final
Phase 9 Advisory head. A Core-only or Phase 8 preflight must pass
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

AUDIT_VERSION = "duplicate-handling-v2"
PHASE8_HEAD = "0015_gate_review_fixes"
CORE_HEAD = "0016_duplicate_handling"
FINAL_HEAD = "0017_duplicate_suggestion_title_key"
SUPPORTED_HEADS = (PHASE8_HEAD, CORE_HEAD, FINAL_HEAD)
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


def _scalar(connection: Connection, statement: str) -> int:
    value = connection.scalar(text(statement))
    return int(value or 0)


def _ordinary_table_exists(connection: Connection, name: str) -> bool:
    return bool(
        connection.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_class AS relation
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND relation.relname = CAST(:name AS text)
                      AND relation.relkind IN ('r', 'p')
                )
                """
            ),
            {"name": name},
        )
    )


def _base_counts(connection: Connection) -> dict[str, int]:
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
                    WHERE table_schema = current_schema()
                      AND table_name = 'work_relationships'
                      AND column_name = 'created_for_duplicate_merge_id'
                )
                OR created_for_duplicate_merge_id IS NULL
              )
            """
            if _ordinary_table_exists(connection, "work_duplicate_merges")
            else "SELECT count(*) FROM work_relationships WHERE relationship_type = 'duplicate-of'",
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
    if expected_head in {CORE_HEAD, FINAL_HEAD}:
        required.update(CORE_FUNCTIONS)
    if expected_head == FINAL_HEAD:
        required.update(ADVISORY_FUNCTIONS)
    return frozenset(required)


def _required_tables(expected_head: str) -> frozenset[str]:
    return CORE_TABLES if expected_head in {CORE_HEAD, FINAL_HEAD} else frozenset()


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


def _catalog(connection: Connection, expected_head: str) -> dict[str, int]:
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
                WHERE namespace.nspname = current_schema()
                  AND procedure.proname = ANY(:names)
                  AND (
                      procedure.proname <> 'mnemonic_duplicate_title_key_v1'
                      OR (
                          procedure.provolatile = 'i'
                          AND procedure.proparallel = 's'
                          AND procedure.proisstrict
                          AND procedure.prorettype = 'text'::pg_catalog.regtype
                          AND language.lanname = 'sql'
                          AND procedure.proconfig @> ARRAY['search_path=pg_catalog']
                      )
                  )
                """
            ),
            {"names": list(required_names)},
        )
    )
    title_key_contract_failure_count = 0
    title_key_signature = "mnemonic_duplicate_title_key_v1(text)"
    if expected_head == FINAL_HEAD and title_key_signature in present:
        title_key_contract_valid = connection.scalar(
            text(
                """
                SELECT mnemonic_duplicate_title_key_v1(:wide) = 'cache repair'
                   AND mnemonic_duplicate_title_key_v1(:spacing) = 'alpha beta'
                   AND mnemonic_duplicate_title_key_v1(:dotted_i) = :dotted_i
                   AND mnemonic_duplicate_title_key_v1(:sharp_s) = :sharp_s
                   AND mnemonic_duplicate_title_key_v1(:line_separator) = :line_separator
                   AND mnemonic_duplicate_title_key_v1(NULL) IS NULL
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
    required_indexes = ADVISORY_INDEXES if expected_head == FINAL_HEAD else frozenset()
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
                    WHERE namespace.nspname = current_schema()
                      AND index_relation.relname = ANY(:names)
                      AND table_relation.relname = 'work_items'
                      AND access_method.amname = 'btree'
                      AND index.indisvalid
                      AND index.indisready
                      AND NOT index.indisunique
                      AND index.indnkeyatts = 3
                      AND pg_catalog.pg_get_indexdef(index.indexrelid, 1, true)
                          = 'project_id'
                      AND pg_catalog.pg_get_indexdef(index.indexrelid, 2, true)
                          = 'mnemonic_duplicate_title_key_v1(title::text)'
                      AND pg_catalog.pg_get_indexdef(index.indexrelid, 3, true)
                          = 'id'
                      AND pg_catalog.pg_get_expr(
                          index.indpred, index.indrelid, true
                      ) IN ('deleted_at IS NULL', '(deleted_at IS NULL)')
                    """
                ),
                {"names": list(required_indexes)},
            )
        )
    return {
        "server_version_num": _scalar(connection, "SELECT current_setting('server_version_num')"),
        "required_table_count": len(required_tables),
        "missing_table_count": sum(
            not _ordinary_table_exists(connection, table_name)
            for table_name in required_tables
        ),
        "required_function_count": len(required),
        "missing_function_count": len(required - present),
        "title_key_contract_failure_count": title_key_contract_failure_count,
        "required_index_count": len(required_indexes),
        "missing_index_count": len(required_indexes - present_indexes),
        "database_bytes": _scalar(connection, "SELECT pg_database_size(current_database())"),
    }


def _blocking_counts(counts: Mapping[str, int]) -> dict[str, int]:
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
    }
    return {name: value for name, value in counts.items() if name not in informational and value}


def _catalog_blocking_counts(catalog: Mapping[str, int]) -> dict[str, int]:
    blocking: dict[str, int] = {}
    if catalog["server_version_num"] < 170000:
        blocking["postgres_version_too_old"] = 1
    if catalog["missing_table_count"]:
        blocking["missing_required_tables"] = catalog["missing_table_count"]
    if catalog["missing_function_count"]:
        blocking["missing_required_functions"] = catalog["missing_function_count"]
    if catalog["missing_index_count"]:
        blocking["missing_required_indexes"] = catalog["missing_index_count"]
    if catalog["title_key_contract_failure_count"]:
        blocking["duplicate_title_key_contract_failures"] = catalog[
            "title_key_contract_failure_count"
        ]
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
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url is required")
    return args


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
            head_matches, head_count = _migration_head_status(
                connection, args.expected_head
            )
            counts = _base_counts(connection)
            if _ordinary_table_exists(connection, "work_duplicate_merges"):
                counts.update(_core_counts(connection))
            catalog = _catalog(connection, args.expected_head)
            connection.rollback()
        engine.dispose()

        if not args.backup_directory.is_dir():
            raise RuntimeError("Configured backup directory is unavailable")
        free_bytes = shutil.disk_usage(args.backup_directory).free
        required_free = args.minimum_backup_free_bytes or max(
            catalog["database_bytes"] * 2, 512 * MIB
        )
        blocking = _blocking_counts(counts)
        blocking.update(_catalog_blocking_counts(catalog))
        if not head_matches:
            blocking["migration_head_mismatch"] = 1
        if free_bytes < required_free:
            blocking["backup_capacity_insufficient"] = 1
        report.update(
            {
                "expected_head": args.expected_head,
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
