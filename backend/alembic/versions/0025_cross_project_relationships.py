"""Allow immutable work relationships to retain globally placed endpoints.

Revision ID: 0025_cross_project_relationships
Revises: 0024_code_reviews
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_cross_project_relationships"
down_revision: str | None = "0024_code_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    bind = op.get_bind()
    name = bind.scalar(sa.text("SELECT current_schema()"))
    if not isinstance(name, str):
        raise RuntimeError("Cross-project relationships require a PostgreSQL schema")
    return bind.dialect.identifier_preparer.quote_identifier(name)


def _replace_function(
    schema: str,
    name: str,
    signature: str,
    replacements: list[tuple[str, str]],
    *,
    reverse: bool = False,
) -> None:
    definition = op.get_bind().scalar(
        sa.text("SELECT pg_get_functiondef(CAST(:function AS regprocedure))"),
        {"function": f"{schema}.{name}({signature})"},
    )
    if not isinstance(definition, str):
        raise RuntimeError(f"Missing predecessor function {name}")
    for old, new in replacements:
        before, after = (new, old) if reverse else (old, new)
        if definition.count(before) != 1:
            raise RuntimeError(f"Unexpected predecessor body for {name}")
        definition = definition.replace(before, after)
    op.execute(sa.text(definition))


def _move_insert_replacements(schema: str) -> list[tuple[str, str]]:
    return [
        (
            f"""            IF EXISTS (
                SELECT 1
                FROM {schema}.work_relationships AS relationship
                WHERE relationship.source_work_item_id = NEW.work_item_id
                   OR relationship.target_work_item_id = NEW.work_item_id
            ) OR EXISTS (
                SELECT 1
                FROM {schema}.work_duplicate_merges AS merge
                WHERE merge.source_work_item_id = NEW.work_item_id
                   OR merge.destination_work_item_id = NEW.work_item_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'related or duplicate work cannot be moved';
            END IF;""",
            f"""            IF EXISTS (
                SELECT 1
                FROM {schema}.work_duplicate_merges AS merge
                WHERE merge.source_work_item_id = NEW.work_item_id
                   OR merge.destination_work_item_id = NEW.work_item_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'duplicate work cannot be moved';
            END IF;""",
        )
    ]


def _move_event_replacements() -> list[tuple[str, str]]:
    return [
        (
            """                    FROM pg_catalog.unnest(ARRAY[
                        NEW.work_item_id,
                        NEW.relationship_source_work_item_id,
                        NEW.relationship_target_work_item_id
                    ]) AS endpoint_id""",
            """                    FROM pg_catalog.unnest(
                        ARRAY[NEW.work_item_id]
                    ) AS endpoint_id""",
        )
    ]


def _source_fact_replacements() -> list[tuple[str, str]]:
    return [
        (
            "                   OR NEW.project_id IS DISTINCT FROM v_relationship.project_id\n",
            "                   -- Relationship events follow the endpoint's current project.\n",
        )
    ]


def _event_state_replacements() -> list[tuple[str, str]]:
    return [
        (
            "                      AND relationship.project_id = NEW.project_id\n",
            "                      -- The edge authority project is immutable historical scope.\n",
        )
    ]


def _duplicate_insert_replacements() -> list[tuple[str, str]]:
    return [
        (
            """                WHERE relationship.project_id = NEW.project_id
                  AND relationship.relationship_type IN ('blocks', 'parent-child')""",
            (
                "                WHERE relationship.relationship_type IN "
                "('blocks', 'parent-child')"
            ),
        )
    ]


def _duplicate_complete_replacements() -> list[tuple[str, str]]:
    return [
        (
            """                WHERE relationship.project_id = v_merge.project_id
                  AND relationship.relationship_type IN ('blocks', 'parent-child')""",
            (
                "                WHERE relationship.relationship_type IN "
                "('blocks', 'parent-child')"
            ),
        )
    ]


def _duplicate_relationship_replacements() -> list[tuple[str, str]]:
    return [
        (
            """                WHERE merge.project_id = v_project_id
                  AND merge.source_work_item_id IN (""",
            """                WHERE merge.source_work_item_id IN (""",
        )
    ]


def _replace_database_guards(schema: str, *, reverse: bool = False) -> None:
    _replace_function(
        schema,
        "mnemonic_guard_work_move_insert",
        "",
        _move_insert_replacements(schema),
        reverse=reverse,
    )
    _replace_function(
        schema,
        "mnemonic_guard_work_move_event",
        "",
        _move_event_replacements(),
        reverse=reverse,
    )
    _replace_function(
        schema,
        "mnemonic_guard_work_event_source_fact",
        "",
        _source_fact_replacements(),
        reverse=reverse,
    )
    _replace_function(
        schema,
        "mnemonic_guard_work_event_state",
        "",
        _event_state_replacements(),
        reverse=reverse,
    )
    _replace_function(
        schema,
        "mnemonic_guard_duplicate_merge_insert",
        "",
        _duplicate_insert_replacements(),
        reverse=reverse,
    )
    _replace_function(
        schema,
        "mnemonic_duplicate_merge_is_complete",
        "uuid,uuid",
        _duplicate_complete_replacements(),
        reverse=reverse,
    )
    _replace_function(
        schema,
        "mnemonic_guard_duplicate_relationship_mutation",
        "",
        _duplicate_relationship_replacements(),
        reverse=reverse,
    )


def _create_authority_guard(schema: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {schema}.mnemonic_guard_relationship_authority()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF TG_RELID <> '{schema}.work_relationships'::regclass
               OR TG_TABLE_NAME <> 'work_relationships'
               OR TG_OP <> 'INSERT'
               OR TG_NAME <> 'relationship_authority_guard'
               OR TG_NARGS <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'relationship authority guard is misconfigured';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM {schema}.work_items AS endpoint
                WHERE endpoint.id IN (
                    NEW.source_work_item_id,
                    NEW.target_work_item_id
                )
                  AND endpoint.project_id = NEW.project_id
                  AND endpoint.deleted_at IS NULL
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'relationship authority must contain a current endpoint';
            END IF;
            RETURN NEW;
        END
        $function$;

        CREATE TRIGGER relationship_authority_guard
        BEFORE INSERT ON {schema}.work_relationships
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_guard_relationship_authority();
        """
    )


def _global_schema() -> None:
    op.drop_constraint(
        "fk_work_relationships_source_work_item",
        "work_relationships",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_work_relationships_target_work_item",
        "work_relationships",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_work_relationships_project",
        "work_relationships",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_work_relationships_source_work_item",
        "work_relationships",
        "work_items",
        ["source_work_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_work_relationships_target_work_item",
        "work_relationships",
        "work_items",
        ["target_work_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "uq_work_relationships_identity",
        "work_relationships",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_work_relationships_identity",
        "work_relationships",
        ["relationship_type", "source_work_item_id", "target_work_item_id"],
    )
    op.drop_index("ix_work_relationships_source", table_name="work_relationships")
    op.drop_index("ix_work_relationships_target", table_name="work_relationships")
    op.create_index(
        "ix_work_relationships_source",
        "work_relationships",
        ["source_work_item_id", "relationship_type", "project_id"],
    )
    op.create_index(
        "ix_work_relationships_target",
        "work_relationships",
        ["target_work_item_id", "relationship_type", "project_id"],
    )


def _require_local_relationships(schema: str) -> None:
    cross_project = op.get_bind().scalar(
        sa.text(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM {schema}.work_relationships AS relationship
                JOIN {schema}.work_items AS source
                  ON source.id = relationship.source_work_item_id
                JOIN {schema}.work_items AS target
                  ON target.id = relationship.target_work_item_id
                WHERE source.project_id <> relationship.project_id
                   OR target.project_id <> relationship.project_id
            )
            OR EXISTS (
                SELECT event.relationship_id
                FROM {schema}.work_events AS event
                WHERE event.event_type IN (
                    'dependency_added', 'dependency_removed',
                    'relationship_added', 'relationship_removed'
                )
                GROUP BY event.relationship_id
                HAVING count(DISTINCT event.project_id) > 1
            )
            """
        )
    )
    if cross_project:
        raise RuntimeError(
            "Cross-project relationship state or event history exists; "
            "downgrade would violate project-local relationship semantics"
        )


def _local_schema() -> None:
    op.drop_index("ix_work_relationships_target", table_name="work_relationships")
    op.drop_index("ix_work_relationships_source", table_name="work_relationships")
    op.create_index(
        "ix_work_relationships_source",
        "work_relationships",
        ["project_id", "source_work_item_id", "relationship_type"],
    )
    op.create_index(
        "ix_work_relationships_target",
        "work_relationships",
        ["project_id", "target_work_item_id", "relationship_type"],
    )
    op.drop_constraint(
        "uq_work_relationships_identity",
        "work_relationships",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_work_relationships_identity",
        "work_relationships",
        ["project_id", "relationship_type", "source_work_item_id", "target_work_item_id"],
    )
    op.drop_constraint(
        "fk_work_relationships_target_work_item",
        "work_relationships",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_work_relationships_source_work_item",
        "work_relationships",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_work_relationships_project",
        "work_relationships",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_work_relationships_source_work_item",
        "work_relationships",
        "work_items",
        ["project_id", "source_work_item_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_work_relationships_target_work_item",
        "work_relationships",
        "work_items",
        ["project_id", "target_work_item_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    schema = _schema()
    op.execute(
        f"LOCK TABLE {schema}.projects, {schema}.work_items, "
        f"{schema}.work_relationships, {schema}.work_item_moves, "
        f"{schema}.work_events, {schema}.work_duplicate_merges "
        "IN ACCESS EXCLUSIVE MODE"
    )
    _global_schema()
    _replace_database_guards(schema)
    _create_authority_guard(schema)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    schema = _schema()
    op.execute(
        f"LOCK TABLE {schema}.projects, {schema}.work_items, "
        f"{schema}.work_relationships, {schema}.work_item_moves, "
        f"{schema}.work_events, {schema}.work_duplicate_merges "
        "IN ACCESS EXCLUSIVE MODE"
    )
    _require_local_relationships(schema)
    op.execute(
        f"DROP TRIGGER relationship_authority_guard ON {schema}.work_relationships"
    )
    op.execute(f"DROP FUNCTION {schema}.mnemonic_guard_relationship_authority()")
    _replace_database_guards(schema, reverse=True)
    _local_schema()
