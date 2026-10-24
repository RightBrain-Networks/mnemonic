"""Let each knowledge domain inherit or override automatic impact review creation."""

import sqlalchemy as sa
from alembic import op

revision = "0059_domain_decision_reviews"
down_revision = "0058_decision_changes"
branch_labels = None
depends_on = None


def _domain_guard(*, review_policy: bool) -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(
        bind.scalar(sa.text("SELECT current_schema()"))
    )
    old = "ROW(OLD.name,OLD.automatic_decision_reviews)"
    new = "ROW(NEW.name,NEW.automatic_decision_reviews)"
    same = f"{new} IS NOT DISTINCT FROM {old}" if review_policy else "NEW.name = OLD.name"
    changed = f"{new} IS DISTINCT FROM {old}" if review_policy else "NEW.name <> OLD.name"
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {schema}.mnemonic_knowledge_domain_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        BEGIN
            IF (NEW.id, NEW.project_id, NEW.prefix, NEW.created_at) IS DISTINCT FROM
               (OLD.id, OLD.project_id, OLD.prefix, OLD.created_at)
               OR NEW.next_ordinal NOT IN (OLD.next_ordinal, OLD.next_ordinal + 1)
               OR ({same} AND NEW.version <> OLD.version)
               OR ({changed} AND NEW.version <> OLD.version + 1) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid knowledge domain update';
            END IF;
            RETURN NEW;
        END $function$;
    """)


def upgrade() -> None:
    op.add_column("knowledge_domains", sa.Column(
        "automatic_decision_reviews", sa.String(7), nullable=False, server_default="inherit",
    ))
    op.create_check_constraint(
        "automatic_decision_reviews", "knowledge_domains",
        "automatic_decision_reviews IN ('inherit','on','off')",
    )
    _domain_guard(review_policy=True)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM knowledge_domains "
        "WHERE automatic_decision_reviews <> 'inherit')"
    )):
        raise RuntimeError("Domain decision review overrides cannot be safely downgraded.")
    _domain_guard(review_policy=False)
    op.drop_constraint(op.f("ck_knowledge_domains_automatic_decision_reviews"), "knowledge_domains")
    op.drop_column("knowledge_domains", "automatic_decision_reviews")
