"""Queue every historical source for durable copying without losing indexed text."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.transcript_copy_db import COPY_CHECKS, INDEX_LEASE_CHECK, copy_columns

revision = "0036_transcript_copies"
down_revision = "0035_prompt_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in copy_columns():
        op.add_column("transcripts", column)
    for name, value in COPY_CHECKS.items():
        op.create_check_constraint(name, "transcripts", value)
    op.drop_constraint("ck_transcripts_lease_valid", "transcripts")
    op.create_check_constraint("lease_valid", "transcripts", INDEX_LEASE_CHECK)
    op.create_index("ix_transcripts_copy_due", "transcripts",
                    ["copy_status", "copy_next_attempt_at"])
    # Invalidate claims from old processes; already-ready text stays readable.
    op.execute("UPDATE transcripts SET status = 'pending', lease_token = NULL, "
               "lease_expires_at = NULL WHERE status = 'processing'")


def downgrade() -> None:
    op.execute("LOCK TABLE transcripts IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM transcripts "
                                    "WHERE copy_attempts > 0 OR storage_key IS NOT NULL)")):
        raise RuntimeError("Populated transcript copies cannot be safely downgraded")
    op.drop_index("ix_transcripts_copy_due", table_name="transcripts")
    op.drop_constraint("ck_transcripts_lease_valid", "transcripts")
    op.create_check_constraint("lease_valid", "transcripts",
        "(status = 'processing' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
        "OR (status <> 'processing' AND lease_token IS NULL AND lease_expires_at IS NULL)")
    for name in COPY_CHECKS:
        op.drop_constraint("ck_transcripts_" + name, "transcripts")
    for column in reversed(copy_columns()):
        op.drop_column("transcripts", column.name)
