"""Retain explicit unchanged-supersession declarations without rewriting prior episodes."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.decision_change_db import supersession_reuse_elements
from mnemonic_api.supersession_reuse_integrity import invalid_declarations

revision = "0060_supersession_reuse"
down_revision = "0059_domain_decision_reviews"
branch_labels = None
depends_on = None
TABLE = "decision_supersession_reuses"


def upgrade() -> None:
    op.create_table(TABLE, *supersession_reuse_elements())
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    invalid = invalid_declarations(f"{schema}.")
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_supersession_reuse_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM {schema}.decision_revisions source
                WHERE source.id=NEW.revision_id
                  AND source.creation_transaction_id=pg_current_xact_id()::text::bigint)
                OR EXISTS (SELECT 1 FROM ({invalid}) invalid
                           WHERE invalid.revision_id=NEW.revision_id
                             AND invalid.target_revision_id=NEW.target_revision_id)
                OR (SELECT count(*) FROM {schema}.{TABLE} reuse
                    JOIN {schema}.decision_revisions target ON target.id=reuse.target_revision_id
                    WHERE reuse.revision_id=NEW.revision_id AND target.document_id=(
                        SELECT document_id FROM {schema}.decision_revisions
                        WHERE id=NEW.target_revision_id))>1
            THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='invalid sealed supersession reuse declaration';
            END IF;
            RETURN NEW;
        END $body$;
        CREATE TRIGGER supersession_reuse_guard AFTER INSERT ON {schema}.{TABLE}
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_supersession_reuse_guard();
        CREATE TRIGGER supersession_reuse_immutable BEFORE UPDATE OR DELETE ON {schema}.{TABLE}
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        CREATE TRIGGER supersession_reuse_no_truncate BEFORE TRUNCATE ON {schema}.{TABLE}
            FOR EACH STATEMENT EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
    """)


def downgrade() -> None:
    op.execute(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {TABLE})")):
        raise RuntimeError("Supersession declarations cannot be safely downgraded.")
    op.drop_table(TABLE)
    op.execute("DROP FUNCTION mnemonic_supersession_reuse_guard()")
