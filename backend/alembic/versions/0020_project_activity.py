"""Add a guarded, transactionally sequenced project activity journal.

Revision ID: 0020_project_activity
Revises: 0019_structured_completion_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mnemonic_api.phase12_db_tables import activity_elements, activity_head_elements

revision: str = "0020_project_activity"
down_revision: str | None = "0019_structured_completion_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    value = op.get_bind().scalar(sa.text("SELECT current_schema()"))
    if not isinstance(value, str):
        raise RuntimeError("Phase 12 requires an explicit PostgreSQL schema")
    return op.get_bind().dialect.identifier_preparer.quote_identifier(value)


def _functions(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_phase12_call_path(function_name text)
    RETURNS boolean LANGUAGE plpgsql SET search_path = pg_catalog AS $f$
    DECLARE stack text;
    BEGIN
        GET DIAGNOSTICS stack = PG_CONTEXT;
        RETURN strpos(stack, 'function ' || function_name || '(') > 0
            OR strpos(stack, 'function ' || '{s}.' || function_name || '(') > 0
            OR strpos(stack, 'function ' || replace('{s}', '"', '') || '.' || function_name ||
                '(') > 0;
    END $f$;

    CREATE FUNCTION {s}.mnemonic_reject_activity_mutation()
    RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $f$
    BEGIN
        IF TG_OP <> 'INSERT' OR TG_RELID <> '{s}.project_activity'::regclass
           OR TG_NAME <> 'project_activity_insert_guard' OR pg_trigger_depth() < 2
           OR NOT {s}.mnemonic_phase12_call_path('mnemonic_append_project_activity')
           OR NEW.origin <> 'live'
           OR NEW.sequence IS DISTINCT FROM (
               SELECT last_sequence FROM {s}.project_activity_heads WHERE project_id=NEW.project_id
           ) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='project activity is source managed';
        END IF;
        RETURN NEW;
    END $f$;

    CREATE FUNCTION {s}.mnemonic_guard_activity_head()
    RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $f$
    BEGIN
        IF TG_RELID <> '{s}.project_activity_heads'::regclass THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='activity head guard misconfigured';
        END IF;
        IF TG_OP = 'INSERT' AND pg_trigger_depth() >= 2
           AND {s}.mnemonic_phase12_call_path('mnemonic_activity_project_source')
           AND NEW.last_sequence=0 AND NEW.historical_through_sequence=0 THEN
            RETURN NEW;
        END IF;
        IF TG_OP = 'UPDATE' THEN
            IF ROW(NEW.project_id,NEW.stream_id,NEW.historical_through_sequence)
               IS NOT DISTINCT FROM
                   ROW(OLD.project_id,OLD.stream_id,OLD.historical_through_sequence)
               AND OLD.last_sequence < 9223372036854775807
               AND NEW.last_sequence=OLD.last_sequence+1 AND pg_trigger_depth() >= 2
               AND {s}.mnemonic_phase12_call_path('mnemonic_append_project_activity') THEN
                RETURN NEW;
            END IF;
            IF ROW(NEW.project_id,NEW.last_sequence,NEW.historical_through_sequence)
               IS NOT DISTINCT FROM
                   ROW(OLD.project_id,OLD.last_sequence,OLD.historical_through_sequence)
               AND NEW.stream_id <> OLD.stream_id
               AND {s}.mnemonic_phase12_call_path(
                   'mnemonic_rotate_activity_streams_after_restore')
               AND 3=(SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid()
                   AND granted AND mode='AccessExclusiveLock' AND relation IN (
                       '{s}.projects'::regclass, '{s}.project_activity_heads'::regclass,
                       '{s}.project_activity'::regclass)) THEN
                RETURN NEW;
            END IF;
        END IF;
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='activity head is source managed';
    END $f$;

    CREATE FUNCTION {s}.mnemonic_append_project_activity(entry {s}.project_activity)
    RETURNS bigint LANGUAGE plpgsql SET search_path = pg_catalog AS $f$
    DECLARE allocated bigint;
    BEGIN
        IF pg_trigger_depth() < 1 OR NOT (
            {s}.mnemonic_phase12_call_path('mnemonic_activity_event_source')
            OR {s}.mnemonic_phase12_call_path('mnemonic_activity_project_source')
            OR {s}.mnemonic_phase12_call_path('mnemonic_activity_lease_source')
        ) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='activity allocator requires source trigger';
        END IF;
        UPDATE {s}.project_activity_heads SET last_sequence=last_sequence+1
        WHERE project_id=entry.project_id AND last_sequence < 9223372036854775807
        RETURNING last_sequence INTO allocated;
        IF allocated IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='activity head missing or exhausted';
        END IF;
        entry.sequence := allocated;
        entry.origin := 'live';
        entry.recorded_at := clock_timestamp();
        INSERT INTO {s}.project_activity SELECT (entry).*;
        RETURN allocated;
    END $f$;

    CREATE FUNCTION {s}.mnemonic_activity_event_source()
    RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $f$
    DECLARE entry {s}.project_activity;
    BEGIN
        IF TG_RELID <> '{s}.work_events'::regclass OR TG_OP <> 'INSERT'
           OR TG_NAME <> 'project_activity_event_source' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='activity event source misconfigured';
        END IF;
        entry.project_id := NEW.project_id; entry.work_item_id := NEW.work_item_id;
        entry.kind := 'work_event'; entry.work_event_id := NEW.id;
        PERFORM {s}.mnemonic_append_project_activity(entry);
        RETURN NEW;
    END $f$;

    CREATE FUNCTION {s}.mnemonic_activity_project_source()
    RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $f$
    DECLARE entry {s}.project_activity;
    BEGIN
        IF TG_RELID <> '{s}.projects'::regclass OR TG_OP NOT IN ('INSERT','UPDATE')
           OR TG_NAME <> 'project_activity_project_source' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='activity project source misconfigured';
        END IF;
        entry.project_id := NEW.id;
        IF TG_OP='INSERT' THEN
            INSERT INTO {s}.project_activity_heads(project_id) VALUES(NEW.id);
            entry.kind := 'project_created';
        ELSIF ROW(NEW.name,NEW.slug,NEW.description,NEW.repository_url) IS DISTINCT FROM
              ROW(OLD.name,OLD.slug,OLD.description,OLD.repository_url) THEN
            entry.kind := 'project_updated';
        ELSE
            RETURN NEW;
        END IF;
        PERFORM {s}.mnemonic_append_project_activity(entry);
        RETURN NEW;
    END $f$;

    CREATE FUNCTION {s}.mnemonic_activity_lease_source()
    RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $f$
    DECLARE entry {s}.project_activity;
    BEGIN
        IF TG_RELID <> '{s}.work_leases'::regclass OR TG_OP <> 'UPDATE'
           OR TG_NAME <> 'project_activity_lease_source' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='activity lease source misconfigured';
        END IF;
        IF NEW.lease_generation_id=OLD.lease_generation_id AND NEW.expires_at<>OLD.expires_at THEN
            SELECT project_id INTO STRICT entry.project_id FROM {s}.work_items
            WHERE id=NEW.work_item_id;
            entry.kind := 'lease_renewed'; entry.work_item_id := NEW.work_item_id;
            entry.lease_generation_id := NEW.lease_generation_id;
            PERFORM {s}.mnemonic_append_project_activity(entry);
        END IF;
        RETURN NEW;
    END $f$;

    CREATE FUNCTION {s}.mnemonic_rotate_activity_streams_after_restore()
    RETURNS bigint LANGUAGE plpgsql SET search_path = pg_catalog AS $f$
    DECLARE changed bigint;
    BEGIN
        LOCK TABLE {s}.projects, {s}.project_activity_heads, {s}.project_activity
            IN ACCESS EXCLUSIVE MODE;
        UPDATE {s}.project_activity_heads SET stream_id=gen_random_uuid();
        GET DIAGNOSTICS changed=ROW_COUNT;
        RETURN changed;
    END $f$;
    """)


