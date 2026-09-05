"""Add immutable human reports and atomic closeout/review provenance.

Revision ID: 0021_job_completion_reports
Revises: 0020_project_activity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mnemonic_api.job_report_defaults import DEFAULT_JOB_COMPLETION_REPORT_PROMPT
from mnemonic_api.phase12_db_tables import (
    activity_matrix,
    activity_report_elements,
    follow_up_elements,
    report_count_elements,
    report_elements,
    review_elements,
)

revision: str = "0021_job_completion_reports"
down_revision: str | None = "0020_project_activity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_KINDS = (
    "'create_work', 'add_checkpoint', 'append_event', 'add_relationship', 'update_work', "
    "'defer_work', 'complete_work', 'delete_work', 'remove_relationship', 'release_claim', "
    "'request_human_input', 'resolve_human_input', 'merge_work'"
)
_NEW_KINDS = "'dismiss_job_completion_report', 'create_job_completion_report_follow_up'"


def _schema() -> str:
    value = op.get_bind().scalar(sa.text("SELECT current_schema()"))
    if not isinstance(value, str):
        raise RuntimeError("Phase 12 requires an explicit PostgreSQL schema")
    return op.get_bind().dialect.identifier_preparer.quote_identifier(value)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _validators(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_job_report_text_valid_v1(
        value text, scalar_limit integer, byte_limit integer, multiline boolean
    ) RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    SET search_path=pg_catalog AS $f$
    DECLARE point integer; position integer;
    BEGIN
        IF value IS NULL OR length(value)>scalar_limit OR octet_length(value)>byte_limit
           OR NOT {s}.mnemonic_has_non_whitespace(value) THEN RETURN false; END IF;
        FOR position IN 1..length(value) LOOP
            point := ascii(substr(value,position,1));
            IF (point BETWEEN 0 AND 31 AND NOT (multiline AND point IN (9,10,13)))
               OR point BETWEEN 127 AND 159 OR point BETWEEN 55296 AND 57343
               OR point IN (1564,8206,8207) OR point BETWEEN 8234 AND 8238
               OR point BETWEEN 8294 AND 8303
               OR (NOT multiline AND point IN (8232,8233)) THEN RETURN false; END IF;
        END LOOP;
        RETURN true;
    END $f$;
    CREATE FUNCTION {s}.mnemonic_job_report_fyis_valid_v1(summary text, items text[])
    RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $f$
    DECLARE item text; total bigint := octet_length(summary);
    BEGIN
        IF items IS NULL OR cardinality(items)>10 OR (cardinality(items)>0 AND
            (array_ndims(items)<>1 OR array_lower(items,1)<>1)) THEN RETURN false; END IF;
        FOREACH item IN ARRAY items LOOP
            IF NOT {s}.mnemonic_job_report_text_valid_v1(item,600,2400,false) THEN
                RETURN false;
            END IF;
            total := total+octet_length(item);
        END LOOP;
        RETURN total IS NOT NULL AND total<=16384;
    END $f$;
    """)


