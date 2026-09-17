"""Current-source passage caches; artifact extraction remains the durable authority."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, REAL, UUID

TEXT_HASH_EXPRESSION = "mnemonic_artifact_text_sha256(normalized_text)"


def passage_index_elements() -> list:
    return [
        sa.Column("id", UUID, primary_key=True),
        sa.Column("artifact_id", UUID, sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("model", sa.String(300), nullable=False),
        sa.Column("chunk_config", sa.String(100), nullable=False),
        sa.Column("total_chars", sa.Integer, nullable=False),
        sa.Column("token_limit", sa.Integer, nullable=False),
        sa.Column("next_offset", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_ordinal", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dimensions", sa.Integer),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.String(80)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.CheckConstraint("revision > 0 AND total_chars BETWEEN 0 AND 8000000 "
                           "AND next_offset BETWEEN 0 AND total_chars AND next_ordinal >= 0 "
                           "AND token_limit BETWEEN 1 AND 8192 "
                           "AND (dimensions IS NULL OR dimensions BETWEEN 1 AND 4096)",
                           name="counters_valid"),
        sa.CheckConstraint("status IN ('pending','processing','ready','failed')",
                           name="status_valid"),
        sa.CheckConstraint("text_sha256 ~ '^[0-9a-f]{64}$'", name="hash_valid"),
        sa.Index("ix_artifact_passage_indexes_status", "status", "updated_at"),
    ]


def passage_elements() -> list:
    return [
        sa.Column("index_id", UUID,
                  sa.ForeignKey("artifact_passage_indexes.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("ordinal", sa.Integer, primary_key=True),
        sa.Column("passage_id", sa.String(64), nullable=False),
        sa.Column("start_offset", sa.Integer, nullable=False),
        sa.Column("end_offset", sa.Integer, nullable=False),
        sa.Column("vector", ARRAY(REAL), nullable=False),
        sa.Column("token_count", sa.Integer, nullable=False),
        sa.CheckConstraint("ordinal >= 0 AND start_offset >= 0 AND end_offset > start_offset "
                           "AND end_offset - start_offset <= 1500 "
                           "AND token_count BETWEEN 1 AND 8192", name="range_valid"),
        sa.CheckConstraint("passage_id ~ '^[0-9a-f]{64}$'", name="identity_valid"),
        sa.CheckConstraint("cardinality(vector) BETWEEN 1 AND 4096 "
                           "AND NOT ('NaN'::real = ANY(vector)) "
                           "AND NOT ('Infinity'::real = ANY(vector)) "
                           "AND NOT ('-Infinity'::real = ANY(vector))", name="vector_valid"),
    ]
