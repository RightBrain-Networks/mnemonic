"""Immutable bindings between product decisions and human attention requests."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


def domain_binding_elements() -> list:
    return [
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("domain_id", UUID, nullable=False, unique=True),
        sa.Column("created_automatically", sa.Boolean, nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "domain_id"], ["knowledge_domains.project_id", "knowledge_domains.id"],
            ondelete="RESTRICT", name="fk_product_decision_domain_project",
        ),
    ]


def request_binding_elements() -> list:
    return [
        sa.Column("gate_id", UUID, sa.ForeignKey("work_gates.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("document_id", UUID, nullable=False, unique=True),
        sa.Column("backfilled_revision_count", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint("backfilled_revision_count >= 0", name="backfill_count_nonnegative"),
        sa.ForeignKeyConstraint(
            ["project_id", "document_id"],
            ["decision_documents.project_id", "decision_documents.id"],
            ondelete="RESTRICT", name="fk_product_decision_request_document",
        ),
        sa.Index("ix_product_decision_requests_project", "project_id"),
    ]
