"""Independent restore-time witnesses for product decisions and human questions."""

from mnemonic_api.impact_integrity import _pins

_EXPECTED = """
    WITH question_sources AS (
        SELECT gate.id AS gate_id,gate.project_id,gate.work_item_id,0 AS revision,
            gate.question AS content,'proposed' AS state,gate.created_at AS authored_at,
            gate.requested_by_client AS client,gate.requested_by_session_id AS session,
            gate.requested_by_model AS model,false AS resolved
        FROM work_gates gate
        UNION ALL
        SELECT gate.id,gate.project_id,gate.work_item_id,position::integer,
            entry->>'question','proposed',
            CASE WHEN pg_input_is_valid(entry->>'created_at','timestamp with time zone')
                 THEN (entry->>'created_at')::timestamptz END,
            entry->>'requested_by_client',entry->>'requested_by_session_id',
            entry->>'requested_by_model',false
        FROM work_gates gate CROSS JOIN LATERAL jsonb_array_elements(gate.question_revisions)
            WITH ORDINALITY AS versions(entry,position)
        UNION ALL
        SELECT gate.id,gate.project_id,gate.work_item_id,
            jsonb_array_length(gate.question_revisions)+1,
            E'## Question\\n\\n' || coalesce(gate.question_revisions->-1->>'question',gate.question)
                || E'\\n\\n## Product decision\\n\\n' || gate.resolution,
            'accepted',gate.resolved_at,gate.resolved_by_client,
            gate.resolved_by_session_id,gate.resolved_by_model,true
        FROM work_gates gate WHERE gate.resolved_at IS NOT NULL
    ), expected AS (
        SELECT source.*,binding.document_id,binding.backfilled_revision_count,
            CASE WHEN source.resolved OR source.client IN ('dashboard','human','manual','browser')
                 THEN 'human' ELSE 'agent' END AS author_kind,
            CASE WHEN source.resolved OR source.client IN ('dashboard','human','manual','browser')
                 THEN NULL ELSE source.session END AS agent_session_id,
            CASE WHEN source.resolved OR source.client IN ('dashboard','human','manual','browser')
                 THEN NULL ELSE source.model END AS model_id
        FROM question_sources source JOIN product_decision_requests binding
            ON binding.gate_id=source.gate_id AND binding.project_id=source.project_id
    )
"""

_SOURCE_MATCH = """
    revision.project_id=expected.project_id AND revision.document_id=expected.document_id
    AND revision.revision=expected.revision AND revision.content=expected.content
    AND revision.state=expected.state AND revision.created_at=expected.authored_at
    AND revision.media_type='text/markdown' AND revision.author_kind=expected.author_kind
    AND revision.agent_session_id IS NOT DISTINCT FROM expected.agent_session_id
    AND revision.model_id IS NOT DISTINCT FROM expected.model_id
    AND (EXISTS (SELECT 1 FROM decision_work_item_links link
        WHERE link.revision_id=revision.id AND link.project_id=expected.project_id
            AND link.work_item_id=expected.work_item_id)
        OR (revision.revision<expected.backfilled_revision_count
            AND EXISTS (SELECT 1 FROM work_gates moved_gate
                JOIN work_items work ON work.id=moved_gate.work_item_id
                WHERE moved_gate.id=expected.gate_id AND moved_gate.resolved_at IS NOT NULL
                    AND work.project_id<>moved_gate.project_id
                    AND EXISTS (SELECT 1 FROM work_item_moves move
                        WHERE move.work_item_id=moved_gate.work_item_id
                            AND move.source_project_id=moved_gate.project_id
                            AND move.created_at>=moved_gate.resolved_at))))
"""


def _decision_links(kind: str) -> str:
    return f"""
        coalesce((SELECT jsonb_agg(identifier ORDER BY identifier) FROM (
            SELECT domain.prefix||'-'||lpad(document.ordinal::text,
                greatest(5,length(document.ordinal::text)),'0') AS identifier
            FROM decision_relationships edge
            JOIN decision_documents document ON document.id=edge.target_document_id
            JOIN knowledge_domains domain ON domain.id=document.domain_id
            WHERE edge.revision_id=source.id AND edge.kind='{kind}'
        ) links),'[]'::jsonb)
    """


