"""Add append-only hand-off comments and completion summaries.

Revision ID: 0003_handoff_comments
Revises: 0002_semantic_embeddings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_handoff_comments"
down_revision: str | None = "0002_semantic_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handoff_comments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("handoff_id", sa.UUID(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="comment", nullable=False),
        sa.Column("source_client", sa.String(length=80), nullable=False),
        sa.Column("source_session_id", sa.String(length=200), nullable=False),
        sa.Column("source_model", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('english'::regconfig, coalesce(body, '')), 'B')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(body)) BETWEEN 1 AND 50000",
            name=op.f("ck_handoff_comments_body_length"),
        ),
        sa.CheckConstraint(
            "length(body) <= 50000", name=op.f("ck_handoff_comments_body_max_length")
        ),
        sa.CheckConstraint(
            "kind IN ('comment', 'work-summary')",
            name=op.f("ck_handoff_comments_kind_valid"),
        ),
        sa.CheckConstraint(
            "length(btrim(source_client)) > 0",
            name=op.f("ck_handoff_comments_source_client_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(source_session_id)) > 0",
            name=op.f("ck_handoff_comments_session_id_nonblank"),
        ),
        sa.ForeignKeyConstraint(
            ["handoff_id"],
            ["handoffs.id"],
            name=op.f("fk_handoff_comments_handoff_id_handoffs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handoff_comments")),
    )
    op.create_index(
        "ix_handoff_comments_handoff_created",
        "handoff_comments",
        ["handoff_id", "created_at", "id"],
    )
    op.create_index(
        "ix_handoff_comments_search_vector",
        "handoff_comments",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_handoff_comments_search_vector", table_name="handoff_comments")
    op.drop_index("ix_handoff_comments_handoff_created", table_name="handoff_comments")
    op.drop_table("handoff_comments")
