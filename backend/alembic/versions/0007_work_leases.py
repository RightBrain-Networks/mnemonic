"""Add expiring, replay-safe work leases.

Revision ID: 0007_work_leases
Revises: 0006_work_graph_contract
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_work_leases"
down_revision: str | None = "0006_work_graph_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_leases",
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("holder_client", sa.String(length=80), nullable=False),
        sa.Column("holder_session_id", sa.String(length=200), nullable=False),
        sa.Column("claim_request_id", sa.String(length=200), nullable=False),
        sa.Column("lease_token", sa.String(length=200), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(btrim(holder_client)) > 0",
            name=op.f("ck_work_leases_holder_client_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(holder_session_id)) > 0",
            name=op.f("ck_work_leases_holder_session_id_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(claim_request_id)) > 0",
            name=op.f("ck_work_leases_claim_request_id_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(lease_token)) > 0",
            name=op.f("ck_work_leases_lease_token_nonblank"),
        ),
        sa.CheckConstraint(
            "acquired_at <= renewed_at AND renewed_at < expires_at",
            name=op.f("ck_work_leases_timestamp_order"),
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=op.f("fk_work_leases_work_item_id_work_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("work_item_id", name=op.f("pk_work_leases")),
    )
    op.create_index("ix_work_leases_expires_at", "work_leases", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_work_leases_expires_at", table_name="work_leases")
    op.drop_table("work_leases")
