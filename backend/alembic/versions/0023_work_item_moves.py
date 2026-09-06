"""Add identity-preserving work-item moves between projects.

Revision ID: 0023_work_item_moves
Revises: 0022_external_references
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_work_item_moves"
down_revision: str | None = "0022_external_references"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPERATION_KINDS = (
    "'create_work', 'add_checkpoint', 'append_event', 'add_relationship', "
    "'update_work', 'defer_work', 'complete_work', 'delete_work', "
    "'remove_relationship', 'release_claim', 'request_human_input', "
    "'resolve_human_input', 'merge_work', 'dismiss_job_completion_report', "
    "'create_job_completion_report_follow_up'"
)
_EVENT_TYPES = (
    "'work_created', 'work_updated', 'work_status_changed', 'work_reopened', "
    "'work_claimed', 'work_released', 'checkpoint_added', 'progress', "
    "'dependency_added', 'dependency_removed', 'relationship_added', "
    "'relationship_removed', 'work_completed', 'work_deleted', 'work_merged', "
    "'human_attention_requested', 'human_attention_resolved'"
)
_EVENT_VALIDATOR_ARGUMENTS = (
    "event_type, origin, work_item_id, checkpoint_id, lease_generation_id, "
    "lease_release_id, relationship_id, relationship_source_work_item_id, "
    "relationship_target_work_item_id, relationship_context_checkpoint_work_item_id, "
    "relationship_context_checkpoint_id, metadata_version, metadata"
)

_HISTORICAL_OWNER_FKS = (
    (
        "verification_results",
        "fk_verification_results_work_item",
        ("project_id", "work_item_id"),
        ("project_id", "id"),
        ("work_item_id",),
        ("id",),
    ),
    (
        "artifact_references",
        "fk_artifact_references_work_item",
        ("project_id", "work_item_id"),
        ("project_id", "id"),
        ("work_item_id",),
        ("id",),
    ),
    (
        "work_gates",
        "fk_work_gates_work_item",
        ("project_id", "work_item_id"),
        ("project_id", "id"),
        ("work_item_id",),
        ("id",),
    ),
    (
        "work_events",
        "fk_work_events_work_item",
        ("project_id", "work_item_id"),
        ("project_id", "id"),
        ("work_item_id",),
        ("id",),
    ),
    (
        "work_events",
        "fk_work_events_relationship_source_work_item",
        ("project_id", "relationship_source_work_item_id"),
        ("project_id", "id"),
        ("relationship_source_work_item_id",),
        ("id",),
    ),
    (
        "work_events",
        "fk_work_events_relationship_target_work_item",
        ("project_id", "relationship_target_work_item_id"),
        ("project_id", "id"),
        ("relationship_target_work_item_id",),
        ("id",),
    ),
    (
        "project_activity",
        "fk_project_activity_work",
        ("project_id", "work_item_id"),
        ("project_id", "id"),
        ("work_item_id",),
        ("id",),
    ),
    (
        "job_completion_reports",
        "fk_job_reports_work",
        ("project_id", "work_item_id"),
        ("project_id", "id"),
        ("work_item_id",),
        ("id",),
    ),
    (
        "job_completion_report_follow_ups",
        "fk_job_report_follow_ups_work",
        ("project_id", "follow_up_work_item_id"),
        ("project_id", "id"),
        ("follow_up_work_item_id",),
        ("id",),
    ),
)


def _schema() -> str:
    bind = op.get_bind()
    value = bind.scalar(sa.text("SELECT current_schema()"))
    if not isinstance(value, str):
        raise RuntimeError("Work-item moves require an explicit PostgreSQL schema")
    return bind.dialect.identifier_preparer.quote_identifier(value)


def _replace_operation_kinds(*, include_move: bool) -> None:
    op.drop_constraint(
        op.f("ck_client_operations_operation_kind_valid"),
        "client_operations",
        type_="check",
    )
    kinds = (
        _OPERATION_KINDS.replace(
            "'delete_work', ",
            "'delete_work', 'move_work', ",
        )
        if include_move
        else _OPERATION_KINDS
    )
    op.create_check_constraint(
        op.f("ck_client_operations_operation_kind_valid"),
        "client_operations",
        f"operation_kind IN ({kinds})",
    )


def _replace_historical_owner_fks(*, movable: bool) -> None:
    for table, name, old_local, old_remote, new_local, new_remote in _HISTORICAL_OWNER_FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        local = new_local if movable else old_local
        remote = new_remote if movable else old_remote
        op.create_foreign_key(
            name,
            table,
            "work_items",
            list(local),
            list(remote),
            ondelete="RESTRICT",
        )


def _create_move_table() -> None:
    op.create_table(
        "work_item_moves",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("source_project_id", sa.UUID(), nullable=False),
        sa.Column("target_project_id", sa.UUID(), nullable=False),
        sa.Column("source_work_version", sa.Integer(), nullable=False),
        sa.Column("resulting_work_version", sa.Integer(), nullable=False),
        sa.Column("preserved_status", sa.String(length=20), nullable=False),
        sa.Column("actor_kind", sa.String(length=20), nullable=False),
        sa.Column("actor_client", sa.String(length=80), nullable=True),
        sa.Column("actor_session_id", sa.String(length=200), nullable=True),
        sa.Column("actor_model", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_project_id <> target_project_id",
            name=op.f("ck_work_item_moves_projects_distinct"),
        ),
        sa.CheckConstraint(
            "source_work_version >= 1 "
            "AND resulting_work_version = source_work_version + 1",
            name=op.f("ck_work_item_moves_versions_valid"),
        ),
        sa.CheckConstraint(
            "preserved_status IN ('pending', 'deferred', 'done', 'wont-do', 'promoted')",
            name=op.f("ck_work_item_moves_status_valid"),
        ),
        sa.CheckConstraint(
            "(actor_kind = 'client' AND actor_client IS NOT NULL "
            "AND mnemonic_has_non_whitespace(actor_client) "
            "AND actor_session_id IS NOT NULL "
            "AND mnemonic_has_non_whitespace(actor_session_id) "
            "AND (actor_model IS NULL OR mnemonic_has_non_whitespace(actor_model))) OR "
            "(actor_kind = 'unattributed' AND actor_client IS NULL "
            "AND actor_session_id IS NULL AND actor_model IS NULL)",
            name=op.f("ck_work_item_moves_actor_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_work_item_moves_work_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_project_id"],
            ["projects.id"],
            name="fk_work_item_moves_source_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_project_id"],
            ["projects.id"],
            name="fk_work_item_moves_target_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_item_moves")),
        sa.UniqueConstraint(
            "work_item_id",
            "resulting_work_version",
            name="uq_work_item_moves_work_version",
        ),
    )
    op.create_index(
        "ix_work_item_moves_work_created",
        "work_item_moves",
        ["work_item_id", "created_at", "id"],
    )


def _create_work_provenance_sequences(s: str) -> None:
    op.create_table(
        "work_report_provenance_heads",
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "last_sequence >= 0",
            name=op.f("ck_work_report_provenance_heads_sequence_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_work_report_provenance_heads_work_item",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "work_item_id",
            name=op.f("pk_work_report_provenance_heads"),
        ),
    )
    op.add_column(
        "job_completion_report_follow_ups",
        sa.Column("source_work_sequence", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "job_completion_report_follow_ups",
        sa.Column("follow_up_work_sequence", sa.BigInteger(), nullable=True),
    )
    op.execute(
        f"ALTER TABLE {s}.job_completion_report_follow_ups "
        "DISABLE TRIGGER job_report_immutable"
    )
    op.execute(
        f"""
        WITH endpoint_facts AS (
            SELECT id,source_work_item_id AS work_item_id,
                   'source'::text AS role,created_at
            FROM {s}.job_completion_report_follow_ups
            UNION ALL
            SELECT id,follow_up_work_item_id AS work_item_id,
                   'follow_up'::text AS role,created_at
            FROM {s}.job_completion_report_follow_ups
        ), ranked AS (
            SELECT id,role,
                   row_number() OVER (
                       PARTITION BY work_item_id
                       ORDER BY created_at,id,role
                   ) AS sequence
            FROM endpoint_facts
        ), pivoted AS (
            SELECT id,
                   max(sequence) FILTER (WHERE role='source') AS source_sequence,
                   max(sequence) FILTER (WHERE role='follow_up') AS follow_up_sequence
            FROM ranked
            GROUP BY id
        )
        UPDATE {s}.job_completion_report_follow_ups AS follow_up
        SET source_work_sequence=pivoted.source_sequence,
            follow_up_work_sequence=pivoted.follow_up_sequence
        FROM pivoted
        WHERE follow_up.id=pivoted.id
        """
    )
    op.execute(
        f"ALTER TABLE {s}.job_completion_report_follow_ups "
        "ENABLE TRIGGER job_report_immutable"
    )
    op.execute(
        f"""
        INSERT INTO {s}.work_report_provenance_heads(work_item_id,last_sequence)
        SELECT work_item_id,max(sequence)
        FROM (
            SELECT source_work_item_id AS work_item_id,
                   source_work_sequence AS sequence
            FROM {s}.job_completion_report_follow_ups
            UNION ALL
            SELECT follow_up_work_item_id AS work_item_id,
                   follow_up_work_sequence AS sequence
            FROM {s}.job_completion_report_follow_ups
        ) AS provenance
        GROUP BY work_item_id
        """
    )
    op.alter_column(
        "job_completion_report_follow_ups",
        "source_work_sequence",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.alter_column(
        "job_completion_report_follow_ups",
        "follow_up_work_sequence",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_job_completion_report_follow_ups_source_work_sequence_positive"),
        "job_completion_report_follow_ups",
        "source_work_sequence > 0",
    )
    op.create_check_constraint(
        op.f("ck_job_completion_report_follow_ups_follow_up_work_sequence_positive"),
        "job_completion_report_follow_ups",
        "follow_up_work_sequence > 0",
    )
    op.create_unique_constraint(
        "uq_job_report_follow_ups_source_work_sequence",
        "job_completion_report_follow_ups",
        ["source_work_item_id", "source_work_sequence"],
    )
    op.create_unique_constraint(
        "uq_job_report_follow_ups_follow_up_work_sequence",
        "job_completion_report_follow_ups",
        ["follow_up_work_item_id", "follow_up_work_sequence"],
    )


def _replace_follow_up_allocator(s: str, *, sequenced: bool) -> None:
    definition = op.get_bind().scalar(
        sa.text(
            f"SELECT pg_get_functiondef('{s}.mnemonic_activity_follow_up_source()'"
            "::regprocedure)"
        )
    )
    if not isinstance(definition, str):
        raise RuntimeError("Report follow-up allocator is unavailable")
    anchor = "        NEW.created_at:=clock_timestamp();\n"
    addition = f"""        INSERT INTO {s}.work_report_provenance_heads(
            work_item_id,last_sequence
        )
        SELECT endpoint_id,0
        FROM pg_catalog.unnest(ARRAY[
            NEW.source_work_item_id,NEW.follow_up_work_item_id
        ]) AS endpoint(endpoint_id)
        ORDER BY endpoint_id
        ON CONFLICT (work_item_id) DO NOTHING;
        PERFORM head.work_item_id
        FROM {s}.work_report_provenance_heads AS head
        WHERE head.work_item_id=ANY(ARRAY[
            NEW.source_work_item_id,NEW.follow_up_work_item_id
        ])
        ORDER BY head.work_item_id
        FOR UPDATE;
        UPDATE {s}.work_report_provenance_heads
        SET last_sequence=last_sequence+1
        WHERE work_item_id=NEW.source_work_item_id
          AND last_sequence<9223372036854775807
        RETURNING last_sequence INTO NEW.source_work_sequence;
        UPDATE {s}.work_report_provenance_heads
        SET last_sequence=last_sequence+1
        WHERE work_item_id=NEW.follow_up_work_item_id
          AND last_sequence<9223372036854775807
        RETURNING last_sequence INTO NEW.follow_up_work_sequence;
        IF NEW.source_work_sequence IS NULL OR NEW.follow_up_work_sequence IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='work report provenance head missing or exhausted';
        END IF;
