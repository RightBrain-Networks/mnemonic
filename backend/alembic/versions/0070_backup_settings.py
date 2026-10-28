"""Store opt-in nightly backup schedules and day-based retention per project."""

import sqlalchemy as sa
from alembic import op

revision = "0070_backup_settings"
down_revision = "0069_transcript_directories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_settings",
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("time", sa.String(5), nullable=False, server_default="02:00"),
        sa.Column("timezone", sa.String(100), nullable=False, server_default="UTC"),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("retention_days BETWEEN 0 AND 36500", name="retention_days_valid"),
        sa.CheckConstraint("time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'", name="time_valid"),
        sa.CheckConstraint("length(timezone) BETWEEN 1 AND 100", name="timezone_valid"),
    )


def downgrade() -> None:
    op.drop_table("backup_settings")
