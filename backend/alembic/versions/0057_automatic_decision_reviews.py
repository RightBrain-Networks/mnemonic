"""Opt-in decision-change investigations using ordinary work items."""

import sqlalchemy as sa
from alembic import op

revision = "0057_automatic_decision_reviews"
down_revision = "0056_product_decisions"
branch_labels = None
depends_on = None


def _settings_guard(*, reverse: bool = False) -> None:
    definition = op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=current_schema() AND p.proname='mnemonic_guard_job_report_settings'"
    ))
    replacements = (
        ("changed:=ROW(NEW.priority_rubric,",
         "changed:=ROW(NEW.automatic_decision_reviews,NEW.decision_review_priority,"
         "NEW.priority_rubric,"),
        ("ROW(OLD.priority_rubric,",
         "ROW(OLD.automatic_decision_reviews,OLD.decision_review_priority,OLD.priority_rubric,"),
        ("AND NOT NEW.code_review_policy_touched",
         "AND NOT NEW.code_review_policy_touched AND NOT NEW.automatic_decision_reviews "
         "AND NEW.decision_review_priority=40"),
    )
    for old, new in replacements:
        source, target = (new, old) if reverse else (old, new)
        if not isinstance(definition, str) or definition.count(source) != 1:
            raise RuntimeError("Unexpected project settings guard definition")
        definition = definition.replace(source, target)
    op.execute(definition)


def _work_guard() -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_decision_review_work_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF TG_OP='UPDATE' AND
                NEW.decision_review_revision_id IS DISTINCT FROM OLD.decision_review_revision_id
            THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='decision review origin is immutable'; END IF;
            IF TG_OP='INSERT' AND NEW.decision_review_revision_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM {schema}.decision_revisions
                WHERE id=NEW.decision_review_revision_id AND project_id=NEW.project_id
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='decision review origin must belong to its project'; END IF;
            RETURN NEW;
        END $body$;
        CREATE TRIGGER decision_review_work_guard BEFORE INSERT OR UPDATE ON {schema}.work_items
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_decision_review_work_guard();
    """)


def upgrade() -> None:
    op.add_column("project_settings", sa.Column(
        "automatic_decision_reviews", sa.Boolean(), nullable=False, server_default="false",
    ))
    op.add_column("project_settings", sa.Column(
        "decision_review_priority", sa.SmallInteger(), nullable=False, server_default="40",
    ))
    op.create_check_constraint(
        "decision_review_priority_range", "project_settings",
        "decision_review_priority BETWEEN 0 AND 100",
    )
    _settings_guard()
    op.add_column("work_items", sa.Column("decision_review_revision_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_work_items_decision_review_revision_id_decision_revisions", "work_items",
        "decision_revisions", ["decision_review_revision_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_work_items_decision_review_revision_id", "work_items", ["decision_review_revision_id"],
    )
    _work_guard()


def downgrade() -> None:
    op.execute("LOCK TABLE project_settings, work_items IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM project_settings WHERE automatic_decision_reviews "
        "OR decision_review_priority <> 40) OR EXISTS(SELECT 1 FROM work_items "
        "WHERE decision_review_revision_id IS NOT NULL)"
    )):
        raise RuntimeError(
            "Automatic decision review settings and work cannot be safely downgraded"
        )
    op.execute("DROP TRIGGER decision_review_work_guard ON work_items")
    op.execute("DROP FUNCTION mnemonic_decision_review_work_guard()")
    op.drop_constraint("uq_work_items_decision_review_revision_id", "work_items")
    op.drop_constraint("fk_work_items_decision_review_revision_id_decision_revisions", "work_items")
    op.drop_column("work_items", "decision_review_revision_id")
    _settings_guard(reverse=True)
    op.drop_constraint("ck_project_settings_decision_review_priority_range", "project_settings")
    op.drop_column("project_settings", "decision_review_priority")
    op.drop_column("project_settings", "automatic_decision_reviews")
