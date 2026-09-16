"""Persist common structured conversations and queue retained-copy backfill."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.transcript_normalization_db import (
    NORMALIZATION_CHECKS,
    normalization_columns,
    normalization_elements,
    segment_elements,
)

revision = "0040_normalized_transcripts"
down_revision = "0039_manual_review_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in normalization_columns():
        op.add_column("transcripts", column)
    for name, value in NORMALIZATION_CHECKS.items():
        op.create_check_constraint(name, "transcripts", value)
    op.create_table("transcript_normalizations", *normalization_elements())
    op.create_table("transcript_segments", *segment_elements())
    # Existing ready text remains available until a complete replacement publishes.
    # Invalidate all pre-migration worker claims; ordinary job reconciliation resumes.
    op.execute("UPDATE transcripts SET generation = generation + 1, attempts = 0, "
        "status = CASE WHEN status IN ('processing','failed') THEN 'pending' ELSE status END, "
        "reindex_status = CASE WHEN status = 'ready' THEN 'pending' ELSE NULL END, "
        "reindex_error_code = NULL, lease_token = NULL, lease_expires_at = NULL, "
        "next_attempt_at = clock_timestamp()")


def downgrade() -> None:
    op.execute("LOCK TABLE transcripts IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM transcript_normalizations)")):
        raise RuntimeError("Populated normalized conversations cannot be safely downgraded")
    op.drop_table("transcript_segments")
    op.drop_table("transcript_normalizations")
    for name in NORMALIZATION_CHECKS:
        op.drop_constraint("ck_transcripts_" + name, "transcripts")
    for column in reversed(normalization_columns()):
        op.drop_column("transcripts", column.name)
