"""Shared definitions for durable transcript jobs and workspace preferences."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


def transcript_elements(*, include_imports: bool = True) -> list:
    elements = [
        sa.Column("id", UUID, primary_key=True),
        sa.Column("work_item_id", UUID, sa.ForeignKey("work_items.id", ondelete="RESTRICT"),
                  nullable=include_imports),
        sa.Column("lease_generation_id", UUID, nullable=include_imports),
        sa.Column("client", sa.String(80), nullable=False),
        sa.Column("session_id", sa.String(200), nullable=include_imports),
        sa.Column("source_path", sa.String(4096), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="waiting"),
        sa.Column("generation", sa.Integer, nullable=False, server_default="1"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("indexing_started_at", sa.DateTime(timezone=True)),
        sa.Column("indexing_completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("mime_type", sa.String(120)),
        sa.Column("format", sa.String(80)),
        sa.Column("sha256", sa.String(64)),
        sa.Column("text_sha256", sa.String(64)),
        sa.Column("normalized_text", sa.Text),
        sa.Column("extracted_metadata", JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("truncated", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("lease_token", UUID),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("work_item_id", "lease_generation_id", "source_path", "kind",
                            name="uq_transcripts_source"),
        sa.CheckConstraint("kind IN ('primary','subagent','imported')" if include_imports
                           else "kind IN ('primary','subagent')", name="kind_valid"),
        sa.CheckConstraint("status IN ('waiting','pending','processing','ready','failed')",
                           name="status_valid"),
        sa.CheckConstraint("generation > 0 AND attempts >= 0", name="counters_valid"),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes BETWEEN 0 AND 268435456",
                           name="size_valid"),
        sa.CheckConstraint("left(source_path, 1) = '/'", name="path_absolute"),
        sa.CheckConstraint("(status = 'processing' AND lease_token IS NOT NULL "
                           "AND lease_expires_at IS NOT NULL) OR "
                           "(status <> 'processing' AND lease_token IS NULL "
                           "AND lease_expires_at IS NULL)", name="lease_valid"),
        sa.CheckConstraint("normalized_text IS NULL OR (status = 'ready' "
                           "AND char_length(normalized_text) <= 8000000)", name="text_valid"),
        sa.CheckConstraint("jsonb_typeof(extracted_metadata) = 'object' "
                           "AND octet_length(extracted_metadata::text) <= 16384",
                           name="metadata_valid"),
        sa.Index("ix_transcripts_work_created", "work_item_id", "created_at", "id"),
        sa.Index("ix_transcripts_due", "status", "next_attempt_at"),
    ]
    if include_imports:
        elements.extend([
            sa.Column("import_project_id", UUID,
                      sa.ForeignKey("projects.id", ondelete="RESTRICT")),
            sa.CheckConstraint(IMPORT_PROVENANCE, name="provenance_valid"),
            sa.UniqueConstraint("import_project_id", "source_path",
                                name="uq_transcripts_import_source"),
        ])
    return elements


IMPORT_PROVENANCE = (
    "(kind = 'imported' AND import_project_id IS NOT NULL AND work_item_id IS NULL "
    "AND lease_generation_id IS NULL AND session_id IS NULL) OR "
    "(kind IN ('primary','subagent') AND import_project_id IS NULL "
    "AND work_item_id IS NOT NULL AND lease_generation_id IS NOT NULL "
    "AND session_id IS NOT NULL)"
)


def import_elements() -> list:
    return [
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("client_operation_id", UUID, primary_key=True),
        sa.Column("directory", sa.String(4096), nullable=False),
        sa.Column("imported", sa.Integer, nullable=False),
        sa.Column("existing", sa.Integer, nullable=False),
        sa.Column("skipped", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.CheckConstraint("imported >= 0 AND existing >= 0 AND imported + existing <= 5000 "
                           "AND skipped BETWEEN 0 AND 50000", name="counts_valid"),
        sa.CheckConstraint("left(directory, 1) = '/'", name="directory_absolute"),
    ]


def settings_elements() -> list:
    return [
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("max_file_size_bytes", sa.BigInteger, nullable=False, server_default="67108864"),
        sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
        sa.CheckConstraint("revision > 0", name="revision_positive"),
        sa.CheckConstraint("max_file_size_bytes BETWEEN 1 AND 268435456", name="size_valid"),
    ]


def rebuild_elements() -> list:
    return [
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("client_operation_id", UUID, primary_key=True),
        sa.Column("queued", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.CheckConstraint("queued >= 0", name="queued_valid"),
    ]
