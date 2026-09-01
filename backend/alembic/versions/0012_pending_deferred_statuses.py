"""Replace open work with pending and add human-controlled deferral.

Revision ID: 0012_pending_deferred_statuses
Revises: 0011_project_settings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_pending_deferred_statuses"
down_revision: str | None = "0011_project_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_VALIDATOR_ARGUMENTS = (
    "event_type, origin, work_item_id, checkpoint_id, lease_generation_id, "
    "lease_release_id, relationship_id, relationship_source_work_item_id, "
    "relationship_target_work_item_id, relationship_context_checkpoint_work_item_id, "
    "relationship_context_checkpoint_id, metadata_version, metadata"
)
EVENT_VALIDATOR_TYPES = (
    "text, text, uuid, uuid, uuid, uuid, uuid, uuid, uuid, uuid, uuid, smallint, jsonb"
)


def _quoted_current_schema() -> str:
    bind = op.get_bind()
    schema = bind.scalar(sa.text("SELECT current_schema()"))
    if not isinstance(schema, str):
        raise RuntimeError("0012_pending_deferred_statuses requires a current PostgreSQL schema")
    return bind.dialect.identifier_preparer.quote_identifier(schema)


def _create_event_validator(schema: str) -> None:
    """Delegate unchanged and historical events to v1 after mapping new statuses."""
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_work_event_metadata_v2_is_valid(
            p_event_type text,
            p_origin text,
            p_work_item_id uuid,
            p_checkpoint_id uuid,
            p_lease_generation_id uuid,
            p_lease_release_id uuid,
            p_relationship_id uuid,
            p_relationship_source_work_item_id uuid,
            p_relationship_target_work_item_id uuid,
            p_relationship_context_checkpoint_work_item_id uuid,
            p_relationship_context_checkpoint_id uuid,
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
            v_legacy jsonb := p_metadata;
        BEGIN
            IF {schema}.mnemonic_work_event_metadata_v1_is_valid(
                p_event_type, p_origin, p_work_item_id, p_checkpoint_id,
                p_lease_generation_id, p_lease_release_id, p_relationship_id,
                p_relationship_source_work_item_id, p_relationship_target_work_item_id,
                p_relationship_context_checkpoint_work_item_id,
                p_relationship_context_checkpoint_id, p_metadata_version, p_metadata
            ) THEN
                RETURN true;
            END IF;

            IF p_event_type = 'work_created' AND p_origin = 'live' THEN
                IF v_legacy #>> '{{initial,status}}' = 'pending' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{initial,status}}', '"open"'::jsonb, false
                    );
                ELSIF v_legacy #>> '{{initial,status}}' = 'deferred' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{initial,status}}', '"wont-do"'::jsonb, false
                    );
                END IF;
            ELSIF p_event_type IN (
                'work_updated', 'work_status_changed', 'work_reopened'
            ) THEN
                IF v_legacy #>> '{{changes,status,before}}' = 'pending' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{changes,status,before}}', '"open"'::jsonb, false
                    );
                ELSIF v_legacy #>> '{{changes,status,before}}' = 'deferred' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{changes,status,before}}', '"wont-do"'::jsonb, false
                    );
                END IF;
                IF v_legacy #>> '{{changes,status,after}}' = 'pending' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{changes,status,after}}', '"open"'::jsonb, false
                    );
                ELSIF v_legacy #>> '{{changes,status,after}}' = 'deferred' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{changes,status,after}}', '"wont-do"'::jsonb, false
                    );
                END IF;
                IF v_legacy ->> 'from_status' = 'pending' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{from_status}}', '"open"'::jsonb, false
                    );
                ELSIF v_legacy ->> 'from_status' = 'deferred' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{from_status}}', '"wont-do"'::jsonb, false
                    );
                END IF;
                IF v_legacy ->> 'to_status' = 'pending' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{to_status}}', '"open"'::jsonb, false
                    );
                ELSIF v_legacy ->> 'to_status' = 'deferred' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{to_status}}', '"wont-do"'::jsonb, false
                    );
                END IF;
            ELSIF p_event_type = 'work_completed' AND p_origin = 'live' THEN
                IF v_legacy ->> 'from_status' = 'pending' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{from_status}}', '"open"'::jsonb, false
                    );
                END IF;
            ELSIF p_event_type = 'work_deleted' THEN
                IF v_legacy ->> 'final_status' = 'pending' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{final_status}}', '"open"'::jsonb, false
                    );
                ELSIF v_legacy ->> 'final_status' = 'deferred' THEN
                    v_legacy := pg_catalog.jsonb_set(
                        v_legacy, '{{final_status}}', '"wont-do"'::jsonb, false
                    );
                END IF;
            END IF;

            RETURN {schema}.mnemonic_work_event_metadata_v1_is_valid(
                p_event_type, p_origin, p_work_item_id, p_checkpoint_id,
                p_lease_generation_id, p_lease_release_id, p_relationship_id,
                p_relationship_source_work_item_id, p_relationship_target_work_item_id,
                p_relationship_context_checkpoint_work_item_id,
                p_relationship_context_checkpoint_id, p_metadata_version, v_legacy
            );
        EXCEPTION
            WHEN others THEN
                RETURN false;
        END
        $function$;
        """
    )


