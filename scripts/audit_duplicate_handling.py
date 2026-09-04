"""Read-only, aggregate-only preflight and integrity audit through Phase 11.

Run this with the backend environment. The default expected head is the final
Phase 11 structured-completion-evidence head. An earlier preflight must pass
``--expected-head 0018_repository_freshness``,
``--expected-head 0017_duplicate_suggestion_title_key``,
``--expected-head 0016_duplicate_handling`` or
``--expected-head 0015_gate_review_fixes`` explicitly.

Phase 11 high-waters, inventories, and pages share the single repeatable-read,
read-only transaction established by :func:`main`. PostgreSQL sequence state is
non-MVCC and is sampled once, so run the audit with writers quiesced to avoid a
conservative transient sequence finding.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
from collections.abc import Iterator, Mapping, Sequence
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Connection, create_engine, text

AUDIT_VERSION = "structured-completion-evidence-v1"
PHASE8_HEAD = "0015_gate_review_fixes"
CORE_HEAD = "0016_duplicate_handling"
ADVISORY_HEAD = "0017_duplicate_suggestion_title_key"
REPOSITORY_FRESHNESS_HEAD = "0018_repository_freshness"
FINAL_HEAD = "0019_structured_completion_evidence"
SUPPORTED_HEADS = (
    PHASE8_HEAD,
    CORE_HEAD,
    ADVISORY_HEAD,
    REPOSITORY_FRESHNESS_HEAD,
    FINAL_HEAD,
)
MIB = 1024 * 1024
PHASE11_AUDIT_BATCH_SIZE = 256

_PHASE11_AUDIT_SCANS = {
    "work_items": ("work_items", "TRUE"),
    "checkpoints": ("checkpoints", "TRUE"),
    "completion_checkpoints": ("checkpoints", "kind = 'completion'"),
    "evidence_checkpoints": (
        "checkpoints",
        (
            "EXISTS (SELECT 1 FROM verification_results AS result "
            "WHERE result.completion_checkpoint_id = checkpoints.id) "
            "OR EXISTS (SELECT 1 FROM artifact_references AS artifact "
            "WHERE artifact.completion_checkpoint_id = checkpoints.id)"
        ),
    ),
    "work_events": ("work_events", "TRUE"),
    "completion_events": ("work_events", "event_type = 'work_completed'"),
    "lifecycle_events": (
        "work_events",
        "event_type IN ('work_completed', 'work_reopened')",
    ),
    "verification_results": ("verification_results", "TRUE"),
    "artifact_references": ("artifact_references", "TRUE"),
    "completion_operations": (
        "client_operations",
        "operation_kind = 'complete_work'",
    ),
}

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
COMPLETION_EVIDENCE_TABLES = frozenset({"verification_results", "artifact_references"})
COMPLETION_EVIDENCE_FUNCTIONS = frozenset(
    {
        "mnemonic_completion_artifact_reference_v1_is_valid(text, text)",
        "mnemonic_completion_evidence_text_bytes_v1(uuid, uuid)",
        "mnemonic_completion_episode_is_sealed(uuid, bigint)",
        "mnemonic_guard_completion_generation()",
        "mnemonic_require_completion_state_episode()",
        "mnemonic_guard_completion_pending_exit()",
        "mnemonic_guard_completion_unsealed_deletion()",
        "mnemonic_guard_completion_episode_departure()",
        "mnemonic_require_completion_generation_reopen()",
        "mnemonic_guard_completion_checkpoint_insert()",
        "mnemonic_require_completion_checkpoint_episode()",
        "mnemonic_guard_completion_evidence_insert()",
        "mnemonic_require_completion_evidence_episode()",
        "mnemonic_guard_completion_lifecycle_event_insert()",
        "mnemonic_require_completion_reopen_event_episode()",
        "mnemonic_reject_completion_evidence_mutation()",
        "mnemonic_reject_completion_evidence_truncate()",
        "mnemonic_reject_phase11_history_truncate()",
    }
)
COMPLETION_EVIDENCE_INDEXES = frozenset(
    {
        "uq_checkpoints_completion_generation",
        "uq_work_events_reopen_generation",
        "ix_work_events_completion_evidence_history",
        "ix_work_events_live_completion_version_order",
        "ix_client_operations_completion_checkpoint_receipt",
        "ix_client_operations_completion_receipt_correspondence",
        "ix_verification_results_completion_checkpoint_id_id",
        "ix_artifact_references_completion_checkpoint_id_id",
        "uq_verification_results_episode_position",
        "uq_artifact_references_episode_position",
    }
)
COMPLETION_EVIDENCE_TRIGGERS = frozenset(
    {
        "completion_generation_guard",
        "completion_state_episode_guard",
        "completion_pending_exit_guard",
        "completion_unsealed_deletion_guard",
        "completion_episode_departure_guard",
        "completion_generation_reopen_guard",
        "completion_lifecycle_event_insert_guard",
        "completion_reopen_event_episode_guard",
        "completion_checkpoint_insert_guard",
        "completion_checkpoint_episode_guard",
        "verification_results_insert_guard",
        "verification_results_episode_guard",
        "artifact_references_insert_guard",
        "artifact_references_episode_guard",
        "verification_results_immutable",
        "artifact_references_immutable",
        "verification_results_truncate_guard",
        "artifact_references_truncate_guard",
        "work_events_phase11_truncate_guard",
        "client_operations_phase11_truncate_guard",
    }
)
PHASE11_CATALOG_SHA256 = {
    "relations": "8b8a389da0f398be9e5ab62cefa36828695813f64e1a8d4c12714fcc4c77a1bb",
    "columns": "661ec98d3d0ceab6a33651bca9a78630ff6698cb9633dfa2a22fc43f6b162973",
    "constraints": "949e2f283ebe8c755fef57429e0be77a10e0fb759e6c35495bf6050aaecbecd5",
    "indexes": "e42d2073ac878f00f54c33bb6eebeeb74bd71b1be400c8f480730c8a8e22ae09",
    "triggers": "d373c87879d6d720758656059da6303e46fcd9af78f4de61cf16f5311e233fce",
    "functions": "fe5c30629aa6664b58e76daf5ceb9236ce04fc4b76d4e21238200d80925b7a4a",
}
PHASE11_ALL_INDEXES = sorted(
    COMPLETION_EVIDENCE_INDEXES
    | {
        "pk_verification_results",
        "pk_artifact_references",
        "uq_verification_results_work_item_id_id",
        "uq_artifact_references_work_item_id_id",
        "uq_artifact_references_episode_reference",
    }
)


@lru_cache(maxsize=1)
def _phase11_revision_contract() -> ModuleType:
    """Load frozen read-only contracts from the final migration revision."""

    path = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "alembic"
        / "versions"
        / "0019_structured_completion_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("mnemonic_phase11_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the Phase 11 migration contract")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if getattr(module, "revision", None) != FINAL_HEAD:
        raise RuntimeError("The Phase 11 audit contract has an unexpected revision")
    return module


def _phase11_downgrade_blocking_count(
    connection: Connection,
    audit_schema: str | None = None,
    *,
    operation_ids: Sequence[int] | None = None,
    include_evidence_rows: bool = True,
) -> int:
    schema = audit_schema or connection.scalar(text("SELECT pg_catalog.current_schema()"))
    if not isinstance(schema, str):
        raise TypeError("Could not resolve the Phase 11 audit schema")
    quoted_schema = connection.dialect.identifier_preparer.quote_identifier(schema)
    contract = _phase11_revision_contract()
    helper = getattr(contract, "_phase11_downgrade_blocking_count", None)
    if not callable(helper):
        raise TypeError("The Phase 11 downgrade audit contract is unavailable")
    count = helper(
        connection,
        quoted_schema,
        operation_ids=operation_ids,
        include_evidence_rows=include_evidence_rows,
    )
    if not isinstance(count, int) or count < 0:
        raise RuntimeError("The Phase 11 downgrade audit contract returned invalid data")
    return count


def _phase10_survivor_catalog_failure_count(connection: Connection, audit_schema: str) -> int:
    contract = _phase11_revision_contract()
    helper = getattr(contract, "_phase10_survivor_catalog_digest", None)
    expected = getattr(contract, "_PHASE10_SURVIVOR_CATALOG_SHA256S", None)
    if (
        not callable(helper)
        or not isinstance(expected, frozenset)
        or not expected
        or not all(isinstance(digest, str) for digest in expected)
    ):
        raise TypeError("The Phase 10 survivor catalog contract is unavailable")
    digest = helper(audit_schema, connection=connection)
    if not isinstance(digest, str):
        raise TypeError("The Phase 10 survivor catalog contract returned invalid data")
    return int(digest not in expected)


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


def _phase11_audit_page_is_invalid(
    rows: Sequence[object],
    cursor: object | None,
    high_water: object,
) -> bool:
    return (
        len(rows) > PHASE11_AUDIT_BATCH_SIZE
        or (cursor is not None and rows[0] <= cursor)
        or rows[-1] > high_water
        or any(left >= right for left, right in pairwise(rows))
    )


def _phase11_audit_inventory(
    connection: Connection,
    table_name: str,
    predicate: str,
) -> tuple[object | None, int]:
    high_water = connection.scalar(
        text(
            f"""
            /* phase11-audit-high-water */
            SELECT id
            FROM {table_name}
            WHERE ({predicate})
            ORDER BY id DESC
            LIMIT 1
            """
        )
    )
    expected_count = _scalar(
        connection,
        f"""
        /* phase11-audit-inventory */
        SELECT pg_catalog.count(*)
        FROM {table_name}
        WHERE ({predicate})
        """,
    )
    if (high_water is None) != (expected_count == 0):
        raise RuntimeError("Phase 11 audit keyset inventory is inconsistent")
    return high_water, expected_count


def _phase11_id_batches(
    connection: Connection,
    scan: str,
) -> Iterator[tuple[object, ...]]:
    """Yield content-free primary-key pages under the caller's shared snapshot."""

    try:
        table_name, predicate = _PHASE11_AUDIT_SCANS[scan]
    except KeyError as error:
        raise ValueError("Unknown trusted Phase 11 audit scan") from error
    if not isinstance(PHASE11_AUDIT_BATCH_SIZE, int) or PHASE11_AUDIT_BATCH_SIZE < 1:
        raise RuntimeError("The Phase 11 audit batch size is invalid")
    high_water, expected_count = _phase11_audit_inventory(
        connection, table_name, predicate
    )
    if high_water is None:
        return
    cursor: object | None = None
    scanned_count = 0
    while cursor != high_water:
        cursor_clause = "" if cursor is None else "AND id > :audit_cursor"
        rows = tuple(
            connection.scalars(
                text(
                    f"""
                    /* phase11-audit-keyset */
                    SELECT id
                    FROM {table_name}
                    WHERE ({predicate})
                      AND id <= :audit_high_water
                      {cursor_clause}
                    ORDER BY id
                    LIMIT :audit_batch_size
                    """
                ),
                {
                    "audit_batch_size": PHASE11_AUDIT_BATCH_SIZE,
                    "audit_high_water": high_water,
                    **({"audit_cursor": cursor} if cursor is not None else {}),
                },
            )
        )
        if not rows:
            raise RuntimeError("Phase 11 audit keyset scan ended before completion")
        if _phase11_audit_page_is_invalid(rows, cursor, high_water):
            raise RuntimeError("Phase 11 audit keyset scan did not advance")
        scanned_count += len(rows)
        if scanned_count > expected_count:
            raise RuntimeError("Phase 11 audit keyset scan exceeded its inventory")
        yield rows
        cursor = rows[-1]
    if scanned_count != expected_count:
        raise RuntimeError("Phase 11 audit keyset scan did not cover its inventory")


