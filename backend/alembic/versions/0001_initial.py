"""Projects, durable hand-offs, and weighted PostgreSQL full-text search.

Revision ID: 0001_initial
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("description", sa.String(4000), server_default="", nullable=False),
        sa.Column("repository_url", sa.String(2000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_projects_name_nonblank")),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name=op.f("ck_projects_slug_format")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("slug", name=op.f("uq_projects_slug")),
    )
    op.create_table(
        "handoffs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.String(1000), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("source_client", sa.String(80), nullable=False),
        sa.Column("source_session_id", sa.String(200), nullable=False),
        sa.Column("source_model", sa.String(120), nullable=True),
        sa.Column("source_session_url", sa.String(2000), nullable=True),
        sa.Column("repository_branch", sa.String(200), nullable=True),
        sa.Column("verified_against", sa.String(64), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(50)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), server_default="open", nullable=False),
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
                "setweight(to_tsvector('english'::regconfig, coalesce(summary, '')), 'B') || "
                "setweight(to_tsvector('english'::regconfig, coalesce(prompt, '')), 'C')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint("length(btrim(title)) > 0", name=op.f("ck_handoffs_title_nonblank")),
        sa.CheckConstraint("length(btrim(summary)) > 0", name=op.f("ck_handoffs_summary_nonblank")),
        sa.CheckConstraint(
            "length(btrim(prompt)) BETWEEN 1 AND 100000", name=op.f("ck_handoffs_prompt_length")
        ),
        sa.CheckConstraint("length(prompt) <= 100000", name=op.f("ck_handoffs_prompt_max_length")),
        sa.CheckConstraint(
            "length(btrim(source_client)) > 0", name=op.f("ck_handoffs_source_client_nonblank")
        ),
        sa.CheckConstraint(
            "length(btrim(source_session_id)) > 0", name=op.f("ck_handoffs_session_id_nonblank")
        ),
        sa.CheckConstraint(
            "status IN ('open', 'done', 'wont-do', 'promoted')",
            name=op.f("ck_handoffs_status_valid"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_handoffs_version_positive")),
        sa.CheckConstraint("cardinality(tags) <= 20", name=op.f("ck_handoffs_tags_count")),
        sa.CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'", name=op.f("ck_handoffs_metadata_object")
        ),
        sa.CheckConstraint(
            "verified_against IS NULL OR verified_against ~ '^[0-9a-f]{7,64}$'",
            name=op.f("ck_handoffs_commit_format"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="RESTRICT",
            name=op.f("fk_handoffs_project_id_projects"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handoffs")),
    )
    op.create_index(
        "ix_handoffs_project_status_updated",
        "handoffs",
        ["project_id", "status", "updated_at", "id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_handoffs_search_vector", "handoffs", ["search_vector"], postgresql_using="gin"
    )
    op.create_index("ix_handoffs_tags", "handoffs", ["tags"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_handoffs_tags", table_name="handoffs")
    op.drop_index("ix_handoffs_search_vector", table_name="handoffs")
    op.drop_index("ix_handoffs_project_status_updated", table_name="handoffs")
    op.drop_table("handoffs")
    op.drop_table("projects")
