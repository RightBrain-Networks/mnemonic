"""Current review metadata additions; the original review table catalog stays frozen."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import SchemaItem

from mnemonic_api.code_review_db_tables import review_elements as original_review_elements

REASON_CHECK = (
    "(request_reason = 'mandatory' AND answer_id IS NULL AND manual_request IS NULL) OR "
    "(request_reason = 'recommended' AND answer_id IS NOT NULL AND manual_request IS NULL) OR "
    "(request_reason = 'manual' AND answer_id IS NULL AND manual_request IS NOT NULL "
    "AND requesting_client = 'dashboard' AND requesting_model IS NULL)"
)


def review_elements() -> list[SchemaItem]:
    elements = original_review_elements()
    for element in elements:
        if isinstance(element, sa.Column) and element.name in {
            "policy_decision_id",
            "scope_sha256",
        }:
            element.nullable = True
        if isinstance(element, sa.CheckConstraint) and element.name == "reason_valid":
            element.sqltext = sa.text(REASON_CHECK)
    return [
        *elements,
        sa.Column("manual_request", JSONB(none_as_null=True)),
        sa.Column("scope_preparation", JSONB(none_as_null=True)),
        sa.CheckConstraint(
            "request_reason = 'manual' OR "
            "(policy_decision_id IS NOT NULL AND scope_sha256 IS NOT NULL)",
            name="automatic_review_scope",
        ),
    ]