"""
    timestamp_assignment = f"""        SELECT greatest(
            clock_timestamp(),
            coalesce(
                pg_catalog.max(prior.created_at)+interval '1 microsecond',
                '-infinity'::timestamptz
            )
        )
        INTO NEW.created_at
        FROM (
            SELECT created_at
            FROM {s}.job_completion_report_follow_ups
            WHERE source_work_item_id=ANY(ARRAY[
                NEW.source_work_item_id,NEW.follow_up_work_item_id
            ])
            UNION ALL
            SELECT created_at
            FROM {s}.job_completion_report_follow_ups
            WHERE follow_up_work_item_id=ANY(ARRAY[
                NEW.source_work_item_id,NEW.follow_up_work_item_id
            ])
        ) AS prior;
"""
    replacement = addition + timestamp_assignment
    before = anchor if sequenced else replacement
    after = replacement if sequenced else anchor
    if definition.count(before) != 1:
        raise RuntimeError("Report follow-up allocator does not match revision 0022")
    op.execute(sa.text(definition.replace(before, after)))


def _create_work_provenance_guards(s: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {s}.mnemonic_guard_work_report_provenance_head()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path=pg_catalog
        AS $function$
        BEGIN
            IF TG_RELID<>'{s}.work_report_provenance_heads'::regclass
               OR TG_NAME NOT IN (
                   'work_report_provenance_head_guard',
                   'work_report_provenance_head_truncate_guard'
               ) THEN
                RAISE EXCEPTION USING ERRCODE='55000',
                    MESSAGE='work report provenance head guard is misconfigured';
            END IF;
            IF TG_OP='INSERT' AND pg_trigger_depth()>=2
               AND {s}.mnemonic_phase12_call_path('mnemonic_activity_follow_up_source')
               AND NEW.last_sequence=0 THEN
                RETURN NEW;
            END IF;
            IF TG_OP='UPDATE'
               AND NEW.work_item_id IS NOT DISTINCT FROM OLD.work_item_id
               AND OLD.last_sequence<9223372036854775807
               AND NEW.last_sequence=OLD.last_sequence+1
               AND pg_trigger_depth()>=2
               AND {s}.mnemonic_phase12_call_path('mnemonic_activity_follow_up_source') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='work report provenance head is source managed';
        END
        $function$;

        CREATE TRIGGER work_report_provenance_head_guard
        BEFORE INSERT OR UPDATE OR DELETE ON {s}.work_report_provenance_heads
        FOR EACH ROW
        EXECUTE FUNCTION {s}.mnemonic_guard_work_report_provenance_head();

        CREATE TRIGGER work_report_provenance_head_truncate_guard
        BEFORE TRUNCATE ON {s}.work_report_provenance_heads
        FOR EACH STATEMENT
        EXECUTE FUNCTION {s}.mnemonic_guard_work_report_provenance_head();
        """
    )


