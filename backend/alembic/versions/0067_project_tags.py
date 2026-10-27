"""Replace decorative checkpoint tags with project label catalogs and current assignments."""

import sqlalchemy as sa
from alembic import op

revision = "0067_project_tags"
down_revision = "0066_candidate_reuse"
branch_labels = None
depends_on = None

SYSTEM = (
    ("code-review", "Review a completed work item's implementation.", "#b8d8f0"),
    ("remediation", "Address findings from a completed code review.", "#edd1bc"),
    ("dir", "Investigate the impact of a decision change.", "#dec9ed"),
)
LEGACY_OPERATION_KINDS = (
    "create_work add_checkpoint append_event add_relationship update_work defer_work "
    "complete_work delete_work move_work remove_relationship release_claim request_human_input "
    "resolve_human_input merge_work dismiss_job_completion_report "
    "create_job_completion_report_follow_up respond_to_work_follow_up complete_code_review "
    "create_knowledge_domain rename_knowledge_domain put_decision append_decision_scope "
    "assess_decision_impact reconcile_decision_scope update_decision_change "
    "assess_decision_impact_batch delete_knowledge_domain delete_decision"
).split()
TAG_OPERATION_KINDS = "create_tag update_tag delete_tag reorder_tags".split()


def upgrade() -> None:
    op.create_table("project_tags",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("description", sa.String(240), nullable=False, server_default=""),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("menu_order", sa.Integer(), nullable=False),
        sa.Column("system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("project_id", "name", name="uq_project_tags_name"),
        sa.CheckConstraint("name ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="name_valid"),
        sa.CheckConstraint("color ~ '^#[0-9a-fA-F]{6}$'", name="color_valid"),
        sa.CheckConstraint("menu_order >= 0", name="menu_order_nonnegative"),
    )
    op.add_column("work_items", sa.Column("tags", sa.ARRAY(sa.String(50)), nullable=True))
    op.create_index("ix_work_items_tags", "work_items", ["tags"], postgresql_using="gin")
    # Explicitly requested destructive reset; no historical labels remain searchable.
    op.execute("ALTER TABLE checkpoints DISABLE TRIGGER USER")
    op.execute("UPDATE checkpoints SET tags = '{}'::text[] WHERE cardinality(tags) > 0")
    op.execute("ALTER TABLE checkpoints ENABLE TRIGGER USER")
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    values = ",".join(
        f"('{name}','{description.replace(chr(39), chr(39) * 2)}','{color}',{index})"
        for index, (name, description, color) in enumerate(SYSTEM))
    op.execute(f"""
        INSERT INTO {schema}.project_tags(id,project_id,name,description,color,menu_order,system)
        SELECT gen_random_uuid(),p.id,t.name,t.description,t.color,t.menu_order,true
        FROM {schema}.projects p CROSS JOIN (VALUES {values}) t(name,description,color,menu_order);
        CREATE FUNCTION {schema}.mnemonic_seed_project_tags() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            INSERT INTO {schema}.project_tags
                (id,project_id,name,description,color,menu_order,system)
            SELECT gen_random_uuid(),NEW.id,t.name,t.description,t.color,t.menu_order,true
            FROM (VALUES {values}) t(name,description,color,menu_order);
            RETURN NEW;
        END $body$;
        CREATE TRIGGER project_tags_seed AFTER INSERT ON {schema}.projects
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_seed_project_tags();
        CREATE FUNCTION {schema}.mnemonic_system_tag_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF OLD.system AND (TG_OP='DELETE' OR
                (NEW.id,NEW.project_id,NEW.name,NEW.description,NEW.color,NEW.system)
                IS DISTINCT FROM
                (OLD.id,OLD.project_id,OLD.name,OLD.description,OLD.color,OLD.system))
            THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='system tags are immutable';
            END IF;
            RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END $body$;
        CREATE TRIGGER project_tags_system_guard BEFORE UPDATE OR DELETE ON {schema}.project_tags
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_system_tag_guard();
    """)
    _event_contract(schema)
    op.drop_constraint("ck_client_operations_operation_kind_valid", "client_operations")
    op.create_check_constraint("operation_kind_valid", "client_operations",
        "operation_kind IN (" + ",".join(
            f"'{kind}'" for kind in LEGACY_OPERATION_KINDS + TAG_OPERATION_KINDS) + ")")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("""
        SELECT EXISTS(SELECT 1 FROM work_items WHERE tags IS NOT NULL)
            OR EXISTS(SELECT 1 FROM client_operations
                WHERE operation_kind IN ('create_tag','update_tag','delete_tag','reorder_tags'))
            OR EXISTS(SELECT 1 FROM work_events
                WHERE metadata->'initial' ? 'tags' OR metadata->'changes' ? 'tags')
    """)):
        raise RuntimeError("Current tag assignments and durable tag history prevent downgrade.")
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    _replace_event_contract(schema, reverse=True)
    op.execute(f"DROP TRIGGER project_tags_seed ON {schema}.projects")
    op.drop_table("project_tags")
    op.drop_index("ix_work_items_tags", table_name="work_items")
    op.drop_column("work_items", "tags")
    for name in ("mnemonic_seed_project_tags()", "mnemonic_system_tag_guard()",
                 "mnemonic_assigned_tags_valid(jsonb)"):
        op.execute(f"DROP FUNCTION {schema}.{name}")
    # No current tag receipts exist, so restricting the check preserves all retained receipts.
    op.drop_constraint("ck_client_operations_operation_kind_valid", "client_operations")
    op.create_check_constraint("operation_kind_valid", "client_operations",
        "operation_kind IN (" + ",".join(f"'{kind}'" for kind in LEGACY_OPERATION_KINDS) + ")")


def _event_contract(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_assigned_tags_valid(value jsonb) RETURNS boolean
        LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $body$
        SELECT value='null'::jsonb OR (jsonb_typeof(value)='array'
            AND jsonb_array_length(value) BETWEEN 1 AND 20
            AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(value) t
                WHERE jsonb_typeof(t)<>'string' OR length(t #>> '{{}}')>50
                    OR (t #>> '{{}}') !~ '^[a-z0-9]+(-[a-z0-9]+)*$')
            AND (SELECT count(*)=count(DISTINCT t) FROM jsonb_array_elements(value) t))
        $body$;
    """)
    _replace_event_contract(schema)


def _event_replacements(schema: str) -> list[tuple[str, str]]:
    return [
        ("RETURN (v_keys IS NOT DISTINCT FROM",
         "RETURN (array_remove(v_keys, 'tags') IS NOT DISTINCT FROM"),
        ("OR v_keys IS NOT DISTINCT FROM ARRAY['external_references',",
         "OR array_remove(v_keys, 'tags') IS NOT DISTINCT FROM ARRAY['external_references',"),
        ("AND (NOT (v_initial ? 'external_references')",
         f"AND (NOT (v_initial ? 'tags') OR {schema}.mnemonic_assigned_tags_valid("
         "v_initial->'tags')) AND (NOT (v_initial ? 'external_references')"),
        (") > 5\n                   OR pg_catalog.jsonb_typeof(p_metadata",
         ") > 6\n                   OR pg_catalog.jsonb_typeof(p_metadata"),
        ("'status', 'external_references') THEN", "'status', 'external_references', 'tags') THEN"),
        ("IF v_key = 'external_references' THEN", "IF v_key = 'tags' THEN\n"
         f"IF NOT {schema}.mnemonic_assigned_tags_valid(v_before) OR NOT "
         f"{schema}.mnemonic_assigned_tags_valid(v_after) THEN RETURN false; END IF;\n"
         "ELSIF v_key = 'external_references' THEN"),
    ]


def _replace_event_contract(schema: str, *, reverse: bool = False) -> None:
    signature = "text,text,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,smallint,jsonb"
    name = f"{schema}.mnemonic_work_event_metadata_v1_is_valid({signature})"
    body = op.get_bind().scalar(sa.text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"),
                               {"name": name})
    for old, new in _event_replacements(schema):
        before, after = (new, old) if reverse else (old, new)
        if body.count(before) != 1:
            raise RuntimeError("Unexpected work-event predecessor for project tags")
        body = body.replace(before, after)
    op.execute(sa.text(body))
