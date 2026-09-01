"""Add project-local typed work relationships.

Revision ID: 0008_work_relationships
Revises: 0007_work_leases
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_work_relationships"
down_revision: str | None = "0007_work_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_relationships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("relationship_type", sa.String(length=32), nullable=False),
        sa.Column("source_work_item_id", sa.UUID(), nullable=False),
        sa.Column("target_work_item_id", sa.UUID(), nullable=False),
        sa.Column("context_checkpoint_work_item_id", sa.UUID(), nullable=True),
        sa.Column("context_checkpoint_id", sa.UUID(), nullable=True),
        sa.Column("created_by_client", sa.String(length=80), nullable=False),
        sa.Column("created_by_session_id", sa.String(length=200), nullable=False),
        sa.Column("created_by_model", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "relationship_type IN "
            "('blocks', 'parent-child', 'discovered-from', 'duplicate-of', 'related')",
            name=op.f("ck_work_relationships_type_valid"),
        ),
        sa.CheckConstraint(
            "source_work_item_id <> target_work_item_id",
            name=op.f("ck_work_relationships_endpoints_differ"),
        ),
        sa.CheckConstraint(
            "(context_checkpoint_work_item_id IS NULL AND context_checkpoint_id IS NULL) OR "
            "(context_checkpoint_work_item_id IS NOT NULL AND context_checkpoint_id IS NOT NULL)",
            name=op.f("ck_work_relationships_context_pair"),
        ),
        sa.CheckConstraint(
            "context_checkpoint_work_item_id IS NULL OR "
            "context_checkpoint_work_item_id IN (source_work_item_id, target_work_item_id)",
            name=op.f("ck_work_relationships_context_endpoint"),
        ),
        sa.CheckConstraint(
            "relationship_type <> 'discovered-from' OR "
            "(context_checkpoint_id IS NOT NULL AND "
            "context_checkpoint_work_item_id = target_work_item_id)",
            name=op.f("ck_work_relationships_discovery_context"),
        ),
        sa.CheckConstraint(
            "relationship_type <> 'related' OR source_work_item_id < target_work_item_id",
            name=op.f("ck_work_relationships_related_normalized"),
        ),
        sa.CheckConstraint(
            "length(btrim(created_by_client)) > 0",
            name=op.f("ck_work_relationships_created_by_client_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(created_by_session_id)) > 0",
            name=op.f("ck_work_relationships_created_by_session_id_nonblank"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_relationships_source_work_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "target_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_relationships_target_work_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["context_checkpoint_work_item_id", "context_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_relationships_context_checkpoint",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_relationships")),
        sa.UniqueConstraint(
            "project_id",
            "relationship_type",
            "source_work_item_id",
            "target_work_item_id",
            name="uq_work_relationships_identity",
        ),
    )
    op.create_index(
        "uq_work_relationships_one_parent",
        "work_relationships",
        ["target_work_item_id"],
        unique=True,
        postgresql_where=sa.text("relationship_type = 'parent-child'"),
    )
    op.create_index(
        "ix_work_relationships_source",
        "work_relationships",
        ["project_id", "source_work_item_id", "relationship_type"],
    )
    op.create_index(
        "ix_work_relationships_target",
        "work_relationships",
        ["project_id", "target_work_item_id", "relationship_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_relationships_target", table_name="work_relationships")
    op.drop_index("ix_work_relationships_source", table_name="work_relationships")
    op.drop_index("uq_work_relationships_one_parent", table_name="work_relationships")
    op.drop_table("work_relationships")
