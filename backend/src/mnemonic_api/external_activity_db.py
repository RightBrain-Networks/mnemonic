"""Migration 0074 activity capture for resource definitions and lease generations."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.phase12_db_tables import activity_matrix
from mnemonic_api.project_changes_db import resource_type_check


def _schema() -> str:
    connection = op.get_bind()
    return connection.dialect.identifier_preparer.quote_identifier(
        connection.scalar(sa.text("SELECT current_schema()"))
    )


def _constraints(*, external: bool) -> None:
    for table, name, condition in (
        ("project_activity", "variant_valid", activity_matrix(reports=True, external=external)),
        ("project_changes", "resource_type_valid", resource_type_check(external=external)),
    ):
        op.drop_constraint(op.f(f"ck_{table}_{name}"), table)
        op.create_check_constraint(name, table, condition)


def _allocator_sources(s: str, *, add: bool) -> None:
    for allocator, table, existing in (
        ("mnemonic_append_project_change", "project_changes", "mnemonic_change_work_source"),
        ("mnemonic_append_project_activity", "project_activity", "mnemonic_activity_event_source"),
    ):
        definition = op.get_bind().scalar(sa.text(
            "SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"
        ), {"signature": f"{s}.{allocator}({s}.{table})"})
        anchor = f"{s}.mnemonic_phase12_call_path('{existing}')"
        expanded = f"{anchor} OR {s}.mnemonic_phase12_call_path('mnemonic_external_source')"
        old, new = (anchor, expanded) if add else (expanded, anchor)
        if definition is None or definition.count(old) != 1:
            raise RuntimeError(f"Unexpected source guard in {allocator}")
        op.execute(definition.replace(old, new))


def install() -> None:
    s = _schema()
    op.add_column("project_activity", sa.Column("external_resource_id", sa.UUID))
    op.add_column("project_activity", sa.Column("external_lease_id", sa.UUID))
    _constraints(external=True)
    _allocator_sources(s, add=True)
    op.execute(f"""
        CREATE FUNCTION {s}.mnemonic_external_source() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
        DECLARE change {s}.project_changes; activity {s}.project_activity;
            fields text[]; prior jsonb := '{{}}'; fresh jsonb;
        BEGIN
            IF TG_RELID='{s}.external_resources'::regclass THEN
                change.project_id := NEW.project_id;
                change.resource_id := NEW.id;
                change.title := NEW.name;
                fields := ARRAY['name','capacity','minimum_minutes',
                                'default_minutes','maximum_minutes'];
                change.operation := CASE WHEN TG_OP='INSERT' THEN 'external_resource_created'
                                         ELSE 'external_resource_updated' END;
            ELSIF TG_RELID='{s}.external_resource_leases'::regclass THEN
                SELECT project_id,name INTO STRICT change.project_id,change.title
                    FROM {s}.external_resources WHERE id=NEW.resource_id;
                change.resource_id := NEW.resource_id;
                activity.external_lease_id := NEW.id;
                fields := ARRAY['id','holder_client','holder_session_id','quantity',
                                'acquired_at','renewed_at','expires_at','ended_reason','ended_at'];
                change.operation := 'external_lease_' || CASE WHEN TG_OP='INSERT' THEN 'claimed'
                    ELSE coalesce(NEW.ended_reason,'renewed') END;
                IF NEW.ended_reason='expired' THEN change.occurred_at := NEW.expires_at; END IF;
            ELSE
                RAISE EXCEPTION 'Unexpected external lease source';
            END IF;
            IF TG_OP='UPDATE' THEN prior := {s}.mnemonic_change_pick(to_jsonb(OLD),fields); END IF;
            fresh := {s}.mnemonic_change_pick(to_jsonb(NEW),fields);
            SELECT array_agg(k ORDER BY k) INTO change.changed_fields FROM unnest(fields) k
                WHERE prior->k IS DISTINCT FROM fresh->k;
            IF change.changed_fields IS NULL THEN RETURN NULL; END IF;
            change.before_state := prior;
            change.after_state := fresh;
            change.resource_type := 'external_leases';
            activity.project_id := change.project_id;
            activity.external_resource_id := change.resource_id;
            activity.kind := change.operation;
            PERFORM {s}.mnemonic_append_project_change(change);
            PERFORM {s}.mnemonic_append_project_activity(activity);
            RETURN NULL;
        END $f$;
    """)
    for table in ("external_resources", "external_resource_leases"):
        op.execute(f"CREATE TRIGGER external_activity_source AFTER INSERT OR UPDATE ON {s}.{table} "
                   f"FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_external_source()")


def uninstall() -> None:
    s = _schema()
    for table in ("external_resources", "external_resource_leases"):
        op.execute(f"DROP TRIGGER external_activity_source ON {s}.{table}")
    op.execute(f"DROP FUNCTION {s}.mnemonic_external_source()")
    _allocator_sources(s, add=False)
    _constraints(external=False)
    op.drop_column("project_activity", "external_lease_id")
    op.drop_column("project_activity", "external_resource_id")
