"""Add first-class human gates and their immutable audit events.

Revision ID: 0014_human_gates
Revises: 0013_idempotent_mutations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_human_gates"
down_revision: str | None = "0013_idempotent_mutations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_OPERATION_KINDS = (
    "'create_work', 'add_checkpoint', 'append_event', 'add_relationship', "
    "'update_work', 'defer_work', 'complete_work', 'delete_work', "
    "'remove_relationship', 'release_claim'"
)
OPERATION_KINDS = (
    f"{LEGACY_OPERATION_KINDS}, 'request_human_input', 'resolve_human_input'"
)
LEGACY_EVENT_TYPES = (
    "'work_created', 'work_updated', 'work_status_changed', 'work_reopened', "
    "'work_claimed', 'work_released', 'checkpoint_added', 'progress', "
    "'dependency_added', 'dependency_removed', 'relationship_added', "
    "'relationship_removed', 'work_completed', 'work_deleted'"
)
GATE_EVENT_TYPES = "'human_attention_requested', 'human_attention_resolved'"
EVENT_VALIDATOR_ARGUMENTS = (
    "event_type, origin, work_item_id, checkpoint_id, lease_generation_id, "
    "lease_release_id, relationship_id, relationship_source_work_item_id, "
    "relationship_target_work_item_id, relationship_context_checkpoint_work_item_id, "
    "relationship_context_checkpoint_id, metadata_version, metadata"
)


def _quoted_current_schema() -> str:
    bind = op.get_bind()
    schema = bind.scalar(sa.text("SELECT current_schema()"))
    if not isinstance(schema, str):
        raise RuntimeError("0014_human_gates requires a current PostgreSQL schema")
    return bind.dialect.identifier_preparer.quote_identifier(schema)


def _create_work_gates() -> None:
    op.create_table(
        "work_gates",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "attention_sequence",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("gate_type", sa.String(length=16), server_default="human", nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("requested_by_client", sa.String(length=80), nullable=False),
        sa.Column("requested_by_session_id", sa.String(length=200), nullable=False),
        sa.Column("requested_by_model", sa.String(length=120), nullable=True),
        sa.Column("requested_work_version", sa.Integer(), nullable=False),
        sa.Column("requested_context_checkpoint_id", sa.UUID(), nullable=False),
        sa.Column("requested_relationship_event_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_by_client", sa.String(length=80), nullable=True),
        sa.Column("resolved_by_session_id", sa.String(length=200), nullable=True),
        sa.Column("resolved_by_model", sa.String(length=120), nullable=True),
        sa.Column("resolved_work_version", sa.Integer(), nullable=True),
        sa.Column("resolved_context_checkpoint_id", sa.UUID(), nullable=True),
        sa.Column("resolved_relationship_event_count", sa.BigInteger(), nullable=True),
        sa.Column("context_changed_at_resolution", sa.Boolean(), nullable=True),
        sa.Column("context_change_acknowledged", sa.Boolean(), nullable=True),
        sa.CheckConstraint(
            "attention_sequence > 0",
            name=op.f("ck_work_gates_attention_sequence_positive"),
        ),
        sa.CheckConstraint(
            "gate_type = 'human'",
            name=op.f("ck_work_gates_gate_type_valid"),
        ),
        sa.CheckConstraint(
            "mnemonic_has_non_whitespace(question) AND length(question) <= 4000",
            name=op.f("ck_work_gates_question_valid"),
        ),
        sa.CheckConstraint(
            "mnemonic_has_non_whitespace(requested_by_client) "
            "AND mnemonic_has_non_whitespace(requested_by_session_id) "
            "AND (requested_by_model IS NULL "
            "OR mnemonic_has_non_whitespace(requested_by_model))",
            name=op.f("ck_work_gates_requester_valid"),
        ),
        sa.CheckConstraint(
            "requested_work_version > 0 "
            "AND requested_relationship_event_count >= 0",
            name=op.f("ck_work_gates_requested_revision_valid"),
        ),
        sa.CheckConstraint(
            "(resolved_at IS NULL AND resolution IS NULL "
            "AND resolved_by_client IS NULL AND resolved_by_session_id IS NULL "
            "AND resolved_by_model IS NULL AND resolved_work_version IS NULL "
            "AND resolved_context_checkpoint_id IS NULL "
            "AND resolved_relationship_event_count IS NULL "
            "AND context_changed_at_resolution IS NULL "
            "AND context_change_acknowledged IS NULL) OR "
            "(resolved_at IS NOT NULL AND resolution IS NOT NULL "
            "AND resolved_by_client IS NOT NULL "
            "AND resolved_by_session_id IS NOT NULL "
            "AND resolved_work_version IS NOT NULL "
            "AND resolved_context_checkpoint_id IS NOT NULL "
            "AND resolved_relationship_event_count IS NOT NULL "
            "AND context_changed_at_resolution IS NOT NULL "
            "AND context_change_acknowledged IS NOT NULL)",
            name=op.f("ck_work_gates_resolution_state_valid"),
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR "
            "(mnemonic_has_non_whitespace(resolution) AND length(resolution) <= 4000)",
            name=op.f("ck_work_gates_resolution_valid"),
        ),
        sa.CheckConstraint(
            "resolved_by_client IS NULL OR mnemonic_has_non_whitespace(resolved_by_client)",
            name=op.f("ck_work_gates_resolver_client_valid"),
        ),
        sa.CheckConstraint(
            "resolved_by_session_id IS NULL "
            "OR mnemonic_has_non_whitespace(resolved_by_session_id)",
            name=op.f("ck_work_gates_resolver_session_valid"),
        ),
        sa.CheckConstraint(
            "resolved_by_model IS NULL OR mnemonic_has_non_whitespace(resolved_by_model)",
            name=op.f("ck_work_gates_resolver_model_valid"),
        ),
        sa.CheckConstraint(
            "resolved_work_version IS NULL OR resolved_work_version > 0",
            name=op.f("ck_work_gates_resolved_work_version_positive"),
        ),
        sa.CheckConstraint(
            "resolved_relationship_event_count IS NULL "
            "OR resolved_relationship_event_count >= 0",
            name=op.f("ck_work_gates_resolved_relationship_event_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= created_at",
            name=op.f("ck_work_gates_timestamp_order"),
        ),
        sa.CheckConstraint(
            "context_changed_at_resolution IS NULL OR "
            "context_changed_at_resolution = ("
            "resolved_work_version IS DISTINCT FROM requested_work_version OR "
            "resolved_context_checkpoint_id IS DISTINCT FROM "
            "requested_context_checkpoint_id OR "
            "resolved_relationship_event_count IS DISTINCT FROM "
            "requested_relationship_event_count)",
            name=op.f("ck_work_gates_resolution_drift_coherent"),
        ),
        sa.CheckConstraint(
            "context_change_acknowledged IS NULL OR "
            "context_change_acknowledged = context_changed_at_resolution",
            name=op.f("ck_work_gates_context_acknowledgement_coherent"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_gates_work_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id", "requested_context_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_gates_requested_context_checkpoint",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id", "resolved_context_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_gates_resolved_context_checkpoint",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_gates")),
        sa.UniqueConstraint(
            "attention_sequence",
            name="uq_work_gates_attention_sequence",
        ),
        sa.UniqueConstraint(
            "work_item_id",
            "id",
            name="uq_work_gates_work_item_id_id",
        ),
    )
    op.create_index(
        "ix_work_gates_project_unresolved",
        "work_gates",
        ["project_id", "attention_sequence"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_index(
        "ix_work_gates_work_unresolved",
        "work_gates",
        ["work_item_id", "attention_sequence"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_index(
        "ix_work_gates_work_timeline",
        "work_gates",
        ["work_item_id", sa.text("attention_sequence DESC")],
    )
    op.create_index(
        "ix_work_gates_work_resolved_recent",
        "work_gates",
        ["work_item_id", sa.text("resolved_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("resolved_at IS NOT NULL"),
    )


def _replace_client_operation_kinds(kinds: str) -> None:
    op.drop_constraint(
        op.f("ck_client_operations_operation_kind_valid"),
        "client_operations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_client_operations_operation_kind_valid"),
        "client_operations",
        f"operation_kind IN ({kinds})",
    )


def _replace_work_event_constraints(*, include_gates: bool) -> None:
    for name in (
        "event_type_valid",
        "actor_matrix_valid",
        "body_valid",
        "metadata_v1_valid",
        "backfill_event_type_valid",
    ):
        op.drop_constraint(op.f(f"ck_work_events_{name}"), "work_events", type_="check")

    if include_gates:
        event_types = f"{LEGACY_EVENT_TYPES}, {GATE_EVENT_TYPES}"
        required_client_types = (
            "'work_created', 'checkpoint_added', 'work_completed', 'work_claimed', "
            "'dependency_added', 'relationship_added', 'progress', "
            f"{GATE_EVENT_TYPES}"
        )
        body_types = f"'progress', {GATE_EVENT_TYPES}"
        metadata_check = (
            f"(event_type IN ({GATE_EVENT_TYPES}) AND metadata_version = 1 "
            "AND metadata = jsonb_build_object("
            "'gate_id', gate_id::text, 'gate_type', 'human')) OR "
            f"(event_type NOT IN ({GATE_EVENT_TYPES}) AND "
            f"mnemonic_work_event_metadata_v2_is_valid({EVENT_VALIDATOR_ARGUMENTS}))"
        )
    else:
        event_types = LEGACY_EVENT_TYPES
        required_client_types = (
            "'work_created', 'checkpoint_added', 'work_completed', 'work_claimed', "
            "'dependency_added', 'relationship_added', 'progress'"
        )
        body_types = "'progress'"
        metadata_check = (
            f"mnemonic_work_event_metadata_v2_is_valid({EVENT_VALIDATOR_ARGUMENTS})"
        )

    op.create_check_constraint(
        op.f("ck_work_events_event_type_valid"),
        "work_events",
        f"event_type IN ({event_types})",
    )
    op.create_check_constraint(
        op.f("ck_work_events_actor_matrix_valid"),
        "work_events",
        "(origin = 'live' AND (event_type NOT IN ("
        f"{required_client_types}) OR actor_kind = 'client')) OR "
        "(origin = 'backfill' AND (event_type <> 'work_deleted' "
        "OR actor_kind = 'unattributed'))",
    )
    op.create_check_constraint(
        op.f("ck_work_events_body_valid"),
        "work_events",
        f"(event_type IN ({body_types}) AND body IS NOT NULL "
        "AND length(body) <= 4000 AND mnemonic_has_non_whitespace(body)) OR "
        f"(event_type NOT IN ({body_types}) AND body IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_work_events_metadata_v1_valid"),
        "work_events",
        metadata_check,
    )
    op.create_check_constraint(
        op.f("ck_work_events_backfill_event_type_valid"),
        "work_events",
        "origin = 'live' OR event_type IN ('work_created', 'checkpoint_added', "
        "'work_completed', 'work_claimed', 'dependency_added', "
        "'relationship_added', 'work_deleted')",
    )


def _extend_work_events(schema: str) -> None:
    op.add_column("work_events", sa.Column("gate_id", sa.UUID(), nullable=True))
    _replace_work_event_constraints(include_gates=True)
    op.create_check_constraint(
        op.f("ck_work_events_gate_reference_valid"),
        "work_events",
        f"(event_type IN ({GATE_EVENT_TYPES}) AND gate_id IS NOT NULL) OR "
        f"(event_type NOT IN ({GATE_EVENT_TYPES}) AND gate_id IS NULL)",
    )
    # Preserve any historically legal progress metadata while reserving the
    # typed gate envelope for every row written after this revision.
    op.execute(
        f"""
        ALTER TABLE {schema}.work_events
        ADD CONSTRAINT ck_work_events_gate_metadata_reserved
        CHECK (
            event_type IN ({GATE_EVENT_TYPES})
            OR NOT (metadata ? 'gate_id' OR metadata ? 'gate_type')
        )
        NOT VALID
        """
    )
    op.create_foreign_key(
        "fk_work_events_gate",
        "work_events",
        "work_gates",
        ["work_item_id", "gate_id"],
        ["work_item_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_work_events_gate_fact",
        "work_events",
        ["work_item_id", "gate_id", "event_type"],
        unique=True,
        postgresql_where=sa.text("gate_id IS NOT NULL"),
    )


def _create_gate_guards(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_work_gate_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_work {schema}.work_items%ROWTYPE;
            v_context_checkpoint_id uuid;
            v_relationship_event_count bigint;
        BEGIN
            IF NEW.resolved_at IS NOT NULL
               OR NEW.resolution IS NOT NULL
               OR NEW.resolved_by_client IS NOT NULL
               OR NEW.resolved_by_session_id IS NOT NULL
               OR NEW.resolved_by_model IS NOT NULL
               OR NEW.resolved_work_version IS NOT NULL
               OR NEW.resolved_context_checkpoint_id IS NOT NULL
               OR NEW.resolved_relationship_event_count IS NOT NULL
               OR NEW.context_changed_at_resolution IS NOT NULL
               OR NEW.context_change_acknowledged IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'work gates must be inserted unresolved';
            END IF;

            SELECT *
            INTO v_work
            FROM {schema}.work_items
            WHERE id = NEW.work_item_id
            FOR UPDATE;
            IF NOT FOUND
               OR v_work.project_id IS DISTINCT FROM NEW.project_id
               OR v_work.deleted_at IS NOT NULL
               OR v_work.status <> 'pending' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'work gate requires visible pending work';
            END IF;

            SELECT checkpoint.id
            INTO v_context_checkpoint_id
            FROM {schema}.checkpoints AS checkpoint
            WHERE checkpoint.work_item_id = NEW.work_item_id
              AND checkpoint.kind = 'context'
            ORDER BY checkpoint.created_at DESC, checkpoint.id DESC
            LIMIT 1;

            SELECT pg_catalog.count(*)
            INTO v_relationship_event_count
            FROM {schema}.work_events AS event
            WHERE event.work_item_id = NEW.work_item_id
              AND event.event_type IN (
                  'dependency_added', 'dependency_removed',
                  'relationship_added', 'relationship_removed'
              );

            IF v_context_checkpoint_id IS NULL
               OR NEW.requested_work_version IS DISTINCT FROM v_work.version
               OR NEW.requested_context_checkpoint_id
                  IS DISTINCT FROM v_context_checkpoint_id
               OR NEW.requested_relationship_event_count
                  IS DISTINCT FROM v_relationship_event_count THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'work gate request revision does not match retained state';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER work_gate_insert_guard
        BEFORE INSERT ON {schema}.work_gates
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_work_gate_insert();
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_work_gate_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_work {schema}.work_items%ROWTYPE;
            v_context_checkpoint_id uuid;
            v_relationship_event_count bigint;
            v_context_changed boolean;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'work gates cannot be deleted';
            END IF;

            IF OLD.resolved_at IS NOT NULL
               OR NEW.resolved_at IS NULL
               OR NEW.id IS DISTINCT FROM OLD.id
               OR NEW.attention_sequence IS DISTINCT FROM OLD.attention_sequence
               OR NEW.project_id IS DISTINCT FROM OLD.project_id
               OR NEW.work_item_id IS DISTINCT FROM OLD.work_item_id
               OR NEW.gate_type IS DISTINCT FROM OLD.gate_type
               OR NEW.question IS DISTINCT FROM OLD.question
               OR NEW.requested_by_client IS DISTINCT FROM OLD.requested_by_client
               OR NEW.requested_by_session_id
                  IS DISTINCT FROM OLD.requested_by_session_id
               OR NEW.requested_by_model IS DISTINCT FROM OLD.requested_by_model
               OR NEW.requested_work_version IS DISTINCT FROM OLD.requested_work_version
               OR NEW.requested_context_checkpoint_id
                  IS DISTINCT FROM OLD.requested_context_checkpoint_id
               OR NEW.requested_relationship_event_count
                  IS DISTINCT FROM OLD.requested_relationship_event_count
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'work gate mutation is not permitted';
            END IF;

            SELECT *
            INTO v_work
            FROM {schema}.work_items
            WHERE id = OLD.work_item_id
              AND project_id = OLD.project_id
            FOR UPDATE;
            IF NOT FOUND OR v_work.deleted_at IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'work gate resolution requires retained visible work';
            END IF;

            SELECT checkpoint.id
            INTO v_context_checkpoint_id
            FROM {schema}.checkpoints AS checkpoint
            WHERE checkpoint.work_item_id = OLD.work_item_id
              AND checkpoint.kind = 'context'
            ORDER BY checkpoint.created_at DESC, checkpoint.id DESC
            LIMIT 1;

            SELECT pg_catalog.count(*)
            INTO v_relationship_event_count
            FROM {schema}.work_events AS event
            WHERE event.work_item_id = OLD.work_item_id
              AND event.event_type IN (
                  'dependency_added', 'dependency_removed',
                  'relationship_added', 'relationship_removed'
              );

            IF v_context_checkpoint_id IS NULL
               OR NEW.resolved_work_version IS DISTINCT FROM v_work.version
               OR NEW.resolved_context_checkpoint_id
                  IS DISTINCT FROM v_context_checkpoint_id
               OR NEW.resolved_relationship_event_count
                  IS DISTINCT FROM v_relationship_event_count THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'work gate resolution revision does not match retained state';
            END IF;

            v_context_changed :=
                NEW.resolved_work_version IS DISTINCT FROM OLD.requested_work_version
                OR NEW.resolved_context_checkpoint_id
                   IS DISTINCT FROM OLD.requested_context_checkpoint_id
                OR NEW.resolved_relationship_event_count
                   IS DISTINCT FROM OLD.requested_relationship_event_count;
            IF NEW.context_changed_at_resolution IS DISTINCT FROM v_context_changed
               OR NEW.context_change_acknowledged IS DISTINCT FROM v_context_changed THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'work gate resolution drift evidence is invalid';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER work_gate_mutation_guard
        BEFORE UPDATE OR DELETE ON {schema}.work_gates
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_work_gate_mutation();
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_require_work_gate_events()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_gate {schema}.work_gates%ROWTYPE;
            v_requested_count bigint;
            v_resolved_count bigint;
        BEGIN
            SELECT *
            INTO v_gate
            FROM {schema}.work_gates
            WHERE id = NEW.id;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'work gate source fact must be retained';
            END IF;

            SELECT
                pg_catalog.count(*) FILTER (
                    WHERE event_type = 'human_attention_requested'
                ),
                pg_catalog.count(*) FILTER (
                    WHERE event_type = 'human_attention_resolved'
                )
            INTO v_requested_count, v_resolved_count
            FROM {schema}.work_events
            WHERE work_item_id = v_gate.work_item_id
              AND gate_id = v_gate.id;

            IF v_requested_count <> 1
               OR (v_gate.resolved_at IS NULL AND v_resolved_count <> 0)
               OR (v_gate.resolved_at IS NOT NULL AND v_resolved_count <> 1) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'work gate requires exact retained audit events';
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER work_gate_event_completeness_guard
        AFTER INSERT OR UPDATE ON {schema}.work_gates
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_require_work_gate_events();
        """
    )


