"""Add immutable, witnessed duplicate-merge facts.

Revision ID: 0016_duplicate_handling
Revises: 0015_gate_review_fixes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_duplicate_handling"
down_revision: str | None = "0015_gate_review_fixes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OPERATION_KINDS = (
    "'create_work', 'add_checkpoint', 'append_event', 'add_relationship', "
    "'update_work', 'defer_work', 'complete_work', 'delete_work', "
    "'remove_relationship', 'release_claim', 'request_human_input', "
    "'resolve_human_input', 'merge_work'"
)


def _quoted_current_schema() -> str:
    bind = op.get_bind()
    schema = bind.scalar(sa.text("SELECT current_schema()"))
    if not isinstance(schema, str):
        raise RuntimeError("0016_duplicate_handling requires a current PostgreSQL schema")
    return bind.dialect.identifier_preparer.quote_identifier(schema)


def _create_merge_table() -> None:
    op.create_unique_constraint(
        "uq_work_relationships_merge_identity",
        "work_relationships",
        [
            "project_id",
            "id",
            "relationship_type",
            "source_work_item_id",
            "target_work_item_id",
        ],
    )
    op.create_table(
        "work_duplicate_merges",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "merge_sequence",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("source_work_item_id", sa.UUID(), nullable=False),
        sa.Column("destination_work_item_id", sa.UUID(), nullable=False),
        sa.Column("duplicate_relationship_id", sa.UUID(), nullable=False),
        sa.Column("duplicate_relationship_type", sa.String(length=32), nullable=False),
        sa.Column("reviewed_source_work_version", sa.Integer(), nullable=False),
        sa.Column("reviewed_source_context_checkpoint_id", sa.UUID(), nullable=False),
        sa.Column("reviewed_source_work_event_count", sa.BigInteger(), nullable=False),
        sa.Column("reviewed_destination_work_version", sa.Integer(), nullable=False),
        sa.Column("reviewed_destination_context_checkpoint_id", sa.UUID(), nullable=False),
        sa.Column("reviewed_destination_work_event_count", sa.BigInteger(), nullable=False),
        sa.Column("resulting_source_work_version", sa.Integer(), nullable=False),
        sa.Column("resulting_destination_work_version", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.String(length=4000), nullable=False),
        sa.Column("merged_by_client", sa.String(length=80), nullable=False),
        sa.Column("merged_by_session_id", sa.String(length=200), nullable=False),
        sa.Column("merged_by_model", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "duplicate_relationship_type = 'duplicate-of'",
            name=op.f("ck_work_duplicate_merges_relationship_type_valid"),
        ),
        sa.CheckConstraint(
            "source_work_item_id <> destination_work_item_id",
            name=op.f("ck_work_duplicate_merges_endpoints_differ"),
        ),
        sa.CheckConstraint(
            "reviewed_source_work_version > 0 "
            "AND reviewed_destination_work_version > 0 "
            "AND reviewed_source_work_event_count > 0 "
            "AND reviewed_destination_work_event_count > 0",
            name=op.f("ck_work_duplicate_merges_review_revision_positive"),
        ),
        sa.CheckConstraint(
            "resulting_source_work_version = reviewed_source_work_version + 1 "
            "AND resulting_destination_work_version = "
            "reviewed_destination_work_version + 1",
            name=op.f("ck_work_duplicate_merges_result_versions_valid"),
        ),
        sa.CheckConstraint(
            "mnemonic_has_non_whitespace(rationale)",
            name=op.f("ck_work_duplicate_merges_rationale_nonblank"),
        ),
        sa.CheckConstraint(
            "mnemonic_has_non_whitespace(merged_by_client)",
            name=op.f("ck_work_duplicate_merges_merged_by_client_nonblank"),
        ),
        sa.CheckConstraint(
            "mnemonic_has_non_whitespace(merged_by_session_id)",
            name=op.f("ck_work_duplicate_merges_merged_by_session_id_nonblank"),
        ),
        sa.CheckConstraint(
            "merged_by_model IS NULL OR mnemonic_has_non_whitespace(merged_by_model)",
            name=op.f("ck_work_duplicate_merges_merged_by_model_nonblank"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_work_duplicate_merges_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_duplicate_merges_source_work_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "destination_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_duplicate_merges_destination_work_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_work_item_id", "reviewed_source_context_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_duplicate_merges_source_context_checkpoint",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["destination_work_item_id", "reviewed_destination_context_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_duplicate_merges_destination_context_checkpoint",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "project_id",
                "duplicate_relationship_id",
                "duplicate_relationship_type",
                "source_work_item_id",
                "destination_work_item_id",
            ],
            [
                "work_relationships.project_id",
                "work_relationships.id",
                "work_relationships.relationship_type",
                "work_relationships.source_work_item_id",
                "work_relationships.target_work_item_id",
            ],
            name="fk_work_duplicate_merges_relationship",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_duplicate_merges")),
        sa.UniqueConstraint(
            "merge_sequence",
            name="uq_work_duplicate_merges_merge_sequence",
        ),
        sa.UniqueConstraint(
            "project_id",
            "source_work_item_id",
            name="uq_work_duplicate_merges_source",
        ),
        sa.UniqueConstraint(
            "duplicate_relationship_id",
            name="uq_work_duplicate_merges_relationship",
        ),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_work_duplicate_merges_project_id_id",
        ),
    )
    op.create_index(
        "ix_work_duplicate_merges_destination",
        "work_duplicate_merges",
        ["project_id", "destination_work_item_id", "merge_sequence", "id"],
    )
    op.create_index(
        "ix_work_duplicate_merges_audit",
        "work_duplicate_merges",
        ["project_id", "merge_sequence", "id"],
    )


def _extend_relationships() -> None:
    op.add_column(
        "work_relationships",
        sa.Column("created_for_duplicate_merge_id", sa.UUID(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_work_relationships_merge_witness",
        "work_relationships",
        ["project_id", "created_for_duplicate_merge_id"],
    )
    op.create_foreign_key(
        "fk_work_relationships_duplicate_merge",
        "work_relationships",
        "work_duplicate_merges",
        ["project_id", "created_for_duplicate_merge_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )


def _create_work_merged_validator(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_work_merged_metadata_v1_is_valid(
            p_work_item_id uuid,
            p_work_duplicate_merge_id uuid,
            p_metadata_version smallint,
            p_metadata jsonb
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_keys text[];
            v_role text;
            v_source_work_item_id uuid;
            v_destination_work_item_id uuid;
        BEGIN
            IF p_metadata_version <> 1
               OR p_work_duplicate_merge_id IS NULL
               OR p_metadata IS NULL
               OR pg_catalog.jsonb_typeof(p_metadata) <> 'object' THEN
                RETURN false;
            END IF;

            SELECT pg_catalog.array_agg(key ORDER BY key)
            INTO v_keys
            FROM pg_catalog.jsonb_object_keys(p_metadata) AS metadata_key(key);
            IF v_keys IS DISTINCT FROM ARRAY[
                'destination_work_item_id',
                'destination_work_version',
                'merge_id',
                'role',
                'source_work_item_id',
                'source_work_version'
            ]::text[]
               OR pg_catalog.jsonb_typeof(p_metadata -> 'merge_id') <> 'string'
               OR p_metadata ->> 'merge_id' !~
                  '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
               OR (p_metadata ->> 'merge_id')::uuid
                  IS DISTINCT FROM p_work_duplicate_merge_id
               OR pg_catalog.jsonb_typeof(p_metadata -> 'source_work_item_id') <> 'string'
               OR p_metadata ->> 'source_work_item_id' !~
                  '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
               OR pg_catalog.jsonb_typeof(p_metadata -> 'destination_work_item_id')
                  <> 'string'
               OR p_metadata ->> 'destination_work_item_id' !~
                  '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
               OR pg_catalog.jsonb_typeof(p_metadata -> 'role') <> 'string'
               OR p_metadata ->> 'role' NOT IN ('source', 'destination')
               OR pg_catalog.jsonb_typeof(p_metadata -> 'source_work_version') <> 'number'
               OR p_metadata ->> 'source_work_version' !~ '^[1-9][0-9]*$'
               OR pg_catalog.jsonb_typeof(p_metadata -> 'destination_work_version')
                  <> 'number'
               OR p_metadata ->> 'destination_work_version' !~ '^[1-9][0-9]*$' THEN
                RETURN false;
            END IF;

            v_role := p_metadata ->> 'role';
            v_source_work_item_id := (p_metadata ->> 'source_work_item_id')::uuid;
            v_destination_work_item_id :=
                (p_metadata ->> 'destination_work_item_id')::uuid;
            RETURN v_source_work_item_id <> v_destination_work_item_id
                AND (p_metadata ->> 'source_work_version')::integer > 0
                AND (p_metadata ->> 'destination_work_version')::integer > 0
                AND p_work_item_id = CASE v_role
                    WHEN 'source' THEN v_source_work_item_id
                    ELSE v_destination_work_item_id
                END;
        EXCEPTION
            WHEN others THEN
                RETURN false;
        END
        $function$;
        """
    )


