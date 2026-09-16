"""Versioned manifest and streamable segment rows for normalized conversations."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


def normalization_columns() -> list:
    return [
        sa.Column("normalization_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("normalization_error_code", sa.String(80)),
        sa.Column("normalized_revision", sa.String(64)),
        sa.Column("normalized_sha256", sa.String(64)),
        sa.Column("normalization_schema_version", sa.Integer),
        sa.Column("normalizer_version", sa.Integer),
        sa.Column("normalized_size_bytes", sa.BigInteger),
        sa.Column("segment_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("normalization_incomplete", sa.Boolean, nullable=False, server_default="false"),
    ]


def normalization_elements() -> list:
    return [
        sa.Column("transcript_id", UUID, sa.ForeignKey("transcripts.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("snapshot_id", UUID, nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False),
        sa.Column("normalizer_version", sa.Integer, nullable=False),
        sa.Column("format", sa.String(80), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("metadata", JSONB, nullable=False),
        sa.Column("incomplete", sa.Boolean, nullable=False),
        sa.Column("segment_count", sa.Integer, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.CheckConstraint("schema_version > 0 AND normalizer_version > 0 AND segment_count >= 0",
                           name="counters_valid"),
    ]


def segment_elements() -> list:
    return [
        sa.Column("transcript_id", UUID, primary_key=True),
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("ordinal", sa.Integer, primary_key=True),
        sa.Column("segment_id", sa.String(24), nullable=False),
        sa.Column("content_kind", sa.String(24), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("segment_data", JSONB, nullable=False),
        sa.ForeignKeyConstraint(["transcript_id", "revision"],
            ["transcript_normalizations.transcript_id", "transcript_normalizations.revision"],
            ondelete="CASCADE"),
        sa.UniqueConstraint("transcript_id", "revision", "segment_id",
                            name="uq_transcript_segments_identity"),
        sa.CheckConstraint("ordinal >= 0", name="ordinal_valid"),
        sa.CheckConstraint("content_kind IN ('human_text','assistant_text','tool_call',"
                           "'tool_result','system_text','reasoning','summary','unsupported')",
                           name="kind_valid"),
        sa.CheckConstraint("jsonb_typeof(segment_data) = 'object' "
                           "AND segment_data->>'text' = text "
                           "AND segment_data->>'segment_id' = segment_id "
                           "AND segment_data->>'content_kind' = content_kind "
                           "AND (segment_data->>'ordinal')::integer = ordinal", name="data_valid"),
        sa.Index("ix_transcript_segments_kind", "transcript_id", "revision", "content_kind"),
    ]


NORMALIZATION_CHECKS = {
    "normalization_status_valid":
        "normalization_status IN ('pending','processing','ready','failed')",
    "normalization_counters_valid": "segment_count >= 0 AND "
        "(normalized_size_bytes IS NULL OR normalized_size_bytes >= 0)",
    "normalization_ready_valid":
        "normalization_status <> 'ready' OR normalized_revision IS NOT NULL",
    "normalization_snapshot_valid": "(normalized_revision IS NULL AND normalized_sha256 IS NULL "
        "AND normalization_schema_version IS NULL AND normalizer_version IS NULL "
        "AND normalized_size_bytes IS NULL AND segment_count = 0) OR "
        "(normalized_revision IS NOT NULL AND normalized_sha256 IS NOT NULL "
        "AND normalization_schema_version > 0 AND normalizer_version > 0 "
        "AND normalized_size_bytes IS NOT NULL)",
}


def normalization_checks() -> list:
    return [sa.CheckConstraint(value, name=name) for name, value in NORMALIZATION_CHECKS.items()]
