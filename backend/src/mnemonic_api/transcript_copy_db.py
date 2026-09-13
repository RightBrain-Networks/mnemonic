"""Durable capture identity is independent from a rebuild's indexing generation."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


def copy_columns() -> list[sa.Column]:
    return [
        sa.Column("snapshot_id", UUID, nullable=False, server_default=sa.func.gen_random_uuid()),
        sa.Column("copy_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("storage_key", sa.String(100)),
        sa.Column("copy_sha256", sa.String(64)),
        sa.Column("copy_size_bytes", sa.BigInteger),
        sa.Column("copied_at", sa.DateTime(timezone=True)),
        sa.Column("copy_error_code", sa.String(80)),
        sa.Column("copy_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("copy_next_attempt_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("copy_lease_token", UUID),
        sa.Column("copy_lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("reindex_status", sa.String(16)),
        sa.Column("reindex_error_code", sa.String(80)),
    ]


COPY_CHECKS = {
    "copy_status_valid": "copy_status IN ('pending','processing','ready','failed')",
    "copy_attempts_valid": "copy_attempts >= 0",
    "reindex_status_valid": "reindex_status IS NULL OR (status = 'ready' "
        "AND reindex_status IN ('pending','processing','failed'))",
    "copy_lease_valid": "(copy_status = 'processing' AND copy_lease_token IS NOT NULL "
        "AND copy_lease_expires_at IS NOT NULL) OR (copy_status <> 'processing' "
        "AND copy_lease_token IS NULL AND copy_lease_expires_at IS NULL)",
    "copy_snapshot_valid": "(copy_status = 'ready' AND storage_key IS NOT NULL "
        "AND storage_key = id::text || '/' || snapshot_id::text || '/transcript.jsonl' "
        "AND copy_sha256 IS NOT NULL AND copy_sha256 ~ '^[0-9a-f]{64}$' "
        "AND copy_size_bytes IS NOT NULL AND copy_size_bytes BETWEEN 0 AND 268435456 "
        "AND copied_at IS NOT NULL) OR (copy_status <> 'ready' AND storage_key IS NULL "
        "AND copy_sha256 IS NULL AND copy_size_bytes IS NULL AND copied_at IS NULL)",
}

INDEX_LEASE_CHECK = (
    "((status = 'processing' OR coalesce(reindex_status, '') = 'processing') "
    "AND lease_token IS NOT NULL "
    "AND lease_expires_at IS NOT NULL) OR (status <> 'processing' "
    "AND reindex_status IS DISTINCT FROM 'processing' AND lease_token IS NULL "
    "AND lease_expires_at IS NULL)"
)


def copy_elements() -> list:
    return [*copy_columns(), *(sa.CheckConstraint(value, name=name)
                              for name, value in COPY_CHECKS.items()),
            sa.Index("ix_transcripts_copy_due", "copy_status", "copy_next_attempt_at")]
