"""Domain-local review policy and durable dashboard deletion tombstones."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.knowledge_deletion_db import deleted_edge_elements

revision = "0065_knowledge_management"
down_revision = "0064_work_change_inheritance"
branch_labels = None
depends_on = None

_EDGE_TRIGGERS = {
    "decision_relationships": "knowledge_immutable",
    "decision_dependencies": "knowledge_immutable",
    "decision_supersession_details": "knowledge_immutable",
    "decision_supersession_reuses": "supersession_reuse_immutable",
    "decision_work_item_links": "knowledge_immutable",
    "decision_artifact_links": "knowledge_immutable",
    "decision_scopes": "knowledge_immutable",
    "decision_commit_mappings": "knowledge_immutable",
}
_KINDS = (
    "create_work", "add_checkpoint", "append_event", "add_relationship", "update_work",
    "defer_work", "complete_work", "delete_work", "move_work", "remove_relationship",
    "release_claim", "request_human_input", "resolve_human_input", "merge_work",
    "dismiss_job_completion_report", "create_job_completion_report_follow_up",
    "respond_to_work_follow_up", "complete_code_review", "create_knowledge_domain",
    "rename_knowledge_domain", "put_decision", "append_decision_scope", "assess_decision_impact",
    "reconcile_decision_scope", "update_decision_change", "assess_decision_impact_batch",
)


def _schema() -> str:
    bind = op.get_bind()
    return bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))


def _operation_kinds(*, management: bool) -> None:
    kinds = (*_KINDS, "delete_knowledge_domain", "delete_decision") if management else _KINDS
    op.drop_constraint(op.f("ck_client_operations_operation_kind_valid"), "client_operations")
    op.create_check_constraint(
        "operation_kind_valid", "client_operations",
        "operation_kind IN (" + ",".join(f"'{kind}'" for kind in kinds) + ")",
    )


def _identity_guards(*, management: bool) -> None:
    schema = _schema()
    fields = "name,automatic_decision_reviews"
    if management:
        fields += ",decision_review_priority,deleted_at"
    old = "ROW(" + ",".join(f"OLD.{field}" for field in fields.split(",")) + ")"
    new = old.replace("OLD.", "NEW.")
    pending_receipt = f"""EXISTS (SELECT 1 FROM {schema}.client_operations receipt
        WHERE receipt.project_id=NEW.project_id AND receipt.state='pending'
          AND receipt.operation_kind IN ('delete_knowledge_domain','delete_decision')
          AND receipt.xmin=pg_current_xact_id()::text::xid)"""
    domain_deleted = (f"OR OLD.deleted_at IS NOT NULL OR (NEW.deleted_at IS NOT NULL "
                      f"AND NOT ({pending_receipt}))") if management else ""
    document_revision = (
        "OR OLD.deleted_at IS NOT NULL OR NOT ((NEW.deleted_at IS NULL AND "
        "NEW.current_revision=OLD.current_revision+1) OR (NEW.deleted_at IS NOT NULL "
        "AND NEW.current_revision=OLD.current_revision)) "
        f"OR (NEW.deleted_at IS NOT NULL AND NOT ({pending_receipt}))"
        if management else "OR NEW.current_revision <> OLD.current_revision + 1"
    )
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {schema}.mnemonic_knowledge_domain_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF (NEW.id,NEW.project_id,NEW.prefix,NEW.created_at) IS DISTINCT FROM
               (OLD.id,OLD.project_id,OLD.prefix,OLD.created_at)
               OR NEW.next_ordinal NOT IN (OLD.next_ordinal,OLD.next_ordinal+1)
               OR ({new} IS NOT DISTINCT FROM {old} AND NEW.version <> OLD.version)
               OR ({new} IS DISTINCT FROM {old} AND NEW.version <> OLD.version+1)
               {domain_deleted} THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid knowledge domain update';
            END IF;
            RETURN NEW;
        END $body$;
        CREATE OR REPLACE FUNCTION {schema}.mnemonic_knowledge_document_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF (NEW.id,NEW.project_id,NEW.domain_id,NEW.ordinal,NEW.created_at) IS DISTINCT FROM
               (OLD.id,OLD.project_id,OLD.domain_id,OLD.ordinal,OLD.created_at)
               {document_revision} THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid decision revision update';
            END IF;
            RETURN NEW;
        END $body$;
    """)


