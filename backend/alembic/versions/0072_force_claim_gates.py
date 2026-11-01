"""Allow gated lease acquisition only with matching explicit force-claim intent."""

import sqlalchemy as sa
from alembic import op

revision = "0072_force_claim_gates"
down_revision = "0071_automatic_review_cycles"
branch_labels = None
depends_on = None


def _patch_guard(*, reverse: bool = False) -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    definition = bind.scalar(sa.text(
        "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=current_schema() AND p.proname='mnemonic_reject_gated_lease_acquisition'"
    ))
    original = "IF v_is_acquisition AND EXISTS ("
    amended = f"""IF v_is_acquisition AND NOT (
                (TG_OP = 'INSERT' OR (
                    NEW.work_item_id = OLD.work_item_id
                    AND NEW.lease_generation_id IS DISTINCT FROM OLD.lease_generation_id
                )) AND EXISTS (
                    SELECT 1 FROM {schema}.work_force_claims AS forced
                    WHERE forced.work_item_id = NEW.work_item_id
                      AND forced.claim_request_id = NEW.claim_request_id
                      AND forced.lease_generation_id = NEW.lease_generation_id
                )
            ) AND EXISTS ("""
    before, after = (amended, original) if reverse else (original, amended)
    if not isinstance(definition, str) or definition.count(before) != 1:
        raise RuntimeError("Unexpected gated lease acquisition guard definition")
    op.execute(definition.replace(before, after))


def upgrade() -> None:
    op.execute("LOCK TABLE work_leases, work_force_claims IN ACCESS EXCLUSIVE MODE")
    _patch_guard()


def downgrade() -> None:
    op.execute("LOCK TABLE work_leases, work_force_claims IN ACCESS EXCLUSIVE MODE")
    _patch_guard(reverse=True)