def _use_event_validator(version: int) -> None:
    op.drop_constraint(
        op.f("ck_work_events_metadata_v1_valid"),
        "work_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_work_events_metadata_v1_valid"),
        "work_events",
        f"mnemonic_work_event_metadata_v{version}_is_valid({EVENT_VALIDATOR_ARGUMENTS})",
    )


def upgrade() -> None:
    schema = _quoted_current_schema()
    _create_event_validator(schema)
    # A single Alembic upgrade can backfill Phase 5 event rows in 0010 and then
    # reach this revision in the same transaction. Fire its deferred constraint
    # triggers before altering the event table.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    _use_event_validator(2)

    op.drop_index("ix_work_items_ready_order", table_name="work_items")
    op.drop_constraint(op.f("ck_work_items_status_valid"), "work_items", type_="check")
    op.alter_column("work_items", "status", server_default="pending")
    op.execute("UPDATE work_items SET status = 'pending' WHERE status = 'open'")
    op.create_check_constraint(
        op.f("ck_work_items_status_valid"),
        "work_items",
        "status IN ('pending', 'deferred', 'done', 'wont-do', 'promoted')",
    )
    op.create_index(
        "ix_work_items_ready_order",
        "work_items",
        ["project_id", sa.text("priority DESC"), sa.text("created_at ASC"), sa.text("id ASC")],
        postgresql_where=sa.text("deleted_at IS NULL AND status = 'pending'"),
    )


def _rewrite_pending_event_history() -> None:
    op.execute("DROP TRIGGER events_immutable ON work_events")
    replacements = (
        ("work_created", "{initial,status}"),
        ("work_updated", "{changes,status,before}"),
        ("work_updated", "{changes,status,after}"),
        ("work_status_changed", "{changes,status,before}"),
        ("work_status_changed", "{changes,status,after}"),
        ("work_status_changed", "{from_status}"),
        ("work_status_changed", "{to_status}"),
        ("work_reopened", "{changes,status,before}"),
        ("work_reopened", "{changes,status,after}"),
        ("work_reopened", "{from_status}"),
        ("work_reopened", "{to_status}"),
        ("work_completed", "{from_status}"),
        ("work_deleted", "{final_status}"),
    )
    for event_type, path in replacements:
        op.execute(
            sa.text(
                "UPDATE work_events "
                "SET metadata = jsonb_set("
                "metadata, CAST(:path AS text[]), '\"open\"'::jsonb, false) "
                "WHERE event_type = :event_type "
                "AND metadata #>> CAST(:path AS text[]) = 'pending'"
            ).bindparams(path=path, event_type=event_type)
        )
    op.execute(
        """
        CREATE TRIGGER events_immutable
        BEFORE UPDATE OR DELETE ON work_events
        FOR EACH ROW
        EXECUTE FUNCTION mnemonic_reject_work_event_mutation()
        """
    )


def downgrade() -> None:
    deferred_history = op.get_bind().scalar(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM work_items WHERE status = 'deferred'
                UNION ALL
                SELECT 1
                FROM work_events
                WHERE metadata #>> '{initial,status}' = 'deferred'
                   OR metadata #>> '{changes,status,before}' = 'deferred'
                   OR metadata #>> '{changes,status,after}' = 'deferred'
                   OR metadata ->> 'from_status' = 'deferred'
                   OR metadata ->> 'to_status' = 'deferred'
                   OR metadata ->> 'final_status' = 'deferred'
            )
            """
        )
    )
    if deferred_history:
        raise RuntimeError(
            "Cannot downgrade while deferred work history exists; move it to a supported "
            "lifecycle and preserve the Phase 6 schema."
        )

    # Flush any deferred foreign-key checks before this revision alters tables;
    # callers may supply an outer transaction that already changed a work row.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    _rewrite_pending_event_history()
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    _use_event_validator(1)
    op.execute(
        f"DROP FUNCTION mnemonic_work_event_metadata_v2_is_valid({EVENT_VALIDATOR_TYPES})"
    )

    op.drop_index("ix_work_items_ready_order", table_name="work_items")
    op.drop_constraint(op.f("ck_work_items_status_valid"), "work_items", type_="check")
    op.alter_column("work_items", "status", server_default="open")
    op.execute("UPDATE work_items SET status = 'open' WHERE status = 'pending'")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.create_check_constraint(
        op.f("ck_work_items_status_valid"),
        "work_items",
        "status IN ('open', 'done', 'wont-do', 'promoted')",
    )
    op.create_index(
        "ix_work_items_ready_order",
        "work_items",
        ["project_id", sa.text("priority DESC"), sa.text("created_at ASC"), sa.text("id ASC")],
        postgresql_where=sa.text("deleted_at IS NULL AND status = 'open'"),
    )