def _settings(s: str) -> None:
    op.alter_column("project_settings", "recall_pointer_template", nullable=True)
    op.add_column("project_settings", sa.Column("job_completion_report_prompt", sa.Text()))
    op.add_column(
        "project_settings",
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.execute(
        sa.text(f"UPDATE {s}.project_settings SET job_completion_report_prompt=:prompt").bindparams(
            prompt=DEFAULT_JOB_COMPLETION_REPORT_PROMPT
        )
    )
    op.execute(
        sa.text(
            f"INSERT INTO {s}.project_settings(project_id,job_completion_report_prompt) "
            f"SELECT id,:prompt FROM {s}.projects WHERE id NOT IN "
            f"(SELECT project_id FROM {s}.project_settings)"
        ).bindparams(prompt=DEFAULT_JOB_COMPLETION_REPORT_PROMPT)
    )
    op.alter_column("project_settings", "job_completion_report_prompt", nullable=False)
    op.drop_constraint(
        "fk_project_settings_project_id_projects", "project_settings", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_project_settings_project_id_projects",
        "project_settings",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint("revision_positive", "project_settings", "revision > 0")
    op.create_check_constraint(
        "report_prompt_valid",
        "project_settings",
        "mnemonic_job_report_text_valid_v1(job_completion_report_prompt, 8000, 16384, true)",
    )


def _tables(s: str) -> None:
    op.add_column("work_items", sa.Column("last_reportable_closeout_version", sa.Integer()))
    op.add_column("work_events", sa.Column("job_completion_report_id", sa.UUID()))
    op.create_unique_constraint(
        "uq_work_events_report_owner",
        "work_events",
        ["project_id", "work_item_id", "id", "job_completion_report_id"],
    )
    op.create_table("job_completion_reports", *report_elements())
    op.create_table("job_completion_report_reviews", *review_elements())
    op.create_table("project_job_completion_report_counts", *report_count_elements())
    op.create_table("job_completion_report_follow_ups", *follow_up_elements())
    op.create_foreign_key(
        "fk_work_events_job_report",
        "work_events",
        "job_completion_reports",
        ["project_id", "work_item_id", "job_completion_report_id", "id"],
        ["project_id", "work_item_id", "id", "closeout_event_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    # Attach the new activity columns/constraints only after their source tables exist.
    extras = activity_report_elements()
    for element in extras:
        if isinstance(element, sa.Column):
            op.add_column("project_activity", element)
    for element in extras:
        if isinstance(element, sa.ForeignKeyConstraint):
            references = [item.target_fullname for item in element.elements]
            op.create_foreign_key(
                element.name,
                "project_activity",
                references[0].split(".")[0],
                list(element.column_keys),
                [item.split(".")[1] for item in references],
                ondelete=element.ondelete,
                deferrable=element.deferrable,
                initially=element.initially,
            )
        elif isinstance(element, sa.UniqueConstraint):
            op.create_unique_constraint(
                element.name, "project_activity", list(element._pending_colargs)
            )
        elif isinstance(element, sa.CheckConstraint):
            op.create_check_constraint(element.name, "project_activity", element.sqltext)
        elif isinstance(element, sa.Index):
            op.create_index(
                element.name,
                "project_activity",
                ["job_completion_report_id"],
                unique=True,
                postgresql_where=sa.text("kind = 'job_completion_report_created'"),
            )
    op.drop_constraint(op.f("ck_project_activity_variant_valid"), "project_activity", type_="check")
    op.create_check_constraint("variant_valid", "project_activity", activity_matrix(reports=True))
    op.execute(
        f"INSERT INTO {s}.project_job_completion_report_counts(project_id) "
        f"SELECT id FROM {s}.projects"
    )
    op.drop_constraint(
        op.f("ck_client_operations_operation_kind_valid"), "client_operations", type_="check"
    )
    op.create_check_constraint(
        "operation_kind_valid",
        "client_operations",
        f"operation_kind IN ({_OLD_KINDS}, {_NEW_KINDS})",
    )


def _transition_guards(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_job_report_slot_sealed(work_id uuid, slot integer)
    RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $f$
        SELECT slot IS NULL OR EXISTS (
            SELECT 1 FROM {s}.job_completion_reports report
            JOIN {s}.work_events event ON event.id=report.closeout_event_id
              AND event.job_completion_report_id=report.id
              AND event.project_id=report.project_id AND event.work_item_id=report.work_item_id
            WHERE report.project_id=(SELECT project_id FROM {s}.work_items WHERE id=work_id)
              AND report.work_item_id=work_id AND report.closeout_work_version=slot
        )
    $f$;
    CREATE FUNCTION {s}.mnemonic_guard_job_report_transition()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    BEGIN
        IF TG_RELID <> '{s}.work_items'::regclass
           OR TG_NAME <> 'job_report_transition_guard' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report transition guard misconfigured';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'pending' OR NEW.last_reportable_closeout_version IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='new work must be pending';
            END IF;
            RETURN NEW;
        END IF;
        IF NOT {s}.mnemonic_job_report_slot_sealed(OLD.id,OLD.last_reportable_closeout_version) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='closeout report must seal before departure';
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.last_reportable_closeout_version IS DISTINCT FROM
            OLD.last_reportable_closeout_version THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='closeout witness is database managed';
        END IF;
        IF NEW.status IS DISTINCT FROM OLD.status AND NEW.status IN
            ('done','wont-do','promoted') THEN
            IF OLD.status<>'pending' OR OLD.version=2147483647 OR NEW.version<>OLD.version+1 THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='closeout requires pending transition';
            END IF;
            NEW.last_reportable_closeout_version := NEW.version;
        END IF;
        RETURN NEW;
    END $f$;
    CREATE TRIGGER job_report_transition_guard BEFORE INSERT OR UPDATE OR DELETE ON {s}.work_items
    FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_guard_job_report_transition();

    CREATE FUNCTION {s}.mnemonic_require_job_report_transition_sealed()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    BEGIN
        IF TG_RELID <> '{s}.work_items'::regclass OR TG_OP<>'UPDATE'
           OR TG_NAME <> 'job_report_transition_sealed' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report seal guard misconfigured';
        END IF;
        IF NEW.last_reportable_closeout_version IS DISTINCT FROM
            OLD.last_reportable_closeout_version
           AND NOT {s}.mnemonic_job_report_slot_sealed(
               NEW.id,NEW.last_reportable_closeout_version) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='terminal transition requires a report';
        END IF;
        RETURN NULL;
    END $f$;
    CREATE CONSTRAINT TRIGGER job_report_transition_sealed AFTER UPDATE ON {s}.work_items
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION {s}.mnemonic_require_job_report_transition_sealed();

    CREATE FUNCTION {s}.mnemonic_guard_job_report_event()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE terminal boolean; work {s}.work_items;
    BEGIN
        IF TG_RELID <> '{s}.work_events'::regclass OR TG_OP<>'INSERT'
           OR TG_NAME <> 'job_report_event_guard' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report event guard misconfigured';
        END IF;
        terminal := NEW.event_type='work_completed' OR (NEW.event_type='work_status_changed'
                    AND NEW.metadata->>'to_status' IN ('wont-do','promoted'));
        IF terminal IS NOT TRUE THEN
            IF NEW.job_completion_report_id IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report is not valid on this event';
            END IF;
            RETURN NEW;
        END IF;
        SELECT * INTO STRICT work FROM {s}.work_items WHERE id=NEW.work_item_id;
        IF NEW.job_completion_report_id IS NULL OR NEW.origin<>'live' OR NEW.actor_kind<>'client'
           OR work.last_reportable_closeout_version IS NULL
           OR work.version<>work.last_reportable_closeout_version
           OR NEW.project_id<>work.project_id
           OR NEW.metadata->>'work_version' IS DISTINCT FROM work.version::text
           OR (NEW.event_type='work_completed' AND work.status<>'done')
           OR (NEW.event_type='work_status_changed' AND (
               NEW.metadata->>'from_status' IS DISTINCT FROM 'pending'
               OR NEW.metadata->>'to_status' IS DISTINCT FROM work.status
               OR NEW.metadata->'changes'->'status'->>'before' IS DISTINCT FROM 'pending'
               OR NEW.metadata->'changes'->'status'->>'after' IS DISTINCT FROM work.status
           )) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='report event requires exact transition witness';
        END IF;
        RETURN NEW;
    END $f$;
    CREATE TRIGGER job_report_event_guard BEFORE INSERT ON {s}.work_events
    FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_guard_job_report_event();

    CREATE FUNCTION {s}.mnemonic_guard_job_report_insert()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE work {s}.work_items; event {s}.work_events; settings {s}.project_settings;
    BEGIN
        IF TG_RELID<>'{s}.job_completion_reports'::regclass OR TG_OP<>'INSERT'
           OR TG_NAME<>'job_report_insert_guard' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report insert guard misconfigured';
        END IF;
        SELECT * INTO STRICT work FROM {s}.work_items WHERE id=NEW.work_item_id FOR UPDATE;
        SELECT * INTO STRICT event FROM {s}.work_events WHERE id=NEW.closeout_event_id;
        SELECT * INTO STRICT settings FROM {s}.project_settings WHERE project_id=NEW.project_id;
        IF ROW(NEW.project_id,NEW.closeout_status,
               NEW.closeout_work_version,NEW.work_title_at_closeout)
           IS DISTINCT FROM ROW(work.project_id,work.status,work.version,work.title)
           OR work.last_reportable_closeout_version IS DISTINCT FROM work.version
           OR ROW(event.project_id,event.work_item_id,event.job_completion_report_id,
                  event.actor_client,event.actor_session_id,event.actor_model)
           IS DISTINCT FROM ROW(NEW.project_id,NEW.work_item_id,NEW.id,
                  NEW.actor_client,NEW.actor_session_id,NEW.actor_model)
           OR event.metadata->>'work_version' IS DISTINCT FROM NEW.closeout_work_version::text
           OR NEW.prompt_revision<>settings.revision
           OR NEW.prompt_text<>settings.job_completion_report_prompt
           OR NEW.prompt_sha256<>encode(sha256(convert_to(NEW.prompt_text,'UTF8')),'hex')
           OR (NEW.closeout_status='done' AND (
               event.event_type<>'work_completed'
               OR NEW.completion_checkpoint_id IS DISTINCT FROM event.checkpoint_id
               OR NOT {s}.mnemonic_completion_episode_is_sealed(
                   work.id,work.completion_generation)))
           OR (NEW.closeout_status IN ('wont-do','promoted') AND (
               event.event_type<>'work_status_changed' OR NEW.completion_checkpoint_id IS NOT NULL
               OR event.metadata->>'from_status' IS DISTINCT FROM 'pending'
               OR event.metadata->>'to_status' IS DISTINCT FROM NEW.closeout_status)) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report must match its exact closeout';
        END IF;
        NEW.created_at := clock_timestamp();
        RETURN NEW;
    END $f$;
    CREATE TRIGGER job_report_insert_guard BEFORE INSERT ON {s}.job_completion_reports
    FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_guard_job_report_insert();
    """)


def _review_guards(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_guard_job_report_review()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    BEGIN
        IF TG_RELID<>'{s}.job_completion_report_reviews'::regclass THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report review guard misconfigured';
        END IF;
        IF TG_OP='INSERT' AND pg_trigger_depth()>=2
           AND {s}.mnemonic_phase12_call_path('mnemonic_activity_report_source')
           AND NEW.dismissal_id IS NULL AND NEW.dismissed_at IS NULL
           AND NEW.dismissal_actor_client IS NULL AND NEW.dismissal_actor_session_id IS NULL
           AND NEW.dismissal_actor_model IS NULL AND NEW.follow_up_count=0 THEN
            RETURN NEW;
        END IF;
        IF TG_OP='UPDATE' THEN
            IF ROW(NEW.project_id,NEW.report_id,NEW.work_item_id,NEW.created_sequence)
               IS DISTINCT FROM
                   ROW(OLD.project_id,OLD.report_id,OLD.work_item_id,OLD.created_sequence)
               THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review ownership is immutable';
            END IF;
            IF ROW(NEW.dismissal_id,NEW.dismissed_at,NEW.dismissal_actor_client,
                   NEW.dismissal_actor_session_id,NEW.dismissal_actor_model)
               IS NOT DISTINCT FROM
                   ROW(OLD.dismissal_id,OLD.dismissed_at,OLD.dismissal_actor_client,
                   OLD.dismissal_actor_session_id,OLD.dismissal_actor_model)
               AND OLD.follow_up_count<9223372036854775807
               AND NEW.follow_up_count=OLD.follow_up_count+1 AND pg_trigger_depth()>=2
               AND {s}.mnemonic_phase12_call_path('mnemonic_activity_follow_up_source') THEN
                RETURN NEW;
            END IF;
            IF OLD.dismissal_id IS NULL AND NEW.dismissal_id IS NOT NULL
               AND NEW.follow_up_count=OLD.follow_up_count
               AND NEW.dismissal_actor_client IS NOT NULL
               AND NEW.dismissal_actor_session_id IS NOT NULL THEN
                NEW.dismissed_at := clock_timestamp();
                RETURN NEW;
            END IF;
        END IF;
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report review permits only first dismissal';
    END $f$;
    CREATE TRIGGER job_report_review_guard BEFORE INSERT OR UPDATE OR DELETE
    ON {s}.job_completion_report_reviews FOR EACH ROW
    EXECUTE FUNCTION {s}.mnemonic_guard_job_report_review();

    CREATE FUNCTION {s}.mnemonic_guard_job_report_count()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    BEGIN
        IF TG_RELID<>'{s}.project_job_completion_report_counts'::regclass OR
            pg_trigger_depth()<2 THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report count is source managed';
        END IF;
        IF TG_OP='INSERT' AND NEW.undismissed_count=0
           AND {s}.mnemonic_phase12_call_path('mnemonic_job_report_project_source') THEN RETURN
               NEW; END IF;
        IF TG_OP='UPDATE' AND NEW.project_id=OLD.project_id THEN
            IF OLD.undismissed_count<9223372036854775807
               AND NEW.undismissed_count=OLD.undismissed_count+1
               AND {s}.mnemonic_phase12_call_path('mnemonic_activity_report_source') THEN RETURN
                   NEW; END IF;
            IF OLD.undismissed_count>0 AND NEW.undismissed_count=OLD.undismissed_count-1
               AND {s}.mnemonic_phase12_call_path('mnemonic_activity_review_source') THEN RETURN
                   NEW; END IF;
        END IF;
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report count is source managed';
    END $f$;
    CREATE TRIGGER job_report_count_guard BEFORE INSERT OR UPDATE OR DELETE
    ON {s}.project_job_completion_report_counts FOR EACH ROW
    EXECUTE FUNCTION {s}.mnemonic_guard_job_report_count();

    CREATE FUNCTION {s}.mnemonic_activity_report_source()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE entry {s}.project_activity; allocated bigint;
    BEGIN
        IF TG_RELID<>'{s}.job_completion_reports'::regclass OR TG_OP<>'INSERT'
           OR TG_NAME<>'project_activity_report_source' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report source misconfigured';
        END IF;
        entry.project_id:=NEW.project_id; entry.work_item_id:=NEW.work_item_id;
        entry.kind:='job_completion_report_created'; entry.job_completion_report_id:=NEW.id;
        allocated:={s}.mnemonic_append_project_activity(entry);
        INSERT INTO
            {s}.job_completion_report_reviews(report_id,project_id,work_item_id,created_sequence)
            VALUES(NEW.id,NEW.project_id,NEW.work_item_id,allocated);
        UPDATE {s}.project_job_completion_report_counts SET undismissed_count=undismissed_count+1
            WHERE project_id=NEW.project_id;
        IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='project report count missing'; END IF;
        RETURN NEW;
    END $f$;
    CREATE TRIGGER project_activity_report_source AFTER INSERT ON {s}.job_completion_reports
    FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_activity_report_source();

    CREATE FUNCTION {s}.mnemonic_require_job_report_review()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    BEGIN
        IF TG_RELID<>'{s}.job_completion_reports'::regclass OR TG_OP<>'INSERT'
           OR TG_NAME<>'job_report_review_required' OR NOT EXISTS (
               SELECT 1 FROM {s}.job_completion_report_reviews WHERE report_id=NEW.id
           ) THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report review required'; END IF;
        RETURN NULL;
    END $f$;
    CREATE CONSTRAINT TRIGGER job_report_review_required AFTER INSERT ON {s}.job_completion_reports
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
        {s}.mnemonic_require_job_report_review();

    CREATE FUNCTION {s}.mnemonic_activity_review_source()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE entry {s}.project_activity;
    BEGIN
        IF TG_RELID<>'{s}.job_completion_report_reviews'::regclass OR TG_OP<>'UPDATE'
           OR TG_NAME<>'project_activity_review_source' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report review source misconfigured';
        END IF;
        IF OLD.dismissal_id IS NULL AND NEW.dismissal_id IS NOT NULL THEN
            entry.project_id:=NEW.project_id; entry.work_item_id:=NEW.work_item_id;
            entry.kind:='job_completion_report_dismissed';
                entry.job_completion_report_id:=NEW.report_id;
            entry.human_dismissal_id:=NEW.dismissal_id;
            PERFORM {s}.mnemonic_append_project_activity(entry);
            UPDATE {s}.project_job_completion_report_counts SET
                undismissed_count=undismissed_count-1
                WHERE project_id=NEW.project_id;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='project report count missing'; END IF;
        END IF;
        RETURN NEW;
    END $f$;
    CREATE TRIGGER project_activity_review_source AFTER UPDATE ON {s}.job_completion_report_reviews
    FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_activity_review_source();

    CREATE FUNCTION {s}.mnemonic_activity_follow_up_source()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE entry {s}.project_activity; work {s}.work_items;
    BEGIN
        IF TG_RELID<>'{s}.job_completion_report_follow_ups'::regclass OR TG_OP<>'INSERT'
           OR TG_NAME<>'project_activity_follow_up_source' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report follow-up source misconfigured';
        END IF;
        SELECT * INTO STRICT work FROM {s}.work_items WHERE id=NEW.follow_up_work_item_id;
        IF work.project_id<>NEW.project_id OR work.status<>'pending' OR work.version<>1
           OR work.deleted_at IS NOT NULL OR EXISTS (
               SELECT 1 FROM {s}.work_leases WHERE work_item_id=work.id
           ) OR NOT EXISTS (
               SELECT 1 FROM {s}.work_events event
               JOIN {s}.checkpoints checkpoint ON checkpoint.id=event.checkpoint_id
               WHERE event.work_item_id=work.id AND event.event_type='work_created'
                 AND event.xmin=(pg_current_xact_id()::text::numeric % 4294967296)::text::xid
                 AND ROW(checkpoint.source_client,
                         checkpoint.source_session_id,checkpoint.source_model)
                     IS NOT DISTINCT FROM ROW(NEW.actor_client,NEW.actor_session_id,NEW.actor_model)
           ) OR NOT EXISTS (
               SELECT 1 FROM {s}.job_completion_reports WHERE id=NEW.report_id
                 AND project_id=NEW.project_id AND work_item_id=NEW.source_work_item_id
           ) THEN RAISE EXCEPTION USING ERRCODE='23514',
               MESSAGE='follow-up must own fresh pending work'; END IF;
        entry.project_id:=NEW.project_id; entry.work_item_id:=NEW.follow_up_work_item_id;
        entry.kind:='job_completion_report_follow_up_created';
            entry.job_completion_report_id:=NEW.report_id;
        entry.follow_up_id:=NEW.id;
        NEW.created_sequence:={s}.mnemonic_append_project_activity(entry);
        NEW.created_at:=clock_timestamp();
        UPDATE {s}.job_completion_report_reviews SET follow_up_count=follow_up_count+1
            WHERE report_id=NEW.report_id;
        IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='report review missing'; END IF;
        RETURN NEW;
    END $f$;
    CREATE TRIGGER project_activity_follow_up_source BEFORE INSERT ON
        {s}.job_completion_report_follow_ups
    FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_activity_follow_up_source();
    """)


def _settings_guards(s: str) -> None:
    default = _literal(DEFAULT_JOB_COMPLETION_REPORT_PROMPT)
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_guard_job_report_settings()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE changed boolean;
    BEGIN
        IF TG_RELID<>'{s}.project_settings'::regclass THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='settings guard misconfigured';
        END IF;
        IF TG_OP='INSERT' AND pg_trigger_depth()>=2 AND NEW.revision=1
           AND NEW.recall_pointer_template IS NULL AND NEW.job_completion_report_prompt={default}
           AND {s}.mnemonic_phase12_call_path('mnemonic_job_report_project_source') THEN RETURN
               NEW; END IF;
        IF TG_OP='UPDATE' AND NEW.project_id=OLD.project_id THEN
            changed:=ROW(NEW.recall_pointer_template,NEW.job_completion_report_prompt)
                     IS DISTINCT FROM
                         ROW(OLD.recall_pointer_template,OLD.job_completion_report_prompt);
            IF (changed AND OLD.revision<9223372036854775807 AND NEW.revision=OLD.revision+1)
               OR (NOT changed AND NEW.revision=OLD.revision) THEN RETURN NEW; END IF;
        END IF;
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='project settings require exact revision';
    END $f$;
    CREATE TRIGGER job_report_settings_guard BEFORE INSERT OR UPDATE OR DELETE ON
        {s}.project_settings
    FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_guard_job_report_settings();
    CREATE FUNCTION {s}.mnemonic_job_report_project_source()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    BEGIN
        IF TG_RELID<>'{s}.projects'::regclass OR TG_OP<>'INSERT' OR
            TG_NAME<>'job_report_project_source' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='report project source misconfigured';
        END IF;
        INSERT INTO {s}.project_settings(project_id,job_completion_report_prompt)
            VALUES(NEW.id,{default});
        INSERT INTO {s}.project_job_completion_report_counts(project_id) VALUES(NEW.id);
        RETURN NEW;
    END $f$;
    CREATE TRIGGER job_report_project_source AFTER INSERT ON {s}.projects
    FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_job_report_project_source();
    CREATE FUNCTION {s}.mnemonic_activity_settings_source()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE entry {s}.project_activity;
    BEGIN
        IF TG_RELID<>'{s}.project_settings'::regclass OR TG_OP<>'UPDATE'
           OR TG_NAME<>'project_activity_settings_source' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='settings source misconfigured';
        END IF;
        IF NEW.revision<>OLD.revision THEN
            entry.project_id:=NEW.project_id; entry.kind:='project_settings_updated';
            entry.settings_revision:=NEW.revision;
            PERFORM {s}.mnemonic_append_project_activity(entry);
        END IF;
        RETURN NEW;
    END $f$;
    CREATE TRIGGER project_activity_settings_source AFTER UPDATE ON {s}.project_settings
    FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_activity_settings_source();
    """)


def _extend_allocator(s: str) -> None:
    definition = op.get_bind().scalar(
        sa.text(
            f"SELECT pg_get_functiondef('{s}.mnemonic_append_project_activity"
            f"({s}.project_activity)'::regprocedure)"
        )
    )
    if not isinstance(definition, str):
        raise RuntimeError("Cannot extend missing Phase 12 allocator")
    anchor = f"OR {s}.mnemonic_phase12_call_path('mnemonic_activity_lease_source')"
    if anchor not in definition:
        raise RuntimeError("Phase 12 allocator source path changed unexpectedly")
    additions = "\n".join(
        f"OR {s}.mnemonic_phase12_call_path('{name}')"
        for name in (
            "mnemonic_activity_report_source",
            "mnemonic_activity_review_source",
            "mnemonic_activity_follow_up_source",
            "mnemonic_activity_settings_source",
        )
    )
    op.execute(definition.replace(anchor, anchor + "\n" + additions))


def _immutable_guards(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_reject_job_report_mutation()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    BEGIN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='job report history is immutable';
    END $f$;
    """)
    for table in ("job_completion_reports", "job_completion_report_follow_ups"):
        op.execute(
            f"CREATE TRIGGER job_report_immutable BEFORE UPDATE OR DELETE ON {s}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_reject_job_report_mutation()"
        )
    for table in (
        "job_completion_reports",
        "job_completion_report_reviews",
        "job_completion_report_follow_ups",
        "project_job_completion_report_counts",
        "project_settings",
    ):
        op.execute(
            f"CREATE TRIGGER job_report_truncate_guard BEFORE TRUNCATE ON {s}.{table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {s}.mnemonic_reject_job_report_mutation()"
        )


def upgrade() -> None:
    s = _schema()
    op.execute(
        f"LOCK TABLE {s}.projects, {s}.project_settings, {s}.work_items, {s}.work_events, "
        f"{s}.checkpoints, {s}.client_operations, {s}.project_activity_heads, {s}.project_activity "
        "IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(sa.text("SHOW server_encoding")) != "UTF8":
        raise RuntimeError("Job completion reports require UTF8 database encoding")
    _validators(s)
    _settings(s)
    _tables(s)
    _extend_allocator(s)
    _transition_guards(s)
    _review_guards(s)
    _settings_guards(s)
    _immutable_guards(s)


def _require_unused(s: str) -> None:
    used = op.get_bind().scalar(
        sa.text(f"""
        SELECT EXISTS(SELECT 1 FROM {s}.job_completion_reports)
        OR EXISTS(SELECT 1 FROM {s}.job_completion_report_reviews)
        OR EXISTS(SELECT 1 FROM {s}.job_completion_report_follow_ups)
        OR EXISTS(SELECT 1 FROM {s}.project_job_completion_report_counts WHERE undismissed_count<>0)
        OR EXISTS(SELECT 1 FROM {s}.project_settings
                  WHERE revision<>1 OR job_completion_report_prompt<>:default)
        OR EXISTS(SELECT 1 FROM {s}.work_items WHERE last_reportable_closeout_version IS NOT NULL)
        OR EXISTS(SELECT 1 FROM {s}.work_events WHERE job_completion_report_id IS NOT NULL)
        OR EXISTS(SELECT 1 FROM {s}.project_activity WHERE settings_revision IS NOT NULL
                  OR job_completion_report_id IS NOT NULL
                  OR human_dismissal_id IS NOT NULL OR follow_up_id IS NOT NULL)
        OR EXISTS(SELECT 1 FROM {s}.client_operations WHERE operation_kind IN ({_NEW_KINDS}))
    """),
        {"default": DEFAULT_JOB_COMPLETION_REPORT_PROMPT},
    )
    if used:
        raise RuntimeError("Phase 12 reports/settings have been used; downgrade would lose facts")


def downgrade() -> None:
    s = _schema()
    op.execute(
        f"LOCK TABLE {s}.projects, {s}.project_settings, {s}.work_items, {s}.work_events, "
        f"{s}.checkpoints, {s}.client_operations, "
        f"{s}.project_activity_heads, {s}.project_activity, "
        f"{s}.job_completion_reports, {s}.job_completion_report_reviews, "
        f"{s}.job_completion_report_follow_ups, {s}.project_job_completion_report_counts "
        "IN ACCESS EXCLUSIVE MODE"
    )
    _require_unused(s)
    for table, triggers in {
        "projects": ["job_report_project_source"],
        "project_settings": [
            "job_report_settings_guard",
            "project_activity_settings_source",
            "job_report_truncate_guard",
        ],
        "work_items": ["job_report_transition_guard", "job_report_transition_sealed"],
        "work_events": ["job_report_event_guard"],
    }.items():
        for trigger in triggers:
            op.execute(f"DROP TRIGGER {trigger} ON {s}.{table}")
    definition = op.get_bind().scalar(
        sa.text(
            f"SELECT pg_get_functiondef('{s}.mnemonic_append_project_activity"
            f"({s}.project_activity)'::regprocedure)"
        )
    )
    for name in (
        "mnemonic_activity_report_source",
        "mnemonic_activity_review_source",
        "mnemonic_activity_follow_up_source",
        "mnemonic_activity_settings_source",
    ):
        definition = definition.replace(f"\nOR {s}.mnemonic_phase12_call_path('{name}')", "")
    op.execute(definition)
    for name in (
        "fk_project_activity_report",
        "fk_project_activity_dismissal",
        "fk_project_activity_follow_up",
    ):
        op.drop_constraint(name, "project_activity", type_="foreignkey")
    op.drop_constraint("fk_work_events_job_report", "work_events", type_="foreignkey")
    op.drop_constraint("fk_job_reports_event", "job_completion_reports", type_="foreignkey")
    for table in (
        "job_completion_report_follow_ups",
        "job_completion_report_reviews",
        "job_completion_reports",
        "project_job_completion_report_counts",
    ):
        op.drop_table(table)
    op.drop_constraint("uq_work_events_report_owner", "work_events", type_="unique")
    op.drop_column("work_events", "job_completion_report_id")
    op.drop_column("work_items", "last_reportable_closeout_version")
    op.drop_constraint(op.f("ck_project_activity_variant_valid"), "project_activity", type_="check")
    for column in (
        "job_completion_report_id",
        "human_dismissal_id",
        "follow_up_id",
        "settings_revision",
    ):
        op.drop_column("project_activity", column)
    op.create_check_constraint("variant_valid", "project_activity", activity_matrix(reports=False))
    op.drop_constraint(
        op.f("ck_project_settings_report_prompt_valid"), "project_settings", type_="check"
    )
    op.drop_constraint(
        op.f("ck_project_settings_revision_positive"), "project_settings", type_="check"
    )
    op.execute(f"DELETE FROM {s}.project_settings WHERE recall_pointer_template IS NULL")
    op.drop_column("project_settings", "job_completion_report_prompt")
    op.drop_column("project_settings", "revision")
    op.alter_column("project_settings", "recall_pointer_template", nullable=False)
    op.drop_constraint(
        "fk_project_settings_project_id_projects", "project_settings", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_project_settings_project_id_projects",
        "project_settings",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        op.f("ck_client_operations_operation_kind_valid"), "client_operations", type_="check"
    )
    op.create_check_constraint(
        "operation_kind_valid", "client_operations", f"operation_kind IN ({_OLD_KINDS})"
    )
    for name in (
        "mnemonic_guard_job_report_transition()",
        "mnemonic_require_job_report_transition_sealed()",
        "mnemonic_guard_job_report_event()",
        "mnemonic_guard_job_report_insert()",
        "mnemonic_guard_job_report_review()",
        "mnemonic_guard_job_report_count()",
        "mnemonic_activity_report_source()",
        "mnemonic_require_job_report_review()",
        "mnemonic_activity_review_source()",
        "mnemonic_activity_follow_up_source()",
        "mnemonic_guard_job_report_settings()",
        "mnemonic_job_report_project_source()",
        "mnemonic_activity_settings_source()",
        "mnemonic_reject_job_report_mutation()",
        "mnemonic_job_report_slot_sealed(uuid, integer)",
        "mnemonic_job_report_fyis_valid_v1(text, text[])",
        "mnemonic_job_report_text_valid_v1(text, integer, integer, boolean)",
    ):
        op.execute(f"DROP FUNCTION {s}.{name}")
