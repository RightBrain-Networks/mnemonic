"""Bind singleton and atomic batch receipts to their complete immutable histories."""

from mnemonic_api.impact_finding_integrity import disposition_receipt_contains

DISPOSITION_RECEIPT_ROWS = """
    SELECT receipt.project_id, receipt.client_operation_id, receipt.operation_kind,
           receipt.response_status, receipt.mutation_applied, receipt.response_body AS item
    FROM client_operations receipt
    WHERE receipt.operation_kind='assess_decision_impact' AND receipt.state='completed'
    UNION ALL
    SELECT receipt.project_id, receipt.client_operation_id, receipt.operation_kind,
           receipt.response_status, receipt.mutation_applied, item
    FROM client_operations receipt CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(receipt.response_body->'items')='array'
             THEN receipt.response_body->'items' ELSE '[]'::jsonb END) item
    WHERE receipt.operation_kind='assess_decision_impact_batch' AND receipt.state='completed'
"""

BATCH_DISPOSITION_FINDINGS = {
    "invalid_decision_impact_batch_receipts": f"""
        SELECT count(*) FROM client_operations receipt
        WHERE receipt.operation_kind='assess_decision_impact_batch' AND receipt.state='completed'
        AND (receipt.response_status<>201 OR NOT receipt.mutation_applied
          OR receipt.response_body->>'project_id' IS DISTINCT FROM receipt.project_id::text
          OR jsonb_typeof(receipt.response_body->'items') IS DISTINCT FROM 'array'
          OR NOT EXISTS (
            SELECT 1 FROM decision_impact_assessments assessment
            WHERE assessment.id::text=receipt.response_body->>'assessment_id'
              AND assessment.project_id=receipt.project_id)
          OR (SELECT count(*) NOT BETWEEN 1 AND 100
              OR count(DISTINCT item->>'id')<>count(*)
              OR count(DISTINCT item->>'candidate_reference')<>count(*)
              OR count(DISTINCT item->'actor')<>1
              OR bool_or(item->'project_id' IS DISTINCT FROM receipt.response_body->'project_id'
                OR item->'assessment_id' IS DISTINCT FROM receipt.response_body->'assessment_id'
                OR item->'version' IS DISTINCT FROM to_jsonb(
                    (receipt.response_body->>'version')::bigint
                    - jsonb_array_length(receipt.response_body->'items') + ordinal)
                OR NOT EXISTS (SELECT 1 FROM decision_impact_dispositions disposition
                    WHERE disposition.project_id=receipt.project_id
                      AND {disposition_receipt_contains('item')}))
              FROM jsonb_array_elements(CASE
                  WHEN jsonb_typeof(receipt.response_body->'items')='array'
                  THEN receipt.response_body->'items' ELSE '[]'::jsonb END)
                  WITH ORDINALITY AS entries(item, ordinal)))
    """,
}
