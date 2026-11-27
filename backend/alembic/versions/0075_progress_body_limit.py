"""Move the fresh progress body size limit into API configuration."""

import sqlalchemy as sa
from alembic import op

revision = "0075_progress_body_limit"
down_revision = "0074_external_resources"
branch_labels = None
depends_on = None


def _replace_body_check(length_rule: str) -> None:
    name = op.f("ck_work_events_body_valid")
    op.drop_constraint(name, "work_events")
    op.create_check_constraint(
        name, "work_events",
        "(event_type IN ('progress', 'work_merged', 'human_attention_requested', "
        "'human_attention_resolved') AND body IS NOT NULL "
        f"AND {length_rule} AND mnemonic_has_non_whitespace(body)) OR "
        "(event_type NOT IN ('progress', 'work_merged', 'human_attention_requested', "
        "'human_attention_resolved') AND body IS NULL)",
    )


def upgrade() -> None:
    _replace_body_check("(event_type = 'progress' OR length(body) <= 4000)")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM work_events WHERE event_type = 'progress' "
        "AND length(body) > 4000)"
    )):
        raise RuntimeError("Retained progress bodies exceed the previous 4000-character limit.")
    _replace_body_check("length(body) <= 4000")
