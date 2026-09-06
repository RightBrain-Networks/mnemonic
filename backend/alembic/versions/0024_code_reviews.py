"""First-class code reviews, durable agent questions and bounded remediation ancestry.

Revision ID: 0024_code_reviews
Revises: 0023_work_item_moves
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from mnemonic_api import code_review_db_sql as sql
from mnemonic_api.code_review_db_tables import TABLE_ELEMENTS, event_extension_elements

revision: str = "0024_code_reviews"
down_revision: str | None = "0023_work_item_moves"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASE_EVENTS = (
    "work_created",
    "work_updated",
    "work_status_changed",
    "work_reopened",
    "work_claimed",
    "work_released",
    "checkpoint_added",
    "progress",
    "dependency_added",
    "dependency_removed",
    "relationship_added",
    "relationship_removed",
    "work_completed",
    "work_deleted",
    "work_merged",
    "work_moved",
    "human_attention_requested",
    "human_attention_resolved",
)
NEW_EVENTS = (
    "work_follow_up_requested",
    "work_follow_up_answered",
    "work_follow_up_superseded",
    "code_review_requested",
    "code_review_completed",
    "code_review_superseded",
)
BASE_KINDS = (
    "create_work",
    "add_checkpoint",
    "append_event",
    "add_relationship",
    "update_work",
    "defer_work",
    "complete_work",
    "delete_work",
    "move_work",
    "remove_relationship",
    "release_claim",
    "request_human_input",
    "resolve_human_input",
    "merge_work",
    "dismiss_job_completion_report",
    "create_job_completion_report_follow_up",
)
NEW_KINDS = ("respond_to_work_follow_up", "complete_code_review")
EVENT_METADATA_CHECK = (
    "(event_type IN ('human_attention_requested', 'human_attention_resolved') "
    "AND metadata_version = 1 AND metadata = jsonb_build_object('gate_id', gate_id::text, "
    "'gate_type', 'human')) OR (event_type = 'work_merged' AND "
    "mnemonic_work_merged_metadata_v1_is_valid(work_item_id, work_duplicate_merge_id, "
    "metadata_version, metadata)) OR "
    "(event_type = 'work_moved' AND mnemonic_work_moved_metadata_v1_is_valid(work_item_id, "
    "project_id, work_move_id, metadata_version, metadata)) OR "
    "(event_type NOT IN ('human_attention_requested', 'human_attention_resolved', "
    "'work_merged', 'work_moved') "
    "AND mnemonic_work_event_metadata_v2_is_valid(event_type, origin, work_item_id, checkpoint_id, "
    "lease_generation_id, lease_release_id, relationship_id, relationship_source_work_item_id, "
    "relationship_target_work_item_id, relationship_context_checkpoint_work_item_id, "
    "relationship_context_checkpoint_id, metadata_version, metadata))"
)
EVENT_COLUMNS = (
    "code_review_id",
    "work_follow_up_id",
    "work_follow_up_answer_id",
    "code_review_result_id",
)
SETTINGS_COLUMNS = (
    "code_review_required_min_priority",
    "code_review_optional_min_priority",
    "allow_remediation_code_reviews",
    "code_review_policy_touched",
)
WORK_COLUMNS = (
    "remediation_depth",
    "remediation_id",
    "completion_review_checkpoint_id",
    "completion_review_policy_snapshot",
)
LEASE_COLUMNS = ("purpose", "code_review_id", "mode")


def _schema() -> str:
    name = op.get_bind().scalar(sa.text("SELECT current_schema()"))
    if not isinstance(name, str):
        raise RuntimeError("Code reviews require an explicit PostgreSQL schema")
    return op.get_bind().dialect.identifier_preparer.quote_identifier(name)


def _execute(source: str, schema: str) -> None:
    op.execute(sa.text(source.replace("SCHEMA", schema)))


def _enum_constraint(table: str, column: str, name: str, values: tuple[str, ...]) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(
        name, table, column + " IN (" + ",".join(repr(v) for v in values) + ")"
    )


def _fk(table: str, constraint: sa.ForeignKeyConstraint) -> None:
    targets = [element.target_fullname.split(".") for element in constraint.elements]
    op.create_foreign_key(
        constraint.name,
        table,
        targets[0][0],
        list(constraint.column_keys),
        [target[1] for target in targets],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )


def _columns() -> None:
    op.add_column(
        "checkpoints",
        sa.Column(
            "requires_code_review_policy", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    for name in SETTINGS_COLUMNS[:2]:
        op.add_column(
            "project_settings",
            sa.Column(name, sa.SmallInteger(), nullable=False, server_default="100"),
        )
    for name in SETTINGS_COLUMNS[2:]:
        op.add_column(
            "project_settings",
            sa.Column(name, sa.Boolean(), nullable=False, server_default="false"),
        )
    op.create_check_constraint(
        "review_thresholds",
        "project_settings",
        "code_review_required_min_priority BETWEEN 0 AND 100 AND "
        "code_review_required_min_priority % 5 = 0 AND "
        "code_review_optional_min_priority BETWEEN 0 AND 100 AND "
        "code_review_optional_min_priority % 5 = 0",
    )
    op.add_column(
        "work_items",
        sa.Column("remediation_depth", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column("work_items", sa.Column("remediation_id", sa.UUID()))
    op.add_column("work_items", sa.Column("completion_review_checkpoint_id", sa.UUID()))
    op.add_column("work_items", sa.Column("completion_review_policy_snapshot", JSONB()))
    op.create_check_constraint(
        "remediation_provenance",
        "work_items",
        "(remediation_depth = 0 AND remediation_id IS NULL) OR "
        "(remediation_depth IN (1,2) AND remediation_id IS NOT NULL)",
    )
    op.add_column(
        "work_leases",
        sa.Column("purpose", sa.String(20), nullable=False, server_default="implementation"),
    )
    op.add_column("work_leases", sa.Column("code_review_id", sa.UUID()))
    op.add_column("work_leases", sa.Column("mode", sa.String(8)))
    op.create_check_constraint(
        "purpose_valid",
        "work_leases",
        "(purpose = 'implementation' AND code_review_id IS NULL AND mode IS NULL) OR "
        "(purpose = 'code_review' AND code_review_id IS NOT NULL AND mode IN ('cold','warm'))",
    )
    for name in EVENT_COLUMNS:
        op.add_column("work_events", sa.Column(name, sa.UUID()))


def _tables() -> None:
    references: list[tuple[str, sa.ForeignKeyConstraint]] = []
    for table, factory in TABLE_ELEMENTS.items():
        elements = factory()
        references.extend(
            (table, item) for item in elements if isinstance(item, sa.ForeignKeyConstraint)
        )
        op.create_table(
            table, *(item for item in elements if not isinstance(item, sa.ForeignKeyConstraint))
        )
    for table, constraint in references:
        _fk(table, constraint)
    for constraint in event_extension_elements():
        if isinstance(constraint, sa.ForeignKeyConstraint):
            _fk("work_events", constraint)
        elif isinstance(constraint, sa.CheckConstraint):
            op.create_check_constraint(constraint.name, "work_events", constraint.sqltext)
    op.create_foreign_key(
        "fk_work_items_remediation",
        "work_items",
        "code_review_remediations",
        ["project_id", "id", "remediation_id", "remediation_depth"],
        ["project_id", "remediation_work_item_id", "id", "depth"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_work_items_review_checkpoint",
        "work_items",
        "checkpoints",
        ["id", "completion_review_checkpoint_id"],
        ["work_item_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_work_leases_review",
        "work_leases",
        "code_reviews",
        ["work_item_id", "code_review_id"],
        ["work_item_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )


def _settings_guard(schema: str, *, reverse: bool = False) -> None:
    definition = op.get_bind().scalar(
        sa.text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"),
        {"name": f"{schema}.mnemonic_guard_job_report_settings()"},
    )
    if not isinstance(definition, str):
        raise RuntimeError("Missing predecessor settings guard")
    original = "changed:=ROW(NEW.recall_pointer_template,NEW.job_completion_report_prompt)"
    extended = (
        "IF NEW.code_review_policy_touched IS DISTINCT FROM OLD.code_review_policy_touched THEN\n"
        "                RAISE EXCEPTION USING ERRCODE='23514', "
        "MESSAGE='review policy witness is database managed';\n"
        "            END IF;\n"
        "            NEW.code_review_policy_touched:=OLD.code_review_policy_touched OR\n"
        "                ROW(NEW.code_review_required_min_priority,"
        "NEW.code_review_optional_min_priority,\n"
        "                    NEW.allow_remediation_code_reviews) IS DISTINCT FROM\n"
        "                ROW(OLD.code_review_required_min_priority,"
        "OLD.code_review_optional_min_priority,\n"
        "                    OLD.allow_remediation_code_reviews);\n"
        "            changed:=ROW(NEW.recall_pointer_template,NEW.job_completion_report_prompt,\n"
        "                         NEW.code_review_required_min_priority,"
        "NEW.code_review_optional_min_priority,\n"
        "                         NEW.allow_remediation_code_reviews)"
    )
    replacements = [
        (original, extended),
        (
            "ROW(OLD.recall_pointer_template,OLD.job_completion_report_prompt);",
            "ROW(OLD.recall_pointer_template,OLD.job_completion_report_prompt,\n"
            "                             OLD.code_review_required_min_priority,"
            "OLD.code_review_optional_min_priority,\n"
            "                             OLD.allow_remediation_code_reviews);",
        ),
        (
            "IF TG_OP='INSERT' AND pg_trigger_depth()>=2 AND NEW.revision=1",
            "IF TG_OP='INSERT' AND pg_trigger_depth()>=2 AND NEW.revision=1\n"
            "           AND NEW.code_review_required_min_priority=100 "
            "AND NEW.code_review_optional_min_priority=100\n"
            "           AND NOT NEW.allow_remediation_code_reviews "
            "AND NOT NEW.code_review_policy_touched",
        ),
    ]
    for old, new in replacements:
        before, after = (new, old) if reverse else (old, new)
        if definition.count(before) != 1:
            raise RuntimeError("Unexpected predecessor settings guard body")
        definition = definition.replace(before, after)
    op.execute(sa.text(definition))


def _guards(schema: str) -> None:
    _execute(sql.WORK_GUARDS, schema)
    _execute(sql.RESOURCE_GUARDS, schema)
    _execute(sql.SEAL_GUARDS, schema)
    for table in TABLE_ELEMENTS:
        if table != "work_completion_review_policies":
            op.execute(
                f"CREATE TRIGGER code_review_resource_insert BEFORE INSERT ON {schema}.{table} "
                "FOR EACH ROW EXECUTE FUNCTION "
                f"{schema}.mnemonic_code_review_resource_insert()"
            )
        op.execute(
            "CREATE TRIGGER code_review_resource_mutation BEFORE UPDATE OR DELETE "
            f"ON {schema}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_code_review_resource_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER code_review_truncate BEFORE TRUNCATE ON {schema}.{table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {schema}.mnemonic_code_review_resource_mutation()"
        )
        op.execute(
            f"CREATE CONSTRAINT TRIGGER code_review_resource_sealed AFTER INSERT OR UPDATE "
            f"ON {schema}.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            f"EXECUTE FUNCTION {schema}.mnemonic_code_review_resource_sealed()"
        )


def upgrade() -> None:
    schema = _schema()
    _execute(sql.VALIDATORS, schema)
    _columns()
    _tables()
    _enum_constraint("work_events", "event_type", "event_type_valid", BASE_EVENTS + NEW_EVENTS)
    _enum_constraint(
        "client_operations", "operation_kind", "operation_kind_valid", BASE_KINDS + NEW_KINDS
    )
    op.drop_constraint(op.f("ck_work_events_metadata_v1_valid"), "work_events", type_="check")
    expression = EVENT_METADATA_CHECK.replace("metadata_v2_is_valid", "metadata_v3_is_valid")
    expression = expression[:-2] + ", " + ", ".join(EVENT_COLUMNS) + "))"
    op.create_check_constraint("metadata_v1_valid", "work_events", expression)
    _settings_guard(schema)
    _guards(schema)


def _downgrade_guard(schema: str) -> None:
    tables = tuple(TABLE_ELEMENTS) + (
        "project_settings",
        "work_items",
        "work_item_moves",
        "checkpoints",
        "work_leases",
        "work_events",
        "client_operations",
        "project_activity",
    )
    op.execute(
        "LOCK TABLE "
        + ",".join(f"{schema}.{table}" for table in tables)
        + " IN ACCESS EXCLUSIVE MODE"
    )
    checks = [f"EXISTS(SELECT 1 FROM {schema}.{table})" for table in TABLE_ELEMENTS]
    checks += [
        f"EXISTS(SELECT 1 FROM {schema}.project_settings WHERE code_review_policy_touched OR "
        "code_review_required_min_priority<>100 OR code_review_optional_min_priority<>100 OR "
        "allow_remediation_code_reviews)",
        f"EXISTS(SELECT 1 FROM {schema}.work_items WHERE remediation_depth<>0 OR "
        "completion_review_checkpoint_id IS NOT NULL OR "
        "completion_review_policy_snapshot IS NOT NULL)",
        f"EXISTS(SELECT 1 FROM {schema}.work_leases WHERE purpose<>'implementation')",
        f"EXISTS(SELECT 1 FROM {schema}.checkpoints WHERE requires_code_review_policy)",
        f"EXISTS(SELECT 1 FROM {schema}.work_events WHERE code_review_id IS NOT NULL OR "
        "work_follow_up_id IS NOT NULL OR work_follow_up_answer_id IS NOT NULL OR "
        "code_review_result_id IS NOT NULL)",
        f"EXISTS(SELECT 1 FROM {schema}.client_operations WHERE operation_kind IN "
        "('respond_to_work_follow_up','complete_code_review'))",
    ]
    if op.get_bind().scalar(sa.text("SELECT " + " OR ".join(checks))):
        raise RuntimeError("Code-review downgrade refused: feature history or policy changes exist")


def downgrade() -> None:
    schema = _schema()
    _downgrade_guard(schema)
    _settings_guard(schema, reverse=True)
    _enum_constraint("work_events", "event_type", "event_type_valid", BASE_EVENTS)
    _enum_constraint("client_operations", "operation_kind", "operation_kind_valid", BASE_KINDS)
    op.drop_constraint(op.f("ck_work_events_metadata_v1_valid"), "work_events", type_="check")
    op.create_check_constraint("metadata_v1_valid", "work_events", EVENT_METADATA_CHECK)
    _drop_guards(schema)
    _drop_tables()
    _drop_columns()
    _drop_functions(schema)


def _drop_guards(schema: str) -> None:
    op.execute(f"DROP TRIGGER code_review_checkpoint_guard ON {schema}.checkpoints")
    for table, trigger in (
        ("work_items", "review_work_guard"),
        ("work_item_moves", "code_review_move_guard"),
        ("work_items", "code_review_work_sealed"),
        ("work_relationships", "code_review_edge_guard"),
        ("work_duplicate_merges", "code_review_merge_guard"),
        ("work_leases", "code_review_lease_guard"),
        ("work_events", "code_review_event_guard"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {schema}.{table}")


def _drop_tables() -> None:
    for table, name in (
        ("work_items", "fk_work_items_remediation"),
        ("work_items", "fk_work_items_review_checkpoint"),
        ("work_leases", "fk_work_leases_review"),
    ):
        op.drop_constraint(name, table, type_="foreignkey")
    for column in EVENT_COLUMNS:
        op.drop_constraint(f"fk_work_events_{column}", "work_events", type_="foreignkey")
    op.drop_constraint(
        op.f("ck_work_events_code_review_references_valid"), "work_events", type_="check"
    )
    for table, factory in TABLE_ELEMENTS.items():
        for element in factory():
            if isinstance(element, sa.ForeignKeyConstraint):
                op.drop_constraint(element.name, table, type_="foreignkey")
    for table in reversed(TABLE_ELEMENTS):
        op.drop_table(table)


def _drop_columns() -> None:
    op.drop_column("checkpoints", "requires_code_review_policy")
    for table, constraint in (
        ("project_settings", "review_thresholds"),
        ("work_items", "remediation_provenance"),
        ("work_leases", "purpose_valid"),
    ):
        op.drop_constraint(op.f(f"ck_{table}_{constraint}"), table, type_="check")
    for table, columns in (
        ("project_settings", SETTINGS_COLUMNS),
        ("work_items", WORK_COLUMNS),
        ("work_leases", LEASE_COLUMNS),
        ("work_events", EVENT_COLUMNS),
    ):
        for column in columns:
            op.drop_column(table, column)


def _drop_functions(schema: str) -> None:
    signatures = (
        op.get_bind()
        .scalars(
            sa.text("""
        SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace=CAST(:schema AS regnamespace)
          AND (proname LIKE 'mnemonic_code_review_%'
               OR proname='mnemonic_work_event_metadata_v3_is_valid')
        ORDER BY proname DESC
    """),
            {"schema": schema},
        )
        .all()
    )
    # PL/pgSQL bodies do not retain dependency links; all column/check callers were removed above.
    for signature in signatures:
        op.execute(f"DROP FUNCTION {signature}")
