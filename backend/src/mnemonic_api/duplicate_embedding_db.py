"""Disposable, demand-driven duplicate vector refresh generations."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


def refresh_elements() -> list:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("config", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("target_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
        sa.Column("next_offset", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.String(80)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.CheckConstraint("cardinality(target_ids) BETWEEN 1 AND 10000", name="targets_bounded"),
        sa.CheckConstraint("next_offset BETWEEN 0 AND cardinality(target_ids)",
                           name="cursor_valid"),
        sa.CheckConstraint("status IN ('pending','completed','failed')", name="status_valid"),
    ]
