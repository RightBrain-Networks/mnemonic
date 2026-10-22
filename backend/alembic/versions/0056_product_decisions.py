"""Make product decisions the durable knowledge records behind human attention."""

import hashlib
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

from mnemonic_api.migration_data.product_decisions_0056 import (
    product_decision_author,
    product_decision_content,
    product_decision_title,
)
from mnemonic_api.product_decision_db import domain_binding_elements, request_binding_elements

revision = "0056_product_decisions"
down_revision = "0055_decision_provenance"
branch_labels = None
depends_on = None

_AUTHOR_CHECK = (
    "(author_kind='human' AND agent_session_id IS NULL AND model_id IS NULL) OR "
    "(author_kind='agent' AND agent_session_id IS NOT NULL "
    "AND length(btrim(agent_session_id)) > 0 AND "
    "(model_id IS NULL OR length(btrim(model_id)) > 0))"
)


def _seed_domains(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_seed_product_decision_domain(selected_project uuid)
        RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        DECLARE selected_domain uuid; domain_name text := 'Product Decision Records';
            suffix integer := 1; automatically_created boolean := false;
        BEGIN
            PERFORM 1 FROM {schema}.projects WHERE id=selected_project FOR UPDATE;
            IF EXISTS (SELECT 1 FROM {schema}.project_product_decision_domains
                       WHERE project_id=selected_project) THEN RETURN; END IF;
            SELECT id INTO selected_domain FROM {schema}.knowledge_domains
                WHERE project_id=selected_project AND prefix='PDR';
            IF selected_domain IS NULL THEN
                WHILE EXISTS (SELECT 1 FROM {schema}.knowledge_domains
                    WHERE project_id=selected_project AND lower(name)=lower(domain_name)) LOOP
                    domain_name := 'Product Decision Records (PDR' ||
                        CASE WHEN suffix=1 THEN '' ELSE ' ' || suffix::text END || ')';
                    suffix := suffix + 1;
                END LOOP;
                selected_domain := gen_random_uuid();
                INSERT INTO {schema}.knowledge_domains(id,project_id,name,prefix)
                    VALUES(selected_domain,selected_project,domain_name,'PDR');
                automatically_created := true;
            END IF;
            INSERT INTO {schema}.project_product_decision_domains
                (project_id,domain_id,created_automatically)
                VALUES(selected_project,selected_domain,automatically_created);
        END $body$;
        CREATE FUNCTION {schema}.mnemonic_seed_project_product_decisions() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            PERFORM {schema}.mnemonic_seed_product_decision_domain(NEW.id);
            RETURN NEW;
        END $body$;
        CREATE TRIGGER project_product_decisions_seed AFTER INSERT ON {schema}.projects
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_seed_project_product_decisions();
        SELECT {schema}.mnemonic_seed_product_decision_domain(id) FROM {schema}.projects;
    """)


def _append_revision(connection, gate, document_id, number, source, *, resolved=False) -> None:
    question = source["question"]
    content = product_decision_content(question, gate["resolution"] if resolved else None)
    kind, session, model = product_decision_author(
        source["requested_by_client"], source["requested_by_session_id"],
        source["requested_by_model"], resolved=resolved,
    )
    timestamp = gate["resolved_at"] if resolved else source["created_at"]
    if number:
        connection.execute(sa.text("""
            UPDATE decision_documents SET current_revision=:number,updated_at=:timestamp
            WHERE id=:id
        """), {"number": number, "timestamp": timestamp, "id": document_id})
    revision_id = uuid4()
    connection.execute(sa.text("""
        INSERT INTO decision_revisions(id,project_id,document_id,revision,title,content,
            media_type,state,size_bytes,sha256,author_kind,agent_session_id,model_id,created_at)
        VALUES(:id,:project,:document,:revision,:title,:content,'text/markdown',:state,:size,
               :hash,:author,:session,:model,:timestamp)
    """), {
        "id": revision_id, "project": gate["project_id"], "document": document_id,
        "revision": number, "title": product_decision_title(gate["work_title"]),
        "content": content, "state": "accepted" if resolved else "proposed",
        "size": len(content.encode("utf-8")),
        "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "author": kind, "session": session, "model": model, "timestamp": timestamp,
    })
    if gate["project_id"] == gate["work_project_id"]:
        connection.execute(sa.text("""
            INSERT INTO decision_work_item_links(revision_id,work_item_id,project_id)
            VALUES(:revision,:work,:project)
        """), {"revision": revision_id, "work": gate["work_item_id"],
               "project": gate["project_id"]})


def _backfill() -> None:
    connection = op.get_bind()
    gates = connection.execute(sa.text("""
        SELECT gate.*,work.title AS work_title,work.project_id AS work_project_id,binding.domain_id,
            EXISTS(SELECT 1 FROM work_item_moves move WHERE move.work_item_id=gate.work_item_id
                AND move.source_project_id=gate.project_id
                AND move.created_at>=gate.resolved_at) AS work_moved_out
        FROM work_gates gate JOIN work_items work ON work.id=gate.work_item_id
        JOIN project_product_decision_domains binding ON binding.project_id=gate.project_id
        ORDER BY gate.project_id,gate.attention_sequence
    """)).mappings()
    for gate in gates:
        if gate["project_id"] != gate["work_project_id"] and not gate["work_moved_out"]:
            raise RuntimeError("A historical product question has no witnessed work relocation")
        ordinal = connection.scalar(sa.text("""
            UPDATE knowledge_domains SET next_ordinal=next_ordinal+1
            WHERE id=:domain RETURNING next_ordinal-1
        """), {"domain": gate["domain_id"]})
        document_id = uuid4()
        connection.execute(sa.text("""
            INSERT INTO decision_documents(id,project_id,domain_id,ordinal,current_revision,
                created_at,updated_at) VALUES(:id,:project,:domain,:ordinal,0,:created,:created)
        """), {"id": document_id, "project": gate["project_id"], "domain": gate["domain_id"],
               "ordinal": ordinal, "created": gate["created_at"]})
        connection.execute(sa.text("""
            INSERT INTO product_decision_requests
                (gate_id,project_id,document_id,backfilled_revision_count)
            VALUES(:gate,:project,:document,:backfilled)
        """), {"gate": gate["id"], "project": gate["project_id"], "document": document_id,
               "backfilled": len(gate["question_revisions"]) + 1
                   + int(gate["resolved_at"] is not None)})
        sources = [gate, *gate["question_revisions"]]
        for number, source in enumerate(sources):
            _append_revision(connection, gate, document_id, number, source)
        if gate["resolved_at"] is not None:
            _append_revision(connection, gate, document_id, len(sources), sources[-1],
                             resolved=True)


def _validation_functions(schema: str) -> None:
    # Frozen independently from runtime audit SQL: each revision is a rendering of
    # a specific immutable gate source, including historical unknown model IDs.
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_product_decision_revision_valid(selected_revision uuid)
        RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
        SELECT EXISTS (
            SELECT 1 FROM {schema}.decision_revisions revision
            JOIN {schema}.product_decision_requests binding
                ON binding.document_id=revision.document_id
                AND binding.project_id=revision.project_id
            JOIN {schema}.work_gates gate ON gate.id=binding.gate_id
                AND gate.project_id=binding.project_id
            CROSS JOIN LATERAL (
                SELECT 0 AS number,gate.question AS content,'proposed' AS state,
                    gate.created_at AS authored_at,gate.requested_by_client AS client,
                    gate.requested_by_session_id AS session,gate.requested_by_model AS model,
                    false AS resolved
                UNION ALL
                SELECT position::integer,entry->>'question','proposed',
                    (entry->>'created_at')::timestamptz,entry->>'requested_by_client',
                    entry->>'requested_by_session_id',entry->>'requested_by_model',false
                FROM jsonb_array_elements(gate.question_revisions)
                    WITH ORDINALITY AS versions(entry,position)
                UNION ALL
                SELECT jsonb_array_length(gate.question_revisions)+1,
                    E'## Question\\n\\n' || coalesce(gate.question_revisions->-1->>'question',
                        gate.question) || E'\\n\\n## Product decision\\n\\n' || gate.resolution,
                    'accepted',gate.resolved_at,gate.resolved_by_client,
                    gate.resolved_by_session_id,gate.resolved_by_model,true
                WHERE gate.resolved_at IS NOT NULL
            ) source
            WHERE revision.id=selected_revision AND source.number=revision.revision
                AND revision.content=source.content AND revision.state=source.state
                AND revision.created_at=source.authored_at AND revision.media_type='text/markdown'
                AND revision.author_kind=CASE WHEN source.resolved
                    OR source.client IN ('dashboard','human','manual','browser')
                    THEN 'human' ELSE 'agent' END
                AND revision.agent_session_id IS NOT DISTINCT FROM CASE WHEN source.resolved
                    OR source.client IN ('dashboard','human','manual','browser')
                    THEN NULL ELSE source.session END
                AND revision.model_id IS NOT DISTINCT FROM CASE WHEN source.resolved
                    OR source.client IN ('dashboard','human','manual','browser')
                    THEN NULL ELSE source.model END
                AND (revision.author_kind='human' OR revision.model_id IS NOT NULL
                    OR revision.revision<binding.backfilled_revision_count)
                AND (EXISTS (SELECT 1 FROM {schema}.decision_work_item_links link
                    WHERE link.revision_id=revision.id AND link.work_item_id=gate.work_item_id
                        AND link.project_id=gate.project_id)
                    OR (revision.revision<binding.backfilled_revision_count
                        AND gate.resolved_at IS NOT NULL
                        AND EXISTS (SELECT 1 FROM {schema}.work_items work
                            WHERE work.id=gate.work_item_id AND work.project_id<>gate.project_id)
                        AND EXISTS (SELECT 1 FROM {schema}.work_item_moves move
                            WHERE move.work_item_id=gate.work_item_id
                                AND move.source_project_id=gate.project_id
                                AND move.created_at>=gate.resolved_at)))
        )
        $body$;
        CREATE FUNCTION {schema}.mnemonic_product_decision_request_valid(selected_gate uuid)
        RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
        SELECT EXISTS (
            SELECT 1 FROM {schema}.product_decision_requests binding
            JOIN {schema}.work_gates gate ON gate.id=binding.gate_id
                AND gate.project_id=binding.project_id
            JOIN {schema}.decision_documents document ON document.id=binding.document_id
                AND document.project_id=binding.project_id
            JOIN {schema}.project_product_decision_domains domain
                ON domain.project_id=binding.project_id AND domain.domain_id=document.domain_id
            WHERE gate.id=selected_gate AND document.created_at=gate.created_at
                AND binding.backfilled_revision_count<=document.current_revision+1
                AND document.current_revision=jsonb_array_length(gate.question_revisions)
                    + CASE WHEN gate.resolved_at IS NULL THEN 0 ELSE 1 END
                AND document.updated_at=coalesce(gate.resolved_at,
                    (gate.question_revisions->-1->>'created_at')::timestamptz,gate.created_at)
                AND document.current_revision+1=(SELECT count(*)
                    FROM {schema}.decision_revisions WHERE document_id=document.id)
                AND NOT EXISTS (SELECT 1 FROM {schema}.decision_revisions revision
                    WHERE revision.document_id=document.id AND NOT
                        {schema}.mnemonic_product_decision_revision_valid(revision.id))
        )
        $body$;
    """)


