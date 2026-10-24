"""Bound automatic impact reviews to explicit substantive change identities."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api import decision_change_db as definitions

revision = "0058_decision_changes"
down_revision = "0057_automatic_decision_reviews"
branch_labels = None
depends_on = None

TABLES = (
    ("decision_changes", definitions.change_elements),
    ("decision_revision_changes", definitions.revision_elements),
    ("decision_change_assessments", definitions.assessment_elements),
    ("decision_change_work", definitions.work_elements),
)
PREVIOUS_KINDS = (
    "create_work", "add_checkpoint", "append_event", "add_relationship", "update_work",
    "defer_work", "complete_work", "delete_work", "move_work", "remove_relationship",
    "release_claim", "request_human_input", "resolve_human_input", "merge_work",
    "dismiss_job_completion_report", "create_job_completion_report_follow_up",
    "respond_to_work_follow_up", "complete_code_review", "create_knowledge_domain",
    "rename_knowledge_domain", "put_decision", "append_decision_scope", "assess_decision_impact",
    "reconcile_decision_scope",
)


def _operation_kinds(kinds: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_client_operations_operation_kind_valid"), "client_operations")
    op.create_check_constraint(
        "operation_kind_valid", "client_operations",
        "operation_kind IN (" + ",".join(f"'{kind}'" for kind in kinds) + ")",
    )


def _schema() -> str:
    bind = op.get_bind()
    return bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))


def _backfill() -> None:
    # Preserve each historical driver, including moved work and retired aliases.
    # Classification was not recorded then: do not invent it retrospectively.
    op.execute("""
        INSERT INTO decision_changes (
            id, project_id, origin_revision_id, review_work_item_id,
            scope_version, acknowledged_scope_version, created_at, updated_at
        ) SELECT r.id, r.project_id, r.id, w.id, episodes.amount,
            CASE WHEN w.status IN ('done','wont-do','promoted') OR w.deleted_at IS NOT NULL
                      OR EXISTS (SELECT 1 FROM work_duplicate_merges merge
                                 WHERE merge.source_work_item_id=w.id)
                 THEN episodes.amount ELSE 0 END, w.created_at, w.created_at
        FROM work_items w JOIN decision_revisions r ON r.id=w.decision_review_revision_id
        CROSS JOIN LATERAL (
            SELECT count(*)::integer amount FROM decision_impact_assessments a
            WHERE a.replacement_revision_id=r.id
        ) episodes;
        INSERT INTO decision_change_assessments (change_id, assessment_id, created_at)
            SELECT c.id, a.id, c.created_at FROM decision_changes c
            JOIN decision_impact_assessments a ON a.replacement_revision_id=c.origin_revision_id;
        WITH RECURSIVE lineage(work_item_id,change_id) AS (
            SELECT review_work_item_id,id FROM decision_changes
            UNION
            SELECT edge.source_work_item_id,lineage.change_id FROM lineage
            JOIN work_relationships edge ON edge.target_work_item_id=lineage.work_item_id
            WHERE edge.relationship_type='discovered-from'
        ) INSERT INTO decision_change_work (work_item_id,change_id)
            SELECT work_item_id,change_id FROM lineage;
    """)


def _identity_guard(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_decision_change_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF TG_OP='UPDATE' AND (
                ROW(NEW.id,NEW.project_id,NEW.parent_change_id,NEW.admission_actor_client,
                    NEW.admission_actor_session_id,NEW.admission_actor_model,NEW.created_at)
                IS DISTINCT FROM
                ROW(OLD.id,OLD.project_id,OLD.parent_change_id,OLD.admission_actor_client,
                    OLD.admission_actor_session_id,OLD.admission_actor_model,OLD.created_at)
                OR (OLD.origin_revision_id IS NOT NULL AND
                    NEW.origin_revision_id IS DISTINCT FROM OLD.origin_revision_id)
                OR (OLD.review_work_item_id IS NOT NULL AND
                    NEW.review_work_item_id IS DISTINCT FROM OLD.review_work_item_id)
                OR NEW.scope_version < OLD.scope_version
                OR NEW.acknowledged_scope_version < OLD.acknowledged_scope_version
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='decision change identity and acknowledged scope cannot be rewritten';
            END IF;
            IF NEW.origin_revision_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM {schema}.decision_revisions r
                WHERE r.id=NEW.origin_revision_id AND r.project_id=NEW.project_id
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='decision change origin must belong to its source project'; END IF;
            IF NEW.parent_change_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM {schema}.decision_changes c
                WHERE c.id=NEW.parent_change_id AND c.project_id=NEW.project_id
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='decision change parent must belong to its source project'; END IF;
            IF NEW.review_work_item_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM {schema}.work_items w
                LEFT JOIN {schema}.decision_revisions r ON r.id=w.decision_review_revision_id
                WHERE w.id=NEW.review_work_item_id
                AND (w.decision_review_revision_id IS NULL
                     OR r.project_id=NEW.project_id)
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='decision change driver must retain its source project'; END IF;
            RETURN NEW;
        END $body$;
        CREATE TRIGGER decision_change_guard BEFORE INSERT OR UPDATE ON {schema}.decision_changes
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_decision_change_guard();
        CREATE TRIGGER decision_change_no_delete BEFORE DELETE ON {schema}.decision_changes
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        CREATE TRIGGER decision_change_no_truncate BEFORE TRUNCATE ON {schema}.decision_changes
            FOR EACH STATEMENT EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
    """)