def _triggers(s: str) -> None:
    for table, suffix in (
        ("work_events", "event"),
        ("projects", "project"),
        ("work_leases", "lease"),
    ):
        event = {"event": "INSERT", "project": "INSERT OR UPDATE", "lease": "UPDATE"}[suffix]
        op.execute(f"""CREATE TRIGGER project_activity_{suffix}_source
            AFTER {event} ON {s}.{table} FOR EACH ROW
            EXECUTE FUNCTION {s}.mnemonic_activity_{suffix}_source()""")
    op.execute(f"""
        CREATE TRIGGER project_activity_insert_guard BEFORE INSERT ON {s}.project_activity
        FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_reject_activity_mutation();
        CREATE TRIGGER project_activity_immutable BEFORE UPDATE OR DELETE ON {s}.project_activity
        FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_reject_activity_mutation();
        CREATE TRIGGER project_activity_truncate_guard BEFORE TRUNCATE ON {s}.project_activity
        FOR EACH STATEMENT EXECUTE FUNCTION {s}.mnemonic_reject_activity_mutation();
        CREATE TRIGGER project_activity_head_guard BEFORE INSERT OR UPDATE OR DELETE
        ON {s}.project_activity_heads FOR EACH ROW
        EXECUTE FUNCTION {s}.mnemonic_guard_activity_head();
        CREATE TRIGGER project_activity_head_truncate_guard BEFORE TRUNCATE
        ON {s}.project_activity_heads FOR EACH STATEMENT
        EXECUTE FUNCTION {s}.mnemonic_guard_activity_head();
    """)


