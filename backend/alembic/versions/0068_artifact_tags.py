"""Share project labels with artifacts and seed editable starter labels on new projects."""

import sqlalchemy as sa
from alembic import op

revision = "0068_artifact_tags"
down_revision = "0067_project_tags"
branch_labels = None
depends_on = None

OLD_DIR_DESCRIPTION = "Investigate the impact of a decision change."
DIR_DESCRIPTION = "Design Impact Review: investigate the impact a revised decision document"
# Snapshot read from Fish Food's production catalog on 2026-10-27. These are
# independent, human-editable copies, not a live dependency on the source project.
SYSTEM = (
    ("code-review", "Review a completed work item's implementation.", "#b8d8f0", True),
    ("remediation", "Address findings from a completed code review.", "#edd1bc", True),
    ("dir", DIR_DESCRIPTION, "#dec9ed", True),
)
STARTERS = (
    ("bug-suspected", "A suspected bug that warrants further investigation", "#790000", False),
    ("bug-proven", "A bug that has been proven to exist and needs to be fixed", "#ff8080", False),
    ("infra", "Fix, implement, or improve the CI pipeline or other "
     "infrastructure-related subsystem",
     "#80ff80", False),
    ("tests", "Implement, research, or fix regression tests, test workflow, or test documentation",
     "#804040", False),
    ("ux-ui", "UI or UX-related work", "#d19df0", False),
    ("security", "Fixing, researching, documenting, or implementing security-related functionality",
     "#ffff00", False),
)


def _schema() -> str:
    bind = op.get_bind()
    return bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))


def _seed_function(schema: str, *, previous: bool = False) -> None:
    rows = tuple((name, OLD_DIR_DESCRIPTION if previous and name == "dir" else description,
                  color, system) for name, description, color, system in SYSTEM)
    if not previous:
        rows += STARTERS
    values = ",".join(
        f"('{name}','{description.replace(chr(39), chr(39) * 2)}','{color}',{index}"
        + ("" if previous else f",{'true' if system else 'false'}") + ")"
        for index, (name, description, color, system) in enumerate(rows))
    system_value = "true" if previous else "t.system"
    columns = "name,description,color,menu_order" + ("" if previous else ",system")
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {schema}.mnemonic_seed_project_tags() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            INSERT INTO {schema}.project_tags
                (id,project_id,name,description,color,menu_order,system)
            SELECT gen_random_uuid(),NEW.id,t.name,t.description,t.color,t.menu_order,{system_value}
            FROM (VALUES {values}) t({columns});
            RETURN NEW;
        END $body$;
    """)


def _dir_description(schema: str, description: str) -> None:
    # This is the one authorized system-definition change. Keep the guard intact
    # for every application write, including the remainder of this transaction.
    op.execute(f"ALTER TABLE {schema}.project_tags DISABLE TRIGGER project_tags_system_guard")
    op.get_bind().execute(sa.text(f"UPDATE {schema}.project_tags SET description=:description "
                                 "WHERE system AND name='dir'"), {"description": description})
    op.execute(f"ALTER TABLE {schema}.project_tags ENABLE TRIGGER project_tags_system_guard")


def upgrade() -> None:
    schema = _schema()
    for table in ("artifacts", "artifact_revisions"):
        op.add_column(table, sa.Column("tags", sa.ARRAY(sa.String(50)), nullable=True))
        op.create_check_constraint("tags_count", table,
                                   "tags IS NULL OR cardinality(tags) BETWEEN 1 AND 20")
    op.create_index("ix_artifacts_tags", "artifacts", ["tags"], postgresql_using="gin")
    _dir_description(schema, DIR_DESCRIPTION)
    _seed_function(schema)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("""
        SELECT EXISTS(SELECT 1 FROM artifacts WHERE tags IS NOT NULL)
            OR EXISTS(SELECT 1 FROM artifact_revisions WHERE tags IS NOT NULL)
            OR EXISTS(SELECT 1 FROM artifact_operations WHERE intent->'metadata' ? 'tags'
                OR response_body ? 'tags' OR response_body->'artifact' ? 'tags')
    """)):
        raise RuntimeError("Artifact tag assignments and durable tag history prevent downgrade.")
    schema = _schema()
    _seed_function(schema, previous=True)
    _dir_description(schema, OLD_DIR_DESCRIPTION)
    op.drop_index("ix_artifacts_tags", table_name="artifacts")
    for table in ("artifact_revisions", "artifacts"):
        op.drop_constraint(f"ck_{table}_tags_count", table)
        op.drop_column(table, "tags")
    # Starter definitions already created belong to their project; never delete
    # user-owned labels or their work assignments during a schema downgrade.
