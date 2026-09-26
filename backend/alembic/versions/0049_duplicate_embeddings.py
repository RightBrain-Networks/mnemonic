"""Isolate work embedding consumers and queue bounded duplicate cache refreshes."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.duplicate_embedding_db import refresh_elements

revision = "0049_duplicate_embeddings"
down_revision = "0048_force_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_item_embeddings", sa.Column("purpose", sa.String(30), nullable=True))
    op.execute("""
        UPDATE work_item_embeddings SET purpose = CASE
            WHEN model LIKE 'duplicate-suggestion-v1|%' THEN 'duplicate_suggestions'
            ELSE 'work_search' END
    """)
    op.alter_column("work_item_embeddings", "purpose", nullable=False, server_default="work_search")
    op.drop_constraint("pk_work_item_embeddings", "work_item_embeddings", type_="primary")
    op.create_primary_key("pk_work_item_embeddings", "work_item_embeddings",
                          ["work_item_id", "purpose"])
    op.create_check_constraint("purpose_valid", "work_item_embeddings",
                               "purpose IN ('work_search','duplicate_suggestions')")
    op.create_table("duplicate_embedding_refreshes", *refresh_elements())
    op.drop_constraint("ck_background_jobs_kind_valid", "background_jobs")
    op.create_check_constraint("kind_valid", "background_jobs",
        "kind IN ('transcript_copy','transcript_index','backup_create',"
        "'artifact_embed','duplicate_embed')")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM background_jobs WHERE kind='duplicate_embed')"
    )):
        raise RuntimeError("Duplicate embedding delivery history prevents a safe downgrade")
    op.drop_table("duplicate_embedding_refreshes")
    # Derived vectors are disposable; keep the search cache when both consumers exist.
    op.execute("""
        DELETE FROM work_item_embeddings AS duplicate
        USING work_item_embeddings AS search
        WHERE duplicate.work_item_id = search.work_item_id
          AND duplicate.purpose = 'duplicate_suggestions' AND search.purpose = 'work_search'
    """)
    op.drop_constraint("pk_work_item_embeddings", "work_item_embeddings", type_="primary")
    op.drop_constraint("ck_work_item_embeddings_purpose_valid", "work_item_embeddings")
    op.drop_column("work_item_embeddings", "purpose")
    op.create_primary_key("pk_work_item_embeddings", "work_item_embeddings", ["work_item_id"])
    op.drop_constraint("ck_background_jobs_kind_valid", "background_jobs")
    op.create_check_constraint("kind_valid", "background_jobs",
        "kind IN ('transcript_copy','transcript_index','backup_create','artifact_embed')")
