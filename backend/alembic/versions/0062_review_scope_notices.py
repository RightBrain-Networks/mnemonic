"""Retain exact fresh attachment order for acknowledgement-relative notices."""

import sqlalchemy as sa
from alembic import op

revision = "0062_review_scope_notices"
down_revision = "0061_batch_dispositions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decision_change_assessments", sa.Column(
        "attachment_scope_version", sa.Integer(), nullable=True,
    ))
    op.create_check_constraint("attachment_scope_version", "decision_change_assessments",
                               "attachment_scope_version > 0")
    op.create_unique_constraint("uq_decision_change_attachment_scope_version",
                                "decision_change_assessments",
                                ["change_id", "attachment_scope_version"])
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_scope_attachment_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        DECLARE unknown_count bigint; first_known integer; last_known integer; total bigint;
        BEGIN
            PERFORM 1 FROM {schema}.decision_changes WHERE id=NEW.change_id FOR UPDATE;
            SELECT count(*) FILTER (WHERE attachment_scope_version IS NULL),
                   min(attachment_scope_version),max(attachment_scope_version),count(*)
            INTO unknown_count,first_known,last_known,total
            FROM {schema}.decision_change_assessments WHERE change_id=NEW.change_id;
            IF first_known IS NOT NULL AND (
                first_known <> unknown_count + 1 OR last_known <> total
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='known scope attachment versions must follow unknown historical bindings';
            END IF;
            RETURN NULL;
        END $body$;
        CREATE CONSTRAINT TRIGGER decision_scope_attachment_guard AFTER INSERT
            ON {schema}.decision_change_assessments DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_scope_attachment_guard();
    """)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM decision_change_assessments "
        "WHERE attachment_scope_version IS NOT NULL)"
    )):
        raise RuntimeError("Recorded scope attachment versions cannot be safely downgraded.")
    op.execute("DROP TRIGGER decision_scope_attachment_guard ON decision_change_assessments")
    op.execute("DROP FUNCTION mnemonic_scope_attachment_guard()")
    op.drop_constraint("uq_decision_change_attachment_scope_version",
                       "decision_change_assessments", type_="unique")
    op.drop_constraint(op.f("ck_decision_change_assessments_attachment_scope_version"),
                       "decision_change_assessments", type_="check")
    op.drop_column("decision_change_assessments", "attachment_scope_version")
