"""Capture decision use during closeout and retain merged commit reconciliation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from mnemonic_api.provenance_db import mapping_elements

revision = "0055_decision_provenance"
down_revision = "0054_knowledge_impact"
branch_labels = None
depends_on = None

_KINDS = (
    "create_work", "add_checkpoint", "append_event", "add_relationship", "update_work",
    "defer_work", "complete_work", "delete_work", "move_work", "remove_relationship",
    "release_claim", "request_human_input", "resolve_human_input", "merge_work",
    "dismiss_job_completion_report", "create_job_completion_report_follow_up",
    "respond_to_work_follow_up", "complete_code_review", "create_knowledge_domain",
    "rename_knowledge_domain", "put_decision", "append_decision_scope", "assess_decision_impact",
)


def _kinds(kinds: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_client_operations_operation_kind_valid"), "client_operations")
    op.create_check_constraint("operation_kind_valid", "client_operations",
                              "operation_kind IN (" + ",".join(f"'{k}'" for k in kinds) + ")")


def _scope_unique(*, enriched: bool) -> None:
    op.drop_constraint("uq_decision_scopes_provenance", "decision_scopes")
    columns = ["document_id", "revision", "commit_hash", "work_item_id", "agent_session_id"]
    if enriched:
        columns += ["repository", "completion_report_id"]
    op.create_unique_constraint("uq_decision_scopes_provenance", "decision_scopes", columns,
                                postgresql_nulls_not_distinct=True)


def _guards(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_provenance_scope_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        DECLARE report {schema}.job_completion_reports; identifier text;
        BEGIN
            IF NEW.completion_report_id IS NULL THEN RETURN NEW; END IF;
            SELECT * INTO report FROM {schema}.job_completion_reports
                WHERE project_id=NEW.project_id AND id=NEW.completion_report_id;
            SELECT kd.prefix || '-' || lpad(dd.ordinal::text,
                greatest(5,length(dd.ordinal::text)), '0') INTO identifier
                FROM {schema}.decision_documents dd
                JOIN {schema}.knowledge_domains kd ON kd.id=dd.domain_id
                WHERE dd.id=NEW.document_id;
            IF report.id IS NULL OR report.work_item_id IS DISTINCT FROM NEW.work_item_id
               OR report.actor_session_id IS DISTINCT FROM NEW.agent_session_id
               OR report.decision_provenance->>'disposition' IS DISTINCT FROM 'cited'
               OR NOT coalesce(report.decision_provenance->'citations' @>
                    jsonb_build_array(jsonb_build_object('decision_id', identifier,
                                                        'revision', NEW.revision)), false)
               OR NOT coalesce(report.decision_provenance->'commits' @>
                    jsonb_build_array(jsonb_build_object('repository', NEW.repository,
                                                     'commit_hash', NEW.commit_hash)), false) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid completion citation';
            END IF;
            RETURN NEW;
        END $body$;
        CREATE TRIGGER provenance_scope_guard BEFORE INSERT ON {schema}.decision_scopes
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_provenance_scope_guard();

        CREATE FUNCTION {schema}.mnemonic_commit_mapping_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM {schema}.decision_scopes s
                WHERE s.id=NEW.scope_id AND s.project_id=NEW.project_id
                  AND s.repository=NEW.repository AND s.commit_hash=NEW.original_commit_hash)
               OR NEW.original_commit_hash=NEW.merged_commit_hash THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid commit mapping';
            END IF;
            RETURN NEW;
        END $body$;
        CREATE TRIGGER commit_mapping_guard BEFORE INSERT ON {schema}.decision_commit_mappings
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_commit_mapping_guard();
        CREATE TRIGGER knowledge_immutable BEFORE UPDATE OR DELETE
            ON {schema}.decision_commit_mappings
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        CREATE TRIGGER knowledge_no_truncate BEFORE TRUNCATE ON {schema}.decision_commit_mappings
            FOR EACH STATEMENT EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
    """)


def upgrade() -> None:
    op.add_column("job_completion_reports", sa.Column("decision_provenance", JSONB))
    op.add_column("decision_scopes", sa.Column("repository", sa.String(500)))
    op.add_column("decision_scopes", sa.Column("completion_report_id", sa.UUID))
    op.create_foreign_key("fk_decision_scopes_completion_report", "decision_scopes",
                         "job_completion_reports", ["project_id", "completion_report_id"],
                         ["project_id", "id"], ondelete="RESTRICT")
    _scope_unique(enriched=True)
    op.create_table("decision_commit_mappings", *mapping_elements())
    schema = op.get_bind().scalar(sa.text("SELECT current_schema()"))
    quoted = op.get_bind().dialect.identifier_preparer.quote_schema(schema)
    _guards(quoted)
    _kinds((*_KINDS, "reconcile_decision_scope"))


def downgrade() -> None:
    op.execute("LOCK TABLE decision_scopes, decision_commit_mappings, job_completion_reports, "
               "client_operations IN ACCESS EXCLUSIVE MODE")
    populated = op.get_bind().scalar(sa.text("""
        SELECT EXISTS(SELECT 1 FROM decision_scopes
                      WHERE repository IS NOT NULL OR completion_report_id IS NOT NULL)
            OR EXISTS(SELECT 1 FROM decision_commit_mappings)
            OR EXISTS(SELECT 1 FROM job_completion_reports WHERE decision_provenance IS NOT NULL)
            OR EXISTS(SELECT 1 FROM client_operations
                      WHERE operation_kind='reconcile_decision_scope')
    """))
    if populated:
        raise RuntimeError("Decision provenance exists; restore a pre-upgrade backup")
    op.drop_table("decision_commit_mappings")
    op.execute("DROP TRIGGER provenance_scope_guard ON decision_scopes")
    op.execute("DROP FUNCTION mnemonic_provenance_scope_guard()")
    op.execute("DROP FUNCTION mnemonic_commit_mapping_guard()")
    _scope_unique(enriched=False)
    op.drop_constraint("fk_decision_scopes_completion_report", "decision_scopes")
    op.drop_column("decision_scopes", "completion_report_id")
    op.drop_column("decision_scopes", "repository")
    op.drop_column("job_completion_reports", "decision_provenance")
    _kinds(_KINDS)
