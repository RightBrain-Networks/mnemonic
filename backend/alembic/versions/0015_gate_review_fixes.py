"""Fix revision ordering and remove persisted human-gate drift state.

Revision ID: 0015_gate_review_fixes
Revises: 0014_human_gates
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_gate_review_fixes"
down_revision: str | None = "0014_human_gates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted_current_schema() -> str:
    bind = op.get_bind()
    schema = bind.scalar(sa.text("SELECT current_schema()"))
    if not isinstance(schema, str):
        raise RuntimeError("0015_gate_review_fixes requires a current PostgreSQL schema")
    return bind.dialect.identifier_preparer.quote_identifier(schema)


def _replace_gate_guards(schema: str) -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.mnemonic_guard_work_gate_insert()
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
               OR NEW.resolved_relationship_event_count IS NOT NULL THEN
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
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.mnemonic_guard_work_gate_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_work {schema}.work_items%ROWTYPE;
            v_context_checkpoint_id uuid;
            v_relationship_event_count bigint;
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

            RETURN NEW;
        END
        $function$;
        """
    )


def upgrade() -> None:
    schema = _quoted_current_schema()
    op.alter_column(
        "checkpoints",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.text("clock_timestamp()"),
        existing_nullable=False,
    )
    op.alter_column(
        "work_relationships",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.text("clock_timestamp()"),
        existing_nullable=False,
    )

    op.drop_constraint(
        op.f("ck_work_gates_context_acknowledgement_coherent"),
        "work_gates",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_work_gates_resolution_drift_coherent"),
        "work_gates",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_work_gates_resolution_state_valid"),
        "work_gates",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_work_gates_resolution_state_valid"),
        "work_gates",
        "(resolved_at IS NULL AND resolution IS NULL "
        "AND resolved_by_client IS NULL AND resolved_by_session_id IS NULL "
        "AND resolved_by_model IS NULL AND resolved_work_version IS NULL "
        "AND resolved_context_checkpoint_id IS NULL "
        "AND resolved_relationship_event_count IS NULL) OR "
        "(resolved_at IS NOT NULL AND resolution IS NOT NULL "
        "AND resolved_by_client IS NOT NULL "
        "AND resolved_by_session_id IS NOT NULL "
        "AND resolved_work_version IS NOT NULL "
        "AND resolved_context_checkpoint_id IS NOT NULL "
        "AND resolved_relationship_event_count IS NOT NULL)",
    )
    _replace_gate_guards(schema)
    op.drop_column("work_gates", "context_change_acknowledged")
    op.drop_column("work_gates", "context_changed_at_resolution")


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade from 0015_gate_review_fixes is unsupported; restore a pre-upgrade backup"
    )
