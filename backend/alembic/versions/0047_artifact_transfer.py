"""Single-use download grants and reuse of extraction after metadata edits."""

import sqlalchemy as sa
from alembic import op

revision = "0047_artifact_transfer"
down_revision = "0046_shared_transcript_copies"
branch_labels = None
depends_on = None


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
    op.create_table(
        "artifact_download_capabilities",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("artifact_id", sa.UUID(), sa.ForeignKey("artifacts.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("agent_session_id", sa.String(200), nullable=False),
        sa.Column("actor_client", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("clock_timestamp()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    )
    # Keep ready text through a metadata intent so the new revision can reuse it.
    _invalidation(
        "'replace', 'delete'"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION mnemonic_artifact_queue_extraction() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO artifact_extractions(
                artifact_id, revision, status, normalized_text, extracted_metadata,
                truncated, extracted_at)
            SELECT NEW.artifact_id, NEW.revision, 'ready', extraction.normalized_text,
                   extraction.extracted_metadata, extraction.truncated, extraction.extracted_at
            FROM artifact_extractions extraction
            JOIN artifact_revisions previous USING (artifact_id, revision)
            WHERE extraction.artifact_id = NEW.artifact_id
              AND extraction.revision = NEW.revision - 1 AND extraction.status = 'ready'
              AND previous.sha256 = NEW.sha256
              AND EXISTS (SELECT 1 FROM artifact_operations operation
                          WHERE operation.artifact_id = NEW.artifact_id
                            AND operation.kind = 'update' AND operation.state = 'pending');
            IF NOT FOUND THEN
                INSERT INTO artifact_extractions(artifact_id, revision)
                VALUES(NEW.artifact_id, NEW.revision);
            END IF;
            UPDATE artifact_extractions SET normalized_text = NULL, status = 'superseded',
                lease_token = NULL, lease_expires_at = NULL
            WHERE artifact_id = NEW.artifact_id AND revision < NEW.revision
              AND status NOT IN ('superseded', 'deleted');
            RETURN NEW;
        END;
        $$;
    """)
    # A metadata intent never changes bytes. The current-revision guard still
    # excludes old workers and all pending replacements/deletions.
    definition = op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef('mnemonic_artifact_extraction_guard()'::regprocedure)"
    ))
    op.execute(definition.replace("operation.state='pending'",
                                  "operation.state='pending' AND operation.kind <> 'update'"))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM artifact_download_capabilities)")):
        raise RuntimeError("Retained download grants require restoring a matching backup")
    op.drop_table("artifact_download_capabilities")
    _invalidation(
        "'replace', 'delete', 'update'"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION mnemonic_artifact_queue_extraction() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO artifact_extractions(artifact_id, revision)
            VALUES(NEW.artifact_id, NEW.revision);
            RETURN NEW;
        END;
        $$;
    """)
    definition = op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef('mnemonic_artifact_extraction_guard()'::regprocedure)"
    ))
    op.execute(definition.replace(" AND operation.kind <> 'update'", ""))
