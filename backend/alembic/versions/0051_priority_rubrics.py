"""Seed an independently readable, editable priority rubric for every project."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.priority_rubric import (
    DEFAULT_PRIORITY_RUBRIC,
    PRIORITY_RUBRIC_CHECK,
    PRIORITY_RUBRIC_DEFAULT_SQL,
)

revision = "0051_priority_rubrics"
down_revision = "0050_manual_review_modes"
branch_labels = None
depends_on = None


def _settings_guard(*, reverse: bool = False) -> None:
    definition = op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=current_schema() AND p.proname='mnemonic_guard_job_report_settings'"
    ))
    for old, new in (
        ("changed:=ROW(NEW.", "changed:=ROW(NEW.priority_rubric,NEW."),
        ("ROW(OLD.prompt_context_revision,",
         "ROW(OLD.priority_rubric,OLD.prompt_context_revision,"),
    ):
        source, target = (new, old) if reverse else (old, new)
        if definition is None or definition.count(source) != 1:
            raise RuntimeError("Unexpected project settings guard definition")
        definition = definition.replace(source, target)
    op.execute(definition)


def upgrade() -> None:
    op.add_column("project_settings", sa.Column(
        "priority_rubric", sa.Text(), nullable=False,
        server_default=sa.text(PRIORITY_RUBRIC_DEFAULT_SQL),
    ))
    op.create_check_constraint("priority_rubric_valid", "project_settings", PRIORITY_RUBRIC_CHECK)
    _settings_guard()


def downgrade() -> None:
    op.execute("LOCK TABLE project_settings IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM project_settings WHERE priority_rubric <> :original)"
    ), {"original": DEFAULT_PRIORITY_RUBRIC}):
        raise RuntimeError("Customized priority rubrics exist; export them before downgrading")
    _settings_guard(reverse=True)
    op.drop_constraint("ck_project_settings_priority_rubric_valid", "project_settings")
    op.drop_column("project_settings", "priority_rubric")