def _phase11_batched_table_count(connection: Connection, scan: str) -> int:
    return sum(len(batch) for batch in _phase11_id_batches(connection, scan))


def _phase11_batched_downgrade_blocking_count(
    connection: Connection,
    audit_schema: str | None,
    *,
    verification_result_count: int,
    artifact_reference_count: int,
) -> int:
    count = verification_result_count + artifact_reference_count
    for operation_ids in _phase11_id_batches(connection, "completion_operations"):
        count += _phase11_downgrade_blocking_count(
            connection,
            audit_schema,
            operation_ids=operation_ids,
            include_evidence_rows=False,
        )
    return count


def _phase11_batched_completion_inventory(connection: Connection) -> tuple[int, int]:
    completion_count = 0
    structured_count = 0
    for event_ids in _phase11_id_batches(connection, "completion_events"):
        completion_count += len(event_ids)
        structured_count += _scalar(
            connection,
            """
            /* phase11-audit-batch-candidates */
            SELECT pg_catalog.count(*)
            FROM work_events AS event
            WHERE event.id = ANY(CAST(:audit_ids AS bigint[]))
              AND (
                  EXISTS (
                      SELECT 1 FROM verification_results AS result
                      WHERE result.work_item_id = event.work_item_id
                        AND result.completion_checkpoint_id = event.checkpoint_id
                  )
                  OR EXISTS (
                      SELECT 1 FROM artifact_references AS artifact
                      WHERE artifact.work_item_id = event.work_item_id
                        AND artifact.completion_checkpoint_id = event.checkpoint_id
                  )
              )
            """,
            {"audit_ids": list(event_ids)},
        )
    return completion_count, structured_count


def _phase11_batched_unsealed_count(connection: Connection) -> int:
    count = 0
    for checkpoint_ids in _phase11_id_batches(connection, "completion_checkpoints"):
        count += _scalar(
            connection,
            """
            /* phase11-audit-batch-candidates */
            SELECT pg_catalog.count(*)
            FROM checkpoints AS checkpoint
            WHERE checkpoint.id = ANY(CAST(:audit_ids AS uuid[]))
              AND NOT mnemonic_completion_episode_is_sealed(
                  checkpoint.work_item_id, checkpoint.completion_generation
              )
            """,
            {"audit_ids": list(checkpoint_ids)},
        )
    return count


def _phase11_batched_violation_count(
    connection: Connection,
    scan: str,
    statement: str,
    *,
    id_type: str,
) -> int:
    if id_type not in {"bigint", "uuid"}:
        raise ValueError("Unknown trusted Phase 11 audit ID type")
    count = 0
    bounded_statement = (
        "/* phase11-audit-batch-candidates */\n"
        + statement.replace("<audit-id-type>", id_type)
    )
    for record_ids in _phase11_id_batches(connection, scan):
        count += _scalar(
            connection,
            bounded_statement,
            {"audit_ids": list(record_ids)},
        )
    return count


def _phase11_batched_checkpoint_event_violation_count(connection: Connection) -> int:
    checkpoint_violations = _phase11_batched_violation_count(
        connection,
        "completion_checkpoints",
        """
        SELECT pg_catalog.count(*)
        FROM (
            SELECT checkpoint.id
            FROM checkpoints AS checkpoint
            LEFT JOIN work_events AS event
              ON event.work_item_id = checkpoint.work_item_id
             AND event.checkpoint_id = checkpoint.id
             AND event.event_type = 'work_completed'
            WHERE checkpoint.id = ANY(CAST(:audit_ids AS <audit-id-type>[]))
            GROUP BY checkpoint.id
            HAVING pg_catalog.count(event.id) <> 1
        ) AS violations
        """,
        id_type="uuid",
    )
    event_violations = _phase11_batched_violation_count(
        connection,
        "completion_events",
        """
        SELECT pg_catalog.count(*)
        FROM work_events AS event
        LEFT JOIN checkpoints AS checkpoint
          ON checkpoint.work_item_id = event.work_item_id
         AND checkpoint.id = event.checkpoint_id
         AND checkpoint.kind = 'completion'
        WHERE event.id = ANY(CAST(:audit_ids AS <audit-id-type>[]))
          AND checkpoint.id IS NULL
        """,
        id_type="bigint",
    )
    return checkpoint_violations + event_violations


def _phase11_batched_evidence_owner_violation_count(connection: Connection) -> int:
    result_violations = _phase11_batched_violation_count(
        connection,
        "verification_results",
        """
        SELECT pg_catalog.count(*)
        FROM verification_results AS result
        LEFT JOIN work_items AS work
          ON work.project_id = result.project_id
         AND work.id = result.work_item_id
        LEFT JOIN checkpoints AS checkpoint
          ON checkpoint.work_item_id = result.work_item_id
         AND checkpoint.id = result.completion_checkpoint_id
        WHERE result.id = ANY(CAST(:audit_ids AS <audit-id-type>[]))
          AND (
              work.id IS NULL OR checkpoint.id IS NULL
              OR checkpoint.kind <> 'completion'
              OR result.created_at IS DISTINCT FROM checkpoint.created_at
          )
        """,
        id_type="uuid",
    )
    artifact_violations = _phase11_batched_violation_count(
        connection,
        "artifact_references",
        """
        SELECT pg_catalog.count(*)
        FROM artifact_references AS artifact
        LEFT JOIN work_items AS work
          ON work.project_id = artifact.project_id
         AND work.id = artifact.work_item_id
        LEFT JOIN checkpoints AS checkpoint
          ON checkpoint.work_item_id = artifact.work_item_id
         AND checkpoint.id = artifact.completion_checkpoint_id
        WHERE artifact.id = ANY(CAST(:audit_ids AS <audit-id-type>[]))
          AND (
              work.id IS NULL OR checkpoint.id IS NULL
              OR checkpoint.kind <> 'completion'
              OR artifact.created_at IS DISTINCT FROM checkpoint.created_at
          )
        """,
        id_type="uuid",
    )
    return result_violations + artifact_violations


