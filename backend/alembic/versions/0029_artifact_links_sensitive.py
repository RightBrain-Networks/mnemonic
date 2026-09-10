"""Durable artifact relationships, metadata revisions, and single-use content approvals.

Revision ID: 0029_artifact_links_sensitive
Revises: 0028_work_summary_limit
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_artifact_links_sensitive"
down_revision: str | None = "0028_work_summary_limit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _invalidation(kinds: str) -> None:
    op.execute(f"""
        CREATE OR REPLACE FUNCTION mnemonic_artifact_invalidate_extraction() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.kind IN ({kinds}) THEN
                UPDATE artifact_extractions
                SET normalized_text = NULL,
                    status = CASE WHEN NEW.kind = 'delete' THEN 'deleted' ELSE 'superseded' END,
                    lease_token = NULL, lease_expires_at = NULL
                WHERE artifact_id = NEW.artifact_id;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE artifacts ADD COLUMN sensitive boolean NOT NULL DEFAULT false;
        ALTER TABLE artifact_revisions ADD COLUMN sensitive boolean NOT NULL DEFAULT false;
        ALTER TABLE artifact_revisions ADD COLUMN related_artifact_ids jsonb
            NOT NULL DEFAULT '[]'::jsonb;
        ALTER TABLE artifact_revisions ADD CONSTRAINT artifact_revisions_related_artifact_ids_check
            CHECK (jsonb_typeof(related_artifact_ids) = 'array'
                   AND jsonb_array_length(related_artifact_ids) <= 50);
        ALTER TABLE artifact_audit ALTER COLUMN action TYPE varchar(32);
        ALTER TABLE artifact_audit ADD COLUMN details jsonb NOT NULL DEFAULT '{}'::jsonb;
        ALTER TABLE artifact_audit ADD CONSTRAINT artifact_audit_details_check
            CHECK (jsonb_typeof(details) = 'object' AND octet_length(details::text) <= 8192);
        ALTER TABLE artifact_audit DROP CONSTRAINT artifact_audit_action_check;
        ALTER TABLE artifact_audit ADD CONSTRAINT artifact_audit_action_check CHECK (
            action IN ('uploaded', 'replaced', 'deleted', 'downloaded', 'metadata_updated',
                       'linked', 'approval_required', 'approval_granted', 'approval_rejected',
                       'sensitive_downloaded', 'sensitive_text_read', 'sensitive_searched')
        );
        ALTER TABLE artifact_operations DROP CONSTRAINT artifact_operations_kind_check;
        ALTER TABLE artifact_operations ADD CONSTRAINT artifact_operations_kind_check
            CHECK (kind IN ('upload', 'replace', 'delete', 'update'));
        CREATE TABLE artifact_links (
            artifact_id uuid NOT NULL,
            related_artifact_id uuid NOT NULL,
            project_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT artifact_links_pkey PRIMARY KEY (artifact_id, related_artifact_id),
            CONSTRAINT artifact_links_artifact_fkey FOREIGN KEY (project_id, artifact_id)
                REFERENCES artifacts(project_id, id) ON DELETE RESTRICT,
            CONSTRAINT artifact_links_related_artifact_fkey
                FOREIGN KEY (project_id, related_artifact_id)
                REFERENCES artifacts(project_id, id) ON DELETE RESTRICT,
            CONSTRAINT artifact_links_order_check CHECK (artifact_id < related_artifact_id)
        );
        CREATE INDEX ix_artifact_links_related_artifact_id ON artifact_links(related_artifact_id);
        CREATE TABLE artifact_access_approvals (
            token_hash varchar(64) NOT NULL,
            artifact_id uuid NOT NULL,
            revision integer NOT NULL,
            action varchar(32) NOT NULL,
            request_hash varchar(64) NOT NULL,
            agent_session_id varchar(200),
            actor_client varchar(80),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            CONSTRAINT artifact_access_approvals_pkey PRIMARY KEY (token_hash),
            CONSTRAINT artifact_access_approvals_artifact_id_fkey FOREIGN KEY (artifact_id)
                REFERENCES artifacts(id) ON DELETE RESTRICT,
            CONSTRAINT artifact_access_approvals_token_hash_check
                CHECK (token_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT artifact_access_approvals_request_hash_check
                CHECK (request_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT artifact_access_approvals_revision_check CHECK (revision > 0),
            CONSTRAINT artifact_access_approvals_action_check
                CHECK (action IN ('download', 'text', 'search')),
            CONSTRAINT artifact_access_approvals_expiry_check CHECK (expires_at > created_at),
            CONSTRAINT artifact_access_approvals_consumption_check
                CHECK (consumed_at IS NULL OR consumed_at >= created_at)
        );
        CREATE FUNCTION mnemonic_artifact_approval_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL
                OR (to_jsonb(NEW) - 'consumed_at') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'consumed_at') THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'artifact approval scope is immutable and consumption is single use';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER artifact_approval_guard BEFORE UPDATE ON artifact_access_approvals
            FOR EACH ROW EXECUTE FUNCTION mnemonic_artifact_approval_guard();
        CREATE TRIGGER artifact_links_append_only BEFORE UPDATE OR DELETE ON artifact_links
            FOR EACH ROW EXECUTE FUNCTION mnemonic_artifact_append_only();
        CREATE TRIGGER artifact_access_approvals_no_delete
            BEFORE DELETE ON artifact_access_approvals
            FOR EACH ROW EXECUTE FUNCTION mnemonic_artifact_append_only();
    """)
    for table in ("artifact_links", "artifact_access_approvals"):
        op.execute(f"""
            CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION mnemonic_artifact_append_only();
        """)
    _invalidation("'replace', 'delete', 'update'")


def downgrade() -> None:
    op.execute("LOCK TABLE artifacts, artifact_operations, artifact_links, "
               "artifact_access_approvals IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("""
        SELECT EXISTS(SELECT 1 FROM artifacts) OR EXISTS(SELECT 1 FROM artifact_links)
               OR EXISTS(SELECT 1 FROM artifact_access_approvals)
    """)):
        raise RuntimeError("Populated artifact metadata cannot be safely downgraded")
    _invalidation("'replace', 'delete'")
    op.drop_table("artifact_access_approvals")
    op.drop_table("artifact_links")
    op.execute("DROP FUNCTION mnemonic_artifact_approval_guard()")
    op.execute("""
        ALTER TABLE artifact_operations DROP CONSTRAINT artifact_operations_kind_check;
        ALTER TABLE artifact_operations ADD CONSTRAINT artifact_operations_kind_check
            CHECK (kind IN ('upload', 'replace', 'delete'));
        ALTER TABLE artifact_audit DROP CONSTRAINT artifact_audit_action_check;
        ALTER TABLE artifact_audit ADD CONSTRAINT artifact_audit_action_check
            CHECK (action IN ('uploaded', 'replaced', 'deleted', 'downloaded'));
        ALTER TABLE artifact_audit ALTER COLUMN action TYPE varchar(20);
        ALTER TABLE artifact_audit DROP COLUMN details;
        ALTER TABLE artifact_revisions DROP COLUMN related_artifact_ids;
        ALTER TABLE artifact_revisions DROP COLUMN sensitive;
        ALTER TABLE artifacts DROP COLUMN sensitive;
    """)
