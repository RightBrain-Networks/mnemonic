"""Pinned dependency evidence and immutable impact reassessment episodes."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api import impact_db

revision = "0054_knowledge_impact"
down_revision = "0053_knowledge_graph"
branch_labels = None
depends_on = None

TABLES = (
    "decision_dependencies",
    "decision_supersession_details",
    "decision_impact_assessments",
    "decision_impact_dispositions",
)
PREVIOUS_KINDS = (
    "create_work",
    "add_checkpoint",
    "append_event",
    "add_relationship",
    "update_work",
    "defer_work",
    "complete_work",
    "delete_work",
    "move_work",
    "remove_relationship",
    "release_claim",
    "request_human_input",
    "resolve_human_input",
    "merge_work",
    "dismiss_job_completion_report",
    "create_job_completion_report_follow_up",
    "respond_to_work_follow_up",
    "complete_code_review",
    "create_knowledge_domain",
    "rename_knowledge_domain",
    "put_decision",
    "append_decision_scope",
)


def operation_kinds(kinds: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_client_operations_operation_kind_valid"), "client_operations")
    op.create_check_constraint(
        "operation_kind_valid",
        "client_operations",
        "operation_kind IN (" + ",".join(f"'{kind}'" for kind in kinds) + ")",
    )


def _edge_guard(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_impact_edge_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $function$
        DECLARE source_tx bigint; source_doc uuid; source_domain uuid;
                target_doc uuid; target_domain uuid;
        BEGIN
            PERFORM 1 FROM {schema}.projects WHERE id=NEW.project_id FOR UPDATE;
            SELECT r.creation_transaction_id,r.document_id,d.domain_id
                INTO source_tx,source_doc,source_domain
                FROM {schema}.decision_revisions r
                JOIN {schema}.decision_documents d ON d.id=r.document_id
                WHERE r.id=NEW.revision_id AND r.project_id=NEW.project_id;
            SELECT r.document_id,d.domain_id INTO target_doc,target_domain
                FROM {schema}.decision_revisions r
                JOIN {schema}.decision_documents d ON d.id=r.document_id
                WHERE r.id=NEW.target_revision_id AND r.project_id=NEW.project_id;
            IF source_tx IS DISTINCT FROM pg_current_xact_id()::text::bigint
                OR source_doc=target_doc THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid sealed decision edge';
            END IF;
            IF TG_TABLE_NAME='decision_supersession_details' AND (
                source_domain IS DISTINCT FROM target_domain OR NOT EXISTS (
                    SELECT 1 FROM {schema}.decision_relationships edge
                    WHERE edge.revision_id=NEW.revision_id
                    AND edge.target_document_id=target_doc AND edge.kind='supersedes')) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid detailed supersession';
            END IF;
            RETURN NEW;
        END $function$;
    """)
    for table in TABLES[:2]:
        op.execute(f"""
            CREATE TRIGGER impact_edge_guard BEFORE INSERT ON {schema}.{table}
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_impact_edge_guard();
        """)


def _assessment_guard(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_impact_assessment_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $function$
        DECLARE source_doc uuid; replacement_doc uuid; old_number integer;
                new_number integer; replacement_tx bigint;
        BEGIN
            SELECT document_id,revision INTO source_doc,old_number
                FROM {schema}.decision_revisions WHERE id=NEW.origin_revision_id;
            SELECT document_id,revision,creation_transaction_id
                INTO replacement_doc,new_number,replacement_tx
                FROM {schema}.decision_revisions WHERE id=NEW.replacement_revision_id;
            IF replacement_tx IS DISTINCT FROM pg_current_xact_id()::text::bigint THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='impact episode must accompany revision';
            END IF;
            IF NEW.trigger='revision' AND
                (source_doc IS DISTINCT FROM replacement_doc OR new_number<>old_number+1) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid revision episode';
            END IF;
            IF NEW.trigger='supersession' AND NOT EXISTS (
                SELECT 1 FROM {schema}.decision_relationships edge
                WHERE edge.revision_id=NEW.replacement_revision_id
                  AND edge.target_document_id=source_doc AND edge.kind='supersedes') THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid supersession episode';
            END IF;
            RETURN NEW;
        END $function$;
        CREATE TRIGGER impact_assessment_guard BEFORE INSERT ON {schema}.decision_impact_assessments
        FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_impact_assessment_guard();

        CREATE FUNCTION {schema}.mnemonic_impact_disposition_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $function$
        DECLARE last_version integer;
        BEGIN
            PERFORM 1 FROM {schema}.projects WHERE id=NEW.project_id FOR UPDATE;
            PERFORM 1 FROM {schema}.decision_impact_assessments
                WHERE id=NEW.assessment_id AND project_id=NEW.project_id FOR UPDATE;
            SELECT coalesce(max(version),0) INTO last_version
                FROM {schema}.decision_impact_dispositions WHERE assessment_id=NEW.assessment_id;
            IF NEW.version<>last_version+1 OR NOT EXISTS (
                SELECT 1 FROM {schema}.decision_impact_assessments a,
                    jsonb_array_elements(a.candidates) item
                WHERE a.id=NEW.assessment_id AND item->>'reference'=NEW.candidate_reference) THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='invalid impact disposition version or candidate';
            END IF;
            RETURN NEW;
        END $function$;
        CREATE TRIGGER impact_disposition_guard BEFORE INSERT
        ON {schema}.decision_impact_dispositions
        FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_impact_disposition_guard();
    """)


def upgrade() -> None:
    operation_kinds((*PREVIOUS_KINDS, "assess_decision_impact"))
    op.create_table(TABLES[0], *impact_db.edge_elements())
    op.create_table(TABLES[1], *impact_db.edge_elements(supersession=True))
    op.create_table(TABLES[2], *impact_db.assessment_elements())
    op.create_table(TABLES[3], *impact_db.disposition_elements())
    schema = op.get_bind().scalar(sa.text("SELECT current_schema()"))
    quoted = op.get_bind().dialect.identifier_preparer.quote(schema)
    for table in TABLES:
        op.execute(f"""
            CREATE TRIGGER knowledge_immutable BEFORE UPDATE OR DELETE ON {quoted}.{table}
            FOR EACH ROW EXECUTE FUNCTION {quoted}.mnemonic_knowledge_immutable();
            CREATE TRIGGER knowledge_no_truncate BEFORE TRUNCATE ON {quoted}.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION {quoted}.mnemonic_knowledge_immutable();
        """)
    _edge_guard(quoted)
    _assessment_guard(quoted)


def downgrade() -> None:
    op.execute(
        "LOCK TABLE " + ",".join((*TABLES, "client_operations")) + " IN ACCESS EXCLUSIVE MODE"
    )
    if any(
        op.get_bind().scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {table})")) for table in TABLES
    ):
        raise RuntimeError("Impact history exists; restore a pre-upgrade backup")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM client_operations "
            "WHERE operation_kind='assess_decision_impact')"
        )
    ):
        raise RuntimeError("Impact receipts exist; restore a pre-upgrade backup")
    for table in reversed(TABLES):
        op.drop_table(table)
    for name in ("edge_guard", "assessment_guard", "disposition_guard"):
        op.execute(f"DROP FUNCTION mnemonic_impact_{name}()")
    operation_kinds(PREVIOUS_KINDS)
