"""Allow shared content-addressed snapshots without changing enrollment provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0046_shared_transcript_copies"
down_revision = "0045_transcript_capacity"
branch_labels = None
depends_on = None

_FIRST = "substr(copy_sha256,1,8)||'-'||substr(copy_sha256,9,4)||'-'||" \
    "substr(copy_sha256,13,4)||'-'||substr(copy_sha256,17,4)||'-'||substr(copy_sha256,21,12)"
_SECOND = "substr(copy_sha256,33,8)||'-'||substr(copy_sha256,41,4)||'-'||" \
    "substr(copy_sha256,45,4)||'-'||substr(copy_sha256,49,4)||'-'||substr(copy_sha256,53,12)"
_LEGACY = "storage_key = id::text || '/' || snapshot_id::text || '/transcript.jsonl'"


def _replace(key_rule: str) -> None:
    name = op.f("ck_transcripts_copy_snapshot_valid")
    op.drop_constraint(name, "transcripts", type_="check")
    op.create_check_constraint(name, "transcripts",
        f"(copy_status = 'ready' AND storage_key IS NOT NULL AND ({key_rule}) "
        "AND copy_sha256 IS NOT NULL AND copy_sha256 ~ '^[0-9a-f]{64}$' "
        "AND copy_size_bytes IS NOT NULL AND copy_size_bytes BETWEEN 0 AND 1073741824 "
        "AND copied_at IS NOT NULL) OR (copy_status <> 'ready' AND storage_key IS NULL "
        "AND copy_sha256 IS NULL AND copy_size_bytes IS NULL AND copied_at IS NULL)")


def upgrade() -> None:
    _replace(f"{_LEGACY} OR storage_key = ({_FIRST})||'/'||({_SECOND})||'/snapshot.bin'")
    op.create_index("ix_transcripts_copy_content", "transcripts",
                    ["copy_sha256", "copy_size_bytes"],
                    postgresql_where=sa.text("copy_status = 'ready'"))
    op.create_index("ix_transcripts_storage_key", "transcripts", ["storage_key"],
                    postgresql_where=sa.text("storage_key IS NOT NULL"))


def downgrade() -> None:
    # Refuse rather than discard or silently expand already shared native data.
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM transcripts WHERE storage_key LIKE '%/snapshot.bin')"
    )):
        raise RuntimeError("transcript copies cannot be safely downgraded while shared objects "
                           "are referenced; restore a matching database and native-store backup")
    _replace(_LEGACY)
    op.drop_index("ix_transcripts_storage_key", table_name="transcripts")
    op.drop_index("ix_transcripts_copy_content", table_name="transcripts")
