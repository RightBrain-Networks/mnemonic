"""Receipt-protected atomic batches of assessment dispositions."""

import sqlalchemy as sa
from alembic import op

revision = "0061_batch_dispositions"
down_revision = "0060_supersession_reuse"
branch_labels = None
depends_on = None

PREVIOUS_KINDS = (
    "create_work", "add_checkpoint", "append_event", "add_relationship", "update_work",
    "defer_work", "complete_work", "delete_work", "move_work", "remove_relationship",
    "release_claim", "request_human_input", "resolve_human_input", "merge_work",
    "dismiss_job_completion_report", "create_job_completion_report_follow_up",
    "respond_to_work_follow_up", "complete_code_review", "create_knowledge_domain",
    "rename_knowledge_domain", "put_decision", "append_decision_scope", "assess_decision_impact",
    "reconcile_decision_scope", "update_decision_change",
)


def _operation_kinds(kinds: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_client_operations_operation_kind_valid"), "client_operations")
    op.create_check_constraint(
        "operation_kind_valid", "client_operations",
        "operation_kind IN (" + ",".join(f"'{kind}'" for kind in kinds) + ")",
    )


def upgrade() -> None:
    _operation_kinds((*PREVIOUS_KINDS, "assess_decision_impact_batch"))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM client_operations "
        "WHERE operation_kind='assess_decision_impact_batch')"
    )):
        raise RuntimeError("Batch disposition receipts prevent downgrade without losing history.")
    _operation_kinds(PREVIOUS_KINDS)
