"""Worker observations are deployment state; transcript diagnostics are durable."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


def diagnostic_columns() -> list[sa.Column]:
    return [sa.Column("copy_error_details", JSONB)]


def worker_health_elements() -> list:
    return [
        sa.Column("worker_id", UUID, primary_key=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report", JSONB, nullable=False),
        sa.CheckConstraint("jsonb_typeof(report) = 'object'", name="report_object"),
        sa.Index("ix_transcript_worker_health_checked", "checked_at"),
    ]
