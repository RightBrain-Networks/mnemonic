"""Archived graph edges provide immutable evidence for dashboard deletion receipts."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


def deleted_edge_elements() -> list:
    return [
        sa.Column("receipt_id", sa.BigInteger, sa.ForeignKey("client_operations.id",
                  ondelete="RESTRICT"), primary_key=True),
        sa.Column("table_name", sa.String(64), primary_key=True),
        sa.Column("edge_key", sa.String(64), primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("row_data", JSONB, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("jsonb_typeof(row_data)='object'", name="row_object"),
        sa.CheckConstraint("edge_key ~ '^[a-f0-9]{64}$'", name="edge_key"),
        sa.Index("ix_knowledge_deleted_edges_project_table", "project_id", "table_name"),
    ]
