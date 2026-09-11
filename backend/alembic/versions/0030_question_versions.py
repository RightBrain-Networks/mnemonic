"""Revise human questions in place while retaining every authored version.

Revision ID: 0030_question_versions
Revises: 0029_artifact_links_sensitive
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql

revision: str = "0030_question_versions"
down_revision: str | None = "0029_artifact_links_sensitive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _revision_guard(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_validate_question_revision(gate {schema}.work_gates)
        RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        DECLARE
            entry jsonb := gate.question_revisions -> -1;
            context_id uuid;
            relationship_count bigint;
            work_version integer;
            previous_time timestamptz := COALESCE(
                (gate.question_revisions -> -2 ->> 'created_at')::timestamptz, gate.created_at);
        BEGIN
            SELECT version INTO work_version FROM {schema}.work_items
            WHERE id = gate.work_item_id AND project_id = gate.project_id
                AND deleted_at IS NULL AND status IN ('pending', 'deferred') FOR UPDATE;
            SELECT id INTO context_id FROM {schema}.checkpoints
            WHERE work_item_id = gate.work_item_id AND kind = 'context'
            ORDER BY created_at DESC, id DESC LIMIT 1;
            SELECT count(*) INTO relationship_count FROM {schema}.work_events
            WHERE work_item_id = gate.work_item_id AND event_type IN (
                'dependency_added', 'dependency_removed',
                'relationship_added', 'relationship_removed');
            IF work_version IS NULL OR context_id IS NULL
               OR jsonb_typeof(entry) IS DISTINCT FROM 'object'
               OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(entry) key)
                  IS DISTINCT FROM ARRAY['context_revision', 'created_at', 'question',
                      'requested_by_client', 'requested_by_model',
                      'requested_by_session_id', 'version']
               OR entry->'version' IS DISTINCT FROM
                  to_jsonb(jsonb_array_length(gate.question_revisions) + 1)
               OR entry->'context_revision' IS DISTINCT FROM jsonb_build_object(
                    'work_version', work_version, 'context_checkpoint_id', context_id,
                    'relationship_event_count', relationship_count)
               OR jsonb_typeof(entry->'question') IS DISTINCT FROM 'string'
               OR NOT {schema}.mnemonic_has_non_whitespace(entry->>'question')
               OR length(entry->>'question') > 4000
               OR jsonb_typeof(entry->'requested_by_client') IS DISTINCT FROM 'string'
               OR NOT {schema}.mnemonic_has_non_whitespace(entry->>'requested_by_client')
               OR length(entry->>'requested_by_client') > 80
               OR jsonb_typeof(entry->'requested_by_session_id') IS DISTINCT FROM 'string'
               OR NOT {schema}.mnemonic_has_non_whitespace(entry->>'requested_by_session_id')
               OR length(entry->>'requested_by_session_id') > 200
               OR (entry->'requested_by_model' <> 'null'::jsonb AND (
                    jsonb_typeof(entry->'requested_by_model') IS DISTINCT FROM 'string'
                    OR NOT {schema}.mnemonic_has_non_whitespace(entry->>'requested_by_model')
                    OR length(entry->>'requested_by_model') > 120))
               OR jsonb_typeof(entry->'created_at') IS DISTINCT FROM 'string'
               OR (entry->>'created_at')::timestamptz < previous_time
               OR (entry->>'created_at')::timestamptz > clock_timestamp() THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'question revision does not match retained work or valid authorship';
            END IF;
        END $function$;
    """)


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
            IF NEW.question_revisions <> '[]'::jsonb
               OR NEW.resolved_at IS NOT NULL
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

            IF NEW.question_revisions IS DISTINCT FROM OLD.question_revisions THEN
                IF OLD.resolved_at IS NOT NULL
                   OR (to_jsonb(NEW) - 'question_revisions')
                      IS DISTINCT FROM (to_jsonb(OLD) - 'question_revisions')
                   OR jsonb_typeof(NEW.question_revisions) <> 'array'
                   OR jsonb_array_length(NEW.question_revisions)
                      <> jsonb_array_length(OLD.question_revisions) + 1
                   OR NEW.question_revisions - (jsonb_array_length(NEW.question_revisions) - 1)
                      IS DISTINCT FROM OLD.question_revisions THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'question history only permits one appended revision';
                END IF;
                PERFORM {schema}.mnemonic_validate_question_revision(NEW);
                RETURN NEW;
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
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_identifier(
        bind.scalar(sa.text("SELECT current_schema()"))
    )
    op.add_column("work_gates", sa.Column("question_revisions", postgresql.JSONB(),
        nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.create_check_constraint(op.f("ck_work_gates_question_revisions_array"), "work_gates",
        "jsonb_typeof(question_revisions) = 'array'")
    _revision_guard(schema)
    _replace_gate_guards(schema)


def downgrade() -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_identifier(
        bind.scalar(sa.text("SELECT current_schema()"))
    )
    op.execute(f"LOCK TABLE {schema}.work_gates, {schema}.client_operations "
               "IN ACCESS EXCLUSIVE MODE")
    if bind.scalar(sa.text(f"""
        SELECT EXISTS(SELECT 1 FROM {schema}.work_gates WHERE question_revisions <> '[]'::jsonb)
            OR EXISTS(SELECT 1 FROM {schema}.client_operations
                WHERE operation_kind IN ('request_human_input', 'resolve_human_input')
                  AND response_body ? 'question_version')
    """)):
        raise RuntimeError("Question versions or receipts exist; restore a pre-upgrade backup")
    previous = ScriptDirectory.from_config(op.get_context().config).get_revision(
        "0015_gate_review_fixes"
    )
    assert previous is not None
    previous.module._replace_gate_guards(schema)
    op.execute(f"DROP FUNCTION {schema}.mnemonic_validate_question_revision({schema}.work_gates)")
    op.drop_constraint(op.f("ck_work_gates_question_revisions_array"), "work_gates", type_="check")
    op.drop_column("work_gates", "question_revisions")