def _phase11_batched_generation_violation_count(connection: Connection) -> int:
    work_violations = _phase11_batched_violation_count(
        connection,
        "work_items",
        """
        SELECT pg_catalog.count(*)
        FROM work_items AS work
        WHERE work.id = ANY(CAST(:audit_ids AS <audit-id-type>[]))
          AND (
              work.completion_generation < -9223372036854775806
              OR (work.completion_generation < 0 AND work.status <> 'done')
              OR (
                  work.status = 'done'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM checkpoints AS completion
                      WHERE completion.work_item_id = work.id
                        AND completion.kind = 'completion'
                        AND completion.completion_generation = work.completion_generation
                  )
              )
              OR (
                  work.status = 'done'
                  AND work.completion_generation < 0
                  AND work.completion_generation::numeric IS DISTINCT FROM -(
                      SELECT pg_catalog.max(event.id)::numeric
                      FROM work_events AS event
                      WHERE event.work_item_id = work.id
                        AND event.event_type = 'work_completed'
                  )
              )
              OR (
                  work.status <> 'done'
                  AND EXISTS (
                      SELECT 1
                      FROM checkpoints AS completion
                      WHERE completion.work_item_id = work.id
                        AND completion.kind = 'completion'
                        AND completion.completion_generation = work.completion_generation
                  )
              )
          )
        """,
        id_type="uuid",
    )
    checkpoint_violations = _phase11_batched_violation_count(
        connection,
        "checkpoints",
        """
        SELECT pg_catalog.count(*)
        FROM checkpoints AS checkpoint
        LEFT JOIN work_items AS work ON work.id = checkpoint.work_item_id
        WHERE checkpoint.id = ANY(CAST(:audit_ids AS <audit-id-type>[]))
          AND (
              work.id IS NULL
              OR (checkpoint.kind = 'completion')
                    IS DISTINCT FROM (checkpoint.completion_generation IS NOT NULL)
              OR (
                  checkpoint.completion_generation >= 0
                  AND checkpoint.completion_generation > work.completion_generation
              )
              OR (
                  checkpoint.completion_generation < 0
                  AND NOT EXISTS (
                      SELECT 1
                      FROM work_events AS completion
                      WHERE completion.work_item_id = checkpoint.work_item_id
                        AND completion.checkpoint_id = checkpoint.id
                        AND completion.event_type = 'work_completed'
                        AND checkpoint.completion_generation::numeric
                              = -completion.id::numeric
                  )
              )
          )
        """,
        id_type="uuid",
    )
    return work_violations + checkpoint_violations


def _phase11_batched_reopen_binding_violation_count(connection: Connection) -> int:
    event_violations = _phase11_batched_violation_count(
        connection,
        "work_events",
        """
        SELECT pg_catalog.count(*)
        FROM work_events AS event
        LEFT JOIN work_items AS work ON work.id = event.work_item_id
        WHERE event.id = ANY(CAST(:audit_ids AS <audit-id-type>[]))
          AND (
              (event.event_type = 'work_reopened')
                    IS DISTINCT FROM (event.reopen_generation IS NOT NULL)
              OR event.reopen_generation = 0
              OR (
                  event.reopen_generation < 0
                  AND event.reopen_generation::numeric
                        IS DISTINCT FROM -event.id::numeric
              )
              OR (
                  event.reopen_generation > 0
                  AND (
                      work.id IS NULL
                      OR event.project_id IS DISTINCT FROM work.project_id
                      OR event.origin IS DISTINCT FROM 'live'
                      OR event.reopen_generation > work.completion_generation
                      OR pg_catalog.jsonb_typeof(event.metadata -> 'work_version')
                            IS DISTINCT FROM 'number'
                      OR event.metadata ->> 'work_version' !~ '^[1-9][0-9]*$'
                      OR (
                          event.metadata ->> 'from_status'
                              IN ('done', 'deferred', 'wont-do', 'promoted')
                      ) IS NOT TRUE
                      OR event.metadata ->> 'to_status' IS DISTINCT FROM 'pending'
                      OR event.metadata -> 'changes' -> 'status' ->> 'before'
                            IS DISTINCT FROM event.metadata ->> 'from_status'
                      OR event.metadata -> 'changes' -> 'status' ->> 'after'
                            IS DISTINCT FROM 'pending'
                  )
              )
          )
        """,
        id_type="bigint",
    )
    prefix_violations = _phase11_batched_violation_count(
        connection,
        "work_items",
        """
        SELECT pg_catalog.count(*)
        FROM work_items AS work
        LEFT JOIN LATERAL (
            SELECT pg_catalog.count(*) AS binding_count,
                   pg_catalog.count(DISTINCT event.reopen_generation)
                       AS distinct_binding_count,
                   pg_catalog.min(event.reopen_generation) AS minimum_generation,
                   pg_catalog.max(event.reopen_generation) AS maximum_generation
            FROM work_events AS event
            WHERE event.work_item_id = work.id
              AND event.event_type = 'work_reopened'
              AND event.reopen_generation > 0
        ) AS bindings ON true
        WHERE work.id = ANY(CAST(:audit_ids AS <audit-id-type>[]))
          AND work.completion_generation > 0
          AND (
              bindings.binding_count <> work.completion_generation
              OR bindings.distinct_binding_count <> work.completion_generation
              OR bindings.minimum_generation <> 1
              OR bindings.maximum_generation <> work.completion_generation
          )
        """,
        id_type="uuid",
    )
    completion_reopen_order_violations = _phase11_batched_violation_count(
        connection,
        "completion_events",
        """
        SELECT pg_catalog.count(*)
        FROM work_events AS completion
        JOIN checkpoints AS checkpoint
          ON checkpoint.work_item_id = completion.work_item_id
         AND checkpoint.id = completion.checkpoint_id
         AND checkpoint.kind = 'completion'
        JOIN work_events AS reopen
          ON reopen.work_item_id = completion.work_item_id
         AND reopen.event_type = 'work_reopened'
         AND reopen.reopen_generation = checkpoint.completion_generation
        WHERE completion.id = ANY(CAST(:audit_ids AS <audit-id-type>[]))
          AND checkpoint.completion_generation > 0
          AND (
              pg_catalog.jsonb_typeof(completion.metadata -> 'work_version')
                    IS DISTINCT FROM 'number'
              OR pg_catalog.jsonb_typeof(reopen.metadata -> 'work_version')
                    IS DISTINCT FROM 'number'
              OR completion.metadata ->> 'work_version' !~ '^[1-9][0-9]*$'
              OR reopen.metadata ->> 'work_version' !~ '^[1-9][0-9]*$'
              OR CASE
                  WHEN pg_catalog.jsonb_typeof(
                           completion.metadata -> 'work_version'
                       ) = 'number'
                   AND pg_catalog.jsonb_typeof(
                           reopen.metadata -> 'work_version'
                       ) = 'number'
                   AND completion.metadata ->> 'work_version' ~ '^[1-9][0-9]*$'
                   AND reopen.metadata ->> 'work_version' ~ '^[1-9][0-9]*$'
                  THEN (completion.metadata ->> 'work_version')::numeric
                       <= (reopen.metadata ->> 'work_version')::numeric
                  ELSE true
                 END
          )
        """,
        id_type="bigint",
    )
    successor_violations = _phase11_batched_violation_count(
        connection,
        "completion_events",
        """
        SELECT pg_catalog.count(*)
        FROM work_events AS completion
        JOIN checkpoints AS checkpoint
          ON checkpoint.work_item_id = completion.work_item_id
         AND checkpoint.id = completion.checkpoint_id
         AND checkpoint.kind = 'completion'
        JOIN work_items AS work ON work.id = completion.work_item_id
        LEFT JOIN work_events AS successor
          ON successor.work_item_id = completion.work_item_id
         AND successor.event_type = 'work_reopened'
         AND successor.reopen_generation = checkpoint.completion_generation + 1
        WHERE completion.id = ANY(CAST(:audit_ids AS <audit-id-type>[]))
          AND checkpoint.completion_generation >= 0
          AND checkpoint.completion_generation < work.completion_generation
          AND (
              successor.id IS NULL
              OR pg_catalog.jsonb_typeof(completion.metadata -> 'work_version')
                    IS DISTINCT FROM 'number'
              OR pg_catalog.jsonb_typeof(successor.metadata -> 'work_version')
                    IS DISTINCT FROM 'number'
              OR completion.metadata ->> 'work_version' !~ '^[1-9][0-9]*$'
              OR successor.metadata ->> 'work_version' !~ '^[1-9][0-9]*$'
              OR CASE
                  WHEN pg_catalog.jsonb_typeof(
                           completion.metadata -> 'work_version'
                       ) = 'number'
                   AND pg_catalog.jsonb_typeof(
                           successor.metadata -> 'work_version'
                       ) = 'number'
                   AND completion.metadata ->> 'work_version' ~ '^[1-9][0-9]*$'
                   AND successor.metadata ->> 'work_version' ~ '^[1-9][0-9]*$'
                  THEN (completion.metadata ->> 'work_version')::numeric
                       >= (successor.metadata ->> 'work_version')::numeric
                  ELSE true
                 END
          )
        """,
        id_type="bigint",
    )
    return (
        event_violations
        + prefix_violations
        + completion_reopen_order_violations
        + successor_violations
    )


def _phase11_batched_completion_event_id_violation_count(connection: Connection) -> int:
    return _phase11_batched_violation_count(
        connection,
        "lifecycle_events",
        """
        SELECT pg_catalog.count(*)
        FROM work_events AS event
        WHERE event.id = ANY(CAST(:audit_ids AS <audit-id-type>[]))
          AND event.id NOT BETWEEN 1 AND 9223372036854775806
        """,
        id_type="bigint",
    )


