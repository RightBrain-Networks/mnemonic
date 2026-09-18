"""Separate complete conversation text and streamed capture from artifact limits."""

from alembic import op

revision = "0045_transcript_capacity"
down_revision = "0044_transcript_metadata"
branch_labels = None
depends_on = None


def _replace(table: str, name: str, expression: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, expression)


def _limits(maximum: int, text_rule: str) -> None:
    _replace("transcripts", "size_valid",
             f"size_bytes IS NULL OR size_bytes BETWEEN 0 AND {maximum}")
    _replace("transcripts", "copy_snapshot_valid",
             "(copy_status = 'ready' AND storage_key IS NOT NULL "
             "AND storage_key = id::text || '/' || snapshot_id::text || '/transcript.jsonl' "
             "AND copy_sha256 IS NOT NULL AND copy_sha256 ~ '^[0-9a-f]{64}$' "
             f"AND copy_size_bytes IS NOT NULL AND copy_size_bytes BETWEEN 0 AND {maximum} "
             "AND copied_at IS NOT NULL) OR (copy_status <> 'ready' AND storage_key IS NULL "
             "AND copy_sha256 IS NULL AND copy_size_bytes IS NULL AND copied_at IS NULL)")
    _replace("transcripts", "text_valid",
             f"normalized_text IS NULL OR (status = 'ready' AND {text_rule})")
    _replace("transcript_settings", "size_valid",
             f"max_file_size_bytes BETWEEN 1 AND {maximum}")
    _replace("transcript_recoveries", "content_valid",
             "expected_sha256 ~ '^[0-9a-f]{64}$' AND "
             f"expected_size_bytes BETWEEN 0 AND {maximum}")


def upgrade() -> None:
    _limits(1_073_741_824, "octet_length(normalized_text) < 1073741824")
    op.alter_column("transcript_settings", "max_file_size_bytes", server_default="536870912")
    # Explicit project limits remain operator choices. The worker rechecks old
    # size failures and refreshes truncated text with normal pause/lease guards.


def downgrade() -> None:
    # PostgreSQL refuses this downgrade if it would invalidate retained data.
    _limits(268_435_456, "char_length(normalized_text) <= 8000000")
    op.alter_column("transcript_settings", "max_file_size_bytes", server_default="67108864")
