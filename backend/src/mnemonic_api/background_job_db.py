"""Frozen, shared schema for the PostgreSQL job ledger and transactional outbox."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


def job_elements() -> list:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False, unique=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="20"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("publish_after", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("lease_token", UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("result", JSONB(none_as_null=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('transcript_copy','transcript_index','backup_create')",
                           name="kind_valid"),
        sa.CheckConstraint("status IN ('pending','running','succeeded','failed')",
                           name="status_valid"),
        sa.CheckConstraint("attempts >= 0 AND max_attempts BETWEEN 1 AND 100",
                           name="attempts_valid"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object' "
                           "AND octet_length(payload::text) <= 4096", name="payload_valid"),
        sa.CheckConstraint("result IS NULL OR (jsonb_typeof(result) = 'object' "
                           "AND octet_length(result::text) <= 16384)", name="result_valid"),
        sa.CheckConstraint("(status = 'running' AND lease_token IS NOT NULL "
                           "AND lease_expires_at IS NOT NULL) OR "
                           "(status <> 'running' AND lease_token IS NULL "
                           "AND lease_expires_at IS NULL)", name="lease_valid"),
        sa.CheckConstraint("(status IN ('succeeded','failed')) = (completed_at IS NOT NULL)",
                           name="completion_valid"),
        sa.Index("ix_background_jobs_dispatch", "status", "publish_after", "due_at"),
    ]
