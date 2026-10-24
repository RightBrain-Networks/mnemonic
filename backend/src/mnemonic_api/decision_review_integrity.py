"""Verify automatic investigation origins after privileged data loading."""

LEGACY_DECISION_REVIEW_FINDINGS = {
    "invalid_decision_review_origins": """
        SELECT count(*) FROM work_items work
        LEFT JOIN checkpoints checkpoint ON checkpoint.id=work.initial_checkpoint_id
            AND checkpoint.work_item_id=work.id
        LEFT JOIN decision_revisions revision ON revision.id=work.decision_review_revision_id
        LEFT JOIN decision_documents document ON document.id=revision.document_id
        LEFT JOIN knowledge_domains domain ON domain.id=document.domain_id
        LEFT JOIN LATERAL (
            SELECT coalesce(jsonb_agg(assessment.id::text ORDER BY assessment.id), '[]'::jsonb) ids
            FROM decision_impact_assessments assessment
            WHERE assessment.replacement_revision_id=revision.id
        ) episodes ON true
        WHERE work.decision_review_revision_id IS NOT NULL AND (
            revision.id IS NULL OR checkpoint.id IS NULL
            OR checkpoint.source_client IS DISTINCT FROM 'mnemonic'
            OR checkpoint.source_session_id IS DISTINCT FROM 'decision-revision:'||revision.id::text
            OR NOT EXISTS (SELECT 1 FROM work_events creation WHERE creation.work_item_id=work.id
                AND creation.event_type='work_created' AND creation.project_id=revision.project_id)
            OR checkpoint.source_metadata->'automatic_decision_review'
                IS DISTINCT FROM 'true'::jsonb
            OR checkpoint.source_metadata->>'decision_project_id'
                IS DISTINCT FROM revision.project_id::text
            OR checkpoint.source_metadata->>'decision_revision_id'
                IS DISTINCT FROM revision.id::text
            OR checkpoint.source_metadata->>'decision_reference' IS DISTINCT FROM
                domain.prefix||'-'||lpad(document.ordinal::text,
                    greatest(5,length(document.ordinal::text)), '0')||'-'||revision.revision::text
            OR checkpoint.source_metadata->>'prompt_id' IS DISTINCT FROM 'decision-impact-review'
            OR coalesce(checkpoint.source_metadata->>'prompt_revision', '') !~ '^[a-f0-9]{64}$'
            OR jsonb_typeof(checkpoint.source_metadata->'assessment_ids') IS DISTINCT FROM 'array'
            OR NOT coalesce((checkpoint.source_metadata->'assessment_ids') @> episodes.ids, false)
            OR NOT coalesce(episodes.ids @> (checkpoint.source_metadata->'assessment_ids'), false)
            OR jsonb_array_length(CASE
                WHEN jsonb_typeof(checkpoint.source_metadata->'assessment_ids')='array'
                THEN checkpoint.source_metadata->'assessment_ids' ELSE '[]'::jsonb END)
                <> jsonb_array_length(episodes.ids)
            OR jsonb_array_length(episodes.ids)=0
        )
    """,
}


DECISION_REVIEW_FINDINGS = {
    "invalid_decision_review_origins": """
        SELECT count(*) FROM work_items work
        LEFT JOIN checkpoints checkpoint ON checkpoint.id=work.initial_checkpoint_id
            AND checkpoint.work_item_id=work.id
        LEFT JOIN decision_revisions revision ON revision.id=work.decision_review_revision_id
        LEFT JOIN decision_documents document ON document.id=revision.document_id
        LEFT JOIN knowledge_domains domain ON domain.id=document.domain_id
        LEFT JOIN LATERAL (
            SELECT coalesce(jsonb_agg(assessment.id::text ORDER BY assessment.id), '[]'::jsonb) ids
            FROM decision_impact_assessments assessment
            WHERE assessment.replacement_revision_id=revision.id
        ) episodes ON true
        WHERE work.decision_review_revision_id IS NOT NULL AND (
            revision.id IS NULL OR checkpoint.id IS NULL
            OR checkpoint.source_client IS DISTINCT FROM 'mnemonic'
            OR checkpoint.source_session_id IS DISTINCT FROM 'decision-revision:'||revision.id::text
            OR NOT EXISTS (SELECT 1 FROM work_events creation WHERE creation.work_item_id=work.id
                AND creation.event_type='work_created' AND creation.project_id=revision.project_id)
            OR checkpoint.source_metadata->'automatic_decision_review'
                IS DISTINCT FROM 'true'::jsonb
            OR checkpoint.source_metadata->>'decision_project_id'
                IS DISTINCT FROM revision.project_id::text
            OR checkpoint.source_metadata->>'decision_revision_id'
                IS DISTINCT FROM revision.id::text
            OR checkpoint.source_metadata->>'decision_reference' IS DISTINCT FROM
                domain.prefix||'-'||lpad(document.ordinal::text,
                    greatest(5,length(document.ordinal::text)), '0')||'-'||revision.revision::text
            OR checkpoint.source_metadata->>'prompt_id' IS DISTINCT FROM 'decision-impact-review'
            OR coalesce(checkpoint.source_metadata->>'prompt_revision', '') !~ '^[a-f0-9]{64}$'
            OR jsonb_typeof(checkpoint.source_metadata->'assessment_ids') IS DISTINCT FROM 'array'
            OR CASE WHEN checkpoint.source_metadata ? 'decision_change_id' THEN (
                NOT EXISTS (
                    SELECT 1 FROM decision_changes change
                    WHERE change.review_work_item_id=work.id
                      AND change.id::text=checkpoint.source_metadata->>'decision_change_id'
                      AND change.project_id=revision.project_id
                )
                OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements(CASE
                        WHEN jsonb_typeof(checkpoint.source_metadata->'assessment_ids')='array'
                        THEN checkpoint.source_metadata->'assessment_ids' ELSE '[]'::jsonb END) item
                    WHERE NOT EXISTS (
                        SELECT 1 FROM decision_change_assessments membership
                        JOIN decision_changes change ON change.id=membership.change_id
                        WHERE change.review_work_item_id=work.id
                          AND to_jsonb(membership.assessment_id::text)=item.value
                    )
                )
                OR (SELECT count(*) <> count(DISTINCT item.value)
                    FROM jsonb_array_elements(CASE
                        WHEN jsonb_typeof(checkpoint.source_metadata->'assessment_ids')='array'
                        THEN checkpoint.source_metadata->'assessment_ids'
                        ELSE '[]'::jsonb END) item)
            ) ELSE (
                NOT coalesce((checkpoint.source_metadata->'assessment_ids') @> episodes.ids, false)
                OR NOT coalesce(
                    episodes.ids @> (checkpoint.source_metadata->'assessment_ids'), false)
                OR jsonb_array_length(CASE
                    WHEN jsonb_typeof(checkpoint.source_metadata->'assessment_ids')='array'
                    THEN checkpoint.source_metadata->'assessment_ids' ELSE '[]'::jsonb END)
                    <> jsonb_array_length(episodes.ids)
            ) END
            OR jsonb_array_length(CASE
                WHEN jsonb_typeof(checkpoint.source_metadata->'assessment_ids')='array'
                THEN checkpoint.source_metadata->'assessment_ids' ELSE '[]'::jsonb END)=0
        )
    """,
}
