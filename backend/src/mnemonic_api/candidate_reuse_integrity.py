"""Validate immutable candidate reuse against captured evidence and reviewed source rows."""

COVERAGE_KEYS = (
    "traversal_complete", "provenance_complete", "max_depth", "candidate_limit",
    "visited_revisions", "reasons", "warnings", "related_semantics", "notes", "path_policy",
)
CANDIDATE_KEYS = (
    "decision_id", "reference", "revision", "title", "state", "is_current", "relationship",
    "path", "rationale", "scopes", "related_work_item_ids", "related_artifact_ids",
    "evidence_truncated",
)


def invalid_candidate_reuses(prefix: str = "") -> str:
    coverage_keys = ",".join(f"'{key}'" for key in COVERAGE_KEYS)
    candidate_keys = ",".join(f"'{key}'" for key in CANDIDATE_KEYS)
    return f"""
        SELECT reuse.assessment_id,reuse.candidate_reference
        FROM {prefix}decision_candidate_reuses reuse
        LEFT JOIN {prefix}decision_impact_assessments current ON current.id=reuse.assessment_id
        LEFT JOIN {prefix}decision_revisions replacement
            ON replacement.id=current.replacement_revision_id
        LEFT JOIN {prefix}decision_supersession_reuses declaration
            ON declaration.revision_id=current.replacement_revision_id
                AND declaration.target_revision_id=current.origin_revision_id
        LEFT JOIN {prefix}decision_impact_assessments prior
            ON prior.replacement_revision_id=declaration.governing_revision_id
                AND prior.origin_revision_id=declaration.target_revision_id
                AND prior.trigger='supersession'
        LEFT JOIN {prefix}decision_impact_dispositions source
            ON source.id=reuse.source_disposition_id
        LEFT JOIN LATERAL (
            SELECT value AS item FROM jsonb_array_elements(current.candidates)
            WHERE value->>'reference'=reuse.candidate_reference
        ) current_candidate ON true
        LEFT JOIN LATERAL (
            SELECT value AS item FROM jsonb_array_elements(prior.candidates)
            WHERE value->>'reference'=reuse.candidate_reference
        ) prior_candidate ON true
        WHERE current.id IS NULL OR prior.id IS NULL OR source.id IS NULL
            OR current.project_id<>reuse.project_id OR source.project_id<>reuse.project_id
            OR current.trigger<>'supersession' OR current.id=prior.id
            OR current.origin_revision_id<>prior.origin_revision_id
            OR current.extent IS DISTINCT FROM prior.extent
            OR current.reason IS DISTINCT FROM prior.reason
            OR current.created_at<source.created_at
            OR source.candidate_reference<>reuse.candidate_reference
            OR current_candidate.item IS NULL OR prior_candidate.item IS NULL
            OR NOT (current.coverage ?& ARRAY[{coverage_keys}])
            OR NOT (prior.coverage ?& ARRAY[{coverage_keys}])
            OR current.coverage->'traversal_complete' IS DISTINCT FROM 'true'::jsonb
            OR current.coverage->'reasons' IS DISTINCT FROM '[]'::jsonb
            OR current.coverage-'snapshot_at' IS DISTINCT FROM prior.coverage-'snapshot_at'
            OR NOT (current_candidate.item ?& ARRAY[{candidate_keys}])
            OR NOT (prior_candidate.item ?& ARRAY[{candidate_keys}])
            OR current_candidate.item-'is_current'
                IS DISTINCT FROM prior_candidate.item-'is_current'
            OR current_candidate.item->'evidence_truncated' IS DISTINCT FROM 'false'::jsonb
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(current_candidate.item->'scopes') scope
                WHERE scope->'completion_citations_truncated'='true'::jsonb)
            OR NOT (current_candidate.item->>'relationship' IN ('origin','depends-on')
                OR (current_candidate.item->>'relationship'='related-only'
                    AND current_candidate.item->>'related_direction'='incoming'))
            OR source.id IS DISTINCT FROM coalesce((
                SELECT disposition.id FROM {prefix}decision_impact_dispositions disposition
                WHERE disposition.assessment_id=prior.id
                    AND disposition.candidate_reference=reuse.candidate_reference
                    AND disposition.created_at<=current.created_at
                ORDER BY disposition.version DESC LIMIT 1
            ), (SELECT previous.source_disposition_id
                FROM {prefix}decision_candidate_reuses previous
                WHERE previous.assessment_id=prior.id
                    AND previous.candidate_reference=reuse.candidate_reference))
    """


CANDIDATE_REUSE_FINDINGS = {
    "invalid_candidate_reuse_references":
        f"SELECT count(*) FROM ({invalid_candidate_reuses()}) invalid",
}
