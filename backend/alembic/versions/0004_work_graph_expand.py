"""Expand the schema with canonical work items and immutable checkpoints.

Revision ID: 0004_work_graph_expand
Revises: 0003_handoff_comments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_work_graph_expand"
down_revision: str | None = "0003_handoff_comments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="open", nullable=False),
        sa.Column("priority", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("initial_checkpoint_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
                "setweight(to_tsvector('english'::regconfig, coalesce(summary, '')), 'B')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(title)) > 0", name=op.f("ck_work_items_title_nonblank")
        ),
        sa.CheckConstraint(
            "length(btrim(summary)) > 0", name=op.f("ck_work_items_summary_nonblank")
        ),
        sa.CheckConstraint(
            "status IN ('open', 'done', 'wont-do', 'promoted')",
            name=op.f("ck_work_items_status_valid"),
        ),
        sa.CheckConstraint(
            "priority BETWEEN 0 AND 100", name=op.f("ck_work_items_priority_range")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_work_items_version_positive")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_work_items_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_items")),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_work_items_project_id_id"
        ),
    )
    op.create_table(
        "checkpoints",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="context", nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("source_client", sa.String(length=80), nullable=False),
        sa.Column("source_session_id", sa.String(length=200), nullable=False),
        sa.Column("source_model", sa.String(length=120), nullable=True),
        sa.Column("source_session_url", sa.String(length=2000), nullable=True),
        sa.Column("repository_branch", sa.String(length=200), nullable=True),
        sa.Column("verified_against", sa.String(length=64), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(length=50)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("migration_origin", sa.String(length=40), nullable=True),
        sa.Column("legacy_record_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('english'::regconfig, coalesce(prompt, '')), 'C')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('context', 'progress', 'completion')",
            name=op.f("ck_checkpoints_kind_valid"),
        ),
        sa.CheckConstraint(
            "length(btrim(prompt)) BETWEEN 1 AND 100000",
            name=op.f("ck_checkpoints_prompt_length"),
        ),
        sa.CheckConstraint(
            "length(prompt) <= 100000", name=op.f("ck_checkpoints_prompt_max_length")
        ),
        sa.CheckConstraint(
            "length(btrim(source_client)) > 0",
            name=op.f("ck_checkpoints_source_client_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(source_session_id)) > 0",
            name=op.f("ck_checkpoints_session_id_nonblank"),
        ),
        sa.CheckConstraint(
            "cardinality(tags) <= 20", name=op.f("ck_checkpoints_tags_count")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name=op.f("ck_checkpoints_metadata_object"),
        ),
        sa.CheckConstraint(
            "verified_against IS NULL OR verified_against ~ '^[0-9a-f]{7,64}$'",
            name=op.f("ck_checkpoints_commit_format"),
        ),
        sa.CheckConstraint(
            "(migration_origin IS NULL AND legacy_record_id IS NULL) OR "
            "(migration_origin IN ('legacy-handoff-snapshot', 'legacy-comment') "
            "AND legacy_record_id IS NOT NULL)",
            name=op.f("ck_checkpoints_migration_fields_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=op.f("fk_checkpoints_work_item_id_work_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checkpoints")),
        sa.UniqueConstraint(
            "work_item_id", "id", name="uq_checkpoints_work_item_id_id"
        ),
        sa.UniqueConstraint(
            "migration_origin",
            "legacy_record_id",
            name="uq_checkpoints_migration_origin_legacy",
        ),
    )
    op.create_foreign_key(
        "fk_work_items_initial_checkpoint",
        "work_items",
        "checkpoints",
        ["id", "initial_checkpoint_id"],
        ["work_item_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        "ix_work_items_project_status_updated",
        "work_items",
        ["project_id", "status", sa.text("updated_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_work_items_search_vector", "work_items", ["search_vector"], postgresql_using="gin"
    )
    op.create_index(
        "ix_checkpoints_work_item_created",
        "checkpoints",
        ["work_item_id", "created_at", "id"],
    )
    op.create_index(
        "ix_checkpoints_search_vector", "checkpoints", ["search_vector"], postgresql_using="gin"
    )
    op.create_index(
        "ix_checkpoints_tags", "checkpoints", ["tags"], postgresql_using="gin"
    )
    op.create_table(
        "work_item_embeddings",
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("model", sa.String(length=300), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("vector", postgresql.ARRAY(sa.REAL()), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "cardinality(vector) > 0", name=op.f("ck_work_item_embeddings_vector_nonempty")
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=op.f("fk_work_item_embeddings_work_item_id_work_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("work_item_id", name=op.f("pk_work_item_embeddings")),
    )


def downgrade() -> None:
    op.drop_table("work_item_embeddings")
    op.drop_index("ix_checkpoints_tags", table_name="checkpoints")
    op.drop_index("ix_checkpoints_search_vector", table_name="checkpoints")
    op.drop_index("ix_checkpoints_work_item_created", table_name="checkpoints")
    op.drop_index("ix_work_items_search_vector", table_name="work_items")
    op.drop_index("ix_work_items_project_status_updated", table_name="work_items")
    op.drop_constraint("fk_work_items_initial_checkpoint", "work_items", type_="foreignkey")
    op.drop_table("checkpoints")
    op.drop_table("work_items")
