"""Creation-only decision-change exclusions and their retained receipt witness."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


def elements() -> list:
    return [
        sa.Column("work_item_id", UUID, sa.ForeignKey("work_items.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("origin_work_item_id", UUID, nullable=False),
        sa.Column("origin_context_checkpoint_id", UUID, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("creation_receipt_id", sa.BigInteger,
                  sa.ForeignKey("client_operations.id", ondelete="RESTRICT"), nullable=False,
                  unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["origin_work_item_id", "origin_context_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"], ondelete="RESTRICT",
            name="fk_work_inheritance_origin_checkpoint",
        ),
        sa.CheckConstraint("work_item_id <> origin_work_item_id", name="distinct_origin"),
        sa.CheckConstraint("length(btrim(reason)) > 0 AND length(reason) <= 4000", name="reason"),
        sa.Index("ix_work_inheritance_origin", "origin_work_item_id"),
    ]


def receipt_witness(schema: str = "") -> str:
    """Alias `exclusion` is supplied by the caller; compare original, not live edges."""
    prefix = f"{schema}." if schema else ""
    return f"""
        SELECT 1 FROM {prefix}client_operations receipt
        JOIN {prefix}work_items child ON child.id=exclusion.work_item_id
        JOIN {prefix}work_items origin ON origin.id=exclusion.origin_work_item_id
        WHERE receipt.id=exclusion.creation_receipt_id
          AND receipt.operation_kind='create_work' AND receipt.state='completed'
          AND receipt.response_status=201 AND receipt.mutation_applied
          AND receipt.response_body #>> '{{work_item,id}}'=child.id::text
          AND receipt.response_body #>> '{{work_item,project_id}}'=receipt.project_id::text
          AND receipt.response_body #> '{{work_item,version}}'='1'::jsonb
          AND receipt.response_body #>> '{{work_item,initial_checkpoint_id}}'
              =child.initial_checkpoint_id::text
          AND receipt.response_body #>> '{{initial_checkpoint,id}}'
                =child.initial_checkpoint_id::text
          AND receipt.response_body #>> '{{initial_checkpoint,work_item_id}}'=child.id::text
          AND child.created_at=exclusion.created_at
          AND (receipt.response_body #> '{{work_item,decision_change_inheritance_exclusion}}')
                - 'created_at' = jsonb_build_object(
                    'origin_work_item_id', exclusion.origin_work_item_id,
                    'origin_context_checkpoint_id', exclusion.origin_context_checkpoint_id,
                    'reason', exclusion.reason)
          AND (receipt.response_body #>
                '{{work_item,decision_change_inheritance_exclusion}}')->'created_at'
                =receipt.response_body #> '{{work_item,created_at}}'
          AND EXISTS (
              SELECT 1 FROM jsonb_array_elements(CASE
                  WHEN jsonb_typeof(receipt.response_body->'initial_relationships')='array'
                  THEN receipt.response_body->'initial_relationships' ELSE '[]'::jsonb END) edge
              WHERE edge->>'relationship_type'='discovered-from'
                AND edge->>'source_work_item_id'=child.id::text
                AND edge->>'target_work_item_id'=origin.id::text
                AND edge->>'context_checkpoint_id'=exclusion.origin_context_checkpoint_id::text
          )
    """


INHERITANCE_FINDINGS = {
    "missing_inherited_decision_change_lineage": """
        SELECT count(*) FROM work_relationships edge
        JOIN decision_change_work origin ON origin.work_item_id=edge.target_work_item_id
        WHERE edge.relationship_type='discovered-from' AND NOT EXISTS (
            SELECT 1 FROM work_change_inheritance_exclusions exclusion
            WHERE exclusion.work_item_id=edge.source_work_item_id
              AND exclusion.origin_work_item_id=edge.target_work_item_id
        ) AND NOT EXISTS (
            SELECT 1 FROM decision_change_work child
            WHERE child.work_item_id=edge.source_work_item_id AND child.change_id=origin.change_id
        )
    """,
    "invalid_work_change_inheritance_exclusions":
        "SELECT count(*) FROM work_change_inheritance_exclusions exclusion WHERE NOT EXISTS ("
        + receipt_witness() + ")",
    "missing_work_change_inheritance_exclusions": """
        SELECT count(*) FROM client_operations receipt
        WHERE receipt.operation_kind='create_work' AND receipt.state='completed'
          AND receipt.response_body #> '{work_item,decision_change_inheritance_exclusion}'
                IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM work_change_inheritance_exclusions exclusion
                          WHERE exclusion.creation_receipt_id=receipt.id)
    """,
}
