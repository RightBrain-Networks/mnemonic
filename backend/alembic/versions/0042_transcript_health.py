"""Retain actionable copy diagnostics and worker filesystem health observations."""

from alembic import op

from mnemonic_api.transcript_health_db import diagnostic_columns, worker_health_elements

revision = "0042_transcript_health"
down_revision = "0041_artifact_passages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in diagnostic_columns():
        op.add_column("transcripts", column)
    op.create_table("transcript_worker_health", *worker_health_elements())


def downgrade() -> None:
    op.drop_table("transcript_worker_health")
    op.drop_column("transcripts", "copy_error_details")