def _restore_identity_guards() -> None:
    """Restore the exact deployed 0059/0053 function bodies for catalog audits."""
    schema = _schema()
    old = "ROW(OLD.name,OLD.automatic_decision_reviews)"
    new = "ROW(NEW.name,NEW.automatic_decision_reviews)"
    same = f"{new} IS NOT DISTINCT FROM {old}"
    changed = f"{new} IS DISTINCT FROM {old}"
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {schema}.mnemonic_knowledge_domain_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        BEGIN
            IF (NEW.id, NEW.project_id, NEW.prefix, NEW.created_at) IS DISTINCT FROM
               (OLD.id, OLD.project_id, OLD.prefix, OLD.created_at)
               OR NEW.next_ordinal NOT IN (OLD.next_ordinal, OLD.next_ordinal + 1)
               OR ({same} AND NEW.version <> OLD.version)
               OR ({changed} AND NEW.version <> OLD.version + 1) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid knowledge domain update';
            END IF;
            RETURN NEW;
        END $function$;
        CREATE OR REPLACE FUNCTION {schema}.mnemonic_knowledge_document_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        BEGIN
            IF (NEW.id, NEW.project_id, NEW.domain_id, NEW.ordinal, NEW.created_at)
               IS DISTINCT FROM
               (OLD.id, OLD.project_id, OLD.domain_id, OLD.ordinal, OLD.created_at)
               OR NEW.current_revision <> OLD.current_revision + 1 THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid decision revision update';
            END IF;
            RETURN NEW;
        END $function$;
    """)


def _edge_guards(*, management: bool) -> None:
    schema = _schema()
    if management:
        op.execute(f"""
            CREATE FUNCTION {schema}.mnemonic_deleted_decision_edge_guard() RETURNS trigger
            LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
            DECLARE value jsonb;
            BEGIN
                IF TG_OP='DELETE' THEN
                    value:=to_jsonb(OLD);
                    IF EXISTS (SELECT 1 FROM {schema}.knowledge_deleted_edges archive
                        JOIN {schema}.client_operations receipt ON receipt.id=archive.receipt_id
                        WHERE archive.table_name=TG_TABLE_NAME AND archive.row_data=value
                          AND archive.project_id=(value->>'project_id')::uuid
                          AND receipt.project_id=archive.project_id AND receipt.state='pending'
                          AND receipt.operation_kind IN
                              ('delete_knowledge_domain','delete_decision')
                          AND receipt.xmin=pg_current_xact_id()::text::xid)
                    AND EXISTS (
                        SELECT 1 FROM {schema}.decision_documents document
                        WHERE document.deleted_at IS NOT NULL AND (
                            document.id=(value->>'document_id')::uuid OR
                            document.id=(value->>'target_document_id')::uuid OR
                            document.id IN (SELECT document_id FROM {schema}.decision_revisions
                                WHERE id IN ((value->>'revision_id')::uuid,
                                             (value->>'target_revision_id')::uuid,
                                             (value->>'governing_revision_id')::uuid)) OR
                            document.id IN (SELECT document_id FROM {schema}.decision_scopes
                                WHERE id=(value->>'scope_id')::uuid)
                        )
                    ) THEN RETURN OLD; END IF;
                END IF;
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='knowledge history is immutable';
            END $body$;
        """)
    function = ("mnemonic_deleted_decision_edge_guard" if management
                else "mnemonic_knowledge_immutable")
    for table, trigger in _EDGE_TRIGGERS.items():
        op.execute(f"""
            DROP TRIGGER {trigger} ON {schema}.{table};
            CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {schema}.{table}
                FOR EACH ROW EXECUTE FUNCTION {schema}.{function}();
        """)
    if not management:
        op.execute(f"DROP FUNCTION {schema}.mnemonic_deleted_decision_edge_guard()")


def _archive_guards() -> None:
    schema = _schema()
    tables = ",".join(f"'{table}'" for table in _EDGE_TRIGGERS)
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_knowledge_archive_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        DECLARE original_exists boolean;
        BEGIN
            IF NEW.table_name NOT IN ({tables}) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='unknown knowledge edge table';
            END IF;
            EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.%I edge WHERE to_jsonb(edge)=$1)',
                           TG_TABLE_SCHEMA, NEW.table_name) INTO original_exists USING NEW.row_data;
            IF NOT original_exists THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='knowledge edge archive must retain exact current evidence'; END IF;
            IF NEW.project_id IS DISTINCT FROM (NEW.row_data->>'project_id')::uuid
               OR NOT EXISTS (SELECT 1 FROM {schema}.client_operations receipt
                    WHERE receipt.id=NEW.receipt_id AND receipt.project_id=NEW.project_id
                      AND receipt.operation_kind IN ('delete_knowledge_domain','delete_decision')
                      AND receipt.state='pending' AND receipt.xmin=pg_current_xact_id()::text::xid)
            THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='knowledge edge archive requires its fresh deletion receipt'; END IF;
            RETURN NEW;
        END $body$;
        CREATE TRIGGER knowledge_archive_guard BEFORE INSERT ON {schema}.knowledge_deleted_edges
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_archive_guard();
        CREATE TRIGGER knowledge_immutable BEFORE UPDATE OR DELETE
            ON {schema}.knowledge_deleted_edges
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        CREATE TRIGGER knowledge_no_truncate BEFORE TRUNCATE ON {schema}.knowledge_deleted_edges
            FOR EACH STATEMENT EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        CREATE FUNCTION {schema}.mnemonic_knowledge_archive_seal() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM {schema}.client_operations receipt
                WHERE receipt.id=NEW.receipt_id AND receipt.project_id=NEW.project_id
                  AND receipt.state='completed' AND receipt.response_status=200
                  AND receipt.mutation_applied
                  AND receipt.operation_kind IN ('delete_knowledge_domain','delete_decision')
                  AND (receipt.response_body->>'deleted_at')::timestamptz=NEW.deleted_at
                  AND (receipt.response_body->>'removed_relationship_count')::bigint=(
                      SELECT count(*) FROM {schema}.knowledge_deleted_edges
                      WHERE receipt_id=NEW.receipt_id))
            THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='knowledge edge archive requires its completed deletion receipt'; END IF;
            RETURN NULL;
        END $body$;
        CREATE CONSTRAINT TRIGGER knowledge_archive_seal AFTER INSERT
            ON {schema}.knowledge_deleted_edges DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_archive_seal();
    """)