def _extend_work_events() -> None:
    op.add_column(
        "work_events",
        sa.Column("created_for_duplicate_merge_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "work_events",
        sa.Column("work_duplicate_merge_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_work_events_created_for_duplicate_merge",
        "work_events",
        "work_duplicate_merges",
        ["project_id", "created_for_duplicate_merge_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_work_events_duplicate_merge",
        "work_events",
        "work_duplicate_merges",
        ["project_id", "work_duplicate_merge_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(
        op.f("ck_work_events_duplicate_merge_references_valid"),
        "work_events",
        "(created_for_duplicate_merge_id IS NULL "
        "OR event_type = 'relationship_added') AND "
        "((event_type = 'work_merged' AND work_duplicate_merge_id IS NOT NULL) OR "
        "(event_type <> 'work_merged' AND work_duplicate_merge_id IS NULL))",
    )
    op.create_index(
        "uq_work_events_relationship_merge_witness",
        "work_events",
        ["project_id", "created_for_duplicate_merge_id", "work_item_id"],
        unique=True,
        postgresql_where=sa.text("created_for_duplicate_merge_id IS NOT NULL"),
    )
    op.create_index(
        "uq_work_events_merge_endpoint",
        "work_events",
        ["project_id", "work_duplicate_merge_id", "work_item_id"],
        unique=True,
        postgresql_where=sa.text("work_duplicate_merge_id IS NOT NULL"),
    )
    op.create_index(
        "uq_work_events_merge_role",
        "work_events",
        ["project_id", "work_duplicate_merge_id", sa.text("(metadata ->> 'role')")],
        unique=True,
        postgresql_where=sa.text("work_duplicate_merge_id IS NOT NULL"),
    )


def _replace_work_event_checks() -> None:
    for name in ("event_type_valid", "actor_matrix_valid", "body_valid", "metadata_v1_valid"):
        op.drop_constraint(op.f(f"ck_work_events_{name}"), "work_events", type_="check")

    op.create_check_constraint(
        op.f("ck_work_events_event_type_valid"),
        "work_events",
        "event_type IN ('work_created', 'work_updated', 'work_status_changed', "
        "'work_reopened', 'work_claimed', 'work_released', 'checkpoint_added', "
        "'progress', 'dependency_added', 'dependency_removed', "
        "'relationship_added', 'relationship_removed', 'work_completed', "
        "'work_deleted', 'work_merged', 'human_attention_requested', "
        "'human_attention_resolved')",
    )
    op.create_check_constraint(
        op.f("ck_work_events_actor_matrix_valid"),
        "work_events",
        "(origin = 'live' AND ("
        "event_type NOT IN ('work_created', 'checkpoint_added', 'work_completed', "
        "'work_claimed', 'dependency_added', 'relationship_added', 'progress', "
        "'work_merged', 'human_attention_requested', 'human_attention_resolved') "
        "OR actor_kind = 'client')) OR "
        "(origin = 'backfill' AND (event_type <> 'work_deleted' "
        "OR actor_kind = 'unattributed'))",
    )
    op.create_check_constraint(
        op.f("ck_work_events_body_valid"),
        "work_events",
        "(event_type IN ('progress', 'work_merged', 'human_attention_requested', "
        "'human_attention_resolved') AND body IS NOT NULL "
        "AND length(body) <= 4000 AND mnemonic_has_non_whitespace(body)) OR "
        "(event_type NOT IN ('progress', 'work_merged', 'human_attention_requested', "
        "'human_attention_resolved') AND body IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_work_events_metadata_v1_valid"),
        "work_events",
        "(event_type IN ('human_attention_requested', 'human_attention_resolved') "
        "AND metadata_version = 1 "
        "AND metadata = jsonb_build_object('gate_id', gate_id::text, "
        "'gate_type', 'human')) OR "
        "(event_type = 'work_merged' AND "
        "mnemonic_work_merged_metadata_v1_is_valid(work_item_id, "
        "work_duplicate_merge_id, metadata_version, metadata)) OR "
        "(event_type NOT IN ('human_attention_requested', 'human_attention_resolved', "
        "'work_merged') AND mnemonic_work_event_metadata_v2_is_valid("
        "event_type, origin, work_item_id, checkpoint_id, lease_generation_id, "
        "lease_release_id, relationship_id, relationship_source_work_item_id, "
        "relationship_target_work_item_id, "
        "relationship_context_checkpoint_work_item_id, "
        "relationship_context_checkpoint_id, metadata_version, metadata))",
    )


def _replace_operation_kinds() -> None:
    op.drop_constraint(
        op.f("ck_client_operations_operation_kind_valid"),
        "client_operations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_client_operations_operation_kind_valid"),
        "client_operations",
        f"operation_kind IN ({OPERATION_KINDS})",
    )


def _create_component_state_function(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_duplicate_component_state(
            p_project_id uuid,
            p_start_work_item_id uuid
        )
        RETURNS TABLE(reverse_depth integer, forward_depth integer, is_valid boolean)
        LANGUAGE sql
        STABLE
        SET search_path = pg_catalog
        AS $function$
            WITH RECURSIVE
            reverse_walk(work_item_id, depth, visited, cycle) AS (
                SELECT p_start_work_item_id, 0,
                       ARRAY[p_start_work_item_id]::uuid[], false
                UNION ALL
                SELECT merge.source_work_item_id,
                       walk.depth + 1,
                       walk.visited || merge.source_work_item_id,
                       merge.source_work_item_id = ANY(walk.visited)
                FROM reverse_walk AS walk
                JOIN {schema}.work_duplicate_merges AS merge
                  ON merge.project_id = p_project_id
                 AND merge.destination_work_item_id = walk.work_item_id
                WHERE NOT walk.cycle
                  AND walk.depth <= 50
            ),
            forward_walk(work_item_id, depth, visited, cycle) AS (
                SELECT p_start_work_item_id, 0,
                       ARRAY[p_start_work_item_id]::uuid[], false
                UNION ALL
                SELECT merge.destination_work_item_id,
                       walk.depth + 1,
                       walk.visited || merge.destination_work_item_id,
                       merge.destination_work_item_id = ANY(walk.visited)
                FROM forward_walk AS walk
                JOIN {schema}.work_duplicate_merges AS merge
                  ON merge.project_id = p_project_id
                 AND merge.source_work_item_id = walk.work_item_id
                WHERE NOT walk.cycle
                  AND walk.depth <= 50
            ),
            nodes AS (
                SELECT work_item_id FROM reverse_walk
                UNION
                SELECT work_item_id FROM forward_walk
            ),
            measurements AS (
                SELECT
                    (SELECT pg_catalog.max(depth) FROM reverse_walk) AS reverse_depth,
                    (SELECT pg_catalog.max(depth) FROM forward_walk) AS forward_depth
            )
            SELECT measurements.reverse_depth,
                   measurements.forward_depth,
                   measurements.reverse_depth <= 50
                   AND measurements.forward_depth <= 50
                   AND NOT EXISTS (SELECT 1 FROM reverse_walk WHERE cycle)
                   AND NOT EXISTS (SELECT 1 FROM forward_walk WHERE cycle)
                   AND NOT EXISTS (
                       SELECT 1
                       FROM nodes
                       LEFT JOIN {schema}.work_items AS work
                         ON work.id = nodes.work_item_id
                        AND work.project_id = p_project_id
                        AND work.deleted_at IS NULL
                       WHERE work.id IS NULL
                   )
                   AND NOT EXISTS (
                       SELECT 1
                       FROM {schema}.work_duplicate_merges AS merge
                       JOIN nodes
                         ON nodes.work_item_id IN (
                             merge.source_work_item_id,
                             merge.destination_work_item_id
                         )
                       WHERE merge.project_id <> p_project_id
                   )
            FROM measurements
        $function$;
        """
    )


def _create_merge_insert_guard(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_duplicate_merge_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_source {schema}.work_items%ROWTYPE;
            v_destination {schema}.work_items%ROWTYPE;
            v_relationship {schema}.work_relationships%ROWTYPE;
            v_source_checkpoint_id uuid;
            v_destination_checkpoint_id uuid;
            v_source_event_count bigint;
            v_destination_event_count bigint;
            v_source_component record;
            v_destination_component record;
        BEGIN
            PERFORM 1
            FROM {schema}.projects
            WHERE id = NEW.project_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge project is not retained';
            END IF;

            IF NEW.source_work_item_id = NEW.destination_work_item_id THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge endpoints must differ';
            END IF;

            PERFORM 1
            FROM {schema}.work_items
            WHERE id IN (NEW.source_work_item_id, NEW.destination_work_item_id)
            ORDER BY id
            FOR UPDATE;

            SELECT *
            INTO v_source
            FROM {schema}.work_items
            WHERE id = NEW.source_work_item_id;
            IF NOT FOUND
               OR v_source.project_id IS DISTINCT FROM NEW.project_id
               OR v_source.deleted_at IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge source must be visible in project';
            END IF;

            SELECT *
            INTO v_destination
            FROM {schema}.work_items
            WHERE id = NEW.destination_work_item_id;
            IF NOT FOUND
               OR v_destination.project_id IS DISTINCT FROM NEW.project_id
               OR v_destination.deleted_at IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge destination must be visible in project';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM {schema}.work_duplicate_merges AS retained
                WHERE retained.project_id = NEW.project_id
                  AND retained.source_work_item_id IN (
                      NEW.source_work_item_id,
                      NEW.destination_work_item_id
                  )
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge endpoints must be current roots';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM {schema}.work_gates AS gate
                WHERE gate.work_item_id = NEW.source_work_item_id
                  AND gate.resolved_at IS NULL
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge source has unresolved human attention';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM {schema}.work_relationships AS relationship
                WHERE relationship.project_id = NEW.project_id
                  AND relationship.relationship_type IN ('blocks', 'parent-child')
                  AND NEW.source_work_item_id IN (
                      relationship.source_work_item_id,
                      relationship.target_work_item_id
                  )
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge source has a structural relationship';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM {schema}.work_leases AS lease
                WHERE lease.work_item_id = NEW.source_work_item_id
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge source lease must be consumed first';
            END IF;

            SELECT checkpoint.id
            INTO v_source_checkpoint_id
            FROM {schema}.checkpoints AS checkpoint
            WHERE checkpoint.work_item_id = NEW.source_work_item_id
              AND checkpoint.kind = 'context'
            ORDER BY checkpoint.created_at DESC, checkpoint.id DESC
            LIMIT 1;
            SELECT checkpoint.id
            INTO v_destination_checkpoint_id
            FROM {schema}.checkpoints AS checkpoint
            WHERE checkpoint.work_item_id = NEW.destination_work_item_id
              AND checkpoint.kind = 'context'
            ORDER BY checkpoint.created_at DESC, checkpoint.id DESC
            LIMIT 1;
            SELECT pg_catalog.count(*)
            INTO v_source_event_count
            FROM {schema}.work_events
            WHERE project_id = NEW.project_id
              AND work_item_id = NEW.source_work_item_id;
            SELECT pg_catalog.count(*)
            INTO v_destination_event_count
            FROM {schema}.work_events
            WHERE project_id = NEW.project_id
              AND work_item_id = NEW.destination_work_item_id;

            IF v_source_checkpoint_id IS NULL
               OR v_destination_checkpoint_id IS NULL
               OR NEW.reviewed_source_context_checkpoint_id
                  IS DISTINCT FROM v_source_checkpoint_id
               OR NEW.reviewed_destination_context_checkpoint_id
                  IS DISTINCT FROM v_destination_checkpoint_id
               OR NEW.reviewed_source_work_event_count
                  IS DISTINCT FROM v_source_event_count
               OR NEW.reviewed_destination_work_event_count
                  IS DISTINCT FROM v_destination_event_count THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge review revision does not match retained state';
            END IF;

            IF v_source.version IS DISTINCT FROM NEW.resulting_source_work_version
               OR v_destination.version
                  IS DISTINCT FROM NEW.resulting_destination_work_version
               OR NEW.resulting_source_work_version
                  IS DISTINCT FROM NEW.reviewed_source_work_version + 1
               OR NEW.resulting_destination_work_version
                  IS DISTINCT FROM NEW.reviewed_destination_work_version + 1
               OR v_source.updated_at IS DISTINCT FROM NEW.created_at
               OR v_destination.updated_at IS DISTINCT FROM NEW.created_at THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge endpoint revision transition is invalid';
            END IF;

            SELECT *
            INTO v_relationship
            FROM {schema}.work_relationships AS relationship
            WHERE relationship.project_id = NEW.project_id
              AND relationship.id = NEW.duplicate_relationship_id
              AND relationship.relationship_type = NEW.duplicate_relationship_type
              AND relationship.source_work_item_id = NEW.source_work_item_id
              AND relationship.target_work_item_id = NEW.destination_work_item_id
            FOR KEY SHARE;
            IF NOT FOUND
               OR v_relationship.relationship_type <> 'duplicate-of'
               OR (
                   v_relationship.created_for_duplicate_merge_id IS NOT NULL
                   AND v_relationship.created_for_duplicate_merge_id <> NEW.id
               ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge relationship does not match endpoints';
            END IF;

            SELECT *
            INTO v_source_component
            FROM {schema}.mnemonic_duplicate_component_state(
                NEW.project_id,
                NEW.source_work_item_id
            );
            SELECT *
            INTO v_destination_component
            FROM {schema}.mnemonic_duplicate_component_state(
                NEW.project_id,
                NEW.destination_work_item_id
            );
            IF NOT v_source_component.is_valid
               OR NOT v_destination_component.is_valid
               OR v_source_component.reverse_depth + 1
                  + v_destination_component.forward_depth > 50
               OR v_destination_component.reverse_depth > 50 THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge would create an invalid graph';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM {schema}.work_events AS event
                WHERE event.project_id = NEW.project_id
                  AND (
                      event.created_for_duplicate_merge_id = NEW.id
                      OR event.work_duplicate_merge_id = NEW.id
                  )
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge events must follow the merge fact';
            END IF;

            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER duplicate_merge_insert_guard
        BEFORE INSERT ON {schema}.work_duplicate_merges
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_duplicate_merge_insert();
        """
    )


def _create_merge_immutability(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_reject_duplicate_merge_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'duplicate merges are immutable';
        END
        $function$;

        CREATE TRIGGER duplicate_merges_immutable
        BEFORE UPDATE OR DELETE ON {schema}.work_duplicate_merges
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_reject_duplicate_merge_mutation();
        """
    )


def _create_completeness_guards(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_duplicate_merge_is_complete(
            p_project_id uuid,
            p_merge_id uuid
        )
        RETURNS boolean
        LANGUAGE plpgsql
        STABLE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_merge {schema}.work_duplicate_merges%ROWTYPE;
            v_relationship {schema}.work_relationships%ROWTYPE;
            v_event_count bigint;
            v_source_count bigint;
            v_destination_count bigint;
            v_source_event_id bigint;
            v_destination_event_id bigint;
            v_source_checkpoint_id uuid;
            v_destination_checkpoint_id uuid;
            v_current_source_event_count bigint;
            v_current_destination_event_count bigint;
            v_expected_event_increment integer;
            v_source_component record;
            v_destination_component record;
        BEGIN
            SELECT *
            INTO v_merge
            FROM {schema}.work_duplicate_merges
            WHERE project_id = p_project_id
              AND id = p_merge_id;
            IF NOT FOUND THEN
                RETURN false;
            END IF;

            SELECT *
            INTO v_relationship
            FROM {schema}.work_relationships
            WHERE project_id = v_merge.project_id
              AND id = v_merge.duplicate_relationship_id
              AND relationship_type = v_merge.duplicate_relationship_type
              AND source_work_item_id = v_merge.source_work_item_id
              AND target_work_item_id = v_merge.destination_work_item_id;
            IF NOT FOUND
               OR v_relationship.relationship_type <> 'duplicate-of'
               OR (
                   v_relationship.created_for_duplicate_merge_id IS NOT NULL
                   AND v_relationship.created_for_duplicate_merge_id <> v_merge.id
               ) THEN
                RETURN false;
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM {schema}.work_items AS source
                JOIN {schema}.work_items AS destination
                  ON destination.id = v_merge.destination_work_item_id
                 AND destination.project_id = v_merge.project_id
                 AND destination.deleted_at IS NULL
                 AND destination.version
                     = v_merge.resulting_destination_work_version
                 AND destination.updated_at = v_merge.created_at
                WHERE source.id = v_merge.source_work_item_id
                  AND source.project_id = v_merge.project_id
                  AND source.deleted_at IS NULL
                  AND source.version = v_merge.resulting_source_work_version
                  AND source.updated_at = v_merge.created_at
            ) THEN
                RETURN false;
            END IF;

            SELECT checkpoint.id
            INTO v_source_checkpoint_id
            FROM {schema}.checkpoints AS checkpoint
            WHERE checkpoint.work_item_id = v_merge.source_work_item_id
              AND checkpoint.kind = 'context'
            ORDER BY checkpoint.created_at DESC, checkpoint.id DESC
            LIMIT 1;
            SELECT checkpoint.id
            INTO v_destination_checkpoint_id
            FROM {schema}.checkpoints AS checkpoint
            WHERE checkpoint.work_item_id = v_merge.destination_work_item_id
              AND checkpoint.kind = 'context'
            ORDER BY checkpoint.created_at DESC, checkpoint.id DESC
            LIMIT 1;
            IF v_source_checkpoint_id
                  IS DISTINCT FROM v_merge.reviewed_source_context_checkpoint_id
               OR v_destination_checkpoint_id
                  IS DISTINCT FROM
                      v_merge.reviewed_destination_context_checkpoint_id THEN
                RETURN false;
            END IF;

            v_expected_event_increment := CASE
                WHEN v_relationship.created_for_duplicate_merge_id IS NULL THEN 1
                ELSE 2
            END;
            SELECT pg_catalog.count(*)
            INTO v_current_source_event_count
            FROM {schema}.work_events
            WHERE project_id = v_merge.project_id
              AND work_item_id = v_merge.source_work_item_id;
            SELECT pg_catalog.count(*)
            INTO v_current_destination_event_count
            FROM {schema}.work_events
            WHERE project_id = v_merge.project_id
              AND work_item_id = v_merge.destination_work_item_id;
            IF v_current_source_event_count
                  <> v_merge.reviewed_source_work_event_count
                     + v_expected_event_increment
               OR v_current_destination_event_count
                  <> v_merge.reviewed_destination_work_event_count
                     + v_expected_event_increment THEN
                RETURN false;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM {schema}.work_duplicate_merges AS retained
                WHERE retained.project_id = v_merge.project_id
                  AND retained.id <> v_merge.id
                  AND retained.source_work_item_id IN (
                      v_merge.source_work_item_id,
                      v_merge.destination_work_item_id
                  )
            ) OR EXISTS (
                SELECT 1
                FROM {schema}.work_gates AS gate
                WHERE gate.work_item_id = v_merge.source_work_item_id
                  AND gate.resolved_at IS NULL
            ) OR EXISTS (
                SELECT 1
                FROM {schema}.work_relationships AS relationship
                WHERE relationship.project_id = v_merge.project_id
                  AND relationship.relationship_type IN ('blocks', 'parent-child')
                  AND v_merge.source_work_item_id IN (
                      relationship.source_work_item_id,
                      relationship.target_work_item_id
                  )
            ) OR EXISTS (
                SELECT 1
                FROM {schema}.work_leases AS lease
                WHERE lease.work_item_id = v_merge.source_work_item_id
            ) THEN
                RETURN false;
            END IF;

            SELECT *
            INTO v_source_component
            FROM {schema}.mnemonic_duplicate_component_state(
                v_merge.project_id,
                v_merge.source_work_item_id
            );
            SELECT *
            INTO v_destination_component
            FROM {schema}.mnemonic_duplicate_component_state(
                v_merge.project_id,
                v_merge.destination_work_item_id
            );
            IF NOT v_source_component.is_valid
               OR NOT v_destination_component.is_valid
               OR v_source_component.reverse_depth
                  + v_source_component.forward_depth > 50
               OR v_destination_component.reverse_depth > 50 THEN
                RETURN false;
            END IF;

            IF v_relationship.created_for_duplicate_merge_id IS NULL THEN
                IF EXISTS (
                    SELECT 1
                    FROM {schema}.work_events
                    WHERE project_id = v_merge.project_id
                      AND created_for_duplicate_merge_id = v_merge.id
                ) THEN
                    RETURN false;
                END IF;
            ELSE
                IF v_relationship.created_at IS DISTINCT FROM v_merge.created_at
                   OR v_relationship.created_by_client
                      IS DISTINCT FROM v_merge.merged_by_client
                   OR v_relationship.created_by_session_id
                      IS DISTINCT FROM v_merge.merged_by_session_id
                   OR v_relationship.created_by_model
                      IS DISTINCT FROM v_merge.merged_by_model THEN
                    RETURN false;
                END IF;

                SELECT
                    pg_catalog.count(*),
                    pg_catalog.count(*) FILTER (
                        WHERE event.work_item_id = v_merge.source_work_item_id
                          AND event.event_type = 'relationship_added'
                          AND event.relationship_id = v_relationship.id
                          AND event.relationship_source_work_item_id
                              = v_relationship.source_work_item_id
                          AND event.relationship_target_work_item_id
                              = v_relationship.target_work_item_id
                          AND event.relationship_context_checkpoint_work_item_id
                              IS NOT DISTINCT FROM
                                  v_relationship.context_checkpoint_work_item_id
                          AND event.relationship_context_checkpoint_id
                              IS NOT DISTINCT FROM v_relationship.context_checkpoint_id
                          AND event.actor_kind = 'client'
                          AND event.actor_client = v_merge.merged_by_client
                          AND event.actor_session_id = v_merge.merged_by_session_id
                          AND event.actor_model IS NOT DISTINCT FROM v_merge.merged_by_model
                          AND event.origin = 'live'
                          AND event.created_at = v_merge.created_at
                          AND event.metadata = pg_catalog.jsonb_build_object(
                              'relationship_type', 'duplicate-of'
                          )
                    ),
                    pg_catalog.count(*) FILTER (
                        WHERE event.work_item_id = v_merge.destination_work_item_id
                          AND event.event_type = 'relationship_added'
                          AND event.relationship_id = v_relationship.id
                          AND event.relationship_source_work_item_id
                              = v_relationship.source_work_item_id
                          AND event.relationship_target_work_item_id
                              = v_relationship.target_work_item_id
                          AND event.relationship_context_checkpoint_work_item_id
                              IS NOT DISTINCT FROM
                                  v_relationship.context_checkpoint_work_item_id
                          AND event.relationship_context_checkpoint_id
                              IS NOT DISTINCT FROM v_relationship.context_checkpoint_id
                          AND event.actor_kind = 'client'
                          AND event.actor_client = v_merge.merged_by_client
                          AND event.actor_session_id = v_merge.merged_by_session_id
                          AND event.actor_model IS NOT DISTINCT FROM v_merge.merged_by_model
                          AND event.origin = 'live'
                          AND event.created_at = v_merge.created_at
                          AND event.metadata = pg_catalog.jsonb_build_object(
                              'relationship_type', 'duplicate-of'
                          )
                    ),
                    pg_catalog.min(event.id) FILTER (
                        WHERE event.work_item_id = v_merge.source_work_item_id
                    ),
                    pg_catalog.min(event.id) FILTER (
                        WHERE event.work_item_id = v_merge.destination_work_item_id
                    )
                INTO
                    v_event_count,
                    v_source_count,
                    v_destination_count,
                    v_source_event_id,
                    v_destination_event_id
                FROM {schema}.work_events AS event
                WHERE event.project_id = v_merge.project_id
                  AND event.created_for_duplicate_merge_id = v_merge.id;
                IF v_event_count <> 2
                   OR v_source_count <> 1
                   OR v_destination_count <> 1
                   OR v_source_event_id >= v_destination_event_id THEN
                    RETURN false;
                END IF;
            END IF;

            SELECT
                pg_catalog.count(*),
                pg_catalog.count(*) FILTER (
                    WHERE event.work_item_id = v_merge.source_work_item_id
                      AND event.event_type = 'work_merged'
                      AND event.actor_kind = 'client'
                      AND event.actor_client = v_merge.merged_by_client
                      AND event.actor_session_id = v_merge.merged_by_session_id
                      AND event.actor_model IS NOT DISTINCT FROM v_merge.merged_by_model
                      AND event.body = v_merge.rationale
                      AND event.origin = 'live'
                      AND event.created_at = v_merge.created_at
                      AND event.metadata = pg_catalog.jsonb_build_object(
                          'merge_id', v_merge.id::text,
                          'source_work_item_id', v_merge.source_work_item_id::text,
                          'destination_work_item_id',
                              v_merge.destination_work_item_id::text,
                          'role', 'source',
                          'source_work_version',
                              v_merge.resulting_source_work_version,
                          'destination_work_version',
                              v_merge.resulting_destination_work_version
                      )
                ),
                pg_catalog.count(*) FILTER (
                    WHERE event.work_item_id = v_merge.destination_work_item_id
                      AND event.event_type = 'work_merged'
                      AND event.actor_kind = 'client'
                      AND event.actor_client = v_merge.merged_by_client
                      AND event.actor_session_id = v_merge.merged_by_session_id
                      AND event.actor_model IS NOT DISTINCT FROM v_merge.merged_by_model
                      AND event.body = v_merge.rationale
                      AND event.origin = 'live'
                      AND event.created_at = v_merge.created_at
                      AND event.metadata = pg_catalog.jsonb_build_object(
                          'merge_id', v_merge.id::text,
                          'source_work_item_id', v_merge.source_work_item_id::text,
                          'destination_work_item_id',
                              v_merge.destination_work_item_id::text,
                          'role', 'destination',
                          'source_work_version',
                              v_merge.resulting_source_work_version,
                          'destination_work_version',
                              v_merge.resulting_destination_work_version
                      )
                ),
                pg_catalog.min(event.id) FILTER (
                    WHERE event.work_item_id = v_merge.source_work_item_id
                ),
                pg_catalog.min(event.id) FILTER (
                    WHERE event.work_item_id = v_merge.destination_work_item_id
                )
            INTO
                v_event_count,
                v_source_count,
                v_destination_count,
                v_source_event_id,
                v_destination_event_id
            FROM {schema}.work_events AS event
            WHERE event.project_id = v_merge.project_id
              AND event.work_duplicate_merge_id = v_merge.id;
            RETURN v_event_count = 2
                AND v_source_count = 1
                AND v_destination_count = 1
                AND v_source_event_id < v_destination_event_id;
        END
        $function$;

        CREATE FUNCTION {schema}.mnemonic_require_duplicate_relationship_merge()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF NEW.relationship_type = 'duplicate-of'
               AND NEW.created_for_duplicate_merge_id IS NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'new duplicate relationship requires a merge witness';
            END IF;
            IF NEW.created_for_duplicate_merge_id IS NOT NULL
               AND (
                   NEW.relationship_type <> 'duplicate-of'
                   OR NOT {schema}.mnemonic_duplicate_merge_is_complete(
                       NEW.project_id,
                       NEW.created_for_duplicate_merge_id
                   )
               ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate relationship merge evidence is incomplete';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER duplicate_relationship_completeness_guard
        AFTER INSERT ON {schema}.work_relationships
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_require_duplicate_relationship_merge();

        CREATE FUNCTION {schema}.mnemonic_require_duplicate_merge_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF NOT {schema}.mnemonic_duplicate_merge_is_complete(
                NEW.project_id,
                NEW.id
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate merge evidence is incomplete';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER duplicate_merge_completeness_guard
        AFTER INSERT ON {schema}.work_duplicate_merges
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_require_duplicate_merge_evidence();
        """
    )


def _create_relationship_guards(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_duplicate_relationship_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_project_id uuid;
            v_source_work_item_id uuid;
            v_target_work_item_id uuid;
            v_relationship_id uuid;
            v_witness_id uuid;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'work relationships are immutable';
            END IF;

            IF TG_OP = 'DELETE' THEN
                v_project_id := OLD.project_id;
                v_source_work_item_id := OLD.source_work_item_id;
                v_target_work_item_id := OLD.target_work_item_id;
                v_relationship_id := OLD.id;
                v_witness_id := OLD.created_for_duplicate_merge_id;
            ELSE
                v_project_id := NEW.project_id;
                v_source_work_item_id := NEW.source_work_item_id;
                v_target_work_item_id := NEW.target_work_item_id;
                v_relationship_id := NEW.id;
                v_witness_id := NEW.created_for_duplicate_merge_id;
            END IF;

            PERFORM 1
            FROM {schema}.projects
            WHERE id = v_project_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'relationship project is not retained';
            END IF;
            PERFORM 1
            FROM {schema}.work_items
            WHERE id IN (v_source_work_item_id, v_target_work_item_id)
            ORDER BY id
            FOR UPDATE;

            IF TG_OP = 'DELETE'
               AND (
                   v_witness_id IS NOT NULL
                   OR EXISTS (
                       SELECT 1
                       FROM {schema}.work_duplicate_merges AS merge
                       WHERE merge.project_id = v_project_id
                         AND merge.duplicate_relationship_id = v_relationship_id
                   )
               ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'duplicate merge relationship is frozen';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM {schema}.work_duplicate_merges AS merge
                WHERE merge.project_id = v_project_id
                  AND merge.source_work_item_id IN (
                      v_source_work_item_id,
                      v_target_work_item_id
                  )
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate aliases cannot gain or lose relationships';
            END IF;

            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END
        $function$;

        CREATE TRIGGER duplicate_relationship_mutation_guard
        BEFORE INSERT OR UPDATE OR DELETE ON {schema}.work_relationships
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_duplicate_relationship_mutation();
        """
    )


def _create_alias_owned_fact_guards(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_reject_alias_work_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF EXISTS (
                    SELECT 1
                    FROM {schema}.work_duplicate_merges AS merge
                    WHERE merge.project_id = OLD.project_id
                      AND OLD.id IN (
                          merge.source_work_item_id,
                          merge.destination_work_item_id
                      )
                ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'duplicate merge endpoints cannot be deleted';
                END IF;
            ELSIF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
                IF EXISTS (
                    SELECT 1
                    FROM {schema}.work_duplicate_merges AS merge
                    WHERE merge.project_id = OLD.project_id
                      AND OLD.id IN (
                          merge.source_work_item_id,
                          merge.destination_work_item_id
                      )
                ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'duplicate merge endpoints cannot be deleted';
                END IF;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM {schema}.work_duplicate_merges AS merge
                WHERE merge.project_id = OLD.project_id
                  AND merge.source_work_item_id = OLD.id
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate aliases are immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END
        $function$;

        CREATE TRIGGER duplicate_alias_work_mutation_guard
        BEFORE UPDATE OR DELETE ON {schema}.work_items
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_reject_alias_work_mutation();

        CREATE FUNCTION {schema}.mnemonic_reject_alias_owned_fact()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_old_work_item_id uuid;
            v_new_work_item_id uuid;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                v_old_work_item_id := OLD.work_item_id;
            END IF;
            IF TG_OP <> 'DELETE' THEN
                v_new_work_item_id := NEW.work_item_id;
            END IF;

            PERFORM 1
            FROM {schema}.work_items
            WHERE id IN (v_old_work_item_id, v_new_work_item_id)
            ORDER BY id
            FOR UPDATE;

            IF EXISTS (
                SELECT 1
                FROM {schema}.work_duplicate_merges AS merge
                WHERE merge.source_work_item_id IN (
                    v_old_work_item_id,
                    v_new_work_item_id
                )
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate aliases cannot mutate owned facts';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END
        $function$;

        CREATE TRIGGER duplicate_alias_checkpoint_guard
        BEFORE INSERT OR UPDATE OR DELETE ON {schema}.checkpoints
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_reject_alias_owned_fact();

        CREATE TRIGGER duplicate_alias_lease_guard
        BEFORE INSERT OR UPDATE OR DELETE ON {schema}.work_leases
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_reject_alias_owned_fact();

        CREATE TRIGGER duplicate_alias_gate_guard
        BEFORE INSERT OR UPDATE OR DELETE ON {schema}.work_gates
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_reject_alias_owned_fact();
        """
    )


def _create_duplicate_event_guard(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_duplicate_work_event()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_merge {schema}.work_duplicate_merges%ROWTYPE;
            v_relationship {schema}.work_relationships%ROWTYPE;
        BEGIN
            PERFORM 1
            FROM {schema}.work_items
            WHERE id = NEW.work_item_id
              AND project_id = NEW.project_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN NEW;
            END IF;

            IF NEW.event_type = 'work_merged' THEN
                SELECT *
                INTO v_merge
                FROM {schema}.work_duplicate_merges
                WHERE project_id = NEW.project_id
                  AND id = NEW.work_duplicate_merge_id;
                IF NOT FOUND
                   OR NEW.work_item_id NOT IN (
                       v_merge.source_work_item_id,
                       v_merge.destination_work_item_id
                   )
                   OR NEW.created_for_duplicate_merge_id IS NOT NULL
                   OR NEW.actor_kind <> 'client'
                   OR NEW.actor_client IS DISTINCT FROM v_merge.merged_by_client
                   OR NEW.actor_session_id IS DISTINCT FROM v_merge.merged_by_session_id
                   OR NEW.actor_model IS DISTINCT FROM v_merge.merged_by_model
                   OR NEW.body IS DISTINCT FROM v_merge.rationale
                   OR NEW.origin <> 'live'
                   OR NEW.created_at IS DISTINCT FROM v_merge.created_at THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'work merged event does not match its merge';
                END IF;
                RETURN NEW;
            END IF;

            IF NEW.created_for_duplicate_merge_id IS NOT NULL THEN
                SELECT *
                INTO v_merge
                FROM {schema}.work_duplicate_merges
                WHERE project_id = NEW.project_id
                  AND id = NEW.created_for_duplicate_merge_id;
                SELECT *
                INTO v_relationship
                FROM {schema}.work_relationships
                WHERE project_id = NEW.project_id
                  AND id = NEW.relationship_id;
                IF v_merge.id IS NULL
                   OR v_relationship.id IS NULL
                   OR NEW.event_type <> 'relationship_added'
                   OR NEW.work_duplicate_merge_id IS NOT NULL
                   OR v_relationship.created_for_duplicate_merge_id
                      IS DISTINCT FROM v_merge.id
                   OR v_relationship.id IS DISTINCT FROM v_merge.duplicate_relationship_id
                   OR NEW.work_item_id NOT IN (
                       v_merge.source_work_item_id,
                       v_merge.destination_work_item_id
                   )
                   OR NEW.actor_kind <> 'client'
                   OR NEW.actor_client IS DISTINCT FROM v_merge.merged_by_client
                   OR NEW.actor_session_id IS DISTINCT FROM v_merge.merged_by_session_id
                   OR NEW.actor_model IS DISTINCT FROM v_merge.merged_by_model
                   OR NEW.origin <> 'live'
                   OR NEW.created_at IS DISTINCT FROM v_merge.created_at THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'relationship event does not match its merge witness';
                END IF;
                RETURN NEW;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM {schema}.work_duplicate_merges AS merge
                WHERE merge.project_id = NEW.project_id
                  AND merge.source_work_item_id = NEW.work_item_id
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'duplicate aliases cannot receive user-authored events';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER duplicate_work_event_guard
        BEFORE INSERT ON {schema}.work_events
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_duplicate_work_event();
        """
    )


def upgrade() -> None:
    # Drain Phase 5/8 deferred guards before altering either retained fact table.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    schema = _quoted_current_schema()
    _create_merge_table()
    _extend_relationships()
    _create_work_merged_validator(schema)
    _extend_work_events()
    _replace_work_event_checks()
    _replace_operation_kinds()
    _create_component_state_function(schema)
    _create_merge_insert_guard(schema)
    _create_merge_immutability(schema)
    _create_completeness_guards(schema)
    _create_relationship_guards(schema)
    _create_alias_owned_fact_guards(schema)
    _create_duplicate_event_guard(schema)


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade from 0016_duplicate_handling is unsupported; restore a pre-upgrade backup"
    )
