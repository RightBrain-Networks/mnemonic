"""Project artifact identities, immutable revision metadata, audit, and recovery intents.

Revision ID: 0026_artifact_library
Revises: 0025_cross_project_relationships
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_artifact_library"
down_revision: str | None = "0025_cross_project_relationships"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE artifacts (
            id uuid PRIMARY KEY,
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            filename varchar(255) NOT NULL,
            description varchar(4000) NOT NULL,
            relative_path varchar(400) NOT NULL,
            revision integer NOT NULL CHECK (revision >= 0),
            size_bytes bigint NOT NULL CHECK (size_bytes >= 0 AND size_bytes <= 1073741824),
            sha256 varchar(64) NOT NULL,
            mime_type varchar(120),
            created_by_agent_session_id varchar(200),
            created_by_client varchar(80),
            originating_work_item_id uuid REFERENCES work_items(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            modified_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            deleted_at timestamptz,
            CONSTRAINT uq_artifacts_project_identity UNIQUE(project_id, id),
            CHECK (revision = 0 OR sha256 ~ '^[0-9a-f]{64}$'),
            CHECK (filename <> '' AND filename NOT IN ('.', '..')
                   AND position('/' in filename) = 0 AND position(chr(92) in filename) = 0),
            CHECK (relative_path = project_id::text || '/' || id::text || '/' || filename)
        );
        CREATE INDEX ix_artifacts_project_modified ON artifacts(project_id, modified_at, id);
        CREATE TABLE artifact_work_links (
            artifact_id uuid NOT NULL REFERENCES artifacts(id) ON DELETE RESTRICT,
            work_item_id uuid NOT NULL REFERENCES work_items(id) ON DELETE RESTRICT,
            PRIMARY KEY(artifact_id, work_item_id)
        );
        CREATE INDEX ix_artifact_work_links_work_item_id ON artifact_work_links(work_item_id);
        CREATE TABLE artifact_revisions (
            artifact_id uuid NOT NULL REFERENCES artifacts(id) ON DELETE RESTRICT,
            revision integer NOT NULL CHECK (revision > 0),
            filename varchar(255) NOT NULL,
            description varchar(4000) NOT NULL,
            size_bytes bigint NOT NULL CHECK (size_bytes >= 0 AND size_bytes <= 1073741824),
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            mime_type varchar(120),
            agent_session_id varchar(200),
            actor_client varchar(80),
            related_work_item_ids jsonb NOT NULL CHECK (
                jsonb_typeof(related_work_item_ids) = 'array'
                AND jsonb_array_length(related_work_item_ids) <= 51
            ),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(artifact_id, revision)
        );
        CREATE TABLE artifact_audit (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            artifact_id uuid NOT NULL REFERENCES artifacts(id) ON DELETE RESTRICT,
            revision integer NOT NULL,
            action varchar(20) NOT NULL CHECK (
                action IN ('uploaded', 'replaced', 'deleted', 'downloaded')
            ),
            filename varchar(255) NOT NULL,
            description varchar(4000) NOT NULL,
            agent_session_id varchar(200),
            actor_client varchar(80),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY(artifact_id, revision)
                REFERENCES artifact_revisions(artifact_id, revision) ON DELETE RESTRICT
        );
        CREATE INDEX ix_artifact_audit_artifact_created
            ON artifact_audit(artifact_id, created_at, id);
        CREATE TABLE artifact_operations (
            id uuid PRIMARY KEY,
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            client_operation_id uuid NOT NULL,
            artifact_id uuid NOT NULL,
            kind varchar(10) NOT NULL CHECK (kind IN ('upload', 'replace', 'delete')),
            state varchar(10) NOT NULL CHECK (state IN ('pending', 'completed')),
            fingerprint varchar(64) NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
            intent jsonb NOT NULL CHECK (jsonb_typeof(intent) = 'object'),
            response_body jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            completed_at timestamptz,
            CONSTRAINT uq_artifact_operations_scope UNIQUE(project_id, client_operation_id),
            FOREIGN KEY(project_id, artifact_id)
                REFERENCES artifacts(project_id, id) ON DELETE RESTRICT,
            CHECK ((state = 'pending' AND response_body IS NULL AND completed_at IS NULL)
                   OR (state = 'completed' AND jsonb_typeof(response_body) = 'object'
                       AND response_body IS NOT NULL AND completed_at IS NOT NULL))
        );
        CREATE UNIQUE INDEX ix_artifact_operations_pending ON artifact_operations(artifact_id)
            WHERE state = 'pending';
        CREATE FUNCTION mnemonic_artifact_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'artifact metadata history is append only';
        END;
        $$;
        CREATE FUNCTION mnemonic_artifact_identity_guard() RETURNS trigger
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
        CREATE TRIGGER artifact_identity_guard BEFORE UPDATE ON artifacts
            FOR EACH ROW EXECUTE FUNCTION mnemonic_artifact_identity_guard();
        CREATE FUNCTION mnemonic_artifact_receipt_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.state = 'completed' OR NEW.state <> 'completed'
                OR (to_jsonb(NEW) - ARRAY['state', 'response_body', 'completed_at'])
                    IS DISTINCT FROM
                   (to_jsonb(OLD) - ARRAY['state', 'response_body', 'completed_at']) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'artifact operation intent and completed receipt are immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER artifact_receipt_guard BEFORE UPDATE ON artifact_operations
            FOR EACH ROW EXECUTE FUNCTION mnemonic_artifact_receipt_guard();
    """)
    for table in ("artifact_revisions", "artifact_audit", "artifact_work_links"):
        op.execute(f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION mnemonic_artifact_append_only();
        """)
    for table in (
        "artifacts",
        "artifact_operations",
        "artifact_revisions",
        "artifact_audit",
        "artifact_work_links",
    ):
        op.execute(f"""
            CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION mnemonic_artifact_append_only();
        """)
    for table in ("artifacts", "artifact_operations"):
        op.execute(f"""
            CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION mnemonic_artifact_append_only();
        """)


def downgrade() -> None:
    # Empty-schema migration tests and unused deployments may safely revert.
    # An artifact identity, even a tombstone, permanently prevents downgrade.
    tables = (
        "artifact_audit",
        "artifact_revisions",
        "artifact_work_links",
        "artifact_operations",
        "artifacts",
    )
    op.execute("LOCK TABLE " + ", ".join(tables) + " IN ACCESS EXCLUSIVE MODE")
    populated = " OR ".join(f"EXISTS(SELECT 1 FROM {table})" for table in tables)
    if op.get_bind().scalar(sa.text("SELECT " + populated)):
        raise RuntimeError("Populated artifact metadata history cannot be safely downgraded")
    for table in tables:
        op.drop_table(table)
    for function in (
        "mnemonic_artifact_receipt_guard",
        "mnemonic_artifact_identity_guard",
        "mnemonic_artifact_append_only",
    ):
        op.execute(f"DROP FUNCTION {function}()")
