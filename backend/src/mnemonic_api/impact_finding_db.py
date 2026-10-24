"""Additive nullable metadata keeps old immutable disposition rows unchanged."""

import sqlalchemy as sa

FINDING_ID_CHECK = "finding_id IS NULL OR finding_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$'"
REMEDIATION_TARGET_CHECK = (
    "remediation_target IS NULL OR (disposition='change-required' "
    "AND remediation_target IN ('code','decision-record','both'))"
)


def finding_elements() -> list:
    return [
        sa.Column("finding_id", sa.String(100)),
        sa.Column("remediation_target", sa.String(15)),
        sa.CheckConstraint(FINDING_ID_CHECK, name="finding_id"),
        sa.CheckConstraint(REMEDIATION_TARGET_CHECK, name="remediation_target"),
    ]
