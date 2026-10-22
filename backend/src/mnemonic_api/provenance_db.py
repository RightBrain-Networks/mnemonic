"""Append-only repository reconciliation, separate from original citations."""

from uuid import uuid4

import sqlalchemy as sa


def mapping_elements() -> list:
    return [
        sa.Column("id", sa.UUID, primary_key=True, default=uuid4),
        sa.Column("project_id", sa.UUID, nullable=False),
        sa.Column("scope_id", sa.UUID, nullable=False),
        sa.Column("repository", sa.String(500), nullable=False),
        sa.Column("original_commit_hash", sa.String(64), nullable=False),
        sa.Column("merged_commit_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.ForeignKeyConstraint(["scope_id"], ["decision_scopes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("scope_id", "merged_commit_hash", name="uq_decision_commit_mapping"),
        sa.CheckConstraint("merged_commit_hash ~ '^([a-f0-9]{40}|[a-f0-9]{64})$'",
                           name="merged_hash"),
        sa.CheckConstraint("original_commit_hash ~ '^([a-f0-9]{40}|[a-f0-9]{64})$'",
                           name="original_hash"),
        sa.Index("ix_decision_commit_mapping_lookup", "project_id", "repository",
                 "merged_commit_hash"),
    ]
