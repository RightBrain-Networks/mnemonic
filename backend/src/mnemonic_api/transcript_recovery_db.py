"""Immutable operator approvals preserve original agent path assertions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


def recovery_elements() -> list:
    return [
        sa.Column("operation_id", UUID, primary_key=True),
        sa.Column("transcript_id", UUID, sa.ForeignKey("transcripts.id", ondelete="RESTRICT"),
                  nullable=False),
        # Historical requested identity, not ownership: recovery follows the
        # transcript when its work moves between projects.
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("original_source_path", sa.String(4096), nullable=False),
        sa.Column("replacement_path", sa.String(4096), nullable=False),
        sa.Column("expected_generation", sa.Integer, nullable=False),
        sa.Column("expected_snapshot_id", UUID, nullable=False),
        sa.Column("expected_sha256", sa.String(64), nullable=False),
        sa.Column("expected_size_bytes", sa.BigInteger, nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("evidence", sa.String(8000), nullable=False),
        sa.Column("resulting_generation", sa.Integer, nullable=False),
        sa.Column("resulting_snapshot_id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.UniqueConstraint("transcript_id", "operation_id", name="uq_transcript_recovery_owner"),
        sa.UniqueConstraint("transcript_id", "resulting_generation",
                            name="uq_transcript_recovery_generation"),
        sa.CheckConstraint("expected_generation > 0 AND "
                           "resulting_generation = expected_generation + 1",
                           name="generation_valid"),
        sa.CheckConstraint("expected_snapshot_id <> resulting_snapshot_id", name="snapshot_valid"),
        sa.CheckConstraint("expected_sha256 ~ '^[0-9a-f]{64}$' AND "
                           "expected_size_bytes BETWEEN 0 AND 268435456", name="content_valid"),
        sa.CheckConstraint("left(original_source_path, 1) = '/' AND "
                           "left(replacement_path, 1) = '/'", name="paths_absolute"),
        sa.CheckConstraint("length(btrim(reason)) > 0 AND length(btrim(evidence)) > 0",
                           name="explanation_nonblank"),
    ]


def recovery_pointer_elements() -> list:
    return [
        sa.Column("recovery_operation_id", UUID),
        sa.ForeignKeyConstraint(["id", "recovery_operation_id"],
            ["transcript_recoveries.transcript_id", "transcript_recoveries.operation_id"],
            name="fk_transcripts_recovery_owner", deferrable=True, initially="DEFERRED"),
    ]


RECOVERY_FINDINGS = {
    "transcript_recovery_witness_invalid": """
        SELECT count(*) FROM transcripts t
        LEFT JOIN transcript_recoveries r ON r.operation_id = t.recovery_operation_id
        WHERE t.recovery_operation_id IS NOT NULL AND (
            r.operation_id IS NULL OR r.transcript_id <> t.id
            OR r.original_source_path <> t.source_path
            OR t.generation < r.resulting_generation
            OR EXISTS (SELECT 1 FROM transcript_recoveries newer
                       WHERE newer.transcript_id = t.id
                         AND newer.resulting_generation > r.resulting_generation)
            OR (t.copy_status = 'ready' AND
                (t.copy_sha256 <> r.expected_sha256 OR t.copy_size_bytes <> r.expected_size_bytes))
        )
    """,
}
