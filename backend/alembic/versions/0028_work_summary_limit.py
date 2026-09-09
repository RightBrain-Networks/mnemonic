"""Move fresh summary length policy to service configuration without truncating history.

Revision ID: 0028_work_summary_limit
Revises: 0027_artifact_fulltext
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_work_summary_limit"
down_revision: str | None = "0027_artifact_fulltext"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SIGNATURE = "text,text,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,smallint,jsonb"
_REPLACEMENTS = (
    ("pg_catalog.length(v_initial ->> 'summary') <= 1000",
     "pg_catalog.length(v_initial ->> 'summary') > 0"),
    ("pg_catalog.length(v_before #>> '{}') > 1000",
     "pg_catalog.length(v_before #>> '{}') < 1"),
    ("pg_catalog.length(v_after #>> '{}') > 1000",
     "pg_catalog.length(v_after #>> '{}') < 1"),
)


def _event_validator(*, reverse: bool = False) -> None:
    bind = op.get_bind()
    schema = bind.scalar(sa.text("SELECT current_schema()"))
    if not isinstance(schema, str):
        raise RuntimeError("Summary policy requires a PostgreSQL schema")
    quoted = bind.dialect.identifier_preparer.quote_identifier(schema)
    definition = bind.scalar(
        sa.text("SELECT pg_get_functiondef(CAST(:function AS regprocedure))"),
        {"function": f"{quoted}.mnemonic_work_event_metadata_v1_is_valid({_SIGNATURE})"},
    )
    if not isinstance(definition, str):
        raise RuntimeError("Missing predecessor work event validator")
    for old, new in _REPLACEMENTS:
        before, after = (new, old) if reverse else (old, new)
        if definition.count(before) != 1:
            raise RuntimeError("Unexpected predecessor work event validator")
        definition = definition.replace(before, after)
    op.execute(sa.text(definition))


def _summary_column(column_type: sa.Text | sa.String) -> None:
    # PostgreSQL requires removing the dependent generated vector before changing its source.
    op.drop_index("ix_work_items_search_vector", table_name="work_items")
    op.drop_column("work_items", "search_vector")
    op.alter_column("work_items", "summary", type_=column_type, existing_nullable=False)
    op.add_column("work_items", sa.Column(
        "search_vector", sa.dialects.postgresql.TSVECTOR(),
        sa.Computed(
            "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english'::regconfig, coalesce(summary, '')), 'B')",
            persisted=True,
        ), nullable=False,
    ))
    op.create_index("ix_work_items_search_vector", "work_items", ["search_vector"],
                    postgresql_using="gin")
    # Its dynamic RECORD fields can retain the old summary parameter type in pooled sessions.
    definition = op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef('mnemonic_guard_work_event_source_fact()'::regprocedure)"
    ))
    if not isinstance(definition, str):
        raise RuntimeError("Missing work event source-fact guard")
    op.execute(sa.text(definition))


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE work_items, work_events IN ACCESS EXCLUSIVE MODE")
    _summary_column(sa.Text())
    _event_validator()


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE work_items, work_events IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("""
        SELECT EXISTS(SELECT 1 FROM work_items WHERE length(summary) > 1000)
            OR EXISTS(SELECT 1 FROM work_events WHERE
                length(metadata #>> '{initial,summary}') > 1000 OR
                length(metadata #>> '{changes,summary,before}') > 1000 OR
                length(metadata #>> '{changes,summary,after}') > 1000)
    """)):
        raise RuntimeError("Long work summaries or retained events cannot be safely downgraded")
    _event_validator(reverse=True)
    _summary_column(sa.String(1000))
