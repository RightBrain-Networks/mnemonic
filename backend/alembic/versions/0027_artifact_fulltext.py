"""Current-revision extracted text, immutable extracted metadata, and durable jobs.

Revision ID: 0027_artifact_fulltext
Revises: 0026_artifact_library
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_artifact_fulltext"
down_revision: str | None = "0026_artifact_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE artifact_extractions (
            artifact_id uuid NOT NULL,
            revision integer NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'pending',
            normalized_text text,
            extracted_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            truncated boolean NOT NULL DEFAULT false,
            attempts integer NOT NULL DEFAULT 0,
            error_code varchar(80),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            extracted_at timestamptz,
            next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            lease_token uuid,
            lease_expires_at timestamptz,
            CONSTRAINT artifact_extractions_pkey PRIMARY KEY(artifact_id, revision),
            CONSTRAINT artifact_extractions_revision_fkey FOREIGN KEY(artifact_id, revision)
                REFERENCES artifact_revisions(artifact_id, revision) ON DELETE RESTRICT,
            CONSTRAINT artifact_extractions_status_check CHECK (
                status IN ('pending', 'processing', 'ready', 'failed', 'superseded', 'deleted')
            ),
            CONSTRAINT artifact_extractions_attempts_check CHECK (attempts >= 0),
            CONSTRAINT artifact_extractions_metadata_check CHECK (
                jsonb_typeof(extracted_metadata) = 'object'
                AND octet_length(extracted_metadata::text) <= 16384
            ),
            CONSTRAINT artifact_extractions_text_check CHECK (
                normalized_text IS NULL OR
                (status = 'ready' AND char_length(normalized_text) <= 8000000)
            ),
            CONSTRAINT artifact_extractions_lease_check CHECK (
                (status = 'processing' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
                OR (status <> 'processing' AND lease_token IS NULL AND lease_expires_at IS NULL)
            )
        );
        CREATE INDEX ix_artifact_extractions_due ON artifact_extractions(status, next_attempt_at);
        INSERT INTO artifact_extractions(artifact_id, revision, status)
        SELECT revision.artifact_id, revision.revision,
               CASE WHEN artifact.deleted_at IS NOT NULL THEN 'deleted'
                    WHEN EXISTS (SELECT 1 FROM artifact_operations operation
                                 WHERE operation.artifact_id = artifact.id
                                   AND operation.state = 'pending'
                                   AND operation.kind = 'delete') THEN 'deleted'
                    WHEN revision.revision <> artifact.revision THEN 'superseded'
                    WHEN EXISTS (SELECT 1 FROM artifact_operations operation
                                 WHERE operation.artifact_id = artifact.id
                                   AND operation.state = 'pending') THEN 'superseded'
                    ELSE 'pending' END
        FROM artifact_revisions revision
        JOIN artifacts artifact ON artifact.id=revision.artifact_id;

        CREATE FUNCTION mnemonic_artifact_queue_extraction() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO artifact_extractions(artifact_id, revision)
            VALUES(NEW.artifact_id, NEW.revision);
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER artifact_revision_queue_extraction AFTER INSERT ON artifact_revisions
            FOR EACH ROW EXECUTE FUNCTION mnemonic_artifact_queue_extraction();

        CREATE FUNCTION mnemonic_artifact_invalidate_extraction() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.kind IN ('replace', 'delete') THEN
                UPDATE artifact_extractions
                SET normalized_text = NULL,
                    status = CASE WHEN NEW.kind = 'delete' THEN 'deleted' ELSE 'superseded' END,
                    lease_token = NULL, lease_expires_at = NULL
                WHERE artifact_id = NEW.artifact_id;
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER artifact_operation_invalidate_extraction BEFORE INSERT ON artifact_operations
            FOR EACH ROW EXECUTE FUNCTION mnemonic_artifact_invalidate_extraction();

        CREATE FUNCTION mnemonic_artifact_extraction_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND (
                (NEW.artifact_id, NEW.revision, NEW.created_at) IS DISTINCT FROM
                (OLD.artifact_id, OLD.revision, OLD.created_at)
                OR (OLD.extracted_at IS NOT NULL AND
                    (NEW.extracted_metadata, NEW.truncated, NEW.extracted_at) IS DISTINCT FROM
                    (OLD.extracted_metadata, OLD.truncated, OLD.extracted_at))
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'artifact extraction identity and extracted metadata are immutable';
            END IF;
            IF NEW.normalized_text IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM artifacts artifact
                WHERE artifact.id=NEW.artifact_id AND artifact.revision=NEW.revision
                  AND artifact.deleted_at IS NULL
                  AND NOT EXISTS (SELECT 1 FROM artifact_operations operation
                                  WHERE operation.artifact_id=artifact.id
                                    AND operation.state='pending')
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'artifact extracted text must belong to available current bytes';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER artifact_extraction_guard BEFORE INSERT OR UPDATE ON artifact_extractions
            FOR EACH ROW EXECUTE FUNCTION mnemonic_artifact_extraction_guard();
        CREATE TRIGGER artifact_extractions_no_delete BEFORE DELETE ON artifact_extractions
            FOR EACH ROW EXECUTE FUNCTION mnemonic_artifact_append_only();
        CREATE TRIGGER artifact_extractions_no_truncate BEFORE TRUNCATE ON artifact_extractions
            FOR EACH STATEMENT EXECUTE FUNCTION mnemonic_artifact_append_only();
    """)


def downgrade() -> None:
    op.execute("LOCK TABLE artifact_extractions IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM artifact_extractions)")):
        raise RuntimeError("Populated artifact extraction metadata cannot be safely downgraded")
    op.execute("DROP TRIGGER artifact_revision_queue_extraction ON artifact_revisions")
    op.execute("DROP TRIGGER artifact_operation_invalidate_extraction ON artifact_operations")
    op.drop_table("artifact_extractions")
    for name in ("queue_extraction", "invalidate_extraction", "extraction_guard"):
        op.execute(f"DROP FUNCTION mnemonic_artifact_{name}()")
