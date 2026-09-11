"""Agent transcript provenance, durable extraction jobs and workspace settings."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.transcript_db_tables import (
    rebuild_elements,
    settings_elements,
    transcript_elements,
)

revision = "0032_agent_transcripts"
down_revision = "0031_review_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("transcripts", *transcript_elements())
    op.create_table("transcript_settings", *settings_elements())
    op.create_table("transcript_rebuilds", *rebuild_elements())


def downgrade() -> None:
    op.execute("LOCK TABLE transcripts, transcript_settings, transcript_rebuilds "
               "IN ACCESS EXCLUSIVE MODE")
    populated = op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM transcripts) OR "
        "EXISTS(SELECT 1 FROM transcript_settings) OR "
        "EXISTS(SELECT 1 FROM transcript_rebuilds)"))
    if populated:
        raise RuntimeError("Populated transcript data cannot be safely downgraded")
    op.drop_table("transcript_rebuilds")
    op.drop_table("transcript_settings")
    op.drop_table("transcripts")
