"""Reconstruct deleted edges only for immutable-history integrity checks.

Application reads never use this reconstruction. The live graph loses its edges;
the private deletion journal lets audits continue comparing exact historical
receipts, completion provenance and assessment snapshots after a human deletion.
"""

EDGE_TABLES = (
    "decision_relationships", "decision_dependencies", "decision_supersession_details",
    "decision_supersession_reuses", "decision_work_item_links", "decision_artifact_links",
    "decision_scopes", "decision_commit_mappings",
)
_LIVE_EDGES = " UNION ALL ".join(
    f"SELECT to_jsonb(edge) AS value FROM {table} edge" for table in EDGE_TABLES
)


def historical_graph_checks(checks: dict[str, str]) -> dict[str, str]:
    result = {}
    for name, query in checks.items():
        tables = [table for table in EDGE_TABLES if table in query]
        if not tables:
            result[name] = query
            continue
        # A nonrecursive CTE's same-name reference resolves to the real relation.
        # Composite types also resolve to the real table, preserving exact types.
        sources = ",\n".join(f"""{table} AS (
            SELECT * FROM {table}
            UNION ALL
            SELECT (jsonb_populate_record(NULL::{table}, archive.row_data)).*
            FROM knowledge_deleted_edges archive WHERE archive.table_name='{table}'
        )""" for table in tables)
        result[name] = f"WITH {sources} SELECT * FROM ({query}) historical_check"
    return result


DELETION_FINDINGS = {
    "live_graph_edges_for_deleted_decisions": f"""
        WITH edges AS ({_LIVE_EDGES}), deleted_documents AS (
            SELECT id FROM decision_documents WHERE deleted_at IS NOT NULL
        ), deleted_revisions AS (
            SELECT id FROM decision_revisions
            WHERE document_id IN (SELECT id FROM deleted_documents)
        ), deleted_scopes AS (
            SELECT id FROM decision_scopes
            WHERE document_id IN (SELECT id FROM deleted_documents)
        )
        SELECT count(*) FROM edges WHERE
            (value->>'document_id')::uuid IN (SELECT id FROM deleted_documents)
            OR (value->>'target_document_id')::uuid IN (SELECT id FROM deleted_documents)
            OR (value->>'revision_id')::uuid IN (SELECT id FROM deleted_revisions)
            OR (value->>'target_revision_id')::uuid IN (SELECT id FROM deleted_revisions)
            OR (value->>'governing_revision_id')::uuid IN (SELECT id FROM deleted_revisions)
            OR (value->>'scope_id')::uuid IN (SELECT id FROM deleted_scopes)
    """,
    "invalid_knowledge_deletion_archives": """
        SELECT count(*) FROM knowledge_deleted_edges archive
        LEFT JOIN client_operations receipt ON receipt.id=archive.receipt_id
        WHERE receipt.id IS NULL OR receipt.project_id<>archive.project_id
          OR receipt.state<>'completed' OR NOT receipt.mutation_applied
          OR receipt.operation_kind NOT IN ('delete_knowledge_domain','delete_decision')
          OR receipt.response_status<>200
          OR archive.row_data->>'project_id' IS DISTINCT FROM archive.project_id::text
          OR archive.table_name NOT IN (
              'decision_relationships','decision_dependencies','decision_supersession_details',
              'decision_supersession_reuses','decision_work_item_links','decision_artifact_links',
              'decision_scopes','decision_commit_mappings')
          OR CASE WHEN pg_input_is_valid(receipt.response_body->>'deleted_at',
                                        'timestamp with time zone')
                  THEN (receipt.response_body->>'deleted_at')::timestamptz END
              IS DISTINCT FROM archive.deleted_at
    """,
    "invalid_knowledge_deletion_receipt_counts": """
        SELECT count(*) FROM client_operations receipt
        WHERE receipt.operation_kind IN ('delete_knowledge_domain','delete_decision')
          AND receipt.state='completed' AND (
            receipt.response_status<>200 OR NOT receipt.mutation_applied
            OR receipt.response_body->>'project_id' IS DISTINCT FROM receipt.project_id::text
            OR receipt.response_body->'relationship_count' IS DISTINCT FROM to_jsonb(
                (receipt.response_body->>'removed_relationship_count')::bigint
                + (receipt.response_body->>'affected_review_count')::bigint)
            OR receipt.response_body->'removed_relationship_count' IS DISTINCT FROM
                to_jsonb((SELECT count(*) FROM knowledge_deleted_edges archive
                          WHERE archive.receipt_id=receipt.id)))
    """,
    "knowledge_document_tombstones_without_receipts": """
        SELECT count(*) FROM decision_documents document
        WHERE document.deleted_at IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM client_operations receipt
            WHERE receipt.project_id=document.project_id AND receipt.state='completed'
              AND receipt.mutation_applied AND receipt.response_status=200
              AND ((receipt.operation_kind='delete_decision'
                    AND receipt.response_body->>'document_id'=document.id::text)
                OR (receipt.operation_kind='delete_knowledge_domain'
                    AND receipt.response_body->>'domain_id'=document.domain_id::text))
              AND CASE WHEN pg_input_is_valid(receipt.response_body->>'deleted_at',
                                             'timestamp with time zone')
                       THEN (receipt.response_body->>'deleted_at')::timestamptz END
                  =document.deleted_at)
    """,
    "knowledge_domain_tombstones_without_receipts": """
        SELECT count(*) FROM knowledge_domains domain
        WHERE domain.deleted_at IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM client_operations receipt
            WHERE receipt.project_id=domain.project_id AND receipt.state='completed'
              AND receipt.mutation_applied AND receipt.response_status=200
              AND receipt.operation_kind='delete_knowledge_domain'
              AND receipt.response_body->>'domain_id'=domain.id::text
              AND CASE WHEN pg_input_is_valid(receipt.response_body->>'deleted_at',
                                             'timestamp with time zone')
                       THEN (receipt.response_body->>'deleted_at')::timestamptz END
                  =domain.deleted_at)
    """,
    "live_documents_in_deleted_domains": """
        SELECT count(*) FROM decision_documents document
        JOIN knowledge_domains domain ON domain.id=document.domain_id
        WHERE domain.deleted_at IS NOT NULL AND document.deleted_at IS NULL
    """,
}
