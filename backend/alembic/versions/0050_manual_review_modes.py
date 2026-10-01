"""Retain the human's requested review mode and enforce it on review leases."""

import sqlalchemy as sa
from alembic import op

revision = "0050_manual_review_modes"
down_revision = "0049_duplicate_embeddings"
branch_labels = None
depends_on = None

PATCHES = (
    (
        "mnemonic_manual_review_work_guard",
        "jsonb_object_keys(entry) key",
        "jsonb_object_keys(entry - 'mode') key",
    ),
    (
        "mnemonic_manual_review_work_guard",
        "OR entry->>'actor_client' IS DISTINCT FROM 'dashboard'",
        "OR (entry ? 'mode' AND entry->'mode' NOT IN ('\"warm\"'::jsonb, '\"cold\"'::jsonb))\n"
        "       OR entry->>'actor_client' IS DISTINCT FROM 'dashboard'",
    ),
    (
        "mnemonic_manual_review_lease_sealed",
        "WHERE l.work_item_id=NEW.work_item_id AND r.scope_sha256 IS NULL",
        "WHERE l.work_item_id=NEW.work_item_id AND (r.scope_sha256 IS NULL\n"
        "              OR (r.manual_request ? 'mode' AND l.mode IS DISTINCT FROM\n"
        "                  r.manual_request->>'mode'))",
    ),
)


def patch_functions(*, reverse: bool = False) -> None:
    bind = op.get_bind()
    for name, old, new in reversed(PATCHES) if reverse else PATCHES:
        definition = bind.scalar(sa.text(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=current_schema() AND p.proname=:name"
        ), {"name": name})
        source, target = (new, old) if reverse else (old, new)
        if definition is None or definition.count(source) != 1:
            raise RuntimeError(f"Unexpected definition for {name}")
        op.execute(definition.replace(source, target))


def upgrade() -> None:
    patch_functions()


def downgrade() -> None:
    op.execute("LOCK TABLE work_items, code_reviews, work_leases IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM work_items WHERE manual_review_request ? 'mode') "
        "OR EXISTS(SELECT 1 FROM code_reviews WHERE manual_request ? 'mode')"
    )):
        raise RuntimeError("Review mode history exists; restore a pre-upgrade backup")
    patch_functions(reverse=True)
