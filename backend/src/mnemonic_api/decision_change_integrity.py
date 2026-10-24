"""Validate change ownership and scope after privileged backup restoration."""

from mnemonic_api.supersession_reuse_integrity import SUPERSESSION_REUSE_FINDINGS

DECISION_CHANGE_FINDINGS = {
    **SUPERSESSION_REUSE_FINDINGS,
    "unadmitted_held_decision_assessments": """
        SELECT count(*) FROM decision_impact_assessments assessment
        JOIN decision_revision_changes classification
            ON classification.revision_id=assessment.replacement_revision_id
        WHERE classification.requires_admission AND NOT EXISTS (
            SELECT 1 FROM decision_changes successor
            JOIN decision_change_assessments membership ON membership.change_id=successor.id
            WHERE successor.parent_change_id=classification.change_id
              AND successor.admission_actor_client='dashboard'
              AND membership.assessment_id=assessment.id
        )
    """,
    "invalid_decision_change_ownership": """
        SELECT count(*) FROM decision_changes c
        LEFT JOIN decision_revisions r ON r.id=c.origin_revision_id
        LEFT JOIN decision_changes parent ON parent.id=c.parent_change_id
        WHERE (c.origin_revision_id IS NOT NULL AND r.project_id IS DISTINCT FROM c.project_id)
           OR (c.parent_change_id IS NOT NULL AND parent.project_id IS DISTINCT FROM c.project_id)
    """,
    "invalid_decision_change_scope": """
        SELECT count(*) FROM decision_changes c
        WHERE c.scope_version <> (
            SELECT count(*) FROM decision_change_assessments a WHERE a.change_id=c.id
        )
    """,
    "invalid_decision_change_drivers": """
        SELECT count(*) FROM decision_changes c
        LEFT JOIN work_items w ON w.id=c.review_work_item_id
        LEFT JOIN decision_revisions r ON r.id=w.decision_review_revision_id
        WHERE c.review_work_item_id IS NOT NULL AND (
            w.id IS NULL OR (w.decision_review_revision_id IS NOT NULL
                AND r.project_id IS DISTINCT FROM c.project_id)
            OR NOT EXISTS (SELECT 1 FROM decision_change_work membership
                WHERE membership.change_id=c.id AND membership.work_item_id=w.id)
        )
    """,
    "missing_decision_change_drivers": """
        SELECT count(*) FROM work_items w
        WHERE w.decision_review_revision_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM decision_changes c WHERE c.review_work_item_id=w.id
        )
    """,
    "invalid_decision_change_assessments": """
        SELECT count(*) FROM decision_change_assessments membership
        JOIN decision_changes c ON c.id=membership.change_id
        JOIN decision_impact_assessments a ON a.id=membership.assessment_id
        WHERE a.project_id <> c.project_id
    """,
    "invalid_decision_revision_changes": """
        SELECT count(*) FROM decision_revision_changes classification
        JOIN decision_revisions r ON r.id=classification.revision_id
        LEFT JOIN decision_changes c ON c.id=classification.change_id
        LEFT JOIN decision_revisions g ON g.id=classification.governing_revision_id
        WHERE (classification.change_id IS NOT NULL AND c.project_id IS DISTINCT FROM r.project_id)
           OR (classification.governing_revision_id IS NOT NULL
               AND g.project_id IS DISTINCT FROM r.project_id)
    """,
    "missing_decision_classification_lineage": """
        SELECT count(*) FROM decision_revision_changes classification
        WHERE classification.change_id IS NOT NULL
          AND classification.source_work_item_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM decision_change_work membership
            WHERE membership.work_item_id=classification.source_work_item_id
              AND membership.change_id=classification.change_id
        )
    """,
    "missing_inherited_decision_change_lineage": """
        SELECT count(*) FROM work_relationships edge
        JOIN decision_change_work origin ON origin.work_item_id=edge.target_work_item_id
        WHERE edge.relationship_type='discovered-from' AND NOT EXISTS (
            SELECT 1 FROM decision_change_work child
            WHERE child.work_item_id=edge.source_work_item_id AND child.change_id=origin.change_id
        )
    """,
    "cyclic_decision_change_admissions": """
        WITH RECURSIVE ancestors(id,parent_id) AS (
            SELECT id,parent_change_id FROM decision_changes WHERE parent_change_id IS NOT NULL
            UNION
            SELECT ancestors.id,c.parent_change_id FROM ancestors
            JOIN decision_changes c ON c.id=ancestors.parent_id
            WHERE c.parent_change_id IS NOT NULL
        ) SELECT count(*) FROM ancestors WHERE id=parent_id
    """,
}
