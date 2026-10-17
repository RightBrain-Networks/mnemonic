"""Explicit parent closeout constraints and serialized DAG enforcement."""

import sqlalchemy as sa
from alembic import op

revision = "0052_dependency_cycles"
down_revision = "0051_priority_rubrics"
branch_labels = None
depends_on = None

_METADATA_GUARD = """
            IF p_metadata ? 'requires_child_closeout' THEN
                IF p_event_type NOT IN ('relationship_added','relationship_removed')
                   OR p_metadata->>'relationship_type' <> 'parent-child'
                   OR p_metadata->'requires_child_closeout' <> 'true'::jsonb THEN
                    RETURN false;
                END IF;
                p_metadata := p_metadata - 'requires_child_closeout';
            END IF;
"""
_SOURCE_GUARD = """
                   OR COALESCE(NEW.metadata->'requires_child_closeout','false'::jsonb)
                      IS DISTINCT FROM to_jsonb(v_relationship.requires_child_closeout)
"""


def _event_guards(*, reverse: bool = False) -> None:
    replacements = (
        ("mnemonic_work_event_metadata_v1_is_valid", "BEGIN\n", "BEGIN\n" + _METADATA_GUARD),
        (
            "mnemonic_guard_work_event_source_fact",
            "OR NEW.relationship_source_work_item_id",
            _SOURCE_GUARD + "                   OR NEW.relationship_source_work_item_id",
        ),
    )
    for name, old, new in replacements:
        definition = op.get_bind().scalar(
            sa.text(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n "
                "ON n.oid=p.pronamespace WHERE n.nspname=current_schema() AND p.proname=:name"
            ),
            {"name": name},
        )
        source, target = (new, old) if reverse else (old, new)
        if definition is None or definition.count(source) != 1:
            raise RuntimeError(f"Unexpected {name} definition")
        op.execute(definition.replace(source, target, 1))


def _guards(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_lock_precedence_graph() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        BEGIN
            IF current_setting('transaction_isolation') <> 'read committed' THEN
                RAISE EXCEPTION USING ERRCODE='25000',
                    MESSAGE='relationship writes require read committed isolation';
            END IF;
            PERFORM pg_advisory_xact_lock(hashtext(TG_TABLE_SCHEMA), 1919251312);
            RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END $function$;
        CREATE TRIGGER precedence_graph_lock BEFORE INSERT OR DELETE
        ON {schema}.work_relationships FOR EACH ROW
        EXECUTE FUNCTION {schema}.mnemonic_lock_precedence_graph();

        CREATE FUNCTION {schema}.mnemonic_check_precedence_cycle() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        DECLARE before_id uuid; after_id uuid; has_cycle boolean;
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM {schema}.work_relationships WHERE id=NEW.id) THEN
                RETURN NULL;
            END IF;
            IF NEW.relationship_type NOT IN ('blocks','parent-child') THEN
                RETURN NULL;
            END IF;
            before_id := CASE WHEN NEW.relationship_type='parent-child'
                THEN NEW.target_work_item_id ELSE NEW.source_work_item_id END;
            after_id := CASE WHEN NEW.relationship_type='parent-child'
                THEN NEW.source_work_item_id ELSE NEW.target_work_item_id END;
            IF NEW.relationship_type='parent-child' THEN
                WITH RECURSIVE reachable(id) AS (
                    SELECT after_id
                    UNION
                    SELECT edge.source_work_item_id FROM reachable
                    JOIN {schema}.work_relationships edge ON edge.target_work_item_id=reachable.id
                    WHERE edge.relationship_type='parent-child' AND edge.id<>NEW.id
                ) SELECT EXISTS(SELECT 1 FROM reachable WHERE id=before_id) INTO has_cycle;
                IF has_cycle THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='parent-child cycle refused';
                END IF;
            END IF;
            IF NEW.relationship_type='blocks' OR NEW.requires_child_closeout THEN
                WITH RECURSIVE precedence AS (
                    SELECT source_work_item_id AS before_id, target_work_item_id AS after_id
                    FROM {schema}.work_relationships WHERE relationship_type='blocks' AND id<>NEW.id
                    UNION ALL
                    SELECT target_work_item_id, source_work_item_id
                    FROM {schema}.work_relationships WHERE relationship_type='parent-child'
                      AND requires_child_closeout AND id<>NEW.id
                ), reachable(id) AS (
                    SELECT after_id
                    UNION
                    SELECT edge.after_id FROM reachable
                    JOIN precedence edge ON edge.before_id=reachable.id
                ) SELECT EXISTS(SELECT 1 FROM reachable WHERE id=before_id) INTO has_cycle;
                IF has_cycle THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='precedence cycle refused';
                END IF;
            END IF;
            RETURN NULL;
        END $function$;
        CREATE CONSTRAINT TRIGGER precedence_cycle_guard AFTER INSERT
        ON {schema}.work_relationships DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_check_precedence_cycle();
    """)


def upgrade() -> None:
    op.add_column(
        "work_relationships",
        sa.Column(
            "requires_child_closeout",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_check_constraint(
        "child_closeout_type",
        "work_relationships",
        "NOT requires_child_closeout OR relationship_type='parent-child'",
    )
    schema = op.get_bind().scalar(sa.text("SELECT current_schema()"))
    quoted = op.get_bind().dialect.identifier_preparer.quote(schema)
    _guards(quoted)
    _event_guards()


def downgrade() -> None:
    op.execute("LOCK TABLE work_relationships IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM work_relationships WHERE requires_child_closeout) "
            "OR EXISTS(SELECT 1 FROM work_events WHERE metadata ? 'requires_child_closeout')"
        )
    ):
        raise RuntimeError(
            "Explicit child closeout constraints exist; cannot erase their semantics"
        )
    _event_guards(reverse=True)
    op.execute("DROP TRIGGER precedence_cycle_guard ON work_relationships")
    op.execute("DROP TRIGGER precedence_graph_lock ON work_relationships")
    op.execute("DROP FUNCTION mnemonic_check_precedence_cycle()")
    op.execute("DROP FUNCTION mnemonic_lock_precedence_graph()")
    op.drop_constraint("ck_work_relationships_child_closeout_type", "work_relationships")
    op.drop_column("work_relationships", "requires_child_closeout")