def _guards(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_product_decision_domain_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM {schema}.knowledge_domains domain
                WHERE domain.id=NEW.domain_id AND domain.project_id=NEW.project_id
                    AND domain.prefix='PDR') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='invalid product decision domain';
            END IF;
            RETURN NEW;
        END $body$;
        CREATE TRIGGER product_decision_domain_guard BEFORE INSERT
            ON {schema}.project_product_decision_domains FOR EACH ROW
            EXECUTE FUNCTION {schema}.mnemonic_product_decision_domain_guard();
        CREATE FUNCTION {schema}.mnemonic_product_decision_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        DECLARE selected_gate uuid;
        BEGIN
            IF TG_TABLE_NAME='work_gates' THEN selected_gate:=NEW.id;
            ELSIF TG_TABLE_NAME='product_decision_requests' THEN
                selected_gate:=NEW.gate_id;
                IF NEW.backfilled_revision_count<>0 THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='fresh product decisions cannot assert migrated revisions';
                END IF;
            ELSIF TG_TABLE_NAME='decision_documents' THEN
                SELECT gate_id INTO selected_gate FROM {schema}.product_decision_requests
                    WHERE document_id=NEW.id;
            ELSE
                SELECT gate_id INTO selected_gate FROM {schema}.product_decision_requests
                    WHERE document_id=NEW.document_id;
            END IF;
            IF selected_gate IS NOT NULL AND NOT
                {schema}.mnemonic_product_decision_request_valid(selected_gate) THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='invalid product decision history';
            END IF;
            IF TG_TABLE_NAME='decision_revisions' THEN
                IF NEW.author_kind='agent' AND NEW.model_id IS NULL
                    AND selected_gate IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='unknown agent model requires retained product decision source';
                END IF;
            END IF;
            RETURN NULL;
        END $body$;
    """)
    for table in ("project_product_decision_domains", "product_decision_requests"):
        op.execute(f"""
            CREATE TRIGGER knowledge_immutable BEFORE UPDATE OR DELETE ON {schema}.{table}
                FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
            CREATE TRIGGER knowledge_no_truncate BEFORE TRUNCATE ON {schema}.{table}
                FOR EACH STATEMENT EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        """)
    for table in ("work_gates", "product_decision_requests", "decision_documents",
                  "decision_revisions"):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER product_decision_guard AFTER INSERT OR UPDATE
                ON {schema}.{table} DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_product_decision_guard();
        """)


