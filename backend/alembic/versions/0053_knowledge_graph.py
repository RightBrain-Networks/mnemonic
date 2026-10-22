"""Project decision domains, immutable revisions and revision-pinned commit provenance."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api import knowledge_db as knowledge

revision = "0053_knowledge_graph"
down_revision = "0052_dependency_cycles"
branch_labels = None
depends_on = None

_PREVIOUS_KINDS = (
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
)
_NEW_KINDS = (
    "create_knowledge_domain",
    "rename_knowledge_domain",
    "put_decision",
    "append_decision_scope",
)
_IMMUTABLE = (
    "decision_revisions",
    "decision_relationships",
    "decision_work_item_links",
    "decision_artifact_links",
    "decision_scopes",
)


def _operation_kinds(kinds: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_client_operations_operation_kind_valid"), "client_operations")
    op.create_check_constraint(
        "operation_kind_valid",
        "client_operations",
        "operation_kind IN (" + ",".join(f"'{kind}'" for kind in kinds) + ")",
    )


def _immutability(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_knowledge_immutable() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        BEGIN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='knowledge history is immutable';
        END $function$;
    """)
    for table in _IMMUTABLE:
        op.execute(f"""
            CREATE TRIGGER knowledge_immutable BEFORE UPDATE OR DELETE ON {schema}.{table}
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
            CREATE TRIGGER knowledge_no_truncate BEFORE TRUNCATE ON {schema}.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        """)
    for table in ("knowledge_domains", "decision_documents"):
        op.execute(f"""
            CREATE TRIGGER knowledge_no_delete BEFORE DELETE ON {schema}.{table}
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
            CREATE TRIGGER knowledge_no_truncate BEFORE TRUNCATE ON {schema}.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        """)


