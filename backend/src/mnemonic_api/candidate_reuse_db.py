"""Immutable references to earlier reviewed candidate conclusions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


def candidate_reuse_elements() -> list:
    return [
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("assessment_id", UUID, primary_key=True),
        sa.Column("candidate_reference", sa.String(36), primary_key=True),
        sa.Column("source_disposition_id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "assessment_id"],
            ["decision_impact_assessments.project_id", "decision_impact_assessments.id"],
            ondelete="RESTRICT", name="fk_candidate_reuse_assessment",
        ),
        sa.ForeignKeyConstraint(
            ["source_disposition_id"], ["decision_impact_dispositions.id"],
            ondelete="RESTRICT", name="fk_candidate_reuse_disposition",
        ),
        sa.Index("ix_candidate_reuse_source", "source_disposition_id"),
    ]