def upgrade() -> None:
    op.create_table("project_product_decision_domains", *domain_binding_elements())
    op.create_table("product_decision_requests", *request_binding_elements())
    op.drop_constraint(op.f("ck_decision_revisions_author_identity"), "decision_revisions")
    op.create_check_constraint("author_identity", "decision_revisions", _AUTHOR_CHECK)
    connection = op.get_bind()
    schema = connection.scalar(sa.text("SELECT current_schema()"))
    quoted = connection.dialect.identifier_preparer.quote_schema(schema)
    _seed_domains(quoted)
    _backfill()
    _validation_functions(quoted)
    _guards(quoted)


def downgrade() -> None:
    op.execute("LOCK TABLE project_product_decision_domains,product_decision_requests,"
               "knowledge_domains,decision_documents,decision_revisions IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("""
        SELECT EXISTS(SELECT 1 FROM product_decision_requests)
            OR EXISTS(SELECT 1 FROM project_product_decision_domains binding
                JOIN knowledge_domains domain ON domain.id=binding.domain_id
                WHERE binding.created_automatically AND (domain.version<>1
                    OR EXISTS(SELECT 1 FROM decision_documents WHERE domain_id=domain.id)
                    OR EXISTS(SELECT 1 FROM client_operations receipt
                        WHERE receipt.operation_kind IN
                            ('create_knowledge_domain','rename_knowledge_domain')
                        AND receipt.response_body->>'id'=domain.id::text)))
    """)):
        raise RuntimeError("Product decision data exists; restore a pre-upgrade backup")
    for table in ("work_gates", "product_decision_requests", "decision_documents",
                  "decision_revisions"):
        op.execute(f"DROP TRIGGER product_decision_guard ON {table}")
    op.execute("DROP TRIGGER project_product_decisions_seed ON projects")
    automatic_domains = list(op.get_bind().scalars(sa.text(
        "SELECT domain_id FROM project_product_decision_domains WHERE created_automatically"
    )))
    op.drop_table("product_decision_requests")
    op.drop_table("project_product_decision_domains")
    if automatic_domains:
        op.execute("ALTER TABLE knowledge_domains DISABLE TRIGGER knowledge_no_delete")
        op.get_bind().execute(sa.text("DELETE FROM knowledge_domains WHERE id=ANY(:ids)"),
                              {"ids": automatic_domains})
        op.execute("ALTER TABLE knowledge_domains ENABLE TRIGGER knowledge_no_delete")
    for function in ("mnemonic_product_decision_guard()",
                     "mnemonic_product_decision_domain_guard()",
                     "mnemonic_product_decision_request_valid(uuid)",
                     "mnemonic_product_decision_revision_valid(uuid)",
                     "mnemonic_seed_project_product_decisions()",
                     "mnemonic_seed_product_decision_domain(uuid)"):
        op.execute(f"DROP FUNCTION {function}")
    op.drop_constraint(op.f("ck_decision_revisions_author_identity"), "decision_revisions")
    op.create_check_constraint("author_identity", "decision_revisions", _AUTHOR_CHECK.replace(
        "(model_id IS NULL OR length(btrim(model_id)) > 0)",
        "model_id IS NOT NULL AND length(btrim(model_id)) > 0",
    ))