def _membership_guards(schema: str) -> None:
    for table in (name for name, _ in TABLES if name != "decision_changes"):
        op.execute(f"""
            CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {schema}.{table}
                FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
            CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {schema}.{table}
                FOR EACH STATEMENT EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        """)
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_decision_change_membership_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        DECLARE source_project uuid;
        BEGIN
            IF TG_TABLE_NAME='decision_revision_changes' THEN
                SELECT project_id INTO source_project FROM {schema}.decision_revisions
                    WHERE id=NEW.revision_id;
                IF NEW.governing_revision_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM {schema}.decision_revisions r
                    WHERE r.id=NEW.governing_revision_id AND r.project_id=source_project
                ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='classification governance must belong to its source project'; END IF;
            ELSE
                SELECT project_id INTO source_project FROM {schema}.decision_impact_assessments
                    WHERE id=NEW.assessment_id;
            END IF;
            IF NEW.change_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM {schema}.decision_changes c
                WHERE c.id=NEW.change_id AND c.project_id=source_project
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='decision change membership must belong to its source project'; END IF;
            RETURN NEW;
        END $body$;
        CREATE TRIGGER decision_revision_change_guard BEFORE INSERT ON
            {schema}.decision_revision_changes FOR EACH ROW
            EXECUTE FUNCTION {schema}.mnemonic_decision_change_membership_guard();
        CREATE TRIGGER decision_change_assessment_guard BEFORE INSERT ON
            {schema}.decision_change_assessments FOR EACH ROW
            EXECUTE FUNCTION {schema}.mnemonic_decision_change_membership_guard();
    """)


def _scope_guard(schema: str) -> None:
    # Application code increments scope while adding membership. Deferral allows
    # either flush order, while preventing a lost increment from being committed.
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_decision_change_scope_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        DECLARE target uuid; current_scope integer; driver uuid;
        BEGIN
            IF TG_TABLE_NAME='decision_changes' THEN target:=NEW.id;
            ELSE target:=NEW.change_id; END IF;
            SELECT scope_version,review_work_item_id INTO current_scope,driver
                FROM {schema}.decision_changes WHERE id=target FOR UPDATE;
            IF current_scope <> (SELECT count(*) FROM {schema}.decision_change_assessments
                                 WHERE change_id=target)
            THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='decision change scope must count its immutable assessment memberships';
            END IF;
            IF driver IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM {schema}.decision_change_work
                WHERE change_id=target AND work_item_id=driver
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='decision change driver must retain its lineage membership'; END IF;
            RETURN NULL;
        END $body$;
        CREATE CONSTRAINT TRIGGER decision_change_scope_guard AFTER INSERT OR UPDATE
            ON {schema}.decision_changes DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_decision_change_scope_guard();
        CREATE CONSTRAINT TRIGGER decision_change_assessment_scope_guard AFTER INSERT
            ON {schema}.decision_change_assessments DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_decision_change_scope_guard();
    """)


def _held_assessment_guard(schema: str, *, reverse: bool = False) -> None:
    definition = op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=current_schema() AND p.proname='mnemonic_impact_assessment_guard'"
    ))
    original = "IF replacement_tx IS DISTINCT FROM pg_current_xact_id()::text::bigint THEN"
    amended = f"""IF replacement_tx IS DISTINCT FROM pg_current_xact_id()::text::bigint
                AND NOT EXISTS (
                    SELECT 1 FROM {schema}.decision_revision_changes classification
                    JOIN {schema}.decision_changes successor
                        ON successor.parent_change_id=classification.change_id
                    WHERE classification.revision_id=NEW.replacement_revision_id
                      AND classification.requires_admission
                      AND successor.admission_actor_client='dashboard'
                ) THEN"""
    before, after = (amended, original) if reverse else (original, amended)
    if not isinstance(definition, str) or definition.count(before) != 1:
        raise RuntimeError("Unexpected assessment guard definition")
    op.execute(definition.replace(before, after))
    if reverse:
        return
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_decision_held_assessment_guard() RETURNS trigger
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
        CREATE CONSTRAINT TRIGGER decision_held_assessment_guard AFTER INSERT
            ON {schema}.decision_impact_assessments DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_decision_held_assessment_guard();
    """)


def upgrade() -> None:
    _operation_kinds((*PREVIOUS_KINDS, "update_decision_change"))
    for name, elements in TABLES:
        op.create_table(name, *elements())
    _backfill()
    schema = _schema()
    _identity_guard(schema)
    _membership_guards(schema)
    _scope_guard(schema)
    _held_assessment_guard(schema)


def downgrade() -> None:
    names = ",".join(["client_operations", "decision_impact_assessments",
                      *(name for name, _ in TABLES)])
    op.execute(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE")
    has_receipts = op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM client_operations "
        "WHERE operation_kind='update_decision_change')"
    ))
    if has_receipts or any(op.get_bind().scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {name})"))
                           for name, _ in TABLES):
        raise RuntimeError(
            "Decision change identities and review scope cannot be safely downgraded"
        )
    _held_assessment_guard(_schema(), reverse=True)
    op.execute("DROP TRIGGER decision_held_assessment_guard ON decision_impact_assessments")
    op.execute("DROP FUNCTION mnemonic_decision_held_assessment_guard()")
    for name, _ in reversed(TABLES):
        op.drop_table(name)
    for name in ("scope", "membership", ""):
        suffix = f"{name}_" if name else ""
        op.execute(f"DROP FUNCTION mnemonic_decision_change_{suffix}guard()")
    _operation_kinds(PREVIOUS_KINDS)
