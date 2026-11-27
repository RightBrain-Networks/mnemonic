"""Schema for project resources and their retained reservation generations."""

import sqlalchemy as sa


def resource_elements() -> tuple:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("capacity", sa.BigInteger(), nullable=False),
        sa.Column("minimum_minutes", sa.Integer(), nullable=False),
        sa.Column("default_minutes", sa.Integer(), nullable=False),
        sa.Column("maximum_minutes", sa.Integer(), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_external_resources_project_name"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="name_nonblank"),
        sa.CheckConstraint("capacity BETWEEN 1 AND 4294967295", name="capacity_valid"),
        sa.CheckConstraint(
            "minimum_minutes >= 1 AND default_minutes >= minimum_minutes "
            "AND maximum_minutes >= default_minutes",
            name="minutes_valid",
        ),
    )


def lease_elements() -> tuple:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "resource_id",
            sa.Uuid(),
            sa.ForeignKey("external_resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("holder_client", sa.String(80), nullable=False),
        sa.Column("holder_session_id", sa.String(200), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("claim_request_id", sa.Uuid(), nullable=False),
        sa.Column("claim_quantity", sa.BigInteger(), nullable=False),
        sa.Column("claim_lease_minutes", sa.Integer()),
        sa.Column("lease_token", sa.String(200)),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_reason", sa.String(8)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("resource_id", "claim_request_id", name="uq_external_lease_request"),
        sa.Index("ix_external_lease_due", "expires_at", "id",
                 postgresql_where=sa.text("ended_at IS NULL")),
        sa.Index(
            "uq_external_lease_open_holder",
            "resource_id",
            "holder_client",
            "holder_session_id",
            unique=True,
            postgresql_where=sa.text("ended_at IS NULL"),
        ),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 4294967295 AND claim_quantity BETWEEN 1 AND 4294967295",
            name="quantity_valid",
        ),
        sa.CheckConstraint("claim_lease_minutes >= 1", name="claim_minutes_valid"),
        sa.CheckConstraint(
            "expires_at > renewed_at AND renewed_at >= acquired_at", name="duration_valid"
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL AND ended_reason IS NULL AND lease_token IS NOT NULL)"
            " OR (ended_at IS NOT NULL AND ended_reason IS NOT NULL AND ended_reason"
            " IN ('released','revoked','expired') AND lease_token IS NULL)",
            name="end_valid",
        ),
    )