def upgrade() -> None:
    s = _schema()
    op.execute(
        f"LOCK TABLE {s}.projects, {s}.work_items, {s}.work_events, {s}.work_leases "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.create_unique_constraint(
        "uq_work_events_project_work_id", "work_events", ["project_id", "work_item_id", "id"]
    )
    op.create_table("project_activity_heads", *activity_head_elements())
    op.create_table("project_activity", *activity_elements(reports=False))
    op.execute(f"""
        INSERT INTO {s}.project_activity_heads(project_id) SELECT id FROM {s}.projects;
        INSERT INTO {s}.project_activity(project_id,sequence,kind,work_event_id,work_item_id,origin)
        SELECT project_id,row_number() OVER(PARTITION BY project_id ORDER BY id),
               'work_event',id,work_item_id,'history_import' FROM {s}.work_events;
        UPDATE {s}.project_activity_heads AS head
        SET last_sequence=counts.amount,historical_through_sequence=counts.amount
        FROM (SELECT project_id,count(*) AS amount FROM {s}.project_activity GROUP BY
            project_id) counts
        WHERE head.project_id=counts.project_id;
    """)
    _functions(s)
    _triggers(s)


def downgrade() -> None:
    s = _schema()
    op.execute(
        f"LOCK TABLE {s}.projects, {s}.work_items, {s}.work_events, {s}.work_leases, "
        f"{s}.project_activity_heads, {s}.project_activity IN ACCESS EXCLUSIVE MODE"
    )
    used = op.get_bind().scalar(
        sa.text(
            f"SELECT EXISTS(SELECT 1 FROM {s}.project_activity_heads "
            "WHERE last_sequence <> historical_through_sequence)"
        )
    )
    if used:
        raise RuntimeError("Phase 12 activity has live facts; downgrade would lose history")
    for table, suffix in (
        ("work_events", "event"),
        ("projects", "project"),
        ("work_leases", "lease"),
    ):
        op.execute(f"DROP TRIGGER project_activity_{suffix}_source ON {s}.{table}")
    for name in (
        "mnemonic_activity_event_source",
        "mnemonic_activity_project_source",
        "mnemonic_activity_lease_source",
        "mnemonic_rotate_activity_streams_after_restore",
    ):
        op.execute(f"DROP FUNCTION {s}.{name}()")
    op.execute(f"DROP FUNCTION {s}.mnemonic_append_project_activity({s}.project_activity)")
    op.drop_table("project_activity")
    op.drop_table("project_activity_heads")
    for name in ("mnemonic_reject_activity_mutation", "mnemonic_guard_activity_head"):
        op.execute(f"DROP FUNCTION {s}.{name}()")
    op.execute(f"DROP FUNCTION {s}.mnemonic_phase12_call_path(text)")
    op.drop_constraint("uq_work_events_project_work_id", "work_events", type_="unique")
