"""Add optional per-project dashboard settings.

Revision ID: 0011_project_settings
Revises: 0010_work_events
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_project_settings"
down_revision: str | None = "0010_work_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_settings",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("recall_pointer_template", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "mnemonic_has_non_whitespace(recall_pointer_template)",
            name=op.f("ck_project_settings_recall_pointer_template_nonblank"),
        ),
        sa.CheckConstraint(
            "length(recall_pointer_template) <= 100000",
            name=op.f("ck_project_settings_recall_pointer_template_max_length"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_settings_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_project_settings")),
    )


def downgrade() -> None:
    op.drop_table("project_settings")
