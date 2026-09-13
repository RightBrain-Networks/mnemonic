"""Persist asynchronous jobs independently of broker delivery and restarts."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.background_job_db import job_elements

revision = "0037_background_jobs"
down_revision = "0036_transcript_copies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("background_jobs", *job_elements())


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM background_jobs)")):
        raise RuntimeError("Durable background jobs cannot be safely downgraded")
    op.drop_table("background_jobs")
