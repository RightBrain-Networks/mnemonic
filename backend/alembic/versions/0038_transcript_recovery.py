"""Record explicit byte-pinned operator recovery without changing reported paths."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.transcript_recovery_db import recovery_elements, recovery_pointer_elements

revision = "0038_transcript_recovery"
down_revision = "0037_background_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("transcript_recoveries", *recovery_elements())
    op.add_column("transcripts", recovery_pointer_elements()[0])
    op.create_foreign_key("fk_transcripts_recovery_owner", "transcripts", "transcript_recoveries",
        ["id", "recovery_operation_id"], ["transcript_id", "operation_id"],
        deferrable=True, initially="DEFERRED")
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_guard_transcript_recovery_history() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'transcript recovery history is immutable';
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER transcript_recovery_history_immutable BEFORE UPDATE OR DELETE
        ON {schema}.transcript_recoveries FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_transcript_recovery_history()
    """)
    op.execute(f"""
        CREATE TRIGGER transcript_recovery_history_no_truncate BEFORE TRUNCATE
        ON {schema}.transcript_recoveries FOR EACH STATEMENT
        EXECUTE FUNCTION {schema}.mnemonic_guard_transcript_recovery_history()
    """)


def downgrade() -> None:
    op.execute("LOCK TABLE transcripts, transcript_recoveries IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM transcript_recoveries)")):
        raise RuntimeError("Transcript recovery history cannot be safely downgraded")
    op.drop_constraint("fk_transcripts_recovery_owner", "transcripts", type_="foreignkey")
    op.drop_column("transcripts", "recovery_operation_id")
    op.drop_table("transcript_recoveries")
    op.execute("DROP FUNCTION mnemonic_guard_transcript_recovery_history()")