def _identity_guards(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_knowledge_domain_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        BEGIN
            IF (NEW.id, NEW.project_id, NEW.prefix, NEW.created_at) IS DISTINCT FROM
               (OLD.id, OLD.project_id, OLD.prefix, OLD.created_at)
               OR NEW.next_ordinal NOT IN (OLD.next_ordinal, OLD.next_ordinal + 1)
               OR (NEW.name = OLD.name AND NEW.version <> OLD.version)
               OR (NEW.name <> OLD.name AND NEW.version <> OLD.version + 1) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid knowledge domain update';
            END IF;
            RETURN NEW;
        END $function$;
        CREATE TRIGGER knowledge_domain_guard BEFORE UPDATE ON {schema}.knowledge_domains
        FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_domain_guard();

        CREATE FUNCTION {schema}.mnemonic_knowledge_document_guard() RETURNS trigger
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
        CREATE TRIGGER knowledge_document_guard BEFORE UPDATE ON {schema}.decision_documents
        FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_document_guard();

        CREATE FUNCTION {schema}.mnemonic_knowledge_revision_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        DECLARE current_number integer;
        BEGIN
            SELECT current_revision INTO current_number FROM {schema}.decision_documents
                WHERE project_id=NEW.project_id AND id=NEW.document_id FOR UPDATE;
            IF current_number IS DISTINCT FROM NEW.revision
               OR NEW.creation_transaction_id <> pg_current_xact_id()::text::bigint
               OR (NEW.revision > 0 AND NOT EXISTS (
                    SELECT 1 FROM {schema}.decision_revisions WHERE document_id=NEW.document_id
                    AND revision=NEW.revision-1)) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid decision revision insert';
            END IF;
            RETURN NEW;
        END $function$;
        CREATE TRIGGER knowledge_revision_guard BEFORE INSERT ON {schema}.decision_revisions
        FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_revision_guard();
    """)


def _link_guards(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_knowledge_link_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        DECLARE revision_tx bigint; source_id uuid; source_domain uuid; target_domain uuid;
        BEGIN
            PERFORM 1 FROM {schema}.projects WHERE id=NEW.project_id FOR UPDATE;
            SELECT r.creation_transaction_id, r.document_id, d.domain_id
                INTO revision_tx, source_id, source_domain
                FROM {schema}.decision_revisions r JOIN {schema}.decision_documents d
                ON d.id=r.document_id WHERE r.id=NEW.revision_id AND r.project_id=NEW.project_id;
            IF revision_tx IS DISTINCT FROM pg_current_xact_id()::text::bigint THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='decision revision links are sealed';
            END IF;
            IF TG_TABLE_NAME='decision_relationships' THEN
                IF NEW.target_document_id=source_id THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='decision self link refused';
                END IF;
                IF NEW.kind='supersedes' THEN
                    SELECT domain_id INTO target_domain FROM {schema}.decision_documents
                        WHERE id=NEW.target_document_id;
                    IF source_domain IS DISTINCT FROM target_domain THEN
                        RAISE EXCEPTION USING ERRCODE='23514',
                            MESSAGE='supersession requires the same domain';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END $function$;
    """)
    for table in ("decision_relationships", "decision_work_item_links", "decision_artifact_links"):
        op.execute(f"""
            CREATE TRIGGER knowledge_link_guard BEFORE INSERT ON {schema}.{table}
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_link_guard();
        """)
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_knowledge_cycle_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        DECLARE source_id uuid; cycle_found boolean;
        BEGIN
            IF NEW.kind <> 'supersedes' THEN RETURN NULL; END IF;
            SELECT r.document_id INTO source_id FROM {schema}.decision_revisions r
                JOIN {schema}.decision_documents d ON d.id=r.document_id
                AND d.current_revision=r.revision WHERE r.id=NEW.revision_id;
            IF source_id IS NULL THEN RETURN NULL; END IF;
            WITH RECURSIVE reachable(id) AS (
                SELECT NEW.target_document_id
                UNION
                SELECT e.target_document_id FROM reachable
                JOIN {schema}.decision_documents d ON d.id=reachable.id
                JOIN {schema}.decision_revisions r ON r.document_id=d.id
                    AND r.revision=d.current_revision
                JOIN {schema}.decision_relationships e ON e.revision_id=r.id
                    AND e.kind='supersedes'
            ) SELECT EXISTS(SELECT 1 FROM reachable WHERE id=source_id) INTO cycle_found;
            IF cycle_found THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='decision supersession cycle refused';
            END IF;
            RETURN NULL;
        END $function$;
        CREATE CONSTRAINT TRIGGER knowledge_cycle_guard AFTER INSERT
        ON {schema}.decision_relationships DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_cycle_guard();
    """)


def upgrade() -> None:
    _operation_kinds((*_PREVIOUS_KINDS, *_NEW_KINDS))
    op.create_table("knowledge_domains", *knowledge.domain_elements())
    # The current-revision FK is added after both sides of the cycle exist.
    elements = [
        element
        for element in knowledge.document_elements()
        if not isinstance(element, sa.ForeignKeyConstraint)
        or element.name != "fk_decision_documents_current_revision"
    ]
    op.create_table("decision_documents", *elements)
    op.create_table("decision_revisions", *knowledge.revision_elements())
    op.create_foreign_key(
        "fk_decision_documents_current_revision",
        "decision_documents",
        "decision_revisions",
        ["project_id", "id", "current_revision"],
        ["project_id", "document_id", "revision"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table("decision_relationships", *knowledge.relationship_elements())
    op.create_table("decision_work_item_links", *knowledge.resource_link_elements("work_item"))
    op.create_table("decision_artifact_links", *knowledge.resource_link_elements("artifact"))
    op.create_table("decision_scopes", *knowledge.scope_elements())
    schema = op.get_bind().scalar(sa.text("SELECT current_schema()"))
    quoted = op.get_bind().dialect.identifier_preparer.quote(schema)
    _immutability(quoted)
    _identity_guards(quoted)
    _link_guards(quoted)


def downgrade() -> None:
    tables = (*_IMMUTABLE, "decision_documents", "knowledge_domains")
    op.execute(
        "LOCK TABLE " + ",".join((*tables, "client_operations")) + " IN ACCESS EXCLUSIVE MODE"
    )
    if any(
        op.get_bind().scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {table})")) for table in tables
    ):
        raise RuntimeError("Knowledge history exists; restore a pre-upgrade backup")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM client_operations WHERE operation_kind IN "
            "('create_knowledge_domain','rename_knowledge_domain','put_decision','append_decision_scope'))"
        )
    ):
        raise RuntimeError("Knowledge operation receipts exist; restore a pre-upgrade backup")
    op.drop_constraint("fk_decision_documents_current_revision", "decision_documents")
    for table in reversed(tables[:5]):
        op.drop_table(table)
    op.drop_table("decision_documents")
    op.drop_table("knowledge_domains")
    for name in (
        "cycle_guard",
        "link_guard",
        "revision_guard",
        "document_guard",
        "domain_guard",
        "immutable",
    ):
        op.execute(f"DROP FUNCTION mnemonic_knowledge_{name}()")
    _operation_kinds(_PREVIOUS_KINDS)
