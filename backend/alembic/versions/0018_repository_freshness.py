"""Add immutable repository dependency scope to full checkpoints.

Revision ID: 0018_repository_freshness
Revises: 0017_duplicate_suggestion_title_key
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_repository_freshness"
down_revision: str | None = "0017_duplicate_suggestion_title_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted_current_schema() -> str:
    schema = op.get_bind().scalar(sa.text("SELECT pg_catalog.current_schema()"))
    if not isinstance(schema, str):
        raise RuntimeError("0018_repository_freshness requires a current PostgreSQL schema")
    return op.get_bind().dialect.identifier_preparer.quote_identifier(schema)


def upgrade() -> None:
    bind = op.get_bind()
    schema = _quoted_current_schema()
    if bind.scalar(sa.text("SELECT pg_catalog.getdatabaseencoding()")) != "UTF8":
        raise RuntimeError("0018_repository_freshness requires UTF-8 database encoding")

    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_affected_paths_valid_v1(value varchar[])
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            path_value text;
            component text;
            total_bytes integer := 0;
            dimensions integer;
        BEGIN
            dimensions := pg_catalog.array_ndims(value);
            IF pg_catalog.cardinality(value) = 0 THEN
                RETURN dimensions IS NULL OR dimensions = 1;
            END IF;
            IF dimensions IS DISTINCT FROM 1
               OR pg_catalog.array_lower(value, 1) IS DISTINCT FROM 1
               OR pg_catalog.cardinality(value) > 64 THEN
                RETURN false;
            END IF;

            FOREACH path_value IN ARRAY value LOOP
                IF path_value IS NULL
                   OR pg_catalog.octet_length(path_value) = 0
                   OR pg_catalog.octet_length(path_value) > 512
                   OR path_value COLLATE "C" !~ '^[A-Za-z0-9._@+=,~*/-]+$' THEN
                    RETURN false;
                END IF;
                total_bytes := total_bytes + pg_catalog.octet_length(path_value);

                FOREACH component IN ARRAY pg_catalog.string_to_array(path_value, '/') LOOP
                    IF component IN ('', '.', '..')
                       OR (
                           pg_catalog.strpos(component, '**') > 0
                           AND component <> '**'
                       ) THEN
                        RETURN false;
                    END IF;
                END LOOP;
            END LOOP;

            IF total_bytes > 16384 THEN
                RETURN false;
            END IF;
            IF (
                SELECT pg_catalog.count(*)
                    <> pg_catalog.count(DISTINCT candidate COLLATE "C")
                FROM pg_catalog.unnest(value) AS item(candidate)
            ) THEN
                RETURN false;
            END IF;
            RETURN true;
        END
        $function$;
        """
    )
    op.add_column(
        "checkpoints",
        sa.Column(
            "affected_paths",
            postgresql.ARRAY(sa.String(length=512)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_checkpoints_affected_paths_valid_v1"),
        "checkpoints",
        "mnemonic_affected_paths_valid_v1(affected_paths)",
    )
    op.create_check_constraint(
        op.f("ck_checkpoints_affected_paths_require_commit"),
        "checkpoints",
        "pg_catalog.cardinality(affected_paths) "
        "OPERATOR(pg_catalog.=) 0 OR verified_against IS NOT NULL",
    )

    invalid_count = bind.scalar(
        sa.text(
            """
            SELECT pg_catalog.count(*)
            FROM checkpoints
            WHERE NOT mnemonic_affected_paths_valid_v1(affected_paths)
               OR (
                   pg_catalog.cardinality(affected_paths) OPERATOR(pg_catalog.>) 0
                   AND verified_against IS NULL
               )
            """
        )
    )
    if invalid_count:
        raise RuntimeError("0018_repository_freshness found invalid checkpoint scope")


def downgrade() -> None:
    bind = op.get_bind()
    isolation = bind.scalar(
        sa.text("SELECT pg_catalog.current_setting('transaction_isolation')")
    )
    if isolation != "read committed":
        raise RuntimeError(
            "0018_repository_freshness downgrade requires READ COMMITTED isolation"
        )
    schema = _quoted_current_schema()
    bind.execute(sa.text(f"LOCK TABLE {schema}.checkpoints IN ACCESS EXCLUSIVE MODE"))
    if bind.scalar(
        sa.text(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM {schema}.checkpoints
                WHERE pg_catalog.cardinality(affected_paths) OPERATOR(pg_catalog.>) 0
            )
            """
        )
    ):
        raise RuntimeError(
            "Cannot downgrade repository freshness after an affected path was stored"
        )

    op.drop_constraint(
        op.f("ck_checkpoints_affected_paths_require_commit"),
        "checkpoints",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_checkpoints_affected_paths_valid_v1"),
        "checkpoints",
        type_="check",
    )
    op.drop_column("checkpoints", "affected_paths")
    op.execute(f"DROP FUNCTION {schema}.mnemonic_affected_paths_valid_v1(varchar[])")
