"""Pin reviewed conclusions for unchanged candidates in new supersession episodes."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.candidate_reuse_db import candidate_reuse_elements
from mnemonic_api.candidate_reuse_integrity import invalid_candidate_reuses

revision = "0066_candidate_reuse"
down_revision = "0065_knowledge_management"
branch_labels = None
depends_on = None
TABLE = "decision_candidate_reuses"


def upgrade() -> None:
    op.create_table(TABLE, *candidate_reuse_elements())
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    invalid = invalid_candidate_reuses(f"{schema}.")
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_candidate_reuse_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM {schema}.decision_impact_assessments episode
                WHERE episode.id=NEW.assessment_id AND episode.project_id=NEW.project_id
                    AND episode.xmin=pg_current_xact_id()::text::xid)
                OR EXISTS (SELECT 1 FROM ({invalid}) invalid
                    WHERE invalid.assessment_id=NEW.assessment_id
                        AND invalid.candidate_reference=NEW.candidate_reference)
            THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='invalid sealed candidate conclusion reuse'; END IF;
            RETURN NEW;
        END $body$;
        CREATE TRIGGER candidate_reuse_guard AFTER INSERT ON {schema}.{TABLE}
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_candidate_reuse_guard();
        CREATE TRIGGER candidate_reuse_immutable BEFORE UPDATE OR DELETE ON {schema}.{TABLE}
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        CREATE TRIGGER candidate_reuse_no_truncate BEFORE TRUNCATE ON {schema}.{TABLE}
            FOR EACH STATEMENT EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
    """)


def downgrade() -> None:
    op.execute(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {TABLE})")):
        raise RuntimeError("Candidate conclusion references cannot be safely downgraded.")
    op.drop_table(TABLE)
    op.execute("DROP FUNCTION mnemonic_candidate_reuse_guard()")