def _phase11_batched_receipt_violation_count(connection: Connection) -> int:
    receipt_violation_count = 0
    for operation_ids in _phase11_id_batches(connection, "completion_operations"):
        receipt_violation_count += _scalar(
            connection,
            r"""
                    /* phase11-audit-batch-candidates */
                    WITH completion_receipts AS (
                        SELECT operation.response_body,
                               operation.response_body #>> '{work_item,id}'
                                   AS work_item_id,
                               operation.response_body #>> '{checkpoint,id}'
                                   AS checkpoint_id,
                               (operation.response_body ? 'completion_evidence') IS TRUE
                                   AS has_evidence
                        FROM client_operations AS operation
                        WHERE operation.id = ANY(CAST(:audit_ids AS bigint[]))
                          AND operation.operation_kind = 'complete_work'
                          AND operation.state = 'completed'
                    ), receipt_targets AS (
                        SELECT DISTINCT
                               CASE WHEN receipt.work_item_id
                                    ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
                                    THEN CAST(receipt.work_item_id AS uuid)
                               END AS work_item_id,
                               CASE WHEN receipt.checkpoint_id
                                    ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
                                    THEN CAST(receipt.checkpoint_id AS uuid)
                               END AS completion_checkpoint_id
                        FROM completion_receipts AS receipt
                    ), verification_json AS (
                        SELECT result.work_item_id, result.completion_checkpoint_id,
                               result.position,
                               pg_catalog.jsonb_strip_nulls(
                                   (pg_catalog.to_jsonb(result) - 'project_id')
                                   || pg_catalog.jsonb_build_object(
                                       'created_at', pg_catalog.regexp_replace(
                                           pg_catalog.to_char(
                                               pg_catalog.timezone(
                                                   'UTC', result.created_at
                                               ),
                                               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                           ),
                                           '[.]000000Z$',
                                           'Z'
                                       ),
                                       'observed_at', CASE
                                           WHEN result.observed_at IS NULL THEN NULL
                                           ELSE pg_catalog.regexp_replace(
                                               pg_catalog.to_char(
                                                   pg_catalog.timezone(
                                                       'UTC', result.observed_at
                                                   ),
                                                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                               ),
                                               '[.]000000Z$',
                                               'Z'
                                           )
                                       END
                                   )
                               ) AS payload
                        FROM verification_results AS result
                        JOIN receipt_targets AS target
                          ON target.work_item_id = result.work_item_id
                         AND target.completion_checkpoint_id
                                = result.completion_checkpoint_id
                    ), artifact_json AS (
                        SELECT artifact.work_item_id,
                               artifact.completion_checkpoint_id,
                               artifact.position,
                               (pg_catalog.to_jsonb(artifact) - 'project_id')
                               || pg_catalog.jsonb_build_object(
                                   'created_at', pg_catalog.regexp_replace(
                                       pg_catalog.to_char(
                                           pg_catalog.timezone(
                                               'UTC', artifact.created_at
                                           ),
                                           'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                       ),
                                       '[.]000000Z$',
                                       'Z'
                                   )
                               ) AS payload
                        FROM artifact_references AS artifact
                        JOIN receipt_targets AS target
                          ON target.work_item_id = artifact.work_item_id
                         AND target.completion_checkpoint_id
                                = artifact.completion_checkpoint_id
                    ), evidence_checkpoints AS (
                        SELECT work_item_id, completion_checkpoint_id
                        FROM verification_json
                        UNION
                        SELECT work_item_id, completion_checkpoint_id
                        FROM artifact_json
                    ), canonical_evidence AS (
                        SELECT evidence.work_item_id,
                               evidence.completion_checkpoint_id,
                               pg_catalog.jsonb_build_object(
                                   'verification_results', COALESCE((
                                       SELECT pg_catalog.jsonb_agg(
                                           result.payload ORDER BY result.position
                                       )
                                       FROM verification_json AS result
                                       WHERE result.work_item_id = evidence.work_item_id
                                         AND result.completion_checkpoint_id
                                             = evidence.completion_checkpoint_id
                                   ), '[]'::jsonb),
                                   'artifact_references', COALESCE((
                                       SELECT pg_catalog.jsonb_agg(
                                           artifact.payload ORDER BY artifact.position
                                       )
                                       FROM artifact_json AS artifact
                                       WHERE artifact.work_item_id = evidence.work_item_id
                                         AND artifact.completion_checkpoint_id
                                             = evidence.completion_checkpoint_id
                                   ), '[]'::jsonb)
                               ) AS payload
                        FROM evidence_checkpoints AS evidence
                    ), evaluated_receipts AS (
                        SELECT receipt.has_evidence,
                               evidence.work_item_id,
                               evidence.completion_checkpoint_id,
                               (
                                   pg_catalog.jsonb_typeof(receipt.response_body)
                                       IS DISTINCT FROM 'object'
                                   OR pg_catalog.jsonb_typeof(
                                          receipt.response_body -> 'work_item'
                                      ) IS DISTINCT FROM 'object'
                                   OR pg_catalog.jsonb_typeof(
                                          receipt.response_body -> 'checkpoint'
                                      ) IS DISTINCT FROM 'object'
                                   OR pg_catalog.jsonb_typeof(
                                          receipt.response_body #> '{work_item,id}'
                                      ) IS DISTINCT FROM 'string'
                                   OR pg_catalog.jsonb_typeof(
                                          receipt.response_body #> '{checkpoint,id}'
                                      ) IS DISTINCT FROM 'string'
                                   OR (
                                       receipt.has_evidence
                                       AND (
                                           evidence.completion_checkpoint_id IS NULL
                                           OR pg_catalog.jsonb_typeof(
                                                  receipt.response_body
                                                      -> 'completion_evidence'
                                              ) IS DISTINCT FROM 'object'
                                           OR (
                                               receipt.response_body
                                                   -> 'completion_evidence'
                                           )::text IS DISTINCT FROM evidence.payload::text
                                       )
                                   )
                                   OR (
                                       NOT receipt.has_evidence
                                       AND evidence.completion_checkpoint_id IS NOT NULL
                                   )
                                   ) AS violates
                        FROM completion_receipts AS receipt
                        LEFT JOIN canonical_evidence AS evidence
                          ON evidence.work_item_id::text = receipt.work_item_id
                         AND evidence.completion_checkpoint_id::text
                                = receipt.checkpoint_id
                    )
                    SELECT pg_catalog.count(*)
                    FROM evaluated_receipts AS receipt
                    WHERE receipt.violates
            """,
            {"audit_ids": list(operation_ids)},
        )
    return receipt_violation_count


def _phase11_batched_evidence_receipt_violation_count(
    connection: Connection,
) -> int:
    violation_count = 0
    representative_scans = (
        (
            "verification_results",
            """
            SELECT result.completion_checkpoint_id
            FROM verification_results AS result
            WHERE result.id = ANY(CAST(:audit_ids AS uuid[]))
              AND NOT EXISTS (
                  SELECT 1
                  FROM verification_results AS prior
                  WHERE prior.completion_checkpoint_id = result.completion_checkpoint_id
                    AND prior.id < result.id
              )
            """,
        ),
        (
            "artifact_references",
            """
            SELECT artifact.completion_checkpoint_id
            FROM artifact_references AS artifact
            WHERE artifact.id = ANY(CAST(:audit_ids AS uuid[]))
              AND NOT EXISTS (
                  SELECT 1
                  FROM verification_results AS result
                  WHERE result.completion_checkpoint_id
                        = artifact.completion_checkpoint_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM artifact_references AS prior
                  WHERE prior.completion_checkpoint_id
                        = artifact.completion_checkpoint_id
                    AND prior.id < artifact.id
              )
            """,
        ),
    )
    for scan, representative_select in representative_scans:
        for evidence_ids in _phase11_id_batches(connection, scan):
            violation_count += _scalar(
                connection,
                rf"""
                /* phase11-audit-batch-candidates */
                WITH representatives AS (
                    {representative_select}
                ), verification_json AS (
                    SELECT result.work_item_id, result.completion_checkpoint_id,
                           result.position,
                           pg_catalog.jsonb_strip_nulls(
                               (pg_catalog.to_jsonb(result) - 'project_id')
                               || pg_catalog.jsonb_build_object(
                                   'created_at', pg_catalog.regexp_replace(
                                       pg_catalog.to_char(
                                           pg_catalog.timezone('UTC', result.created_at),
                                           'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                       ),
                                       '[.]000000Z$',
                                       'Z'
                                   ),
                                   'observed_at', CASE
                                       WHEN result.observed_at IS NULL THEN NULL
                                       ELSE pg_catalog.regexp_replace(
                                           pg_catalog.to_char(
                                               pg_catalog.timezone(
                                                   'UTC', result.observed_at
                                               ),
                                               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                           ),
                                           '[.]000000Z$',
                                           'Z'
                                       )
                                   END
                               )
                           ) AS payload
                    FROM verification_results AS result
                    JOIN representatives AS representative
                      ON representative.completion_checkpoint_id
                            = result.completion_checkpoint_id
                ), artifact_json AS (
                    SELECT artifact.work_item_id,
                           artifact.completion_checkpoint_id,
                           artifact.position,
                           (pg_catalog.to_jsonb(artifact) - 'project_id')
                           || pg_catalog.jsonb_build_object(
                               'created_at', pg_catalog.regexp_replace(
                                   pg_catalog.to_char(
                                       pg_catalog.timezone('UTC', artifact.created_at),
                                       'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                   ),
                                   '[.]000000Z$',
                                   'Z'
                               )
                           ) AS payload
                    FROM artifact_references AS artifact
                    JOIN representatives AS representative
                      ON representative.completion_checkpoint_id
                            = artifact.completion_checkpoint_id
                ), evidence_owners AS (
                    SELECT result.work_item_id, result.completion_checkpoint_id
                    FROM verification_json AS result
                    UNION
                    SELECT artifact.work_item_id, artifact.completion_checkpoint_id
                    FROM artifact_json AS artifact
                ), canonical_evidence AS (
                    SELECT evidence.work_item_id,
                           evidence.completion_checkpoint_id,
                           pg_catalog.jsonb_build_object(
                               'verification_results', COALESCE((
                                   SELECT pg_catalog.jsonb_agg(
                                       result.payload ORDER BY result.position
                                   )
                                   FROM verification_json AS result
                                   WHERE result.work_item_id
                                             = evidence.work_item_id
                                     AND result.completion_checkpoint_id
                                             = evidence.completion_checkpoint_id
                               ), '[]'::jsonb),
                               'artifact_references', COALESCE((
                                   SELECT pg_catalog.jsonb_agg(
                                       artifact.payload ORDER BY artifact.position
                                   )
                                   FROM artifact_json AS artifact
                                   WHERE artifact.work_item_id
                                             = evidence.work_item_id
                                     AND artifact.completion_checkpoint_id
                                             = evidence.completion_checkpoint_id
                               ), '[]'::jsonb)
                           ) AS payload
                    FROM evidence_owners AS evidence
                ), receipt_match_classes AS (
                    SELECT representative.completion_checkpoint_id,
                           (
                               SELECT pg_catalog.count(*)
                               FROM (
                                   SELECT 1
                                   FROM canonical_evidence AS evidence
                                   JOIN client_operations AS operation
                                     ON operation.response_body
                                            #>> '{{work_item,id}}'
                                            = evidence.work_item_id::text
                                    AND operation.response_body
                                            #>> '{{checkpoint,id}}'
                                            = evidence.completion_checkpoint_id::text
                                    AND (
                                        operation.response_body
                                            ? 'completion_evidence'
                                    ) IS TRUE
                                    AND pg_catalog.jsonb_typeof(
                                            operation.response_body
                                                -> 'completion_evidence'
                                        ) = 'object'
                                    AND (
                                        operation.response_body
                                            -> 'completion_evidence'
                                    )::text = evidence.payload::text
                                   WHERE evidence.completion_checkpoint_id
                                            = representative.completion_checkpoint_id
                                     AND operation.operation_kind = 'complete_work'
                                     AND operation.state = 'completed'
                                   LIMIT 2
                               ) AS capped_receipt_matches
                           ) AS capped_match_count
                    FROM representatives AS representative
                )
                /* phase11-audit-reverse-receipt-classification */
                SELECT pg_catalog.count(*)
                FROM receipt_match_classes AS receipt_match
                WHERE receipt_match.capped_match_count <> 1
                """,
                {"audit_ids": list(evidence_ids)},
            )
    return violation_count


