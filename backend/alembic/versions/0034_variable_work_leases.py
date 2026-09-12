"""Per-project lease durations and exact claim-duration replay identity."""

import sqlalchemy as sa
from alembic import op

revision = "0034_variable_work_leases"
down_revision = "0033_transcript_imports"
branch_labels = None
depends_on = None


def _settings_guard(*, reverse: bool = False) -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    definition = bind.scalar(sa.text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"),
                             {"name": f"{schema}.mnemonic_guard_job_report_settings()"})
    if not isinstance(definition, str):
        raise RuntimeError("Missing predecessor settings guard")
    replacements = [
        ("changed:=ROW(NEW.recall_pointer_template,",
         "changed:=ROW(NEW.lease_default_minutes,NEW.lease_minimum_minutes,"
         "NEW.lease_maximum_minutes,NEW.recall_pointer_template,"),
        ("ROW(OLD.recall_pointer_template,",
         "ROW(OLD.lease_default_minutes,OLD.lease_minimum_minutes,"
         "OLD.lease_maximum_minutes,OLD.recall_pointer_template,"),
        ("AND NOT NEW.code_review_policy_touched",
         "AND NOT NEW.code_review_policy_touched AND NEW.lease_default_minutes=15 "
         "AND NEW.lease_minimum_minutes=10 AND NEW.lease_maximum_minutes=120"),
    ]
    for old, new in replacements:
        before, after = (new, old) if reverse else (old, new)
        if definition.count(before) != 1:
            raise RuntimeError("Unexpected predecessor settings guard body")
        definition = definition.replace(before, after)
    op.execute(sa.text(definition))


def upgrade() -> None:
    for name, default in (("default", 15), ("minimum", 10), ("maximum", 120)):
        op.add_column("project_settings", sa.Column(
            f"lease_{name}_minutes", sa.Integer(), nullable=False, server_default=str(default),
        ))
    op.create_check_constraint(
        "lease_minutes_order", "project_settings",
        "lease_minimum_minutes > 0 AND lease_minimum_minutes <= lease_default_minutes "
        "AND lease_default_minutes <= lease_maximum_minutes",
    )
    _settings_guard()
    # Null retains the exact omitted-duration request identity of all earlier claims.
    op.add_column("work_leases", sa.Column("claim_lease_minutes", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "claim_lease_minutes_positive", "work_leases", "claim_lease_minutes > 0",
    )


def downgrade() -> None:
    op.execute("LOCK TABLE project_settings, work_leases IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM project_settings WHERE lease_default_minutes <> 15 "
        "OR lease_minimum_minutes <> 10 OR lease_maximum_minutes <> 120) OR "
        "EXISTS(SELECT 1 FROM work_leases WHERE claim_lease_minutes IS NOT NULL)"
    )):
        raise RuntimeError("Custom lease policies and claims cannot be safely downgraded")
    _settings_guard(reverse=True)
    op.drop_constraint("ck_work_leases_claim_lease_minutes_positive", "work_leases")
    op.drop_column("work_leases", "claim_lease_minutes")
    op.drop_constraint("ck_project_settings_lease_minutes_order", "project_settings")
    for name in ("maximum", "minimum", "default"):
        op.drop_column("project_settings", f"lease_{name}_minutes")