def _tombstone_seals() -> None:
    schema = _schema()
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_knowledge_tombstone_insert_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF NEW.deleted_at IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='knowledge records must be created without a tombstone'; END IF;
            RETURN NEW;
        END $body$;
        CREATE FUNCTION {schema}.mnemonic_knowledge_tombstone_seal() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        DECLARE target_domain uuid;
        BEGIN
            IF NEW.deleted_at IS NULL THEN RETURN NULL; END IF;
            target_domain:=CASE WHEN TG_TABLE_NAME='knowledge_domains' THEN NEW.id
                                ELSE (to_jsonb(NEW)->>'domain_id')::uuid END;
            IF NOT EXISTS (SELECT 1 FROM {schema}.client_operations receipt
                WHERE receipt.project_id=NEW.project_id AND receipt.state='completed'
                  AND receipt.xmin=pg_current_xact_id()::text::xid
                  AND (receipt.response_body->>'deleted_at')::timestamptz=NEW.deleted_at
                  AND (receipt.response_body->>'domain_id')::uuid=target_domain
                  AND ((receipt.operation_kind='delete_knowledge_domain'
                        AND receipt.response_body->'document_id'='null'::jsonb)
                    OR (TG_TABLE_NAME='decision_documents'
                        AND receipt.operation_kind='delete_decision'
                        AND (receipt.response_body->>'document_id')::uuid=NEW.id)))
            THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='knowledge tombstone requires its completed deletion receipt'; END IF;
            RETURN NULL;
        END $body$;
    """)
    for table in ("knowledge_domains", "decision_documents"):
        op.execute(f"""
            CREATE TRIGGER knowledge_tombstone_insert_guard BEFORE INSERT ON {schema}.{table}
                FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_tombstone_insert_guard();
            CREATE CONSTRAINT TRIGGER knowledge_tombstone_seal AFTER UPDATE ON {schema}.{table}
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                EXECUTE FUNCTION {schema}.mnemonic_knowledge_tombstone_seal();
        """)


def upgrade() -> None:
    # Earlier PDR backfills may have queued deferred events in this upgrade transaction.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("SET CONSTRAINTS ALL DEFERRED")
    op.add_column("knowledge_domains", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("decision_documents", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("knowledge_domains", sa.Column(
        "decision_review_priority", sa.SmallInteger(), nullable=False, server_default="40",
    ))
    op.create_check_constraint("decision_review_priority_range", "knowledge_domains",
                               "decision_review_priority BETWEEN 0 AND 100")
    # This representation change preserves effective behavior and version identity.
    op.execute("ALTER TABLE knowledge_domains DISABLE TRIGGER knowledge_domain_guard")
    # Carry forward effective behavior, then retire inheritance. New domains are off.
    op.execute("""
        UPDATE knowledge_domains domain SET
            automatic_decision_reviews=CASE WHEN domain.automatic_decision_reviews='inherit'
                THEN CASE WHEN settings.automatic_decision_reviews THEN 'on' ELSE 'off' END
                ELSE domain.automatic_decision_reviews END,
            decision_review_priority=settings.decision_review_priority
        FROM project_settings settings WHERE settings.project_id=domain.project_id
          AND (domain.automatic_decision_reviews='inherit' OR
               domain.decision_review_priority<>settings.decision_review_priority)
    """)
    _identity_guards(management=True)
    op.execute("ALTER TABLE knowledge_domains ENABLE TRIGGER knowledge_domain_guard")
    op.alter_column("knowledge_domains", "automatic_decision_reviews", server_default="off")
    op.drop_constraint(op.f("ck_knowledge_domains_automatic_decision_reviews"), "knowledge_domains")
    op.create_check_constraint("automatic_decision_reviews", "knowledge_domains",
                               "automatic_decision_reviews IN ('on','off')")
    op.create_table("knowledge_deleted_edges", *deleted_edge_elements())
    _archive_guards()
    _edge_guards(management=True)
    _tombstone_seals()
    _operation_kinds(management=True)


def downgrade() -> None:
    op.execute("LOCK TABLE knowledge_domains, decision_documents IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("""
        SELECT EXISTS(SELECT 1 FROM knowledge_domains WHERE deleted_at IS NOT NULL OR
                      automatic_decision_reviews <> 'off' OR decision_review_priority<>40)
            OR EXISTS(SELECT 1 FROM decision_documents WHERE deleted_at IS NOT NULL)
            OR EXISTS(SELECT 1 FROM project_settings WHERE automatic_decision_reviews
                      OR decision_review_priority<>40)
            OR EXISTS(SELECT 1 FROM client_operations WHERE operation_kind IN
                      ('delete_knowledge_domain','delete_decision'))
    """)):
        raise RuntimeError("Knowledge management state cannot be safely downgraded.")
    _operation_kinds(management=False)
    _edge_guards(management=False)
    op.drop_table("knowledge_deleted_edges")
    op.execute("DROP FUNCTION mnemonic_knowledge_archive_guard()")
    op.execute("DROP FUNCTION mnemonic_knowledge_archive_seal()")
    for table in ("knowledge_domains", "decision_documents"):
        op.execute(f"DROP TRIGGER knowledge_tombstone_seal ON {table}")
        op.execute(f"DROP TRIGGER knowledge_tombstone_insert_guard ON {table}")
    op.execute("DROP FUNCTION mnemonic_knowledge_tombstone_seal()")
    op.execute("DROP FUNCTION mnemonic_knowledge_tombstone_insert_guard()")
    op.drop_constraint(op.f("ck_knowledge_domains_automatic_decision_reviews"), "knowledge_domains")
    op.create_check_constraint("automatic_decision_reviews", "knowledge_domains",
                               "automatic_decision_reviews IN ('inherit','on','off')")
    _restore_identity_guards()
    op.execute("ALTER TABLE knowledge_domains DISABLE TRIGGER knowledge_domain_guard")
    op.execute("UPDATE knowledge_domains SET automatic_decision_reviews='inherit'")
    op.execute("ALTER TABLE knowledge_domains ENABLE TRIGGER knowledge_domain_guard")
    op.alter_column("knowledge_domains", "automatic_decision_reviews", server_default="inherit")
    op.drop_constraint(op.f("ck_knowledge_domains_decision_review_priority_range"),
                       "knowledge_domains")
    op.drop_column("knowledge_domains", "decision_review_priority")
    op.drop_column("knowledge_domains", "deleted_at")
    op.drop_column("decision_documents", "deleted_at")