def _phase11_batched_receipt_evidence_correspondence_violation_count(
    connection: Connection,
) -> int:
    return (
        _phase11_batched_receipt_violation_count(connection)
        + _phase11_batched_evidence_receipt_violation_count(connection)
    )


def _ordinary_table_exists(connection: Connection, name: str, *, schema: str | None = None) -> bool:
    selected_schema = schema or connection.scalar(text("SELECT pg_catalog.current_schema()"))
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


def _work_event_sequence_violation(connection: Connection) -> int:
    """Check identity and the once-sampled, non-MVCC sequence allocation state."""
    sequence = connection.scalar(
        text("SELECT pg_catalog.pg_get_serial_sequence('work_events', 'id')")
    )
    if not isinstance(sequence, str):
        return 1
    parameters = connection.execute(
        text(
            """
            SELECT seq.seqstart, seq.seqincrement, seq.seqmax,
                   seq.seqmin, seq.seqcycle, seq.seqcache
            FROM pg_catalog.pg_sequence AS seq
            WHERE seq.seqrelid = CAST(:sequence AS regclass)
            """
        ),
        {"sequence": sequence},
    ).one_or_none()
    state = connection.execute(text(f"SELECT last_value, is_called FROM {sequence}")).one_or_none()
    maximum = connection.scalar(
        text(
            """
            /* phase11-audit-high-water */
            SELECT id
            FROM work_events
            ORDER BY id DESC
            LIMIT 1
            """
        )
    )
    if parameters is None or state is None:
        return 1
    next_value = state.last_value + (parameters.seqincrement if state.is_called else 0)
    return int(
        parameters.seqstart != 1
        or parameters.seqincrement != 1
        or parameters.seqmin != 1
        or parameters.seqmax != 9223372036854775807
        or parameters.seqcycle
        or parameters.seqcache != 1
        or next_value < 1
        or next_value > 9223372036854775806
        or (maximum is not None and next_value <= maximum)
    )


def _completion_evidence_counts(
    connection: Connection, audit_schema: str | None = None
) -> dict[str, int]:
    """Return content-free Phase 11 counts from one keyset-paged snapshot."""
    completion_episode_count, structured_completion_episode_count = (
        _phase11_batched_completion_inventory(connection)
    )
    verification_result_count = _phase11_batched_table_count(
        connection, "verification_results"
    )
    artifact_reference_count = _phase11_batched_table_count(
        connection, "artifact_references"
    )
    return {
        "completion_episode_count": completion_episode_count,
        "structured_completion_episode_count": structured_completion_episode_count,
        "empty_completion_episode_count": (
            completion_episode_count - structured_completion_episode_count
        ),
        "verification_result_count": verification_result_count,
        "artifact_reference_count": artifact_reference_count,
        "phase11_downgrade_blocking_count": (
            _phase11_batched_downgrade_blocking_count(
                connection,
                audit_schema,
                verification_result_count=verification_result_count,
                artifact_reference_count=artifact_reference_count,
            )
        ),
        "unsealed_completion_episode_count": _phase11_batched_unsealed_count(connection),
        "completion_checkpoint_event_violation_count": (
            _phase11_batched_checkpoint_event_violation_count(connection)
        ),
        "evidence_owner_violation_count": (
            _phase11_batched_evidence_owner_violation_count(connection)
        ),
        "completion_generation_violation_count": (
            _phase11_batched_generation_violation_count(connection)
        ),
        "reopen_binding_violation_count": (
            _phase11_batched_reopen_binding_violation_count(connection)
        ),
        "completion_event_id_violation_count": (
            _phase11_batched_completion_event_id_violation_count(connection)
        ),
        "work_event_identity_sequence_violation_count": (
            _work_event_sequence_violation(connection)
        ),
        "receipt_evidence_correspondence_violation_count": (
            _phase11_batched_receipt_evidence_correspondence_violation_count(connection)
        ),
    }


