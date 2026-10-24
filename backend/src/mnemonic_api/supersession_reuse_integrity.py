"""Validate immutable declarations without inferring historical semantic equivalence."""


def invalid_declarations(prefix: str = "") -> str:
    return f"""
        SELECT reuse.revision_id,reuse.target_revision_id
        FROM {prefix}decision_supersession_reuses reuse
        JOIN {prefix}decision_revisions source ON source.id=reuse.revision_id
        JOIN {prefix}decision_revisions target ON target.id=reuse.target_revision_id
        JOIN {prefix}decision_revisions governing ON governing.id=reuse.governing_revision_id
        LEFT JOIN {prefix}decision_impact_assessments prior
            ON prior.origin_revision_id=target.id AND prior.replacement_revision_id=governing.id
            AND prior.trigger='supersession'
        WHERE source.project_id<>reuse.project_id OR target.project_id<>reuse.project_id
           OR governing.project_id<>reuse.project_id
           OR source.document_id<>governing.document_id OR source.revision<=governing.revision
           OR source.document_id=target.document_id OR prior.id IS NULL
           OR EXISTS (
               SELECT 1 FROM {prefix}decision_impact_assessments newer
               JOIN {prefix}decision_revisions replaced ON replaced.id=newer.origin_revision_id
               JOIN {prefix}decision_revisions replacement
                   ON replacement.id=newer.replacement_revision_id
               WHERE newer.trigger='supersession' AND replaced.document_id=target.document_id
                 AND replacement.document_id=source.document_id
                 AND replacement.revision>governing.revision
                 AND replacement.revision<source.revision
           ) OR EXISTS (
               SELECT 1 FROM {prefix}decision_revisions step
               WHERE step.document_id=source.document_id
                 AND step.revision BETWEEN governing.revision AND source.revision
                 AND (NOT EXISTS (
                     SELECT 1 FROM {prefix}decision_relationships declared
                     WHERE declared.revision_id=step.id AND declared.kind='supersedes'
                       AND declared.target_document_id=target.document_id
                 ) OR COALESCE((
                     SELECT jsonb_agg(jsonb_build_array(detail.target_revision_id,
                                                       detail.extent,detail.reason))
                     FROM {prefix}decision_supersession_details detail
                     JOIN {prefix}decision_revisions pinned ON pinned.id=detail.target_revision_id
                     WHERE detail.revision_id=step.id AND pinned.document_id=target.document_id
                 ),'[]'::jsonb) IS DISTINCT FROM CASE WHEN prior.extent IS NULL THEN '[]'::jsonb
                     ELSE jsonb_build_array(jsonb_build_array(target.id,prior.extent,prior.reason))
                     END)
           )
    """


SUPERSESSION_REUSE_FINDINGS = {
    "invalid_supersession_reuse_declarations":
        f"SELECT count(*) FROM ({invalid_declarations()}) invalid",
    "duplicate_supersession_reuse_targets": """
        SELECT count(*) FROM (
            SELECT reuse.revision_id,target.document_id
            FROM decision_supersession_reuses reuse
            JOIN decision_revisions target ON target.id=reuse.target_revision_id
            GROUP BY reuse.revision_id,target.document_id HAVING count(*)>1
        ) duplicates
    """,
    "invalid_supersession_reuse_receipts": """
        SELECT count(*) FROM client_operations receipt
        JOIN decision_revisions source ON source.project_id=receipt.project_id
            AND receipt.response_body#>>'{decision,id}'=source.document_id::text
            AND receipt.response_body#>'{decision,revision}'=to_jsonb(source.revision)
        WHERE receipt.operation_kind='put_decision' AND receipt.state='completed'
            AND COALESCE(receipt.response_body#>'{decision,change,unchanged_supersessions}',
                         '[]'::jsonb) IS DISTINCT FROM COALESCE((
                SELECT jsonb_agg(item ORDER BY item->>'target_reference') FROM (
                    SELECT jsonb_build_object(
                        'target_reference',target_domain.prefix||'-'||lpad(
                            target_doc.ordinal::text,greatest(5,length(target_doc.ordinal::text)),
                            '0')||'-'||target.revision::text,
                        'governing_reference',replacement_domain.prefix||'-'||lpad(
                            replacement_doc.ordinal::text,
                            greatest(5,length(replacement_doc.ordinal::text)),
                            '0')||'-'||governing.revision::text,
                        'reason',reuse.reason) item
                    FROM decision_supersession_reuses reuse
                    JOIN decision_revisions target ON target.id=reuse.target_revision_id
                    JOIN decision_documents target_doc ON target_doc.id=target.document_id
                    JOIN knowledge_domains target_domain ON target_domain.id=target_doc.domain_id
                    JOIN decision_revisions governing ON governing.id=reuse.governing_revision_id
                    JOIN decision_documents replacement_doc
                        ON replacement_doc.id=governing.document_id
                    JOIN knowledge_domains replacement_domain
                        ON replacement_domain.id=replacement_doc.domain_id
                    WHERE reuse.revision_id=source.id
                ) declarations
            ),'[]'::jsonb)
    """,
}
