"""Restore-time checks for dependency pins and immutable reassessment evidence."""

from mnemonic_api.impact_disposition_integrity import (
    BATCH_DISPOSITION_FINDINGS,
    DISPOSITION_RECEIPT_ROWS,
)
from mnemonic_api.impact_finding_integrity import disposition_receipt_contains
from mnemonic_api.impact_snapshot_integrity import SNAPSHOT_FINDINGS

_IDENTIFIER = (
    "domain.prefix||'-'||lpad(document.ordinal::text,greatest(5,length(document.ordinal::text)),'0')"
)
_REFERENCE = _IDENTIFIER + "||'-'||revision.revision::text"
_SUPERSESSION_FIELDS = "'extent',edge.extent,'reason',edge.reason"


def _pins(table: str, extra: str) -> str:
    return f"""
        coalesce((SELECT jsonb_agg(item ORDER BY identifier, number) FROM (
            SELECT {_IDENTIFIER} AS identifier, revision.revision AS number,
                jsonb_build_object('decision_id',{_IDENTIFIER},'revision',revision.revision,
                                   {extra}) AS item
            FROM {table} edge
            JOIN decision_revisions revision ON revision.id=edge.target_revision_id
            JOIN decision_documents document ON document.id=revision.document_id
            JOIN knowledge_domains domain ON domain.id=document.domain_id
            WHERE edge.revision_id=source.id
        ) items), '[]'::jsonb)
    """


IMPACT_FINDINGS = {
    "invalid_decision_dependency_pins": """
        SELECT count(*) FROM decision_dependencies edge
        JOIN decision_revisions source ON source.id=edge.revision_id
        JOIN decision_revisions target ON target.id=edge.target_revision_id
        WHERE source.document_id=target.document_id
    """,
    "invalid_decision_supersession_details": """
        SELECT count(*) FROM decision_supersession_details edge
        JOIN decision_revisions source ON source.id=edge.revision_id
        JOIN decision_revisions target ON target.id=edge.target_revision_id
        JOIN decision_documents source_doc ON source_doc.id=source.document_id
        JOIN decision_documents target_doc ON target_doc.id=target.document_id
        WHERE source.document_id=target.document_id OR source_doc.domain_id<>target_doc.domain_id
            OR NOT EXISTS (SELECT 1 FROM decision_relationships old_edge
                WHERE old_edge.revision_id=edge.revision_id AND old_edge.kind='supersedes'
                  AND old_edge.target_document_id=target.document_id)
    """,
    "invalid_decision_pinned_receipts": f"""
        SELECT count(*) FROM client_operations receipt
        JOIN decision_revisions source ON source.project_id=receipt.project_id
            AND receipt.response_body#>>'{{decision,id}}'=source.document_id::text
            AND receipt.response_body#>'{{decision,revision}}'=to_jsonb(source.revision)
        WHERE receipt.operation_kind='put_decision' AND receipt.state='completed' AND (
            coalesce(receipt.response_body#>'{{decision,depends_on}}','[]'::jsonb)
                IS DISTINCT FROM {_pins("decision_dependencies", "'rationale',edge.rationale")}
            OR coalesce(receipt.response_body#>'{{decision,supersession_details}}','[]'::jsonb)
                IS DISTINCT FROM {_pins("decision_supersession_details", _SUPERSESSION_FIELDS)}
        )
    """,
    "invalid_decision_impact_episodes": """
        SELECT count(*) FROM decision_impact_assessments assessment
        JOIN decision_revisions before ON before.id=assessment.origin_revision_id
        JOIN decision_revisions after ON after.id=assessment.replacement_revision_id
        WHERE (assessment.trigger='revision' AND (before.document_id<>after.document_id
                  OR after.revision<>before.revision+1))
           OR (assessment.trigger='supersession' AND NOT EXISTS (
                SELECT 1 FROM decision_relationships edge WHERE edge.revision_id=after.id
                AND edge.kind='supersedes' AND edge.target_document_id=before.document_id))
           OR assessment.diff->>'before_sha256' IS DISTINCT FROM before.sha256
           OR assessment.diff->>'after_sha256' IS DISTINCT FROM after.sha256
           OR NOT EXISTS (SELECT 1 FROM client_operations receipt
                WHERE receipt.project_id=assessment.project_id
                AND receipt.state='completed' AND receipt.mutation_applied
                AND ((receipt.operation_kind='put_decision'
                    AND receipt.response_body#>>'{decision,id}'=after.document_id::text)
                  OR (receipt.operation_kind IN ('request_human_input','resolve_human_input')
                    AND receipt.response_body#>>'{decision,document_id}'=after.document_id::text))
                AND receipt.response_body#>'{decision,revision}'=to_jsonb(after.revision))
    """,
    "invalid_decision_impact_candidate_snapshots": f"""
        SELECT count(*) FROM decision_impact_assessments assessment,
            jsonb_array_elements(assessment.candidates) item
        WHERE NOT EXISTS (
            SELECT 1 FROM decision_revisions revision
            JOIN decision_documents document ON document.id=revision.document_id
            JOIN knowledge_domains domain ON domain.id=document.domain_id
            WHERE revision.project_id=assessment.project_id
              AND item @> jsonb_build_object('reference',{_REFERENCE},
                  'decision_id',{_IDENTIFIER},'revision',revision.revision,
                  'title',revision.title,'state',revision.state)
        ) OR jsonb_typeof(item->'path') IS DISTINCT FROM 'array'
          OR item->'path'->>-1 IS DISTINCT FROM item->>'reference'
          OR item->>'relationship' NOT IN (
              'origin','depends-on','depends-on-prerequisite','related-only')
          OR (item ? 'related_direction' AND item->'related_direction'<>'null'::jsonb AND (
              item->>'relationship'<>'related-only'
              OR item->>'related_direction' NOT IN ('incoming','outgoing')))
    """,
    "invalid_decision_impact_dispositions": f"""
        WITH receipt_rows AS ({DISPOSITION_RECEIPT_ROWS})
        SELECT count(*) FROM decision_impact_dispositions disposition
        JOIN decision_impact_assessments assessment ON assessment.id=disposition.assessment_id
        WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(assessment.candidates) candidate
            WHERE candidate->>'reference'=disposition.candidate_reference)
        OR disposition.version<>(SELECT count(*) FROM decision_impact_dispositions previous
            WHERE previous.assessment_id=disposition.assessment_id
            AND previous.version<=disposition.version)
        OR 1<>(SELECT count(*) FROM receipt_rows receipt
            WHERE receipt.project_id=disposition.project_id
            AND receipt.response_status=201 AND receipt.mutation_applied
            AND {disposition_receipt_contains('receipt.item')})
    """,
    "invalid_decision_impact_disposition_receipts": f"""
        SELECT count(*) FROM client_operations receipt
        WHERE receipt.operation_kind='assess_decision_impact' AND receipt.state='completed'
        AND NOT EXISTS (SELECT 1 FROM decision_impact_dispositions disposition
            WHERE disposition.project_id=receipt.project_id
              AND {disposition_receipt_contains('receipt.response_body')}
              AND receipt.response_status=201 AND receipt.mutation_applied)
    """,
}

IMPACT_FINDINGS.update(SNAPSHOT_FINDINGS)
IMPACT_FINDINGS.update(BATCH_DISPOSITION_FINDINGS)