def _core_counts(connection: Connection) -> dict[str, int]:
    return {
        "authoritative_merges": _scalar(connection, "SELECT count(*) FROM work_duplicate_merges"),
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
    if expected_head in {
        CORE_HEAD,
        ADVISORY_HEAD,
        REPOSITORY_FRESHNESS_HEAD,
        FINAL_HEAD,
    }:
        required.update(CORE_FUNCTIONS)
    if expected_head in {ADVISORY_HEAD, REPOSITORY_FRESHNESS_HEAD, FINAL_HEAD}:
        required.update(ADVISORY_FUNCTIONS)
    if expected_head in {REPOSITORY_FRESHNESS_HEAD, FINAL_HEAD}:
        required.update(REPOSITORY_FRESHNESS_FUNCTIONS)
    if expected_head == FINAL_HEAD:
        required.update(COMPLETION_EVIDENCE_FUNCTIONS)
    return frozenset(required)


def _required_tables(expected_head: str) -> frozenset[str]:
    required: set[str] = set()
    if expected_head in {
        CORE_HEAD,
        ADVISORY_HEAD,
        REPOSITORY_FRESHNESS_HEAD,
        FINAL_HEAD,
    }:
        required.update(CORE_TABLES)
    if expected_head == FINAL_HEAD:
        required.update(COMPLETION_EVIDENCE_TABLES)
    return frozenset(required)


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


def _checkpoint_immutability_catalog_failure_count(connection: Connection, schema: str) -> int:
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


def _phase11_catalog_digest(
    connection: Connection,
    audit_schema: str,
    statement: str,
    *,
    names: list[str] | None = None,
) -> str:
    parameters: dict[str, object] = {"audit_schema": audit_schema}
    if names is not None:
        parameters["names"] = names
    rows = [
        [_normalize_phase11_catalog_value(value, audit_schema) for value in row]
        for row in connection.execute(text(statement), parameters)
    ]
    canonical = json.dumps(
        rows,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_phase11_catalog_value(value: object, audit_schema: str) -> object:
    """Normalize schema identifiers without rewriting coincidental substrings."""
    if isinstance(value, (list, tuple)):
        return [_normalize_phase11_catalog_value(item, audit_schema) for item in value]
    if not isinstance(value, str):
        return value
    quoted_schema = '"' + audit_schema.replace('"', '""') + '"'
    normalized = value.replace(f"{quoted_schema}.", "<schema>.")
    normalized = normalized.replace(
        f"{quoted_schema.replace("'", "''")}.", "<schema>."
    )
    if re.fullmatch(r"[a-z_][a-z0-9_$]*", audit_schema):
        normalized = re.sub(
            rf'(?<![A-Za-z0-9_$"]){re.escape(audit_schema)}\.',
            "<schema>.",
            normalized,
        )
    if normalized.startswith("search_path="):
        entries = normalized.removeprefix("search_path=").split(",")
        normalized_entries = [
            "<schema>" if entry.strip() in {audit_schema, quoted_schema} else entry.strip()
            for entry in entries
        ]
        normalized = "search_path=" + ",".join(normalized_entries)
    return normalized


def _phase11_exact_catalog_failures(connection: Connection, audit_schema: str) -> dict[str, int]:
    digests = {
        "relations": _phase11_catalog_digest(
            connection,
            audit_schema,
            """
            SELECT relation.relname, relation.relkind, relation.relpersistence,
                   relation.relispartition, relation.relrowsecurity,
                   relation.relforcerowsecurity, relation.relreplident,
                   relation.relowner = (
                       SELECT role.oid FROM pg_catalog.pg_roles AS role
                       WHERE role.rolname = CURRENT_USER
                   ),
                   (
                       SELECT pg_catalog.count(*) = 8
                          AND pg_catalog.bool_and(
                                  acl.grantee = relation.relowner
                                  AND acl.grantor = relation.relowner
                                  AND NOT acl.is_grantable
                              )
                          AND pg_catalog.array_agg(
                                  acl.privilege_type
                                  ORDER BY acl.privilege_type
                              ) = ARRAY[
                                  'DELETE', 'INSERT', 'MAINTAIN', 'REFERENCES',
                                  'SELECT', 'TRIGGER', 'TRUNCATE', 'UPDATE'
                              ]::text[]
                       FROM pg_catalog.aclexplode(
                           COALESCE(
                               relation.relacl,
                               pg_catalog.acldefault('r', relation.relowner)
                           )
                       ) AS acl
                   ),
                   COALESCE(relation.reloptions, ARRAY[]::text[]),
                   access_method.amname,
                   COALESCE(tablespace.spcname, '')
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_am AS access_method
              ON access_method.oid = relation.relam
            LEFT JOIN pg_catalog.pg_tablespace AS tablespace
              ON tablespace.oid = relation.reltablespace
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND relation.relname = ANY(:names)
            ORDER BY relation.relname
            """,
            names=sorted(COMPLETION_EVIDENCE_TABLES),
        ),
        "columns": _phase11_catalog_digest(
            connection,
            audit_schema,
            """
            SELECT relation.relname, attribute.attname,
                   pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
                   attribute.attnotnull,
                   COALESCE(
                       pg_catalog.pg_get_expr(
                           default_value.adbin, default_value.adrelid, true
                       ),
                       ''
                   ),
                   attribute.attidentity, attribute.attgenerated,
                   COALESCE(attribute.attacl::text, ''),
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
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_attribute AS attribute
              ON attribute.attrelid = relation.oid
            LEFT JOIN pg_catalog.pg_attrdef AS default_value
              ON default_value.adrelid = relation.oid
             AND default_value.adnum = attribute.attnum
            LEFT JOIN pg_catalog.pg_collation AS collation_value
              ON collation_value.oid = attribute.attcollation
            LEFT JOIN pg_catalog.pg_namespace AS collation_namespace
              ON collation_namespace.oid = collation_value.collnamespace
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND NOT attribute.attisdropped
              AND attribute.attnum > 0
              AND (
                  relation.relname IN ('verification_results', 'artifact_references')
                  OR (
                      relation.relname = 'work_items'
                      AND attribute.attname = 'completion_generation'
                  )
                  OR (
                      relation.relname = 'checkpoints'
                      AND attribute.attname = 'completion_generation'
                  )
                  OR (
                      relation.relname = 'work_events'
                      AND attribute.attname = 'reopen_generation'
                  )
              )
            ORDER BY relation.relname, attribute.attname
            """,
        ),
        "constraints": _phase11_catalog_digest(
            connection,
            audit_schema,
            """
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
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND (
                  relation.relname IN ('verification_results', 'artifact_references')
                  OR constraint_value.conname IN (
                      'ck_work_items_completion_generation_range',
                      'ck_checkpoints_completion_generation_kind',
                      'ck_work_events_reopen_generation_kind'
                  )
              )
            ORDER BY relation.relname, constraint_value.conname
            """,
        ),
        "indexes": _phase11_catalog_digest(
            connection,
            audit_schema,
            """
            SELECT table_relation.relname, index_relation.relname,
                   access_method.amname, index_relation.relpersistence,
                   index_relation.relispartition,
                   index_relation.relowner = (
                       SELECT role.oid FROM pg_catalog.pg_roles AS role
                       WHERE role.rolname = CURRENT_USER
                   ),
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
                   COALESCE(
                       pg_catalog.pg_get_expr(
                           index_value.indpred, index_value.indrelid, true
                       ),
                       ''
                   )
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
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND (
                  index_relation.relname = ANY(:names)
                  OR table_relation.relname IN (
                      'verification_results', 'artifact_references'
                  )
              )
            ORDER BY index_relation.relname
            """,
            names=PHASE11_ALL_INDEXES,
        ),
        "triggers": _phase11_catalog_digest(
            connection,
            audit_schema,
            """
            SELECT relation.relname,
                   CASE
                       WHEN trigger_value.tgisinternal THEN
                           'constraint:' || trigger_constraint.conname || ':'
                           || relation.relname || ':' || procedure.proname || ':'
                           || trigger_value.tgtype::text
                       ELSE trigger_value.tgname
                   END,
                   trigger_value.tgenabled, trigger_value.tgtype,
                   trigger_value.tgisinternal, trigger_value.tgdeferrable,
                   trigger_value.tginitdeferred, trigger_value.tgnargs,
                   pg_catalog.encode(trigger_value.tgargs, 'hex'),
                   trigger_value.tgqual IS NOT NULL,
                   COALESCE(constraint_relation.relname, ''),
                   COALESCE(referenced_relation.relname, ''),
                   COALESCE(trigger_constraint.contype::text, ''),
                   COALESCE(trigger_constraint.condeferrable, false),
                   COALESCE(trigger_constraint.condeferred, false),
                   trigger_value.tgparentid = 0,
                   procedure_namespace.nspname = CAST(:audit_schema AS text),
                   procedure_namespace.nspname = 'pg_catalog',
                   procedure.proname,
                   pg_catalog.oidvectortypes(procedure.proargtypes),
                   CASE
                       WHEN trigger_value.tgisinternal THEN ''
                       ELSE pg_catalog.pg_get_triggerdef(trigger_value.oid, true)
                   END
            FROM pg_catalog.pg_trigger AS trigger_value
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = trigger_value.tgrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_proc AS procedure
              ON procedure.oid = trigger_value.tgfoid
            JOIN pg_catalog.pg_namespace AS procedure_namespace
              ON procedure_namespace.oid = procedure.pronamespace
            LEFT JOIN pg_catalog.pg_constraint AS trigger_constraint
              ON trigger_constraint.oid = trigger_value.tgconstraint
            LEFT JOIN pg_catalog.pg_class AS constraint_relation
              ON constraint_relation.oid = trigger_constraint.conrelid
            LEFT JOIN pg_catalog.pg_namespace AS constraint_namespace
              ON constraint_namespace.oid = constraint_relation.relnamespace
            LEFT JOIN pg_catalog.pg_class AS referenced_relation
              ON referenced_relation.oid = trigger_constraint.confrelid
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND (
                  (
                      NOT trigger_value.tgisinternal
                      AND (
                          trigger_value.tgname = ANY(:names)
                          OR relation.relname IN (
                              'verification_results', 'artifact_references'
                          )
                      )
                  )
                  OR (
                      trigger_value.tgisinternal
                      AND trigger_constraint.contype = 'f'
                      AND constraint_namespace.nspname
                            = CAST(:audit_schema AS text)
                      AND constraint_relation.relname IN (
                          'verification_results', 'artifact_references'
                      )
                  )
              )
            ORDER BY relation.relname, 2, trigger_value.tgtype
            """,
            names=sorted(COMPLETION_EVIDENCE_TRIGGERS),
        ),
        "functions": _phase11_catalog_digest(
            connection,
            audit_schema,
            """
            SELECT procedure.proname,
                   pg_catalog.oidvectortypes(procedure.proargtypes),
                   pg_catalog.format_type(procedure.prorettype, NULL),
                   procedure.proowner = (
                       SELECT role.oid FROM pg_catalog.pg_roles AS role
                       WHERE role.rolname = CURRENT_USER
                   ),
                   procedure.proacl IS NOT NULL,
                   (
                       SELECT pg_catalog.count(*) = 1
                          AND pg_catalog.bool_and(
                                  acl.grantee = procedure.proowner
                                  AND acl.grantor = procedure.proowner
                                  AND acl.privilege_type = 'EXECUTE'
                                  AND NOT acl.is_grantable
                              )
                       FROM pg_catalog.aclexplode(procedure.proacl) AS acl
                   ),
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
            WHERE namespace.nspname = CAST(:audit_schema AS text)
              AND procedure.proname = ANY(:names)
            ORDER BY procedure.proname,
                     pg_catalog.oidvectortypes(procedure.proargtypes)
            """,
            names=sorted(
                signature.partition("(")[0] for signature in COMPLETION_EVIDENCE_FUNCTIONS
            ),
        ),
    }
    return {
        f"completion_evidence_{category}_failure_count": int(
            digest != PHASE11_CATALOG_SHA256[category]
        )
        for category, digest in digests.items()
    }


def _completion_evidence_catalog_failures(
    connection: Connection, audit_schema: str
) -> dict[str, int]:
    valid_generation_column_count = _scalar(
        connection,
        """
        WITH expected(table_name, column_name, is_not_null, has_zero_default) AS (
            VALUES
                ('work_items', 'completion_generation', true, true),
                ('checkpoints', 'completion_generation', false, false),
                ('work_events', 'reopen_generation', false, false)
        )
        SELECT count(*)
        FROM expected
        JOIN pg_catalog.pg_class AS relation
          ON relation.relname = expected.table_name
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        JOIN pg_catalog.pg_attribute AS attribute
          ON attribute.attrelid = relation.oid
         AND attribute.attname = expected.column_name
        LEFT JOIN pg_catalog.pg_attrdef AS default_value
          ON default_value.adrelid = relation.oid
         AND default_value.adnum = attribute.attnum
        WHERE namespace.nspname = CAST(:audit_schema AS text)
          AND relation.relkind IN ('r', 'p')
          AND NOT attribute.attisdropped
          AND pg_catalog.format_type(attribute.atttypid, attribute.atttypmod)
              = 'bigint'
          AND attribute.attnotnull = expected.is_not_null
          AND (
              (expected.has_zero_default AND pg_catalog.pg_get_expr(
                  default_value.adbin, default_value.adrelid, true
              ) IN ('0', '0::bigint', '''0''::bigint'))
              OR (NOT expected.has_zero_default AND default_value.oid IS NULL)
          )
        """,
        {"audit_schema": audit_schema},
    )
    valid_trigger_count = _scalar(
        connection,
        """
        WITH expected(
            table_name, trigger_name, function_name, trigger_type,
            is_constraint, has_condition, argument_count
        ) AS (
            VALUES
                ('work_items', 'completion_generation_guard',
                 'mnemonic_guard_completion_generation', 23, false, false, 0),
                ('work_items', 'completion_state_episode_guard',
                 'mnemonic_require_completion_state_episode', 21, true, true, 0),
                ('work_items', 'completion_pending_exit_guard',
                 'mnemonic_guard_completion_pending_exit', 19, false, false, 0),
                ('work_items', 'completion_unsealed_deletion_guard',
                 'mnemonic_guard_completion_unsealed_deletion', 19, false, false, 0),
                ('work_items', 'completion_episode_departure_guard',
                 'mnemonic_guard_completion_episode_departure', 19, false, false, 0),
                ('work_items', 'completion_generation_reopen_guard',
                 'mnemonic_require_completion_generation_reopen', 17, true, true, 0),
                ('work_events', 'completion_lifecycle_event_insert_guard',
                 'mnemonic_guard_completion_lifecycle_event_insert', 7, false, false, 0),
                ('work_events', 'completion_reopen_event_episode_guard',
                 'mnemonic_require_completion_reopen_event_episode', 5, true, true, 0),
                ('checkpoints', 'completion_checkpoint_insert_guard',
                 'mnemonic_guard_completion_checkpoint_insert', 7, false, false, 0),
                ('checkpoints', 'completion_checkpoint_episode_guard',
                 'mnemonic_require_completion_checkpoint_episode', 21, true, true, 0),
                ('verification_results', 'verification_results_insert_guard',
                 'mnemonic_guard_completion_evidence_insert', 7, false, false, 0),
                ('verification_results', 'verification_results_episode_guard',
                 'mnemonic_require_completion_evidence_episode', 5, true, false, 0),
                ('artifact_references', 'artifact_references_insert_guard',
                 'mnemonic_guard_completion_evidence_insert', 7, false, false, 0),
                ('artifact_references', 'artifact_references_episode_guard',
                 'mnemonic_require_completion_evidence_episode', 5, true, false, 0),
                ('verification_results', 'verification_results_immutable',
                 'mnemonic_reject_completion_evidence_mutation', 27, false, false, 1),
                ('artifact_references', 'artifact_references_immutable',
                 'mnemonic_reject_completion_evidence_mutation', 27, false, false, 1),
                ('verification_results', 'verification_results_truncate_guard',
                 'mnemonic_reject_completion_evidence_truncate', 34, false, false, 1),
                ('artifact_references', 'artifact_references_truncate_guard',
                 'mnemonic_reject_completion_evidence_truncate', 34, false, false, 1),
                ('work_events', 'work_events_phase11_truncate_guard',
                 'mnemonic_reject_phase11_history_truncate', 34, false, false, 1),
                ('client_operations', 'client_operations_phase11_truncate_guard',
                 'mnemonic_reject_phase11_history_truncate', 34, false, false, 1)
        )
        SELECT count(*)
        FROM expected
        JOIN pg_catalog.pg_class AS relation
          ON relation.relname = expected.table_name
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        JOIN pg_catalog.pg_trigger AS trigger_value
          ON trigger_value.tgrelid = relation.oid
         AND trigger_value.tgname = expected.trigger_name
        JOIN pg_catalog.pg_proc AS procedure
          ON procedure.oid = trigger_value.tgfoid
        JOIN pg_catalog.pg_namespace AS procedure_namespace
          ON procedure_namespace.oid = procedure.pronamespace
        LEFT JOIN pg_catalog.pg_constraint AS trigger_constraint
          ON trigger_constraint.oid = trigger_value.tgconstraint
        WHERE namespace.nspname = CAST(:audit_schema AS text)
          AND procedure_namespace.nspname = CAST(:audit_schema AS text)
          AND NOT trigger_value.tgisinternal
          AND trigger_value.tgenabled = 'O'
          AND trigger_value.tgtype = expected.trigger_type
          AND procedure.proname = expected.function_name
          AND trigger_value.tgnargs = expected.argument_count
          AND (trigger_value.tgconstraint <> 0) = expected.is_constraint
          AND (trigger_value.tgqual IS NOT NULL) = expected.has_condition
          AND trigger_value.tgdeferrable = expected.is_constraint
          AND trigger_value.tginitdeferred = expected.is_constraint
          AND (
              (NOT expected.is_constraint AND trigger_constraint.oid IS NULL)
              OR (
                  expected.is_constraint
                  AND trigger_constraint.contype = 't'
                  AND trigger_constraint.condeferrable
                  AND trigger_constraint.condeferred
              )
          )
        """,
        {"audit_schema": audit_schema},
    )
    exact = _phase11_exact_catalog_failures(connection, audit_schema)
    return {
        "phase10_survivor_catalog_failure_count": (
            _phase10_survivor_catalog_failure_count(connection, audit_schema)
        ),
        "completion_evidence_relation_failure_count": exact[
            "completion_evidence_relations_failure_count"
        ],
        "completion_evidence_column_failure_count": max(
            3 - valid_generation_column_count,
            exact["completion_evidence_columns_failure_count"],
        ),
        "completion_evidence_constraint_failure_count": exact[
            "completion_evidence_constraints_failure_count"
        ],
        "completion_evidence_index_failure_count": exact[
            "completion_evidence_indexes_failure_count"
        ],
        "completion_evidence_function_failure_count": exact[
            "completion_evidence_functions_failure_count"
        ],
        "completion_evidence_trigger_failure_count": (
            max(
                len(COMPLETION_EVIDENCE_TRIGGERS) - valid_trigger_count,
                exact["completion_evidence_triggers_failure_count"],
            )
        ),
    }


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
    connection.scalar(text("SELECT pg_catalog.set_config('search_path', 'pg_catalog', true)"))
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
    quoted_schema = connection.dialect.identifier_preparer.quote_identifier(audit_schema)
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
    if (
        expected_head in {ADVISORY_HEAD, REPOSITORY_FRESHNESS_HEAD, FINAL_HEAD}
        and title_key_signature in present
    ):
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
    required_indexes = set()
    if expected_head in {ADVISORY_HEAD, REPOSITORY_FRESHNESS_HEAD, FINAL_HEAD}:
        required_indexes.update(ADVISORY_INDEXES)
    if expected_head == FINAL_HEAD:
        required_indexes.update(COMPLETION_EVIDENCE_INDEXES)
    present_indexes: set[str] = set()
    if ADVISORY_INDEXES <= required_indexes:
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
                {"audit_schema": audit_schema, "names": list(ADVISORY_INDEXES)},
            )
        )
    if expected_head == FINAL_HEAD:
        present_indexes.update(
            connection.scalars(
                text(
                    """
                    WITH expected(
                        index_name, table_name, is_unique, key_count,
                        key_one, key_two, key_three, key_options, predicate
                    ) AS (
                        VALUES
                            (
                                'uq_checkpoints_completion_generation',
                                'checkpoints', true, 2,
                                'work_item_id', 'completion_generation', NULL, '0 0',
                                'kind::text = ''completion''::text'
                            ),
                            (
                                'uq_work_events_reopen_generation',
                                'work_events', true, 2,
                                'work_item_id', 'reopen_generation', NULL, '0 0',
                                'event_type::text = ''work_reopened''::text'
                            ),
                            (
                                'ix_work_events_completion_evidence_history',
                                'work_events', false, 3,
                                'project_id', 'work_item_id', 'id', '0 0 3',
                                'event_type::text = ''work_completed''::text'
                            ),
                            (
                                'ix_work_events_live_completion_version_order',
                                'work_events', false, 2,
                                'work_item_id', 'id', NULL, '0 3',
                                'event_type::text = ''work_completed''::text '
                                'AND origin::text = ''live''::text'
                            ),
                            (
                                'uq_verification_results_episode_position',
                                'verification_results', true, 3,
                                'work_item_id', 'completion_checkpoint_id', '"position"', '0 0 0',
                                NULL
                            ),
                            (
                                'uq_artifact_references_episode_position',
                                'artifact_references', true, 3,
                                'work_item_id', 'completion_checkpoint_id', '"position"', '0 0 0',
                                NULL
                            ),
                            (
                                'ix_client_operations_completion_checkpoint_receipt',
                                'client_operations', false, 2,
                                'project_id',
                                '(response_body #>> ''{checkpoint,id}''::text[])',
                                NULL, '0 0',
                                'operation_kind::text = ''complete_work''::text '
                                'AND state::text = ''completed''::text'
                            ),
                            (
                                'ix_client_operations_completion_receipt_correspondence',
                                'client_operations', true, 2,
                                '(response_body #>> ''{checkpoint,id}''::text[])',
                                '(response_body #>> ''{work_item,id}''::text[])',
                                NULL, '0 0',
                                'operation_kind::text = ''complete_work''::text '
                                'AND state::text = ''completed''::text'
                            ),
                            (
                                'ix_verification_results_completion_checkpoint_id_id',
                                'verification_results', false, 2,
                                'completion_checkpoint_id', 'id', NULL, '0 0', NULL
                            ),
                            (
                                'ix_artifact_references_completion_checkpoint_id_id',
                                'artifact_references', false, 2,
                                'completion_checkpoint_id', 'id', NULL, '0 0', NULL
                            )
                    )
                    SELECT index_relation.relname
                    FROM expected
                    JOIN pg_catalog.pg_class AS index_relation
                      ON index_relation.relname = expected.index_name
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = index_relation.relnamespace
                    JOIN pg_catalog.pg_index AS index
                      ON index.indexrelid = index_relation.oid
                    JOIN pg_catalog.pg_class AS table_relation
                      ON table_relation.oid = index.indrelid
                     AND table_relation.relname = expected.table_name
                    JOIN pg_catalog.pg_am AS access_method
                      ON access_method.oid = index_relation.relam
                    WHERE namespace.nspname = CAST(:audit_schema AS text)
                      AND access_method.amname = 'btree'
                      AND index.indisvalid
                      AND index.indisready
                      AND index.indisunique = expected.is_unique
                      AND index.indnkeyatts = expected.key_count
                      AND index.indoption::text = expected.key_options
                      AND pg_catalog.pg_get_indexdef(index.indexrelid, 1, true)
                          = expected.key_one
                      AND pg_catalog.pg_get_indexdef(index.indexrelid, 2, true)
                          = expected.key_two
                      AND (
                          expected.key_three IS NULL
                          OR pg_catalog.pg_get_indexdef(index.indexrelid, 3, true)
                              = expected.key_three
                      )
                      AND COALESCE(
                          pg_catalog.pg_get_expr(
                              index.indpred, index.indrelid, true
                          ),
                          ''
                      ) = COALESCE(expected.predicate, '')
                    """
                ),
                {"audit_schema": audit_schema},
            )
        )

    missing_repository_freshness_function_count = 0
    repository_freshness_definition_failure_count = 0
    repository_freshness_contract_failure_count = 0
    checkpoint_affected_paths_column_failure_count = 0
    checkpoint_affected_paths_constraint_failure_count = 0
    unexpected_affected_paths_index_count = 0
    completion_evidence_catalog = {
        "phase10_survivor_catalog_failure_count": 0,
        "completion_evidence_relation_failure_count": 0,
        "completion_evidence_column_failure_count": 0,
        "completion_evidence_constraint_failure_count": 0,
        "completion_evidence_index_failure_count": 0,
        "completion_evidence_function_failure_count": 0,
        "completion_evidence_trigger_failure_count": 0,
    }
    checkpoint_immutability_trigger_failure_count = _checkpoint_immutability_catalog_failure_count(
        connection, audit_schema
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
    if expected_head in {REPOSITORY_FRESHNESS_HEAD, FINAL_HEAD}:
        missing_repository_freshness_function_count = len(REPOSITORY_FRESHNESS_FUNCTIONS - present)
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
    if expected_head == FINAL_HEAD:
        completion_evidence_catalog = _completion_evidence_catalog_failures(
            connection, audit_schema
        )
    elif expected_head == REPOSITORY_FRESHNESS_HEAD:
        completion_evidence_catalog["phase10_survivor_catalog_failure_count"] = (
            _phase10_survivor_catalog_failure_count(connection, audit_schema)
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
        **completion_evidence_catalog,
        "database_bytes": _scalar(
            connection,
            "SELECT pg_catalog.pg_database_size(pg_catalog.current_database())",
        ),
    }


def _blocking_counts(
    counts: Mapping[str, int],
    *,
    require_empty_scope: bool = False,
    require_empty_completion_evidence: bool = False,
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
        "completion_episode_count",
        "structured_completion_episode_count",
        "empty_completion_episode_count",
        "verification_result_count",
        "artifact_reference_count",
        "phase11_downgrade_blocking_count",
    }
    blocking = {
        name: value for name, value in counts.items() if name not in informational and value
    }
    if require_empty_scope and counts.get("scoped_checkpoint_count", 0):
        blocking["unexpected_pre_enablement_scoped_checkpoint_count"] = counts[
            "scoped_checkpoint_count"
        ]
    if require_empty_completion_evidence and counts.get("phase11_downgrade_blocking_count", 0):
        blocking["unexpected_pre_enablement_completion_evidence_count"] = counts[
            "phase11_downgrade_blocking_count"
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
        "missing_repository_freshness_function_count": ("missing_repository_freshness_function"),
        "repository_freshness_definition_failure_count": (
            "repository_freshness_definition_failures"
        ),
        "repository_freshness_contract_failure_count": ("repository_freshness_contract_failures"),
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
        "phase10_survivor_catalog_failure_count": ("phase10_survivor_catalog_failures"),
        "completion_evidence_column_failure_count": ("completion_evidence_column_failures"),
        "completion_evidence_relation_failure_count": ("completion_evidence_relation_failures"),
        "completion_evidence_constraint_failure_count": ("completion_evidence_constraint_failures"),
        "completion_evidence_index_failure_count": ("completion_evidence_index_failures"),
        "completion_evidence_function_failure_count": ("completion_evidence_function_failures"),
        "completion_evidence_trigger_failure_count": ("completion_evidence_trigger_failures"),
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
    parser.add_argument(
        "--require-empty-completion-evidence",
        action="store_true",
        help=(
            "Pre-enablement gate: treat any evidence row or evidence-bearing "
            "completion receipt as blocking. Do not use after evidence writes begin."
        ),
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url is required")
    if args.require_empty_scope and args.expected_head not in {
        REPOSITORY_FRESHNESS_HEAD,
        FINAL_HEAD,
    }:
        parser.error(
            "--require-empty-scope requires --expected-head "
            "0018_repository_freshness or 0019_structured_completion_evidence"
        )
    if args.require_empty_completion_evidence and args.expected_head != FINAL_HEAD:
        parser.error(
            "--require-empty-completion-evidence requires --expected-head "
            "0019_structured_completion_evidence"
        )
    return args


def _database_audit_snapshot(
    connection: Connection, expected_head: str
) -> tuple[bool, int, dict[str, int], dict[str, int]]:
    """Collect every database fact under one trusted, transaction-local path."""
    audit_schema = connection.scalar(text("SELECT pg_catalog.current_schema()"))
    original_search_path = connection.scalar(
        text("SELECT pg_catalog.current_setting('search_path')")
    )
    if not isinstance(audit_schema, str) or not isinstance(original_search_path, str):
        raise TypeError("Could not establish a safe database audit schema")
    quoted_schema = connection.dialect.identifier_preparer.quote_identifier(audit_schema)
    connection.scalar(
        text("SELECT pg_catalog.set_config('search_path', :search_path, true)"),
        {"search_path": f"pg_catalog, {quoted_schema}"},
    )
    try:
        head_matches, head_count = _migration_head_status(connection, expected_head)
        counts = _base_counts(connection, audit_schema)
        if _ordinary_table_exists(connection, "work_duplicate_merges", schema=audit_schema):
            counts.update(_core_counts(connection))
        if expected_head in {REPOSITORY_FRESHNESS_HEAD, FINAL_HEAD}:
            counts.update(_repository_freshness_counts_on_safe_search_path(connection))
        if expected_head == FINAL_HEAD:
            counts.update(_completion_evidence_counts(connection, audit_schema))
        catalog = _catalog_on_safe_search_path(connection, expected_head, audit_schema)
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
            # All Phase 11 high-waters, inventories, and pages must remain on this
            # connection and transaction. Sequence relations themselves are non-MVCC.
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
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
            require_empty_completion_evidence=(args.require_empty_completion_evidence),
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
                "require_empty_completion_evidence": (args.require_empty_completion_evidence),
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