def _create_move_metadata_validator(s: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {s}.mnemonic_work_moved_metadata_v1_is_valid(
            p_work_item_id uuid,
            p_project_id uuid,
            p_work_move_id uuid,
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
            v_source_project_id uuid;
            v_target_project_id uuid;
            v_work_version bigint;
        BEGIN
            IF p_work_item_id IS NULL
               OR p_project_id IS NULL
               OR p_metadata_version <> 1
               OR p_work_move_id IS NULL
               OR p_metadata IS NULL
               OR pg_catalog.jsonb_typeof(p_metadata) <> 'object' THEN
                RETURN false;
            END IF;

            SELECT pg_catalog.array_agg(key ORDER BY key)
            INTO v_keys
            FROM pg_catalog.jsonb_object_keys(p_metadata) AS metadata_key(key);
            IF v_keys IS DISTINCT FROM ARRAY[
                'move_id',
                'role',
                'source_project_id',
                'target_project_id',
                'work_version'
            ]::text[]
               OR pg_catalog.jsonb_typeof(p_metadata -> 'move_id') <> 'string'
               OR p_metadata ->> 'move_id' !~
                  '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
               OR (p_metadata ->> 'move_id')::uuid IS DISTINCT FROM p_work_move_id
               OR pg_catalog.jsonb_typeof(p_metadata -> 'source_project_id') <> 'string'
               OR p_metadata ->> 'source_project_id' !~
                  '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
               OR pg_catalog.jsonb_typeof(p_metadata -> 'target_project_id') <> 'string'
               OR p_metadata ->> 'target_project_id' !~
                  '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
               OR pg_catalog.jsonb_typeof(p_metadata -> 'role') <> 'string'
               OR p_metadata ->> 'role' NOT IN ('source', 'target')
               OR pg_catalog.jsonb_typeof(p_metadata -> 'work_version') <> 'number'
               OR p_metadata ->> 'work_version' !~ '^[1-9][0-9]*$' THEN
                RETURN false;
            END IF;

            v_role := p_metadata ->> 'role';
            v_source_project_id := (p_metadata ->> 'source_project_id')::uuid;
            v_target_project_id := (p_metadata ->> 'target_project_id')::uuid;
            v_work_version := (p_metadata ->> 'work_version')::bigint;
            RETURN v_source_project_id <> v_target_project_id
                AND v_work_version BETWEEN 2 AND 2147483647
                AND p_project_id = CASE v_role
                    WHEN 'source' THEN v_source_project_id
                    ELSE v_target_project_id
                END;
        EXCEPTION
            WHEN others THEN
                RETURN false;
        END
        $function$;
        """
    )


def _replace_work_event_checks(*, include_move: bool) -> None:
    for name in ("event_type_valid", "metadata_v1_valid"):
        op.drop_constraint(op.f(f"ck_work_events_{name}"), "work_events", type_="check")

    event_types = (
        _EVENT_TYPES.replace(
            "'work_merged', ",
            "'work_merged', 'work_moved', ",
        )
        if include_move
        else _EVENT_TYPES
    )
    op.create_check_constraint(
        op.f("ck_work_events_event_type_valid"),
        "work_events",
        f"event_type IN ({event_types})",
    )
    if include_move:
        metadata_check = (
            "(event_type IN ('human_attention_requested', 'human_attention_resolved') "
            "AND metadata_version = 1 "
            "AND metadata = jsonb_build_object('gate_id', gate_id::text, "
            "'gate_type', 'human')) OR "
            "(event_type = 'work_merged' AND "
            "mnemonic_work_merged_metadata_v1_is_valid(work_item_id, "
            "work_duplicate_merge_id, metadata_version, metadata)) OR "
            "(event_type = 'work_moved' AND "
            "mnemonic_work_moved_metadata_v1_is_valid(work_item_id, project_id, "
            "work_move_id, metadata_version, metadata)) OR "
            "(event_type NOT IN ('human_attention_requested', 'human_attention_resolved', "
            "'work_merged', 'work_moved') AND mnemonic_work_event_metadata_v2_is_valid("
            f"{_EVENT_VALIDATOR_ARGUMENTS}))"
        )
    else:
        metadata_check = (
            "(event_type IN ('human_attention_requested', 'human_attention_resolved') "
            "AND metadata_version = 1 "
            "AND metadata = jsonb_build_object('gate_id', gate_id::text, "
            "'gate_type', 'human')) OR "
            "(event_type = 'work_merged' AND "
            "mnemonic_work_merged_metadata_v1_is_valid(work_item_id, "
            "work_duplicate_merge_id, metadata_version, metadata)) OR "
            "(event_type NOT IN ('human_attention_requested', 'human_attention_resolved', "
            "'work_merged') AND mnemonic_work_event_metadata_v2_is_valid("
            f"{_EVENT_VALIDATOR_ARGUMENTS}))"
        )
    op.create_check_constraint(
        op.f("ck_work_events_metadata_v1_valid"),
        "work_events",
        metadata_check,
    )


def _extend_work_events() -> None:
    op.add_column("work_events", sa.Column("work_move_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_work_events_work_move",
        "work_events",
        "work_item_moves",
        ["work_move_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_work_events_work_move_reference_valid"),
        "work_events",
        "(event_type = 'work_moved' AND work_move_id IS NOT NULL) OR "
        "(event_type <> 'work_moved' AND work_move_id IS NULL)",
    )
    op.create_index(
        "uq_work_events_move_project",
        "work_events",
        ["work_move_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("work_move_id IS NOT NULL"),
    )
    op.create_index(
        "ix_work_events_work_timeline",
        "work_events",
        ["work_item_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )


def _replace_job_report_slot_seal(s: str, *, movable: bool) -> None:
    if not movable:
        op.execute(f"""
    CREATE OR REPLACE FUNCTION {s}.mnemonic_job_report_slot_sealed(work_id uuid, slot integer)
    RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $f$
        SELECT slot IS NULL OR EXISTS (
            SELECT 1 FROM {s}.job_completion_reports report
            JOIN {s}.work_events event ON event.id=report.closeout_event_id
              AND event.job_completion_report_id=report.id
              AND event.project_id=report.project_id AND event.work_item_id=report.work_item_id
            WHERE report.project_id=(SELECT project_id FROM {s}.work_items WHERE id=work_id)
              AND report.work_item_id=work_id AND report.closeout_work_version=slot
        )
    $f$;
    """)
        return
    project_filter = (
        "report.project_id=(SELECT project_id FROM "
        f"{s}.work_items WHERE id=work_id) AND "
        if not movable else ""
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {s}.mnemonic_job_report_slot_sealed(
            work_id uuid,
            slot integer
        )
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SET search_path = pg_catalog
        AS $function$
            SELECT slot IS NULL OR EXISTS (
                SELECT 1
                FROM {s}.job_completion_reports AS report
                JOIN {s}.work_events AS event
                  ON event.id = report.closeout_event_id
                 AND event.job_completion_report_id = report.id
                 AND event.project_id = report.project_id
                 AND event.work_item_id = report.work_item_id
                WHERE {project_filter}report.work_item_id = work_id
                  AND report.closeout_work_version = slot
            )
        $function$;
        """
    )


def _replace_completion_episode_seal(s: str, *, movable: bool) -> None:
    """Make the inherited episode predicate recognize immutable origin facts."""
    helper = f"{s}.mnemonic_work_project_is_historical"
    replacements = {
        "completion_row.project_id IS DISTINCT FROM work_row.project_id": (
            f"NOT {helper}(requested_work_id, completion_row.project_id)"
        ),
        "reopen_row.project_id IS DISTINCT FROM work_row.project_id": (
            f"NOT {helper}(requested_work_id, reopen_row.project_id)"
        ),
        "successor_row.project_id IS DISTINCT FROM work_row.project_id": (
            f"NOT {helper}(requested_work_id, successor_row.project_id)"
        ),
        "result.project_id IS DISTINCT FROM work_row.project_id": (
            "result.project_id IS DISTINCT FROM completion_row.project_id"
        ),
        "artifact.project_id IS DISTINCT FROM work_row.project_id": (
            "artifact.project_id IS DISTINCT FROM completion_row.project_id"
        ),
    }
    if movable:
        op.execute(
            f"""
            CREATE FUNCTION {helper}(requested_work_id uuid, requested_project_id uuid)
            RETURNS boolean
            LANGUAGE sql
            STABLE
            PARALLEL SAFE
            SET search_path = pg_catalog
            AS $function$
                SELECT EXISTS (
                    SELECT 1 FROM {s}.work_items AS work
                    WHERE work.id=requested_work_id
                      AND work.project_id=requested_project_id
                ) OR EXISTS (
                    SELECT 1 FROM {s}.work_item_moves AS move
                    WHERE move.work_item_id=requested_work_id
                      AND requested_project_id IN (
                          move.source_project_id,move.target_project_id
                      )
                )
            $function$;
            """
        )
    definition = op.get_bind().scalar(
        sa.text(
            """
            SELECT pg_get_functiondef(routine.oid)
            FROM pg_proc AS routine
            JOIN pg_namespace AS namespace ON namespace.oid=routine.pronamespace
            WHERE namespace.nspname=current_schema()
              AND routine.proname='mnemonic_completion_episode_is_sealed'
              AND pg_catalog.oidvectortypes(routine.proargtypes)='uuid, bigint'
            """
        )
    )
    if not isinstance(definition, str):
        raise RuntimeError("Completion episode predicate is unavailable")
    changes = replacements if movable else {value: key for key, value in replacements.items()}
    for before, after in changes.items():
        if definition.count(before) != 1:
            raise RuntimeError("Completion episode predicate does not match revision 0022")
        definition = definition.replace(before, after)
    op.get_bind().exec_driver_sql(definition)
    if not movable:
        op.execute(f"DROP FUNCTION {helper}(uuid,uuid)")


def _replace_duplicate_merge_event_scope(s: str, *, movable: bool) -> None:
    """Make merge review counts follow stable work identity across a move."""
    routines = (
        (
            "mnemonic_guard_duplicate_merge_insert",
            "",
            (
                (
                    "WHERE project_id = NEW.project_id\n"
                    "              AND work_item_id = NEW.source_work_item_id",
                    "WHERE work_item_id = NEW.source_work_item_id",
                ),
                (
                    "WHERE project_id = NEW.project_id\n"
                    "              AND work_item_id = NEW.destination_work_item_id",
                    "WHERE work_item_id = NEW.destination_work_item_id",
                ),
            ),
        ),
        (
            "mnemonic_duplicate_merge_is_complete",
            "uuid, uuid",
            (
                (
                    "WHERE project_id = v_merge.project_id\n"
                    "              AND work_item_id = v_merge.source_work_item_id",
                    "WHERE work_item_id = v_merge.source_work_item_id",
                ),
                (
                    "WHERE project_id = v_merge.project_id\n"
                    "              AND work_item_id = v_merge.destination_work_item_id",
                    "WHERE work_item_id = v_merge.destination_work_item_id",
                ),
            ),
        ),
    )
    for routine_name, arguments, replacements in routines:
        definition = op.get_bind().scalar(
            sa.text(
                """
                SELECT pg_get_functiondef(routine.oid)
                FROM pg_proc AS routine
                JOIN pg_namespace AS namespace ON namespace.oid=routine.pronamespace
                WHERE namespace.nspname=current_schema()
                  AND routine.proname=:routine_name
                  AND pg_catalog.oidvectortypes(routine.proargtypes)=:arguments
                """
            ),
            {"routine_name": routine_name, "arguments": arguments},
        )
        if not isinstance(definition, str):
            raise RuntimeError(f"Duplicate merge routine {routine_name} is unavailable")
        changes = replacements if movable else tuple(
            (after, before) for before, after in replacements
        )
        for before, after in changes:
            if definition.count(before) != 1:
                raise RuntimeError(
                    f"Duplicate merge routine {routine_name} does not match revision 0022"
                )
            definition = definition.replace(before, after)
        op.execute(sa.text(definition))


def _create_move_guards(s: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {s}.mnemonic_guard_work_move_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_work {s}.work_items%ROWTYPE;
        BEGIN
            IF TG_RELID <> '{s}.work_item_moves'::regclass
               OR TG_TABLE_NAME <> 'work_item_moves'
               OR TG_OP <> 'INSERT'
               OR TG_NAME <> 'work_move_insert_guard'
               OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'work move insert guard is misconfigured';
            END IF;

            SELECT *
            INTO v_work
            FROM {s}.work_items
            WHERE id = NEW.work_item_id
            FOR UPDATE;
            IF NOT FOUND
               OR v_work.project_id IS DISTINCT FROM NEW.source_project_id
               OR v_work.version IS DISTINCT FROM NEW.source_work_version
               OR v_work.status IS DISTINCT FROM NEW.preserved_status
               OR v_work.deleted_at IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'work move does not match current visible work';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM {s}.work_relationships AS relationship
                WHERE relationship.source_work_item_id = NEW.work_item_id
                   OR relationship.target_work_item_id = NEW.work_item_id
            ) OR EXISTS (
                SELECT 1
                FROM {s}.work_duplicate_merges AS merge
                WHERE merge.source_work_item_id = NEW.work_item_id
                   OR merge.destination_work_item_id = NEW.work_item_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'related or duplicate work cannot be moved';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM {s}.work_gates AS gate
                WHERE gate.work_item_id = NEW.work_item_id
                  AND gate.resolved_at IS NULL
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'unresolved work gates prevent a move';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM {s}.work_leases AS lease
                WHERE lease.work_item_id = NEW.work_item_id
                  AND lease.expires_at > pg_catalog.clock_timestamp()
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'active work leases prevent a move';
            END IF;
            IF v_work.status = 'done' AND NOT {s}.mnemonic_completion_episode_is_sealed(
                v_work.id,
                v_work.completion_generation
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'done work requires sealed completion evidence before moving';
            END IF;
            IF (
                v_work.status IN ('done', 'wont-do', 'promoted')
                AND v_work.last_reportable_closeout_version IS NULL
            ) OR (
                v_work.last_reportable_closeout_version IS NOT NULL
                AND NOT {s}.mnemonic_job_report_slot_sealed(
                    v_work.id,
                    v_work.last_reportable_closeout_version
                )
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'work requires a sealed closeout report before moving';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER work_move_insert_guard
        BEFORE INSERT ON {s}.work_item_moves
        FOR EACH ROW
        EXECUTE FUNCTION {s}.mnemonic_guard_work_move_insert();

        CREATE FUNCTION {s}.mnemonic_guard_work_project_move()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_move {s}.work_item_moves%ROWTYPE;
        BEGIN
            IF TG_RELID <> '{s}.work_items'::regclass
               OR TG_TABLE_NAME <> 'work_items'
               OR TG_OP <> 'UPDATE'
               OR TG_NAME <> 'work_project_move_guard'
               OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'work project move guard is misconfigured';
            END IF;
            IF NEW.project_id IS NOT DISTINCT FROM OLD.project_id THEN
                RETURN NEW;
            END IF;
            IF OLD.deleted_at IS NOT NULL
               OR NEW.version IS DISTINCT FROM OLD.version + 1
               OR ROW(
                    NEW.title,
                    NEW.summary,
                    NEW.external_references,
                    NEW.status,
                    NEW.priority,
                    NEW.initial_checkpoint_id,
                    NEW.last_reportable_closeout_version,
                    NEW.completion_generation,
                    NEW.created_at,
                    NEW.deleted_at
               ) IS DISTINCT FROM ROW(
                    OLD.title,
                    OLD.summary,
                    OLD.external_references,
                    OLD.status,
                    OLD.priority,
                    OLD.initial_checkpoint_id,
                    OLD.last_reportable_closeout_version,
                    OLD.completion_generation,
                    OLD.created_at,
                    OLD.deleted_at
               ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'a work move may only change project, version, and update time';
            END IF;

            SELECT *
            INTO v_move
            FROM {s}.work_item_moves
            WHERE work_item_id = OLD.id
              AND source_project_id = OLD.project_id
              AND target_project_id = NEW.project_id
              AND source_work_version = OLD.version
              AND resulting_work_version = NEW.version;
            IF NOT FOUND
               OR v_move.preserved_status IS DISTINCT FROM OLD.status
               OR NEW.updated_at IS DISTINCT FROM v_move.created_at THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'work project transition requires its exact move fact';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER work_project_move_guard
        BEFORE UPDATE OF project_id ON {s}.work_items
        FOR EACH ROW
        EXECUTE FUNCTION {s}.mnemonic_guard_work_project_move();

        CREATE FUNCTION {s}.mnemonic_guard_work_move_event()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_move {s}.work_item_moves%ROWTYPE;
            v_expected_project_id uuid;
            v_endpoint_id uuid;
        BEGIN
            IF TG_RELID <> '{s}.work_events'::regclass
               OR TG_TABLE_NAME <> 'work_events'
               OR TG_OP <> 'INSERT'
               OR TG_NAME <> 'work_move_event_guard'
               OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'work move event guard is misconfigured';
            END IF;
            IF NEW.event_type <> 'work_moved' THEN
                IF NEW.work_move_id IS NOT NULL THEN
                    RAISE EXCEPTION USING ERRCODE = '23514',
                        MESSAGE = 'move references belong only to moved events';
                END IF;
                FOR v_endpoint_id IN
                    SELECT endpoint_id
                    FROM pg_catalog.unnest(ARRAY[
                        NEW.work_item_id,
                        NEW.relationship_source_work_item_id,
                        NEW.relationship_target_work_item_id
                    ]) AS endpoint_id
                    WHERE endpoint_id IS NOT NULL
                    GROUP BY endpoint_id
                    ORDER BY endpoint_id
                LOOP
                    PERFORM 1
                    FROM {s}.work_items AS work
                    WHERE work.id=v_endpoint_id
                      AND work.project_id=NEW.project_id
                    FOR SHARE;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION USING ERRCODE = '23514',
                            MESSAGE = 'non-move events require current project ownership';
                    END IF;
                END LOOP;
                RETURN NEW;
            END IF;

            SELECT *
            INTO v_move
            FROM {s}.work_item_moves
            WHERE id = NEW.work_move_id;
            v_expected_project_id := CASE NEW.metadata ->> 'role'
                WHEN 'source' THEN v_move.source_project_id
                WHEN 'target' THEN v_move.target_project_id
                ELSE NULL
            END;
            IF NOT FOUND
               OR NEW.work_item_id IS DISTINCT FROM v_move.work_item_id
               OR NEW.project_id IS DISTINCT FROM v_expected_project_id
               OR NEW.metadata ->> 'move_id' IS DISTINCT FROM v_move.id::text
               OR NEW.metadata ->> 'source_project_id'
                  IS DISTINCT FROM v_move.source_project_id::text
               OR NEW.metadata ->> 'target_project_id'
                  IS DISTINCT FROM v_move.target_project_id::text
               OR NEW.metadata ->> 'work_version'
                  IS DISTINCT FROM v_move.resulting_work_version::text
               OR NEW.actor_kind IS DISTINCT FROM v_move.actor_kind
               OR NEW.actor_client IS DISTINCT FROM v_move.actor_client
               OR NEW.actor_session_id IS DISTINCT FROM v_move.actor_session_id
               OR NEW.actor_model IS DISTINCT FROM v_move.actor_model
               OR NEW.body IS NOT NULL
               OR NEW.origin <> 'live'
               OR NEW.created_at IS DISTINCT FROM v_move.created_at THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'work moved event does not match its move fact';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER work_move_event_guard
        BEFORE INSERT ON {s}.work_events
        FOR EACH ROW
        EXECUTE FUNCTION {s}.mnemonic_guard_work_move_event();

        CREATE FUNCTION {s}.mnemonic_require_work_move_complete()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_work {s}.work_items%ROWTYPE;
            v_event_count bigint;
            v_source_count bigint;
            v_target_count bigint;
            v_source_event_id bigint;
            v_target_event_id bigint;
        BEGIN
            IF TG_RELID <> '{s}.work_item_moves'::regclass
               OR TG_TABLE_NAME <> 'work_item_moves'
               OR TG_OP <> 'INSERT'
               OR TG_NAME <> 'work_move_completeness_guard'
               OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'work move completeness guard is misconfigured';
            END IF;

            SELECT *
            INTO v_work
            FROM {s}.work_items
            WHERE id = NEW.work_item_id;
            SELECT pg_catalog.count(*),
                   pg_catalog.count(*) FILTER (
                       WHERE project_id = NEW.source_project_id
                         AND metadata ->> 'role' = 'source'
                   ),
                   pg_catalog.count(*) FILTER (
                       WHERE project_id = NEW.target_project_id
                         AND metadata ->> 'role' = 'target'
                   ),
                   pg_catalog.min(id) FILTER (
                       WHERE project_id = NEW.source_project_id
                         AND metadata ->> 'role' = 'source'
                   ),
                   pg_catalog.min(id) FILTER (
                       WHERE project_id = NEW.target_project_id
                         AND metadata ->> 'role' = 'target'
                   )
            INTO v_event_count, v_source_count, v_target_count,
                 v_source_event_id, v_target_event_id
            FROM {s}.work_events
            WHERE work_move_id = NEW.id;
            IF NOT FOUND
               OR v_work.project_id IS DISTINCT FROM NEW.target_project_id
               OR v_work.version IS DISTINCT FROM NEW.resulting_work_version
               OR v_work.status IS DISTINCT FROM NEW.preserved_status
               OR v_work.updated_at IS DISTINCT FROM NEW.created_at
               OR v_work.deleted_at IS NOT NULL
               OR v_event_count <> 2
               OR v_source_count <> 1
               OR v_target_count <> 1
               OR v_source_event_id >= v_target_event_id THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'work move requires its exact target state and paired events';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER work_move_completeness_guard
        AFTER INSERT ON {s}.work_item_moves
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION {s}.mnemonic_require_work_move_complete();

        CREATE FUNCTION {s}.mnemonic_reject_work_move_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF TG_RELID <> '{s}.work_item_moves'::regclass
               OR TG_TABLE_NAME <> 'work_item_moves'
               OR TG_NARGS <> 0
               OR TG_OP NOT IN ('UPDATE', 'DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'work move immutability guard is misconfigured';
            END IF;
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = 'work move history is immutable';
        END
        $function$;

        CREATE TRIGGER work_move_immutable
        BEFORE UPDATE OR DELETE ON {s}.work_item_moves
        FOR EACH ROW
        EXECUTE FUNCTION {s}.mnemonic_reject_work_move_mutation();

        CREATE TRIGGER work_move_truncate_guard
        BEFORE TRUNCATE ON {s}.work_item_moves
        FOR EACH STATEMENT
        EXECUTE FUNCTION {s}.mnemonic_reject_work_move_mutation();
        """
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    s = _schema()
    op.execute(
        f"LOCK TABLE {s}.projects, {s}.work_items, {s}.checkpoints, {s}.work_leases, "
        f"{s}.work_relationships, {s}.work_duplicate_merges, {s}.work_gates, "
        f"{s}.verification_results, {s}.artifact_references, {s}.work_events, "
        f"{s}.client_operations, {s}.project_activity, {s}.job_completion_reports, "
        f"{s}.job_completion_report_follow_ups IN ACCESS EXCLUSIVE MODE"
    )
    _create_move_table()
    _create_work_provenance_sequences(s)
    _replace_follow_up_allocator(s, sequenced=True)
    _create_work_provenance_guards(s)
    _replace_historical_owner_fks(movable=True)
    _create_move_metadata_validator(s)
    _extend_work_events()
    _replace_work_event_checks(include_move=True)
    _replace_operation_kinds(include_move=True)
    _replace_completion_episode_seal(s, movable=True)
    _replace_job_report_slot_seal(s, movable=True)
    _replace_duplicate_merge_event_scope(s, movable=True)
    _create_move_guards(s)


def _require_unused(s: str) -> None:
    used = op.get_bind().scalar(
        sa.text(
            f"""
            SELECT EXISTS(SELECT 1 FROM {s}.work_item_moves)
                OR EXISTS(
                    SELECT 1 FROM {s}.work_events
                    WHERE event_type = 'work_moved' OR work_move_id IS NOT NULL
                )
                OR EXISTS(
                    SELECT 1 FROM {s}.client_operations
                    WHERE operation_kind = 'move_work'
                )
            """
        )
    )
    if used:
        raise RuntimeError("Work-item move history or receipts exist; downgrade would lose facts")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    s = _schema()
    op.execute(
        f"LOCK TABLE {s}.projects, {s}.work_items, {s}.work_item_moves, {s}.work_events, "
        f"{s}.client_operations, {s}.verification_results, {s}.artifact_references, "
        f"{s}.work_gates, {s}.project_activity, {s}.job_completion_reports, "
        f"{s}.job_completion_report_follow_ups, "
        f"{s}.work_report_provenance_heads IN ACCESS EXCLUSIVE MODE"
    )
    _require_unused(s)

    op.execute(
        f"DROP TRIGGER work_report_provenance_head_truncate_guard "
        f"ON {s}.work_report_provenance_heads"
    )
    op.execute(
        f"DROP TRIGGER work_report_provenance_head_guard "
        f"ON {s}.work_report_provenance_heads"
    )
    _replace_follow_up_allocator(s, sequenced=False)
    op.drop_constraint(
        "uq_job_report_follow_ups_follow_up_work_sequence",
        "job_completion_report_follow_ups",
        type_="unique",
    )
    op.drop_constraint(
        "uq_job_report_follow_ups_source_work_sequence",
        "job_completion_report_follow_ups",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_job_completion_report_follow_ups_follow_up_work_sequence_positive"),
        "job_completion_report_follow_ups",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_job_completion_report_follow_ups_source_work_sequence_positive"),
        "job_completion_report_follow_ups",
        type_="check",
    )
    op.drop_column("job_completion_report_follow_ups", "follow_up_work_sequence")
    op.drop_column("job_completion_report_follow_ups", "source_work_sequence")
    op.drop_table("work_report_provenance_heads")
    op.execute(f"DROP FUNCTION {s}.mnemonic_guard_work_report_provenance_head()")

    op.execute(f"DROP TRIGGER work_move_completeness_guard ON {s}.work_item_moves")
    op.execute(f"DROP TRIGGER work_move_immutable ON {s}.work_item_moves")
    op.execute(f"DROP TRIGGER work_move_truncate_guard ON {s}.work_item_moves")
    op.execute(f"DROP TRIGGER work_move_insert_guard ON {s}.work_item_moves")
    op.execute(f"DROP TRIGGER work_project_move_guard ON {s}.work_items")
    op.execute(f"DROP TRIGGER work_move_event_guard ON {s}.work_events")

    op.drop_index("ix_work_events_work_timeline", table_name="work_events")
    op.drop_index("uq_work_events_move_project", table_name="work_events")
    op.drop_constraint("fk_work_events_work_move", "work_events", type_="foreignkey")
    op.drop_constraint(
        op.f("ck_work_events_work_move_reference_valid"),
        "work_events",
        type_="check",
    )
    _replace_work_event_checks(include_move=False)
    op.drop_column("work_events", "work_move_id")
    _replace_historical_owner_fks(movable=False)
    _replace_operation_kinds(include_move=False)
    _replace_duplicate_merge_event_scope(s, movable=False)
    _replace_completion_episode_seal(s, movable=False)
    _replace_job_report_slot_seal(s, movable=False)
    op.drop_index("ix_work_item_moves_work_created", table_name="work_item_moves")
    op.drop_table("work_item_moves")

    for signature in (
        "mnemonic_reject_work_move_mutation()",
        "mnemonic_require_work_move_complete()",
        "mnemonic_guard_work_move_event()",
        "mnemonic_guard_work_project_move()",
        "mnemonic_guard_work_move_insert()",
        "mnemonic_work_moved_metadata_v1_is_valid(uuid,uuid,uuid,smallint,jsonb)",
    ):
        op.execute(f"DROP FUNCTION {s}.{signature}")
