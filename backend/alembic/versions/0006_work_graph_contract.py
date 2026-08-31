"""Remove legacy hand-off storage after the canonical cutover observation window.

Revision ID: 0006_work_graph_contract
Revises: 0005_work_graph_backfill

This is an operationally forward-only contract migration. Once canonical writes
exist, recreating the old mutable tables cannot faithfully represent checkpoint
history; rollback requires restoring the pre-contract database backup.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_work_graph_contract"
down_revision: str | None = "0005_work_graph_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS handoff_embeddings_read_only ON handoff_embeddings;
        DROP TRIGGER IF EXISTS handoff_comments_read_only ON handoff_comments;
        DROP TRIGGER IF EXISTS handoffs_read_only ON handoffs;
        DROP FUNCTION IF EXISTS mnemonic_reject_legacy_write();
        """
    )
    op.drop_table("handoff_embeddings")
    op.drop_table("handoff_comments")
    op.drop_table("handoffs")


def downgrade() -> None:
    raise RuntimeError(
        "0006_work_graph_contract is operationally forward-only; restore the "
        "pre-contract database backup instead"
    )
