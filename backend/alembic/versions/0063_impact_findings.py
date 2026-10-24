"""Retain optional finding identifiers and explicit remediation targets."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.impact_finding_db import FINDING_ID_CHECK, REMEDIATION_TARGET_CHECK

revision = "0063_impact_findings"
down_revision = "0062_review_scope_notices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decision_impact_dispositions", sa.Column("finding_id", sa.String(100)))
    op.add_column("decision_impact_dispositions", sa.Column("remediation_target", sa.String(15)))
    op.create_check_constraint("finding_id", "decision_impact_dispositions", FINDING_ID_CHECK)
    op.create_check_constraint(
        "remediation_target", "decision_impact_dispositions", REMEDIATION_TARGET_CHECK,
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM decision_impact_dispositions "
        "WHERE finding_id IS NOT NULL OR remediation_target IS NOT NULL)"
    )):
        raise RuntimeError("Disposition finding metadata cannot be safely downgraded.")
    for name in ("remediation_target", "finding_id"):
        op.drop_constraint(op.f(f"ck_decision_impact_dispositions_{name}"),
                           "decision_impact_dispositions")
        op.drop_column("decision_impact_dispositions", name)
