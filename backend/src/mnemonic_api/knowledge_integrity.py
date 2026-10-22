"""Knowledge invariants also checked when privileged backup restore bypasses triggers."""


def _timestamp_matches(value: str, column: str) -> str:
    return (
        f"CASE WHEN pg_input_is_valid({value}, 'timestamp with time zone') "
        f"THEN ({value})::timestamptz END IS NOT DISTINCT FROM {column}"
    )


_IDENTIFIER = (
    "domain.prefix || '-' || lpad(document.ordinal::text, "
    "greatest(5,length(document.ordinal::text)), '0')"
)
_TARGET_IDENTIFIER = (
    "target_domain.prefix || '-' || lpad(target.ordinal::text, "
    "greatest(5,length(target.ordinal::text)), '0')"
)


def _decision_links(kind: str) -> str:
    return f"""
        coalesce((SELECT jsonb_agg(identifier ORDER BY identifier) FROM (
            SELECT {_TARGET_IDENTIFIER} AS identifier
            FROM decision_relationships edge
            JOIN decision_documents target ON target.id=edge.target_document_id
            JOIN knowledge_domains target_domain ON target_domain.id=target.domain_id
            WHERE edge.revision_id=revision.id AND edge.kind='{kind}'
        ) links), '[]'::jsonb)
    """


def _resource_links(table: str, column: str) -> str:
    return f"""
        coalesce((SELECT jsonb_agg({column} ORDER BY {column}) FROM {table}
                  WHERE revision_id=revision.id), '[]'::jsonb)
    """


_DOMAIN_MATCH = f"""
    receipt.response_body @> jsonb_build_object(
        'id', domain.id, 'project_id', domain.project_id, 'prefix', domain.prefix)
    AND {_timestamp_matches("receipt.response_body->>'created_at'", 'domain.created_at')}
"""
# Labels, incoming edges and scope counts are live projections. Compare only the
# immutable identity, revision metadata and outgoing links retained by the receipt.
# The table CHECK constraints independently tie the content bytes to size and hash.
_REVISION_MATCH = f"""
    receipt.response_body->'decision' @> jsonb_build_object(
        'id', document.id, 'project_id', revision.project_id,
        'domain_id', domain.id, 'prefix', domain.prefix, 'ordinal', document.ordinal,
        'decision_id', {_IDENTIFIER},
        'reference', {_IDENTIFIER} || '-' || revision.revision::text,
        'revision', revision.revision, 'title', revision.title, 'state', revision.state,
        'media_type', revision.media_type, 'size_bytes', revision.size_bytes,
        'sha256', revision.sha256, 'author_kind', revision.author_kind,
        'agent_session_id', revision.agent_session_id, 'model_id', revision.model_id)
    AND {_timestamp_matches(
        "receipt.response_body#>>'{decision,created_at}'", 'document.created_at')}
    AND {_timestamp_matches(
        "receipt.response_body#>>'{decision,updated_at}'", 'revision.created_at')}
    AND receipt.response_body#>'{{decision,related_decision_ids}}' = {_decision_links('related')}
    AND receipt.response_body#>'{{decision,supersedes}}' = {_decision_links('supersedes')}
    AND receipt.response_body#>'{{decision,related_work_item_ids}}' =
        {_resource_links('decision_work_item_links', 'work_item_id')}
    AND receipt.response_body#>'{{decision,related_artifact_ids}}' =
        {_resource_links('decision_artifact_links', 'artifact_id')}
"""
_SCOPE_MATCH = f"""
    receipt.response_body->'scope' @> jsonb_build_object(
        'id', scope.id, 'project_id', scope.project_id, 'document_id', document.id,
        'decision_id', {_IDENTIFIER}, 'revision', scope.revision,
        'commit_hash', scope.commit_hash, 'work_item_id', scope.work_item_id,
        'agent_session_id', scope.agent_session_id)
    AND {_timestamp_matches("receipt.response_body#>>'{scope,created_at}'", 'scope.created_at')}
"""


