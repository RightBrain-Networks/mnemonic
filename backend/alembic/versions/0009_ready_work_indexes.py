"""Add indexes supporting deterministic ready-work discovery.

Revision ID: 0009_ready_work_indexes
Revises: 0008_work_relationships
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_ready_work_indexes"
down_revision: str | None = "0008_work_relationships"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_work_items_ready_order",
        "work_items",
        ["project_id", sa.text("priority DESC"), sa.text("created_at ASC"), sa.text("id ASC")],
        postgresql_where=sa.text("deleted_at IS NULL AND status = 'open'"),
    )
    op.execute(
        """
        CREATE FUNCTION mnemonic_normalized_tags(varchar[])
        RETURNS text[]
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $function$
            SELECT COALESCE(
                array_agg(DISTINCT pg_catalog.lower(tag) ORDER BY pg_catalog.lower(tag)),
                ARRAY[]::text[]
            )
            FROM pg_catalog.unnest($1) AS tag
            WHERE tag IS NOT NULL
        $function$;
        """
    )
    op.create_index(
        "ix_checkpoints_normalized_tags_gin",
        "checkpoints",
        [sa.text("mnemonic_normalized_tags(tags)")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_checkpoints_normalized_tags_gin", table_name="checkpoints")
    op.execute("DROP FUNCTION mnemonic_normalized_tags(varchar[])")
    op.drop_index("ix_work_items_ready_order", table_name="work_items")
