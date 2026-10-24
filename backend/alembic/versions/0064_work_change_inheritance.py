"""Seal optional, creation-only exclusions on exact origin discovery edges."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.work_inheritance_db import elements, receipt_witness

revision = "0064_work_change_inheritance"
down_revision = "0063_impact_findings"
branch_labels = None
depends_on = None

TABLE = "work_change_inheritance_exclusions"


def upgrade() -> None:
    op.create_table(TABLE, *elements())
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_work_inheritance_fresh() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM {schema}.work_items child
                JOIN {schema}.client_operations receipt ON receipt.id=NEW.creation_receipt_id
                WHERE child.id=NEW.work_item_id AND child.project_id=receipt.project_id
                  AND child.xmin=pg_current_xact_id()::text::xid
                  AND receipt.xmin=pg_current_xact_id()::text::xid
                  AND receipt.operation_kind='create_work' AND receipt.state='pending'
                  AND NOT EXISTS (SELECT 1 FROM {schema}.work_events event
                                  WHERE event.work_item_id=child.id
                                    AND event.event_type='work_created')
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='inheritance exclusion requires a fresh work creation receipt'; END IF;
            RETURN NEW;
        END $body$;
        CREATE TRIGGER work_inheritance_fresh BEFORE INSERT ON {schema}.{TABLE}
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_work_inheritance_fresh();
        CREATE TRIGGER work_inheritance_immutable BEFORE UPDATE OR DELETE ON {schema}.{TABLE}
            FOR EACH ROW EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        CREATE TRIGGER work_inheritance_no_truncate BEFORE TRUNCATE ON {schema}.{TABLE}
            FOR EACH STATEMENT EXECUTE FUNCTION {schema}.mnemonic_knowledge_immutable();
        CREATE FUNCTION {schema}.mnemonic_work_inheritance_seal() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM {schema}.{TABLE} exclusion
                WHERE exclusion.work_item_id=NEW.work_item_id
                  AND EXISTS ({receipt_witness(schema)})
                  AND EXISTS (SELECT 1 FROM {schema}.work_relationships edge
                      WHERE edge.relationship_type='discovered-from'
                        AND edge.source_work_item_id=exclusion.work_item_id
                        AND edge.target_work_item_id=exclusion.origin_work_item_id
                        AND edge.context_checkpoint_id=exclusion.origin_context_checkpoint_id)
                  AND EXISTS (SELECT 1 FROM {schema}.work_events event
                      WHERE event.work_item_id=exclusion.work_item_id
                        AND event.event_type='work_created')
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='inheritance exclusion requires its sealed creation receipt and origin';
            END IF;
            RETURN NULL;
        END $body$;
        CREATE CONSTRAINT TRIGGER work_inheritance_seal AFTER INSERT ON {schema}.{TABLE}
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION {schema}.mnemonic_work_inheritance_seal();
    """)


def downgrade() -> None:
    op.drop_table(TABLE)
    op.execute("DROP FUNCTION mnemonic_work_inheritance_fresh()")
    op.execute("DROP FUNCTION mnemonic_work_inheritance_seal()")
