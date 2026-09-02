"""Parity checks between the Alembic head schema and ORM metadata."""

from collections.abc import Iterable
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, Engine, text
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api.models import Base

pytestmark = pytest.mark.postgres


def _normalized_definition(definition: str, schemas: Iterable[str]) -> str:
    normalized = definition.replace(" NOT VALID", "")
    for schema in schemas:
        normalized = normalized.replace(f'"{schema}".', "")
        normalized = normalized.replace(f"{schema}.", "")
    return normalized


def _constraint_catalog(
    connection: Connection,
    *,
    schema: str,
    tables: list[str],
    normalized_schemas: tuple[str, str],
) -> dict[str, set[tuple[str, str]]]:
    catalog = {table: set() for table in tables}
    rows = connection.execute(
        text(
            """
            SELECT relation.relname AS table_name,
                   constraint_row.conname AS constraint_name,
                   pg_get_constraintdef(constraint_row.oid, true) AS definition
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS relation
              ON relation.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = :schema
              AND constraint_row.contype <> 't'
              AND relation.relname = ANY(CAST(:tables AS text[]))
            ORDER BY relation.relname, constraint_row.conname
            """
        ),
        {"schema": schema, "tables": tables},
    ).mappings()
    for row in rows:
        catalog[row["table_name"]].add(
            (
                row["constraint_name"],
                _normalized_definition(row["definition"], normalized_schemas),
            )
        )
    return catalog


def _index_catalog(
    connection: Connection,
    *,
    schema: str,
    tables: list[str],
    normalized_schemas: tuple[str, str],
) -> dict[str, set[tuple[str, str]]]:
    catalog = {table: set() for table in tables}
    rows = connection.execute(
        text(
            """
            SELECT tablename AS table_name, indexname AS index_name, indexdef AS definition
            FROM pg_indexes
            WHERE schemaname = :schema
              AND tablename = ANY(CAST(:tables AS text[]))
            ORDER BY tablename, indexname
            """
        ),
        {"schema": schema, "tables": tables},
    ).mappings()
    for row in rows:
        catalog[row["table_name"]].add(
            (
                row["index_name"],
                _normalized_definition(row["definition"], normalized_schemas),
            )
        )
    return catalog


def test_migrated_schema_matches_orm_metadata(postgres_engine: Engine):
    """Alembic head and Base.metadata agree on columns, CHECKs, FKs, and indexes."""
    with postgres_engine.connect() as connection:
        migration_context = MigrationContext.configure(
            connection,
            opts={"compare_server_default": True, "compare_type": True},
        )
        assert compare_metadata(migration_context, Base.metadata) == []

    scratch_schema = f"orm_parity_{uuid4().hex}"
    tables = sorted(Base.metadata.tables)
    with postgres_engine.begin() as connection:
        migrated_schema = connection.scalar(text("SELECT current_schema()"))
        assert migrated_schema is not None
        connection.execute(CreateSchema(scratch_schema))
        connection.execute(
            text(
                f'SET LOCAL search_path = "{scratch_schema}", "{migrated_schema}"'
            )
        )
        Base.metadata.create_all(connection, checkfirst=False)

        schemas = (migrated_schema, scratch_schema)
        orm_constraints = _constraint_catalog(
            connection,
            schema=scratch_schema,
            tables=tables,
            normalized_schemas=schemas,
        )
        migrated_constraints = _constraint_catalog(
            connection,
            schema=migrated_schema,
            tables=tables,
            normalized_schemas=schemas,
        )
        orm_indexes = _index_catalog(
            connection,
            schema=scratch_schema,
            tables=tables,
            normalized_schemas=schemas,
        )
        migrated_indexes = _index_catalog(
            connection,
            schema=migrated_schema,
            tables=tables,
            normalized_schemas=schemas,
        )
        for table in tables:
            assert orm_constraints[table] == migrated_constraints[table], {
                "table": table,
                "only_in_orm": sorted(
                    orm_constraints[table] - migrated_constraints[table]
                ),
                "only_in_migration": sorted(
                    migrated_constraints[table] - orm_constraints[table]
                ),
            }
            assert orm_indexes[table] == migrated_indexes[table], {
                "table": table,
                "only_in_orm": sorted(orm_indexes[table] - migrated_indexes[table]),
                "only_in_migration": sorted(
                    migrated_indexes[table] - orm_indexes[table]
                ),
            }
        connection.execute(DropSchema(scratch_schema, cascade=True))
