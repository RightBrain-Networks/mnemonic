"""Add the derived duplicate-suggestion title key and lookup index.

Revision ID: 0017_duplicate_suggestion_title_key
Revises: 0016_duplicate_handling
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_duplicate_suggestion_title_key"
down_revision: str | None = "0016_duplicate_handling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted_current_schema() -> str:
    schema = op.get_bind().scalar(sa.text("SELECT current_schema()"))
    if not isinstance(schema, str):
        raise RuntimeError(
            "0017_duplicate_suggestion_title_key requires a current PostgreSQL schema"
        )
    return op.get_bind().dialect.identifier_preparer.quote_identifier(schema)


def upgrade() -> None:
    schema = _quoted_current_schema()
    # Alembic's historical default is varchar(32), while this contract's stable
    # descriptive revision identifier is deliberately longer. Existing heads
    # remain byte-for-byte; only the infrastructure column capacity changes.
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_duplicate_title_key_v1(value text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $function$
            SELECT pg_catalog.lower(
                pg_catalog.regexp_replace(
                    pg_catalog.regexp_replace(
                        pg_catalog.normalize(value, 'NFKC') COLLATE "C",
                        '^[[:space:]]+|[[:space:]]+$',
                        '',
                        'g'
                    ),
                    '[[:space:]]+',
                    ' ',
                    'g'
                ) COLLATE "C"
            )
        $function$;
        """
    )
    op.create_index(
        "ix_work_items_duplicate_title_key_v1",
        "work_items",
        ["project_id", sa.text("mnemonic_duplicate_title_key_v1(title)"), "id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    schema = _quoted_current_schema()
    op.drop_index("ix_work_items_duplicate_title_key_v1", table_name="work_items")
    op.execute(f"DROP FUNCTION {schema}.mnemonic_duplicate_title_key_v1(text)")
