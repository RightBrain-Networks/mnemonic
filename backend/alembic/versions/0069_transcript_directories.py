"""Persist project transcript folders independently of immutable import receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0069_transcript_directories"
down_revision = "0068_artifact_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcript_settings", sa.Column(
        "directories", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.create_check_constraint("directories_valid", "transcript_settings",
        "jsonb_typeof(directories) = 'array' AND jsonb_array_length(directories) <= 100")
    # Keep actual import choices and surface historical access failures as editable
    # rows. Healthy automatic enrollments aren't import configuration: thousands
    # of worktrees must not become thousands of dashboard folders. Global operator
    # roots can contain other projects' conversations and are never auto-selected.
    op.execute("""
        WITH candidates AS (
            SELECT DISTINCT project_id, directory FROM transcript_imports
            UNION
            SELECT DISTINCT w.project_id,
                   regexp_replace(t.source_path, '/[^/]+/?$', '') AS directory
            FROM transcripts t JOIN work_items w ON w.id = t.work_item_id
            WHERE t.copy_status <> 'ready' AND t.copy_error_code IS NOT NULL
        ), folders AS (
            SELECT project_id, directory FROM candidates child
            WHERE directory <> '' AND NOT EXISTS (
                SELECT 1 FROM candidates parent
                WHERE parent.project_id = child.project_id AND parent.directory <> ''
                AND parent.directory <> child.directory
                AND starts_with(child.directory, rtrim(parent.directory, '/') || '/'))
        )
        INSERT INTO transcript_settings (project_id, directories)
        SELECT project_id, jsonb_agg(directory ORDER BY directory)
        FROM folders GROUP BY project_id
        ON CONFLICT (project_id) DO UPDATE SET directories = EXCLUDED.directories
    """)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM transcript_settings")):
        raise RuntimeError("Transcript settings cannot be safely downgraded once configured.")
    op.drop_constraint(op.f("ck_transcript_settings_directories_valid"), "transcript_settings")
    op.drop_column("transcript_settings", "directories")
