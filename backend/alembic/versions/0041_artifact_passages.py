"""Add revision-bound artifact passage vectors and bounded worker delivery."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.artifact_passage_db import (
    TEXT_HASH_EXPRESSION,
    passage_elements,
    passage_index_elements,
)

revision = "0041_artifact_passages"
down_revision = "0040_normalized_transcripts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Encoding is fixed, so the UTF-8 hash is immutable despite convert_to's
    # generic catalog stability declaration. Generated values also survive
    # privileged restores, where ordinary row triggers are temporarily disabled.
    op.execute("""
        CREATE FUNCTION mnemonic_artifact_text_sha256(value text) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
            SELECT encode(sha256(convert_to(value, 'UTF8')), 'hex')
        $$
    """)
    op.add_column("artifact_extractions", sa.Column("text_sha256", sa.String(64),
                  sa.Computed(TEXT_HASH_EXPRESSION, persisted=True)))
    op.create_table("artifact_passage_indexes", *passage_index_elements())
    op.create_table("artifact_passages", *passage_elements())
    op.drop_constraint("ck_background_jobs_kind_valid", "background_jobs")
    op.create_check_constraint("kind_valid", "background_jobs",
        "kind IN ('transcript_copy','transcript_index','backup_create','artifact_embed')")
    op.execute("""
        CREATE FUNCTION mnemonic_invalidate_artifact_passages() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status <> 'ready' OR NEW.text_sha256 IS DISTINCT FROM OLD.text_sha256 THEN
                DELETE FROM artifact_passage_indexes
                    WHERE artifact_id = NEW.artifact_id AND revision = NEW.revision;
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER artifact_extraction_invalidate_passages
            AFTER UPDATE ON artifact_extractions FOR EACH ROW
            EXECUTE FUNCTION mnemonic_invalidate_artifact_passages();
    """)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM background_jobs WHERE kind='artifact_embed')"
    )):
        raise RuntimeError("Artifact embedding delivery history prevents a safe downgrade")
    op.execute("DROP TRIGGER artifact_extraction_invalidate_passages ON artifact_extractions")
    op.execute("DROP FUNCTION mnemonic_invalidate_artifact_passages()")
    op.drop_table("artifact_passages")
    op.drop_table("artifact_passage_indexes")
    op.drop_column("artifact_extractions", "text_sha256")
    op.execute("DROP FUNCTION mnemonic_artifact_text_sha256(text)")
    op.drop_constraint("ck_background_jobs_kind_valid", "background_jobs")
    op.create_check_constraint("kind_valid", "background_jobs",
        "kind IN ('transcript_copy','transcript_index','backup_create')")
