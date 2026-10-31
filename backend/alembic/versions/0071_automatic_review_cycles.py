"""Allow automatic successor review cycles without rewriting admission history."""

import sqlalchemy as sa
from alembic import op

revision = "0071_automatic_review_cycles"
down_revision = "0070_backup_settings"
branch_labels = None
depends_on = None


def _schema() -> str:
    bind = op.get_bind()
    return bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))


def _actor_constraint(*, automatic: bool) -> None:
    actors = "IN ('dashboard', 'mnemonic')" if automatic else "= 'dashboard'"
    op.drop_constraint(op.f("ck_decision_changes_human_admission"), "decision_changes")
    op.create_check_constraint(
        "human_admission", "decision_changes",
        "(parent_change_id IS NULL AND admission_actor_client IS NULL "
        "AND admission_actor_session_id IS NULL AND admission_actor_model IS NULL) OR "
        f"(parent_change_id IS NOT NULL AND admission_actor_client {actors} "
        "AND admission_actor_session_id IS NOT NULL "
        "AND length(btrim(admission_actor_session_id)) > 0 "
        "AND (admission_actor_model IS NULL OR length(btrim(admission_actor_model)) > 0))",
    )


def _successors(schema: str) -> str:
    return f"""WITH RECURSIVE successors(id) AS (
        SELECT successor.id FROM {schema}.decision_changes successor
        JOIN {schema}.decision_revision_changes classification
            ON successor.parent_change_id=classification.change_id
        WHERE classification.revision_id=NEW.replacement_revision_id
          AND classification.requires_admission
        UNION
        SELECT successor.id FROM {schema}.decision_changes successor
        JOIN successors parent ON successor.parent_change_id=parent.id
    )"""


def _assessment_guard(schema: str, *, reverse: bool = False) -> None:
    definition = op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=current_schema() AND p.proname='mnemonic_impact_assessment_guard'"
    ))
    original = f"""IF replacement_tx IS DISTINCT FROM pg_current_xact_id()::text::bigint
                AND NOT EXISTS (
                    SELECT 1 FROM {schema}.decision_revision_changes classification
                    JOIN {schema}.decision_changes successor
                        ON successor.parent_change_id=classification.change_id
                    WHERE classification.revision_id=NEW.replacement_revision_id
                      AND classification.requires_admission
                      AND successor.admission_actor_client='dashboard'
                ) THEN"""
    amended = f"""IF replacement_tx IS DISTINCT FROM pg_current_xact_id()::text::bigint
                AND NOT EXISTS ({_successors(schema)} SELECT 1 FROM successors) THEN"""
    before, after = (amended, original) if reverse else (original, amended)
    if not isinstance(definition, str) or definition.count(before) != 1:
        raise RuntimeError("Unexpected assessment guard definition")
    op.execute(definition.replace(before, after))


def _held_guard(schema: str, *, reverse: bool = False) -> None:
    if reverse:
        # Restore 0058's exact retained body, including whitespace and diagnostic:
        # historical catalog audits pin pg_get_functiondef output after downgrade.
        op.execute(f"""
        CREATE OR REPLACE FUNCTION {schema}.mnemonic_decision_held_assessment_guard()
        RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF EXISTS (SELECT 1 FROM {schema}.decision_revision_changes classification
                       WHERE classification.revision_id=NEW.replacement_revision_id
                         AND classification.requires_admission) AND NOT EXISTS (
                SELECT 1 FROM {schema}.decision_revision_changes classification
                JOIN {schema}.decision_changes successor
                    ON successor.parent_change_id=classification.change_id
                JOIN {schema}.decision_change_assessments membership
                    ON membership.change_id=successor.id AND membership.assessment_id=NEW.id
                WHERE classification.revision_id=NEW.replacement_revision_id
                  AND successor.admission_actor_client='dashboard'
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='held decision assessment requires its admitted successor membership';
            END IF;
            RETURN NULL;
        END $body$;
        """)
        return
    membership = f"""{_successors(schema)} SELECT 1 FROM successors
        JOIN {schema}.decision_change_assessments membership
            ON membership.change_id=successors.id AND membership.assessment_id=NEW.id"""
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {schema}.mnemonic_decision_held_assessment_guard()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF EXISTS (SELECT 1 FROM {schema}.decision_revision_changes classification
                       WHERE classification.revision_id=NEW.replacement_revision_id
                         AND classification.requires_admission) AND NOT EXISTS (
                {membership}
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='held decision assessment requires its successor membership';
            END IF;
            RETURN NULL;
        END $body$;
    """)


def upgrade() -> None:
    op.execute("LOCK TABLE decision_changes,decision_impact_assessments IN ACCESS EXCLUSIVE MODE")
    _actor_constraint(automatic=True)
    op.drop_index("ix_decision_changes_parent", table_name="decision_changes")
    op.create_index("ix_decision_changes_parent", "decision_changes", ["parent_change_id"],
                    unique=True)
    schema = _schema()
    _assessment_guard(schema)
    _held_guard(schema)


def downgrade() -> None:
    op.execute("LOCK TABLE decision_changes,decision_impact_assessments IN ACCESS EXCLUSIVE MODE")
    unsupported = op.get_bind().scalar(sa.text("""
        SELECT EXISTS(SELECT 1 FROM decision_changes WHERE admission_actor_client='mnemonic')
        OR EXISTS (
            SELECT 1 FROM decision_impact_assessments assessment
            JOIN decision_revision_changes classification
                ON classification.revision_id=assessment.replacement_revision_id
            WHERE classification.requires_admission AND NOT EXISTS (
                SELECT 1 FROM decision_changes successor
                JOIN decision_change_assessments membership ON membership.change_id=successor.id
                WHERE successor.parent_change_id=classification.change_id
                  AND successor.admission_actor_client='dashboard'
                  AND membership.assessment_id=assessment.id
            )
        )
    """))
    if unsupported:
        raise RuntimeError("Automatic review cycles cannot be safely downgraded")
    schema = _schema()
    _assessment_guard(schema, reverse=True)
    _held_guard(schema, reverse=True)
    op.drop_index("ix_decision_changes_parent", table_name="decision_changes")
    op.create_index("ix_decision_changes_parent", "decision_changes", ["parent_change_id"])
    _actor_constraint(automatic=False)
