"""Retain force-claim retry identities independently of the current lease."""

import sqlalchemy as sa
from alembic import op

revision = "0048_force_claims"
down_revision = "0047_artifact_transfer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_force_claims",
        sa.Column("work_item_id", sa.UUID(), sa.ForeignKey("work_items.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("claim_request_id", sa.String(200), primary_key=True),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("lease_generation_id", sa.UUID(), nullable=False),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE work_force_claims IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM work_force_claims)")):
        raise RuntimeError("Retained force claims require restoring a matching backup")
    op.drop_table("work_force_claims")