def _resource_links(table: str, column: str) -> str:
    return f"""coalesce((SELECT jsonb_agg({column} ORDER BY {column})
        FROM {table} WHERE revision_id=source.id),'[]'::jsonb)"""


_METADATA = f"""
    jsonb_build_object(
        'title',source.title,
        'related_decision_ids',{_decision_links('related')},
        'related_work_item_ids',{_resource_links('decision_work_item_links', 'work_item_id')},
        'related_artifact_ids',{_resource_links('decision_artifact_links', 'artifact_id')},
        'supersedes',{_decision_links('supersedes')},
        'depends_on',{_pins('decision_dependencies', "'rationale',edge.rationale")},
        'supersession_details',{_pins(
            'decision_supersession_details', "'extent',edge.extent,'reason',edge.reason")})
"""

PRODUCT_DECISION_FINDINGS = {
    "projects_without_product_decision_domain": """
        SELECT count(*) FROM projects project WHERE NOT EXISTS (
            SELECT 1 FROM project_product_decision_domains binding
            JOIN knowledge_domains domain ON domain.id=binding.domain_id
                AND domain.project_id=binding.project_id AND domain.prefix='PDR'
            WHERE binding.project_id=project.id)
    """,
    "invalid_product_decision_domains": """
        SELECT count(*) FROM project_product_decision_domains binding WHERE NOT EXISTS (
            SELECT 1 FROM knowledge_domains domain WHERE domain.id=binding.domain_id
                AND domain.project_id=binding.project_id AND domain.prefix='PDR')
    """,
    "human_questions_without_product_decisions": """
        SELECT count(*) FROM work_gates gate WHERE NOT EXISTS (
            SELECT 1 FROM product_decision_requests binding
            WHERE binding.gate_id=gate.id AND binding.project_id=gate.project_id)
    """,
    "invalid_product_decision_requests": """
        SELECT count(*) FROM product_decision_requests binding WHERE NOT EXISTS (
            SELECT 1 FROM work_gates gate
            JOIN decision_documents document ON document.id=binding.document_id
                AND document.project_id=binding.project_id
            JOIN project_product_decision_domains domain
                ON domain.project_id=binding.project_id AND domain.domain_id=document.domain_id
            WHERE gate.id=binding.gate_id AND gate.project_id=binding.project_id
                AND binding.backfilled_revision_count<=document.current_revision+1
                AND document.created_at=gate.created_at
                AND document.current_revision=jsonb_array_length(gate.question_revisions)
                    + CASE WHEN gate.resolved_at IS NULL THEN 0 ELSE 1 END
                AND document.updated_at=coalesce(gate.resolved_at,
                    CASE WHEN pg_input_is_valid(gate.question_revisions->-1->>'created_at',
                                                'timestamp with time zone')
                         THEN (gate.question_revisions->-1->>'created_at')::timestamptz END,
                    gate.created_at))
    """,
    "invalid_product_decision_revision_sources": _EXPECTED + f"""
        SELECT count(*) FROM decision_revisions revision
        JOIN product_decision_requests binding ON binding.document_id=revision.document_id
        WHERE NOT EXISTS (SELECT 1 FROM expected WHERE {_SOURCE_MATCH})
    """,
    "missing_product_decision_revision_sources": _EXPECTED + f"""
        SELECT count(*) FROM expected WHERE NOT EXISTS (
            SELECT 1 FROM decision_revisions revision WHERE {_SOURCE_MATCH})
    """,
    "unknown_decision_models_without_product_source": """
        SELECT count(*) FROM decision_revisions revision
        WHERE author_kind='agent' AND model_id IS NULL AND NOT EXISTS (
            SELECT 1 FROM product_decision_requests binding
            WHERE binding.document_id=revision.document_id
                AND revision.revision<binding.backfilled_revision_count)
    """,
    "fresh_product_decision_revisions_without_receipts": """
        SELECT count(*) FROM product_decision_requests binding
        JOIN decision_revisions revision ON revision.document_id=binding.document_id
        WHERE revision.revision>=binding.backfilled_revision_count AND NOT EXISTS (
            SELECT 1 FROM client_operations receipt
            WHERE receipt.project_id=binding.project_id AND receipt.state='completed'
                AND receipt.mutation_applied
                AND receipt.operation_kind IN ('request_human_input','resolve_human_input')
                AND receipt.response_body->>'id'=binding.gate_id::text
                AND receipt.response_body#>>'{decision,document_id}'=revision.document_id::text
                AND receipt.response_body#>'{decision,revision}'=to_jsonb(revision.revision))
    """,
    "invalid_product_decision_receipt_metadata": f"""
        SELECT count(*) FROM client_operations receipt
        WHERE receipt.operation_kind IN ('request_human_input','resolve_human_input')
          AND receipt.state='completed' AND receipt.response_body ? 'decision'
          AND NOT EXISTS (
            SELECT 1 FROM product_decision_requests binding
            JOIN decision_revisions source ON source.document_id=binding.document_id
                AND source.project_id=binding.project_id
            WHERE binding.project_id=receipt.project_id
                AND receipt.response_body->>'id'=binding.gate_id::text
                AND receipt.response_body#>>'{{decision,document_id}}'=source.document_id::text
                AND receipt.response_body#>'{{decision,revision}}'=to_jsonb(source.revision)
                AND receipt.response_body#>>'{{decision,sha256}}'=source.sha256
                AND receipt.response_body#>'{{decision,metadata}}'={_METADATA}
                AND source.revision=coalesce((receipt.response_body->>'question_version')::int,1)
                    - CASE WHEN receipt.operation_kind='request_human_input' THEN 1 ELSE 0 END
          )
    """,
    "unwitnessed_product_decision_metadata": """
        SELECT count(*) FROM product_decision_requests binding
        JOIN work_gates gate ON gate.id=binding.gate_id
        JOIN decision_revisions source ON source.document_id=binding.document_id
        WHERE NOT EXISTS (SELECT 1 FROM client_operations receipt
            WHERE receipt.project_id=binding.project_id AND receipt.state='completed'
                AND receipt.operation_kind IN ('request_human_input','resolve_human_input')
                AND receipt.response_body#>>'{decision,document_id}'=source.document_id::text
                AND receipt.response_body#>'{decision,revision}'=to_jsonb(source.revision))
          AND (
            EXISTS (SELECT 1 FROM decision_relationships WHERE revision_id=source.id)
            OR EXISTS (SELECT 1 FROM decision_dependencies WHERE revision_id=source.id)
            OR EXISTS (SELECT 1 FROM decision_supersession_details WHERE revision_id=source.id)
            OR EXISTS (SELECT 1 FROM decision_artifact_links WHERE revision_id=source.id)
            OR EXISTS (SELECT 1 FROM decision_work_item_links WHERE revision_id=source.id
                AND work_item_id<>gate.work_item_id))
    """,
}


def has_product_revision_source(revision: str = "revision") -> str:
    """Creation is witnessed by immutable question history, not a synthetic receipt."""
    return f"""EXISTS (SELECT 1 FROM product_decision_requests binding
        WHERE binding.document_id={revision}.document_id
            AND binding.project_id={revision}.project_id)"""


def product_revision_receipt(revision: str, receipt: str = "receipt") -> str:
    return f"""({receipt}.operation_kind IN ('request_human_input','resolve_human_input')
        AND {receipt}.response_body#>>'{{decision,document_id}}'={revision}.document_id::text
        AND {receipt}.response_body#>'{{decision,revision}}'=to_jsonb({revision}.revision))"""
