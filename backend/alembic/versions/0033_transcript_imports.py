"""Project-owned transcript imports and durable import receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from mnemonic_api.transcript_db_tables import IMPORT_PROVENANCE, import_elements

revision = "0033_transcript_imports"
down_revision = "0032_agent_transcripts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("import_project_id", UUID,
                  sa.ForeignKey("projects.id", ondelete="RESTRICT")))
    for column in ("work_item_id", "lease_generation_id", "session_id"):
        op.alter_column("transcripts", column, nullable=True)
    op.drop_constraint("ck_transcripts_kind_valid", "transcripts")
    op.create_check_constraint("kind_valid", "transcripts",
                               "kind IN ('primary','subagent','imported')")
    op.create_check_constraint("provenance_valid", "transcripts", IMPORT_PROVENANCE)
    op.create_unique_constraint("uq_transcripts_import_source", "transcripts",
                                ["import_project_id", "source_path"])
    op.create_table("transcript_imports", *import_elements())


def downgrade() -> None:
    op.execute("LOCK TABLE transcripts, transcript_imports IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM transcripts WHERE kind='imported') OR "
        "EXISTS(SELECT 1 FROM transcript_imports)"
    )):
        raise RuntimeError("Populated transcript imports cannot be safely downgraded")
    op.drop_table("transcript_imports")
    op.drop_constraint("uq_transcripts_import_source", "transcripts")
    op.drop_constraint("ck_transcripts_provenance_valid", "transcripts")
    op.drop_constraint("ck_transcripts_kind_valid", "transcripts")
    op.create_check_constraint("kind_valid", "transcripts", "kind IN ('primary','subagent')")
    for column in ("work_item_id", "lease_generation_id", "session_id"):
        op.alter_column("transcripts", column, nullable=False)
    op.drop_column("transcripts", "import_project_id")
