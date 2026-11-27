"""Project-scoped external resource capacity and lease generations."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api import external_activity_db
from mnemonic_api.external_resource_db import lease_elements, resource_elements

revision = "0074_external_resources"
down_revision = "0073_project_changes"
branch_labels = None
depends_on = None

PREVIOUS_KINDS = (
    "create_work add_checkpoint append_event add_relationship update_work defer_work complete_work "
    "delete_work move_work remove_relationship release_claim request_human_input "
    "resolve_human_input merge_work dismiss_job_completion_report "
    "create_job_completion_report_follow_up respond_to_work_follow_up complete_code_review "
    "create_knowledge_domain rename_knowledge_domain put_decision append_decision_scope "
    "assess_decision_impact reconcile_decision_scope update_decision_change "
    "assess_decision_impact_batch delete_knowledge_domain delete_decision "
    "create_tag update_tag delete_tag reorder_tags"
).split()
EXTERNAL_KINDS = (
    "create_external_resource update_external_resource renew_external_lease "
    "release_external_lease revoke_external_lease"
).split()


def _receipt_kinds(kinds: list[str]) -> None:
    op.drop_constraint(op.f("ck_client_operations_operation_kind_valid"), "client_operations")
    op.create_check_constraint(
        "operation_kind_valid",
        "client_operations",
        "operation_kind IN (" + ",".join(f"'{kind}'" for kind in kinds) + ")",
    )


def upgrade() -> None:
    # Older revisions may have queued deferred journal checks in this transaction.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("SET CONSTRAINTS ALL DEFERRED")
    op.create_table("external_resources", *resource_elements())
    op.create_table("external_resource_leases", *lease_elements())
    _receipt_kinds(PREVIOUS_KINDS + EXTERNAL_KINDS)
    external_activity_db.install()


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM external_resources) OR EXISTS(SELECT 1 "
            "FROM client_operations WHERE operation_kind = ANY(:kinds))"
        ),
        {"kinds": EXTERNAL_KINDS},
    ):
        raise RuntimeError("External resource definitions or receipts prevent downgrade.")
    external_activity_db.uninstall()
    _receipt_kinds(PREVIOUS_KINDS)
    op.drop_table("external_resource_leases")
    op.drop_table("external_resources")
