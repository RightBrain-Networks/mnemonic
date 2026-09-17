"""Remember verified enrollment evidence across external transcript moves."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.transcript_source_db import SOURCE_CHECKS, source_columns

revision = "0043_transcript_source_identity"
down_revision = "0042_transcript_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in source_columns():
        op.add_column("transcripts", column)
    for name, expression in SOURCE_CHECKS.items():
        op.create_check_constraint(op.f(f"ck_transcripts_{name}"), "transcripts", expression)
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_guard_transcript_source_identity() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF OLD.kind <> 'imported' AND
                NEW.source_identity IS DISTINCT FROM OLD.source_identity THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'enrolled transcript source identity is immutable';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER transcript_source_identity_immutable BEFORE UPDATE
        ON {schema}.transcripts FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_transcript_source_identity()
    """)


    # Queue only idle captures; never clear a lease or disturb an in-flight job.
    # Normal reconciliation also checks Active generations and project pauses.
    op.execute("UPDATE transcripts SET generation = generation + 1, attempts = 0, "
        "reindex_status = 'pending', reindex_error_code = NULL, "
        "next_attempt_at = clock_timestamp() "
        "WHERE status = 'ready' AND copy_status = 'ready' AND truncated "
        "AND reindex_status IS NULL AND lease_token IS NULL "
        "AND copy_lease_token IS NULL AND NOT EXISTS (SELECT 1 FROM work_leases "
        "WHERE work_leases.work_item_id = transcripts.work_item_id "
        "AND work_leases.expires_at > clock_timestamp())")


def downgrade() -> None:
    op.execute("LOCK TABLE transcripts IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM transcripts "
                                    "WHERE source_identity IS NOT NULL)")):
        raise RuntimeError("Cannot discard enrolled transcript source evidence")
    op.execute("DROP TRIGGER transcript_source_identity_immutable ON transcripts")
    op.execute("DROP FUNCTION mnemonic_guard_transcript_source_identity()")
    for name in SOURCE_CHECKS:
        op.drop_constraint(op.f(f"ck_transcripts_{name}"), "transcripts", type_="check")
    for name in ("source_identity", "copy_source_path"):
        op.drop_column("transcripts", name)