KNOWLEDGE_FINDINGS = {
    "invalid_knowledge_ordinals": """
        SELECT count(*) FROM knowledge_domains domain
        WHERE domain.next_ordinal <> 1 + coalesce((
            SELECT max(ordinal) FROM decision_documents WHERE domain_id=domain.id
        ), 0)
    """,
    "invalid_decision_revision_history": """
        SELECT count(*) FROM decision_documents document
        WHERE document.current_revision + 1 <> (
            SELECT count(*) FROM decision_revisions WHERE document_id=document.id
        ) OR document.current_revision <> (
            SELECT max(revision) FROM decision_revisions WHERE document_id=document.id
        ) OR 0 <> (
            SELECT min(revision) FROM decision_revisions WHERE document_id=document.id
        )
    """,
    "invalid_decision_relationships": """
        SELECT count(*) FROM decision_relationships edge
        JOIN decision_revisions revision ON revision.id=edge.revision_id
        JOIN decision_documents source ON source.id=revision.document_id
        JOIN decision_documents target ON target.id=edge.target_document_id
        WHERE source.id=target.id
           OR (edge.kind='supersedes' AND source.domain_id<>target.domain_id)
    """,
    "decision_supersession_cycles": """
        WITH RECURSIVE edges AS (
            SELECT revision.document_id AS source, edge.target_document_id AS target
            FROM decision_relationships edge
            JOIN decision_revisions revision ON revision.id=edge.revision_id
            JOIN decision_documents document ON document.id=revision.document_id
                AND document.current_revision=revision.revision
            WHERE edge.kind='supersedes'
        ), reachable(source, target) AS (
            SELECT source, target FROM edges
            UNION
            SELECT reachable.source, edges.target FROM reachable
            JOIN edges ON reachable.target=edges.source
        ) SELECT count(*) FROM reachable WHERE source=target
    """,
    "invalid_knowledge_domain_receipts": f"""
        SELECT count(*) FROM client_operations receipt
        WHERE receipt.operation_kind IN ('create_knowledge_domain','rename_knowledge_domain')
          AND receipt.state='completed' AND NOT EXISTS (
            SELECT 1 FROM knowledge_domains domain
            WHERE domain.project_id=receipt.project_id AND {_DOMAIN_MATCH}
              AND receipt.mutation_applied
              AND receipt.response_status=CASE
                  WHEN receipt.operation_kind='create_knowledge_domain' THEN 201 ELSE 200 END
              AND jsonb_typeof(receipt.response_body->'version')='number'
              AND CASE WHEN receipt.operation_kind='create_knowledge_domain'
                  THEN receipt.response_body->'version'='1'::jsonb
                  ELSE receipt.response_body->'version' BETWEEN '1'::jsonb
                      AND to_jsonb(domain.version) END
          )
    """,
    "knowledge_domains_without_creation_receipts": """
        SELECT count(*) FROM knowledge_domains domain WHERE NOT EXISTS (
            SELECT 1 FROM client_operations receipt
            WHERE receipt.project_id=domain.project_id
              AND receipt.operation_kind='create_knowledge_domain' AND receipt.state='completed'
              AND receipt.response_body->>'id'=domain.id::text
        )
    """,
    "invalid_decision_revision_receipts": f"""
        SELECT count(*) FROM client_operations receipt
        WHERE receipt.operation_kind='put_decision' AND receipt.state='completed'
          AND NOT EXISTS (
            SELECT 1 FROM decision_revisions revision
            JOIN decision_documents document ON document.id=revision.document_id
            JOIN knowledge_domains domain ON domain.id=document.domain_id
            WHERE revision.project_id=receipt.project_id AND {_REVISION_MATCH}
              AND receipt.response_status=200
              AND receipt.response_body->'created'=to_jsonb(receipt.mutation_applied)
          )
    """,
    "decision_revisions_without_creation_receipts": """
        SELECT count(*) FROM decision_revisions revision WHERE NOT EXISTS (
            SELECT 1 FROM client_operations receipt
            WHERE receipt.project_id=revision.project_id
              AND receipt.operation_kind='put_decision' AND receipt.state='completed'
              AND receipt.mutation_applied
              AND receipt.response_body#>>'{decision,id}'=revision.document_id::text
              AND receipt.response_body#>'{decision,revision}'=to_jsonb(revision.revision)
        )
    """,
    "invalid_decision_scope_receipts": f"""
        SELECT count(*) FROM client_operations receipt
        WHERE receipt.operation_kind='append_decision_scope' AND receipt.state='completed'
          AND NOT EXISTS (
            SELECT 1 FROM decision_scopes scope
            JOIN decision_documents document ON document.id=scope.document_id
            JOIN knowledge_domains domain ON domain.id=document.domain_id
            WHERE scope.project_id=receipt.project_id AND {_SCOPE_MATCH}
              AND receipt.response_status=200
              AND receipt.response_body->'created'=to_jsonb(receipt.mutation_applied)
          )
    """,
    "decision_scopes_without_creation_receipts": """
        SELECT count(*) FROM decision_scopes scope WHERE NOT EXISTS (
            SELECT 1 FROM client_operations receipt
            WHERE receipt.project_id=scope.project_id
              AND receipt.operation_kind='append_decision_scope' AND receipt.state='completed'
              AND receipt.mutation_applied AND receipt.response_body#>>'{scope,id}'=scope.id::text
        )
    """,
}
