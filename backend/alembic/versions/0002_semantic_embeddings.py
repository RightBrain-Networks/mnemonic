"""Add derived hand-off embedding cache.

Revision ID: 0002_semantic_embeddings
Revises: 0001_initial
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_semantic_embeddings"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handoff_embeddings",
        sa.Column("handoff_id", sa.UUID(), nullable=False),
        sa.Column("model", sa.String(length=300), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("vector", postgresql.ARRAY(sa.REAL()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cardinality(vector) > 0", name="vector_nonempty"
        ),
        sa.ForeignKeyConstraint(
            ["handoff_id"],
            ["handoffs.id"],
            name="fk_handoff_embeddings_handoff_id_handoffs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("handoff_id", name="pk_handoff_embeddings"),
    )


def downgrade() -> None:
    op.drop_table("handoff_embeddings")