def _create_gate_event_guard(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_gate_event_source_fact()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_gate {schema}.work_gates%ROWTYPE;
        BEGIN
            IF NEW.event_type NOT IN (
                'human_attention_requested', 'human_attention_resolved'
            ) THEN
                RETURN NEW;
            END IF;

            SELECT *
            INTO v_gate
            FROM {schema}.work_gates
            WHERE id = NEW.gate_id
              AND work_item_id = NEW.work_item_id;
            IF NOT FOUND
               OR NEW.project_id IS DISTINCT FROM v_gate.project_id
               OR NEW.origin <> 'live'
               OR NEW.actor_kind <> 'client'
               OR NEW.metadata_version <> 1
               OR NEW.metadata IS DISTINCT FROM pg_catalog.jsonb_build_object(
                   'gate_id', v_gate.id::text,
                   'gate_type', v_gate.gate_type
               ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'gate event source fact does not match retained state';
            END IF;

            IF NEW.event_type = 'human_attention_requested' THEN
                IF NEW.body IS DISTINCT FROM v_gate.question
                   OR NEW.actor_client IS DISTINCT FROM v_gate.requested_by_client
                   OR NEW.actor_session_id
                      IS DISTINCT FROM v_gate.requested_by_session_id
                   OR NEW.actor_model IS DISTINCT FROM v_gate.requested_by_model
                   OR NEW.created_at IS DISTINCT FROM v_gate.created_at THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'gate request event does not match retained state';
                END IF;
            ELSE
                IF v_gate.resolved_at IS NULL
                   OR NEW.body IS DISTINCT FROM v_gate.resolution
                   OR NEW.actor_client IS DISTINCT FROM v_gate.resolved_by_client
                   OR NEW.actor_session_id
                      IS DISTINCT FROM v_gate.resolved_by_session_id
                   OR NEW.actor_model IS DISTINCT FROM v_gate.resolved_by_model
                   OR NEW.created_at IS DISTINCT FROM v_gate.resolved_at THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'gate resolution event does not match retained state';
                END IF;
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER gate_event_source_fact_guard
        BEFORE INSERT ON {schema}.work_events
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_gate_event_source_fact();
        """
    )


def _create_old_backend_guards(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_reject_gated_lease_acquisition()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_is_acquisition boolean;
        BEGIN
            v_is_acquisition := TG_OP = 'INSERT' OR (
                NEW.work_item_id IS DISTINCT FROM OLD.work_item_id
                OR NEW.holder_client IS DISTINCT FROM OLD.holder_client
                OR NEW.holder_session_id IS DISTINCT FROM OLD.holder_session_id
                OR NEW.claim_request_id IS DISTINCT FROM OLD.claim_request_id
                OR NEW.lease_token IS DISTINCT FROM OLD.lease_token
                OR NEW.lease_generation_id IS DISTINCT FROM OLD.lease_generation_id
                OR NEW.acquired_at IS DISTINCT FROM OLD.acquired_at
            );
            IF v_is_acquisition AND EXISTS (
                SELECT 1
                FROM {schema}.work_gates AS gate
                WHERE gate.resolved_at IS NULL
                  AND gate.work_item_id IN (
                      NEW.work_item_id,
                      CASE WHEN TG_OP = 'UPDATE' THEN OLD.work_item_id ELSE NEW.work_item_id END
                  )
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'unresolved work gate prevents lease acquisition';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER gated_lease_acquisition_guard
        BEFORE INSERT OR UPDATE ON {schema}.work_leases
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_reject_gated_lease_acquisition();
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_reject_gated_work_escape()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF (
                (NEW.status IS DISTINCT FROM OLD.status
                 AND NEW.status IN ('done', 'wont-do', 'promoted'))
                OR (OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL)
            ) AND EXISTS (
                SELECT 1
                FROM {schema}.work_gates AS gate
                WHERE gate.work_item_id = OLD.id
                  AND gate.resolved_at IS NULL
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'unresolved work gate prevents terminal or delete transition';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER gated_work_escape_guard
        BEFORE UPDATE ON {schema}.work_items
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_reject_gated_work_escape();
        """
    )


def upgrade() -> None:
    # A fresh legacy-to-head upgrade can still have deferred event guards queued.
    # Drain them before replacing event constraints or adding its gate reference.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    schema = _quoted_current_schema()
    _create_work_gates()
    _extend_work_events(schema)
    _replace_client_operation_kinds(OPERATION_KINDS)
    _create_gate_guards(schema)
    _create_gate_event_guard(schema)
    _create_old_backend_guards(schema)


def _assert_downgrade_is_empty(schema: str) -> None:
    bind = op.get_bind()
    for table in ("client_operations", "work_items", "work_gates", "work_events"):
        bind.execute(sa.text(f"LOCK TABLE {schema}.{table} IN ACCESS EXCLUSIVE MODE"))

    has_gate_data = bind.scalar(
        sa.text(
            f"""
            SELECT EXISTS (SELECT 1 FROM {schema}.work_gates)
                OR EXISTS (
                    SELECT 1
                    FROM {schema}.work_events
                    WHERE gate_id IS NOT NULL
                )
                OR EXISTS (
                    SELECT 1
                    FROM {schema}.client_operations
                    WHERE operation_kind IN (
                        'request_human_input', 'resolve_human_input'
                    )
                )
            """
        )
    )
    if has_gate_data:
        raise RuntimeError("Cannot downgrade human gates after gate history or receipts exist")


def downgrade() -> None:
    schema = _quoted_current_schema()
    _assert_downgrade_is_empty(schema)
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")

    op.execute(f"DROP TRIGGER gated_work_escape_guard ON {schema}.work_items")
    op.execute(f"DROP TRIGGER gated_lease_acquisition_guard ON {schema}.work_leases")
    op.execute(f"DROP FUNCTION {schema}.mnemonic_reject_gated_work_escape()")
    op.execute(f"DROP FUNCTION {schema}.mnemonic_reject_gated_lease_acquisition()")

    op.execute(f"DROP TRIGGER gate_event_source_fact_guard ON {schema}.work_events")
    op.execute(f"DROP FUNCTION {schema}.mnemonic_guard_gate_event_source_fact()")
    op.execute(
        f"DROP TRIGGER work_gate_event_completeness_guard ON {schema}.work_gates"
    )
    op.execute(f"DROP TRIGGER work_gate_mutation_guard ON {schema}.work_gates")
    op.execute(f"DROP TRIGGER work_gate_insert_guard ON {schema}.work_gates")
    op.execute(f"DROP FUNCTION {schema}.mnemonic_require_work_gate_events()")
    op.execute(f"DROP FUNCTION {schema}.mnemonic_guard_work_gate_mutation()")
    op.execute(f"DROP FUNCTION {schema}.mnemonic_guard_work_gate_insert()")

    op.drop_index("uq_work_events_gate_fact", table_name="work_events")
    op.drop_constraint("fk_work_events_gate", "work_events", type_="foreignkey")
    op.drop_constraint(
        op.f("ck_work_events_gate_metadata_reserved"),
        "work_events",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_work_events_gate_reference_valid"),
        "work_events",
        type_="check",
    )
    _replace_work_event_constraints(include_gates=False)
    op.drop_column("work_events", "gate_id")

    _replace_client_operation_kinds(LEGACY_OPERATION_KINDS)
    op.drop_table("work_gates")
