"""Retain the creating LLM model independently of the artifact client."""

import sqlalchemy as sa
from alembic import op

revision = "0073_artifact_creator_model"
down_revision = "0072_force_claim_gates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("created_by_model", sa.String(200), nullable=True))
    op.execute("""
        CREATE OR REPLACE FUNCTION mnemonic_artifact_identity_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.id, NEW.project_id, NEW.filename, NEW.relative_path,
                NEW.created_by_agent_session_id, NEW.created_by_client, NEW.created_by_model,
                NEW.originating_work_item_id, NEW.created_at)
                IS DISTINCT FROM
                (OLD.id, OLD.project_id, OLD.filename, OLD.relative_path,
                 OLD.created_by_agent_session_id, OLD.created_by_client, OLD.created_by_model,
                 OLD.originating_work_item_id, OLD.created_at)
                OR OLD.deleted_at IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'artifact identity and deleted metadata are immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION mnemonic_artifact_identity_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.id, NEW.project_id, NEW.filename, NEW.relative_path,
                NEW.created_by_agent_session_id, NEW.created_by_client,
                NEW.originating_work_item_id, NEW.created_at)
                IS DISTINCT FROM
                (OLD.id, OLD.project_id, OLD.filename, OLD.relative_path,
                 OLD.created_by_agent_session_id, OLD.created_by_client,
                 OLD.originating_work_item_id, OLD.created_at)
                OR OLD.deleted_at IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'artifact identity and deleted metadata are immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.drop_column("artifacts", "created_by_model")
