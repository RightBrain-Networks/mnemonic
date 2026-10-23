"""Audit completion-origin scopes and commit mappings after privileged restore."""

PROVENANCE_FINDINGS = {
    "changed_completion_decision_declarations": """
        SELECT count(*) FROM job_completion_reports report
        JOIN client_operations receipt ON receipt.project_id=report.project_id
            AND receipt.state='completed'
            AND receipt.operation_kind IN ('complete_work','update_work')
            AND receipt.response_body#>>'{job_completion_report,id}'=report.id::text
        WHERE nullif(receipt.response_body#>'{job_completion_report,decision_provenance}',
                     'null'::jsonb) IS DISTINCT FROM
              nullif(report.decision_provenance,'null'::jsonb)
    """,
    "invalid_completion_decision_scopes": """
        SELECT count(*) FROM decision_scopes s
        JOIN decision_documents d ON d.id=s.document_id
        JOIN knowledge_domains domain ON domain.id=d.domain_id
        LEFT JOIN job_completion_reports r ON r.id=s.completion_report_id
        WHERE s.completion_report_id IS NOT NULL AND (
            r.id IS NULL OR r.project_id<>s.project_id OR r.work_item_id<>s.work_item_id
            OR r.actor_session_id IS DISTINCT FROM s.agent_session_id
            OR r.decision_provenance->>'disposition' IS DISTINCT FROM 'cited'
            OR NOT coalesce(r.decision_provenance->'citations' @> jsonb_build_array(
                jsonb_build_object('decision_id', domain.prefix || '-' ||
                    lpad(d.ordinal::text,greatest(5,length(d.ordinal::text)),'0'),
                    'revision',s.revision)),false)
            OR NOT coalesce(r.decision_provenance->'commits' @> jsonb_build_array(
                jsonb_build_object('repository',s.repository,'commit_hash',s.commit_hash)),false)
            OR NOT EXISTS (SELECT 1 FROM client_operations op
                WHERE op.project_id=r.project_id AND op.state='completed'
                  AND op.operation_kind IN ('complete_work','update_work')
                  AND op.response_body#>>'{job_completion_report,id}'=r.id::text
                  AND op.response_body#>'{job_completion_report,decision_provenance}'
                      =r.decision_provenance)
        )
    """,
    "missing_completion_decision_scopes": """
        SELECT count(*) FROM job_completion_reports r
        CROSS JOIN LATERAL jsonb_array_elements(
            coalesce(r.decision_provenance->'citations','[]'::jsonb)) citation
        CROSS JOIN LATERAL jsonb_array_elements(
            coalesce(r.decision_provenance->'commits','[]'::jsonb)) implementation
        WHERE r.decision_provenance->>'disposition'='cited' AND NOT EXISTS (
            SELECT 1 FROM decision_scopes s
            JOIN decision_documents d ON d.id=s.document_id
            JOIN knowledge_domains domain ON domain.id=d.domain_id
            WHERE s.project_id=r.project_id
              AND s.repository=implementation->>'repository'
              AND s.commit_hash=implementation->>'commit_hash'
              AND to_jsonb(s.revision)=citation->'revision'
              AND domain.prefix || '-' ||
                  lpad(d.ordinal::text,greatest(5,length(d.ordinal::text)),'0')
                  =citation->>'decision_id'
        )
    """,
    "invalid_decision_commit_mappings": """
        SELECT count(*) FROM decision_commit_mappings m
        LEFT JOIN decision_scopes s ON s.id=m.scope_id
        WHERE s.id IS NULL OR s.project_id<>m.project_id
           OR s.repository IS DISTINCT FROM m.repository
           OR s.commit_hash<>m.original_commit_hash
           OR m.original_commit_hash=m.merged_commit_hash
    """,
    "decision_commit_mappings_without_receipts": """
        SELECT count(*) FROM decision_commit_mappings m WHERE NOT EXISTS (
            SELECT 1 FROM client_operations op
            WHERE op.project_id=m.project_id AND op.state='completed' AND op.mutation_applied
              AND op.operation_kind='reconcile_decision_scope'
              AND op.response_body->>'repository'=m.repository
              AND op.response_body->>'merged_commit_hash'=m.merged_commit_hash
              AND op.response_body->'scope_ids' @> jsonb_build_array(m.scope_id)
        )
    """,
    "invalid_decision_reconciliation_receipts": """
        SELECT count(*) FROM client_operations receipt
        WHERE receipt.operation_kind='reconcile_decision_scope' AND receipt.state='completed'
          AND (receipt.response_status<>200
            OR receipt.response_body->>'project_id'<>receipt.project_id::text
            OR receipt.response_body->'created'<>to_jsonb(receipt.mutation_applied)
            OR jsonb_array_length(receipt.response_body->'scope_ids')=0
            OR EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(receipt.response_body->'scope_ids') item
                WHERE NOT EXISTS (
                    SELECT 1 FROM decision_scopes scope
                    WHERE scope.id::text=item AND scope.project_id=receipt.project_id
                      AND scope.repository=receipt.response_body->>'repository'
                      AND (scope.commit_hash=receipt.response_body->>'original_commit_hash'
                        OR EXISTS (SELECT 1 FROM decision_commit_mappings mapping
                            WHERE mapping.scope_id=scope.id AND mapping.merged_commit_hash
                                =receipt.response_body->>'original_commit_hash'))
                      AND (scope.commit_hash=receipt.response_body->>'merged_commit_hash'
                        OR EXISTS (SELECT 1 FROM decision_commit_mappings mapping
                            WHERE mapping.scope_id=scope.id AND mapping.merged_commit_hash
                                =receipt.response_body->>'merged_commit_hash'))
                )
            ))
    """,
}
