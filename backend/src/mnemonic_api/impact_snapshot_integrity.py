"""Validate included impact evidence without pretending a bounded snapshot is exhaustive."""

_REFS = """
    WITH refs AS (
        SELECT revision.id,revision.project_id,revision.document_id,revision.revision,
            domain.prefix||'-'||lpad(document.ordinal::text,
                greatest(5,length(document.ordinal::text)),'0')||'-'||revision.revision AS ref
        FROM decision_revisions revision
        JOIN decision_documents document ON document.id=revision.document_id
        JOIN knowledge_domains domain ON domain.id=document.domain_id
    )
"""


def _array(value: str) -> str:
    return f"CASE WHEN jsonb_typeof({value})='array' THEN {value} ELSE '[]'::jsonb END"


_PATH = _array("candidate->'path'")
_SCOPES = _array("candidate->'scopes'")


def _resources(table: str, column: str, field: str) -> str:
    return f"""
        EXISTS (SELECT 1 FROM jsonb_array_elements_text({_array(f"candidate->'{field}'")}) item
            WHERE NOT EXISTS (SELECT 1 FROM {table} link
                WHERE link.revision_id=owned.id AND link.{column}::text=item))
    """


SNAPSHOT_FINDINGS = {
    "invalid_decision_impact_paths": _REFS
    + f"""
        SELECT count(*) FROM decision_impact_assessments assessment
        JOIN refs origin ON origin.id=assessment.origin_revision_id,
            jsonb_array_elements(assessment.candidates) candidate
        WHERE candidate->'path'->>0 IS DISTINCT FROM origin.ref
           OR (candidate->>'relationship'='origin' AND (
                candidate->>'reference' IS DISTINCT FROM origin.ref
                OR jsonb_array_length({_PATH})<>1))
           OR (candidate->>'relationship'<>'origin' AND jsonb_array_length({_PATH})<2)
           OR EXISTS (
                SELECT 1 FROM jsonb_array_elements_text({_PATH}) WITH ORDINALITY hop(ref,number)
                WHERE hop.number>1 AND NOT EXISTS (
                    SELECT 1 FROM refs previous,refs current
                    WHERE previous.project_id=assessment.project_id
                      AND current.project_id=assessment.project_id
                      AND previous.ref=candidate->'path'->>(hop.number::integer-2)
                      AND current.ref=hop.ref AND (
                        (candidate->>'relationship'='related-only'
                         AND hop.number=jsonb_array_length({_PATH}) AND EXISTS (
                            SELECT 1 FROM decision_relationships edge WHERE edge.kind='related'
                            AND ((edge.revision_id=previous.id
                                  AND edge.target_document_id=current.document_id)
                              OR (edge.revision_id=current.id
                                  AND edge.target_document_id=previous.document_id))))
                        OR (candidate->>'relationship'='depends-on-prerequisite'
                         AND hop.number=jsonb_array_length({_PATH}) AND EXISTS (
                            SELECT 1 FROM decision_dependencies edge
                            WHERE edge.revision_id=previous.id
                              AND edge.target_revision_id=current.id
                              AND candidate->>'rationale'=edge.rationale))
                        OR ((candidate->>'relationship' NOT IN (
                                'related-only','depends-on-prerequisite')
                             OR hop.number<jsonb_array_length({_PATH})) AND EXISTS (
                            SELECT 1 FROM decision_dependencies edge
                            WHERE edge.revision_id=current.id
                              AND edge.target_revision_id=previous.id
                              AND (hop.number<jsonb_array_length({_PATH})
                                OR candidate->>'rationale'=edge.rationale)))
                      )
                )
           )
    """,
    "invalid_decision_impact_scope_evidence": _REFS
    + f"""
        SELECT count(*) FROM decision_impact_assessments assessment,
            jsonb_array_elements(assessment.candidates) candidate,
            jsonb_array_elements({_SCOPES}) citation
        WHERE NOT EXISTS (
            SELECT 1 FROM refs owned JOIN decision_scopes scope
                ON scope.document_id=owned.document_id AND scope.revision=owned.revision
            WHERE owned.project_id=assessment.project_id AND owned.ref=candidate->>'reference'
              AND citation @> jsonb_build_object(
                  'id',scope.id,'revision',scope.revision,'commit_hash',scope.commit_hash,
                  'repository',scope.repository,'completion_report_id',scope.completion_report_id,
                  'work_item_id',scope.work_item_id,'agent_session_id',scope.agent_session_id)
              AND jsonb_typeof(citation->'merged_commit_hashes')='array'
              AND NOT EXISTS (
                  SELECT 1 FROM jsonb_array_elements_text(
                    {_array("citation->'merged_commit_hashes'")}) merged
                  WHERE NOT EXISTS (SELECT 1 FROM decision_commit_mappings mapping
                      WHERE mapping.scope_id=scope.id AND mapping.merged_commit_hash=merged))
        )
    """,
    "invalid_decision_impact_completion_citations": f"""
        SELECT count(*) FROM decision_impact_assessments assessment,
            jsonb_array_elements(assessment.candidates) candidate,
            jsonb_array_elements({_SCOPES}) citation
        WHERE (citation ? 'completion_citations' AND (
            jsonb_typeof(citation->'completion_citations') IS DISTINCT FROM 'array'
            OR jsonb_array_length({_array("citation->'completion_citations'")})>10))
          OR (citation ? 'completion_citations_truncated' AND
              jsonb_typeof(citation->'completion_citations_truncated')
                  IS DISTINCT FROM 'boolean')
          OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(
                {_array("citation->'completion_citations'")}) completion
            WHERE NOT EXISTS (
                SELECT 1 FROM job_completion_reports report
                WHERE report.project_id=assessment.project_id
                  AND completion @> jsonb_build_object(
                    'report_id',report.id,'work_item_id',report.work_item_id,
                    'actor_client',report.actor_client,'actor_session_id',report.actor_session_id,
                    'actor_model',report.actor_model)
                  AND CASE WHEN pg_input_is_valid(completion->>'created_at',
                           'timestamp with time zone')
                      THEN (completion->>'created_at')::timestamptz END=report.created_at
                  AND report.created_at<=assessment.created_at
                  AND report.decision_provenance @> jsonb_build_object(
                    'disposition','cited',
                    'citations',jsonb_build_array(jsonb_build_object(
                        'decision_id',candidate->>'decision_id','revision',candidate->'revision')),
                    'commits',jsonb_build_array(jsonb_build_object(
                        'repository',citation->>'repository','commit_hash',citation->>'commit_hash')))
            )
          )
    """,
    "invalid_decision_impact_resource_evidence": _REFS
    + f"""
        SELECT count(*) FROM decision_impact_assessments assessment,
            jsonb_array_elements(assessment.candidates) candidate,refs owned
        WHERE owned.project_id=assessment.project_id AND owned.ref=candidate->>'reference' AND (
            jsonb_typeof(candidate->'scopes') IS DISTINCT FROM 'array'
            OR jsonb_typeof(candidate->'related_work_item_ids') IS DISTINCT FROM 'array'
            OR jsonb_typeof(candidate->'related_artifact_ids') IS DISTINCT FROM 'array'
            OR {_resources("decision_work_item_links", "work_item_id", "related_work_item_ids")}
            OR {_resources("decision_artifact_links", "artifact_id", "related_artifact_ids")}
        )
    """,
}
