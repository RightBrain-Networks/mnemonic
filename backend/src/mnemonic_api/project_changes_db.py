"""Frozen initial schema for the project change journal, independent of dashboard activity."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.schema import SchemaItem


def head_elements() -> list[SchemaItem]:
    return [
        sa.Column("project_id", sa.UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("last_sequence", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("capture_started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.CheckConstraint("last_sequence >= 0", name="sequence_nonnegative"),
    ]


def change_elements() -> list[SchemaItem]:
    return [
        sa.Column("project_id", sa.UUID,
                  sa.ForeignKey("project_change_heads.project_id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("sequence", sa.BigInteger, primary_key=True),
        sa.Column("resource_type", sa.String(24), nullable=False),
        sa.Column("resource_id", sa.UUID, nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("revision", sa.String(80)),
        sa.Column("changed_fields", ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("before_state", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("after_state", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("domain_id", sa.UUID),
        sa.Column("domain_prefix", sa.String(5)),
        sa.Column("tags", ARRAY(sa.String(50))),
        sa.Column("work_status", sa.String(20)),
        sa.Column("origin", sa.String(16), nullable=False, server_default="live"),
        sa.CheckConstraint("sequence > 0", name="sequence_positive"),
        sa.CheckConstraint("resource_type IN ('work_items','artifacts','decisions','project',"
                           "'settings','knowledge_domains','tags','transcripts')",
                           name="resource_type_valid"),
        sa.CheckConstraint("origin IN ('live','history_import')", name="origin_valid"),
        sa.CheckConstraint("jsonb_typeof(before_state)='object' AND "
                           "jsonb_typeof(after_state)='object' AND "
                           "octet_length(before_state::text)<=16384 AND "
                           "octet_length(after_state::text)<=16384", name="state_bounded"),
        sa.Index("ix_project_changes_time", "project_id", "occurred_at", "sequence"),
        sa.Index("ix_project_changes_resource", "project_id", "resource_type", "sequence"),
        sa.Index("ix_project_changes_domain", "project_id", "domain_id", "sequence"),
    ]
