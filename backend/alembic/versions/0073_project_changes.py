"""Capture bounded project changes without altering the dashboard activity stream."""

import sqlalchemy as sa
from alembic import op

from mnemonic_api.project_changes_db import change_elements, head_elements

revision = "0073_project_changes"
down_revision = "0072_force_claim_gates"
branch_labels = None
depends_on = None

SOURCES = (
    "projects", "project_settings", "backup_settings", "transcript_settings",
    "project_tags", "knowledge_domains", "decision_documents", "decision_revisions",
    "decision_scopes", "knowledge_deleted_edges", "work_events", "artifact_audit", "transcripts",
    "job_completion_report_reviews", "job_completion_report_follow_ups",
)
GENERIC = {
    "projects": ("project", "name,slug,description,repository_url",
                 "name,slug,repository_url", "name"),
    "project_settings": ("settings", "priority_rubric,lease_default_minutes,"
        "lease_minimum_minutes,lease_maximum_minutes,code_review_required_min_priority,"
        "code_review_optional_min_priority,allow_remediation_code_reviews,"
        "recall_pointer_sha256,job_completion_report_prompt_sha256",
        "lease_default_minutes,lease_minimum_minutes,lease_maximum_minutes,"
        "code_review_required_min_priority,code_review_optional_min_priority,"
        "allow_remediation_code_reviews,recall_pointer_sha256,job_completion_report_prompt_sha256",
        "project settings"),
    "backup_settings": ("settings", "enabled,time,timezone,retention_days",
                        "enabled,time,timezone,retention_days", "backup settings"),
    "transcript_settings": ("settings", "enabled,max_file_size_bytes,directories",
                            "enabled,max_file_size_bytes", "transcript settings"),
    "project_tags": ("tags", "name,description,color,menu_order", "name,color,menu_order", "name"),
    "knowledge_domains": ("knowledge_domains", "name,automatic_decision_reviews,"
        "decision_review_priority,deleted_at", "name,prefix,automatic_decision_reviews,"
        "decision_review_priority,deleted_at", "name"),
}


def _schema() -> str:
    bind = op.get_bind()
    return bind.dialect.identifier_preparer.quote_identifier(
        bind.scalar(sa.text("SELECT current_schema()")))


def _allocator(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_change_pick(value jsonb, keys text[]) RETURNS jsonb
    LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $f$
        SELECT coalesce(jsonb_object_agg(key,value->key),'{{}}'::jsonb)
        FROM unnest(keys) AS key WHERE value ? key;
    $f$;
    CREATE FUNCTION {s}.mnemonic_change_work_state(metadata jsonb, side text) RETURNS jsonb
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $f$
    DECLARE result jsonb:='{{}}'; pair record; safe text[]:=ARRAY[
        'title','status','priority','version','tags'];
    BEGIN
        IF side='after' THEN
            result:={s}.mnemonic_change_pick(coalesce(metadata->'initial','{{}}'),safe);
        END IF;
        FOR pair IN SELECT * FROM jsonb_each(CASE WHEN
            jsonb_typeof(metadata->'changes')='object' THEN metadata->'changes'
            ELSE '{{}}'::jsonb END) LOOP
            IF pair.key=ANY(safe) THEN
                result:=result||jsonb_build_object(pair.key,pair.value->side);
            END IF;
        END LOOP;
        IF side='before' AND metadata ? 'from_status' THEN
            result:=result||jsonb_build_object('status',metadata->'from_status');
        ELSIF side='after' AND metadata ? 'to_status' THEN
            result:=result||jsonb_build_object('status',metadata->'to_status');
        END IF;
        RETURN result;
    END $f$;
    CREATE FUNCTION {s}.mnemonic_append_project_change(entry {s}.project_changes) RETURNS void
    LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    BEGIN
        IF pg_trigger_depth()<1 OR NOT (
            {s}.mnemonic_phase12_call_path('mnemonic_change_generic_source') OR
            {s}.mnemonic_phase12_call_path('mnemonic_change_work_source') OR
            {s}.mnemonic_phase12_call_path('mnemonic_change_artifact_source') OR
            {s}.mnemonic_phase12_call_path('mnemonic_change_decision_source') OR
            {s}.mnemonic_phase12_call_path('mnemonic_change_transcript_source') OR
            {s}.mnemonic_phase12_call_path('mnemonic_change_prompt_source') OR
            {s}.mnemonic_phase12_call_path('mnemonic_change_report_source')
        ) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='change capture requires source trigger';
        END IF;
        INSERT INTO {s}.project_change_heads(project_id) VALUES(entry.project_id)
            ON CONFLICT DO NOTHING;
        UPDATE {s}.project_change_heads SET last_sequence=last_sequence+1
            WHERE project_id=entry.project_id AND last_sequence<9223372036854775807
            RETURNING last_sequence INTO entry.sequence;
        IF entry.sequence IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='project change sequence exhausted';
        END IF;
        entry.recorded_at:=clock_timestamp();
        entry.occurred_at:=coalesce(entry.occurred_at,entry.recorded_at);
        entry.origin:='live';
        entry.changed_fields:=coalesce(entry.changed_fields,'{{}}'::text[]);
        entry.before_state:=coalesce(entry.before_state,'{{}}'::jsonb);
        entry.after_state:=coalesce(entry.after_state,'{{}}'::jsonb);
        INSERT INTO {s}.project_changes SELECT (entry).*;
    END $f$;
    CREATE FUNCTION {s}.mnemonic_change_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    BEGIN
        IF TG_TABLE_NAME='project_changes' THEN
          IF TG_OP='INSERT' AND pg_trigger_depth()>=2
           AND {s}.mnemonic_phase12_call_path('mnemonic_append_project_change')
           AND NEW.origin='live' AND NEW.sequence=(SELECT last_sequence
               FROM {s}.project_change_heads WHERE project_id=NEW.project_id) THEN
            RETURN NEW;
          END IF;
        END IF;
        IF TG_TABLE_NAME='project_change_heads' AND pg_trigger_depth()>=2
           AND {s}.mnemonic_phase12_call_path('mnemonic_append_project_change') THEN
            IF TG_OP='INSERT' AND NEW.last_sequence=0 THEN RETURN NEW; END IF;
            IF TG_OP='UPDATE' AND NEW.project_id=OLD.project_id
               AND NEW.capture_started_at=OLD.capture_started_at
               AND NEW.last_sequence=OLD.last_sequence+1 THEN RETURN NEW; END IF;
        END IF;
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='project changes are source managed';
    END $f$;
    """)


def _generic_source(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_change_generic_source() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE entry {s}.project_changes; previous jsonb:='{{}}'; current jsonb:='{{}}';
            fields text[]; safe text[]; row_value jsonb;
    BEGIN
        IF TG_OP<>'INSERT' THEN previous:=to_jsonb(OLD); END IF;
        IF TG_OP<>'DELETE' THEN current:=to_jsonb(NEW); END IF;
        fields:=string_to_array(TG_ARGV[1],','); safe:=string_to_array(TG_ARGV[2],',');
        SELECT array_agg(k ORDER BY k) INTO entry.changed_fields FROM unnest(fields) k
            WHERE previous->k IS DISTINCT FROM current->k;
        IF entry.changed_fields IS NULL THEN RETURN NULL; END IF;
        row_value:=CASE WHEN TG_OP='DELETE' THEN previous ELSE current END;
        entry.project_id:=coalesce((row_value->>'project_id')::uuid,(row_value->>'id')::uuid);
        entry.resource_id:=coalesce((row_value->>'id')::uuid,entry.project_id);
        entry.resource_type:=TG_ARGV[0]; entry.operation:=TG_TABLE_NAME||'_'||lower(TG_OP);
        IF TG_TABLE_NAME IN ('backup_settings','transcript_settings') THEN
            entry.resource_id:=md5(entry.project_id::text||':settings:'||TG_TABLE_NAME)::uuid;
        END IF;
        IF current->>'deleted_at' IS NOT NULL AND previous->>'deleted_at' IS NULL THEN
            entry.operation:=TG_TABLE_NAME||'_delete';
        END IF;
        entry.title:=left(coalesce(row_value->>TG_ARGV[3],TG_ARGV[3]),500);
        entry.revision:=coalesce(row_value->>'version',row_value->>'revision');
        entry.before_state:={s}.mnemonic_change_pick(previous,safe);
        entry.after_state:={s}.mnemonic_change_pick(current,safe);
        IF TG_TABLE_NAME='knowledge_domains' THEN
            entry.domain_id:=entry.resource_id; entry.domain_prefix:=row_value->>'prefix';
        END IF;
        PERFORM {s}.mnemonic_append_project_change(entry); RETURN NULL;
    END $f$;
    CREATE FUNCTION {s}.mnemonic_change_prompt_source() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE entry {s}.project_changes;
    BEGIN
        IF NEW.sha256 IS NOT DISTINCT FROM OLD.sha256 THEN RETURN NULL; END IF;
        entry.project_id:=NEW.project_id;
        entry.resource_id:=md5(NEW.project_id::text||':prompt:'||NEW.prompt_id)::uuid;
        entry.resource_type:='settings'; entry.operation:='prompt_updated';
        entry.title:=NEW.prompt_id; entry.revision:=NEW.sha256;
        entry.changed_fields:=ARRAY['prompts.'||NEW.prompt_id];
        entry.before_state:=jsonb_build_object('sha256',OLD.sha256);
        entry.after_state:=jsonb_build_object('sha256',NEW.sha256);
        PERFORM {s}.mnemonic_append_project_change(entry); RETURN NULL;
    END $f$;
    """)


def _work_source(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_change_work_source() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE entry {s}.project_changes; item {s}.work_items; safe text[]:=ARRAY[
        'title','status','priority','version','tags']; pair record; facts jsonb;
    BEGIN
        facts:=CASE WHEN NEW.event_type IN ('work_created','work_updated',
            'work_status_changed','work_reopened','work_completed')
            THEN NEW.metadata ELSE '{{}}'::jsonb END;
        SELECT * INTO STRICT item FROM {s}.work_items WHERE id=NEW.work_item_id;
        entry.project_id:=NEW.project_id; entry.resource_id:=NEW.work_item_id;
        entry.resource_type:='work_items'; entry.operation:=NEW.event_type;
        entry.occurred_at:=NEW.created_at; entry.title:=item.title;
        entry.revision:=item.version::text; entry.tags:=item.tags; entry.work_status:=item.status;
        entry.after_state:={s}.mnemonic_change_pick(to_jsonb(item),safe);
        entry.before_state:=entry.after_state-'version'; entry.changed_fields:='{{}}';
        FOR pair IN SELECT * FROM jsonb_each(CASE WHEN
            jsonb_typeof(facts->'changes')='object' THEN facts->'changes'
            ELSE '{{}}'::jsonb END) LOOP
            entry.changed_fields:=array_append(entry.changed_fields,pair.key);
            IF pair.key=ANY(safe) THEN
                entry.before_state:=entry.before_state||jsonb_build_object(pair.key,pair.value->'before');
                entry.after_state:=entry.after_state||jsonb_build_object(pair.key,pair.value->'after');
            END IF;
        END LOOP;
        IF NEW.event_type='work_created' THEN
            entry.before_state:='{{}}';
            entry.after_state:={s}.mnemonic_change_pick(facts->'initial',safe);
        END IF;
        IF facts ? 'from_status' THEN
            entry.before_state:=entry.before_state||jsonb_build_object('status',facts->'from_status');
            entry.after_state:=entry.after_state||jsonb_build_object('status',facts->'to_status');
        END IF;
        PERFORM {s}.mnemonic_append_project_change(entry); RETURN NULL;
    END $f$;
    """)


def _artifact_source(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_change_artifact_source() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE entry {s}.project_changes; item {s}.artifacts; prior jsonb; current_revision jsonb;
            safe text[]:=ARRAY['filename','revision','size_bytes','sha256',
                'mime_type','tags','sensitive'];
    BEGIN
        IF NEW.action NOT IN ('uploaded','replaced','deleted','metadata_updated','linked') THEN
            RETURN NULL;
        END IF;
        SELECT * INTO STRICT item FROM {s}.artifacts WHERE id=NEW.artifact_id;
        entry.project_id:=item.project_id; entry.resource_id:=item.id;
        entry.resource_type:='artifacts'; entry.operation:=NEW.action; entry.title:=NEW.filename;
        entry.occurred_at:=NEW.created_at; entry.revision:=NEW.revision::text;
        entry.tags:=item.tags;
        SELECT to_jsonb(r) INTO prior FROM {s}.artifact_revisions r
            WHERE artifact_id=NEW.artifact_id AND revision=NEW.revision-1;
        SELECT to_jsonb(r) INTO current_revision FROM {s}.artifact_revisions r
            WHERE artifact_id=NEW.artifact_id AND revision=NEW.revision;
        entry.before_state:={s}.mnemonic_change_pick(prior,safe);
        entry.after_state:={s}.mnemonic_change_pick(to_jsonb(item),safe);
        IF NEW.action='deleted' THEN
            entry.before_state:=entry.after_state; entry.after_state:='{{}}';
        ELSIF NEW.action='linked' THEN entry.before_state:=entry.after_state; END IF;
        SELECT array_agg(k ORDER BY k) INTO entry.changed_fields FROM unnest(
            ARRAY['filename','description','size_bytes','sha256','mime_type','tags','sensitive',
                  'related_work_item_ids','related_artifact_ids']) k
            WHERE prior->k IS DISTINCT FROM current_revision->k;
        IF NEW.action='deleted' THEN entry.changed_fields:=ARRAY['deleted_at'];
        ELSIF NEW.action='linked' THEN entry.changed_fields:=ARRAY['links'];
        ELSE entry.changed_fields:=array_append(entry.changed_fields,'revision'); END IF;
        PERFORM {s}.mnemonic_append_project_change(entry); RETURN NULL;
    END $f$;
    """)


def _decision_source(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_change_decision_source() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE entry {s}.project_changes; document {s}.decision_documents;
            domain {s}.knowledge_domains; current jsonb; previous jsonb;
            safe text[]:=ARRAY['title','state','revision','sha256','media_type'];
    BEGIN
        IF TG_TABLE_NAME='decision_documents' THEN
            IF NEW.deleted_at IS NOT DISTINCT FROM OLD.deleted_at THEN RETURN NULL; END IF;
            document:=NEW; entry.operation:='decision_deleted'; entry.occurred_at:=NEW.deleted_at;
            entry.revision:=NEW.current_revision::text;
        ELSE
            SELECT * INTO STRICT document FROM {s}.decision_documents WHERE id=NEW.document_id;
            entry.revision:=NEW.revision::text; entry.occurred_at:=NEW.created_at;
            IF TG_TABLE_NAME='decision_scopes' THEN
                entry.operation:='scope_added'; entry.changed_fields:=ARRAY['implementation_scope'];
                entry.after_state:=jsonb_build_object('scope_id',NEW.id,'commit_hash',NEW.commit_hash);
            ELSE
                entry.operation:=CASE WHEN NEW.revision=0 THEN 'decision_created'
                                      ELSE 'decision_revised' END;
                current:=to_jsonb(NEW);
                SELECT to_jsonb(r) INTO previous FROM {s}.decision_revisions r
                    WHERE r.document_id=NEW.document_id AND r.revision=NEW.revision-1;
                entry.before_state:={s}.mnemonic_change_pick(previous,safe);
                entry.after_state:={s}.mnemonic_change_pick(current,safe);
                SELECT array_agg(k ORDER BY k) INTO entry.changed_fields FROM unnest(
                    ARRAY['title','state','content','media_type']) k
                    WHERE previous->k IS DISTINCT FROM current->k;
                entry.changed_fields:=array_append(entry.changed_fields,'revision');
            END IF;
        END IF;
        SELECT * INTO STRICT domain FROM {s}.knowledge_domains WHERE id=document.domain_id;
        entry.project_id:=document.project_id; entry.resource_id:=document.id;
        entry.resource_type:='decisions'; entry.domain_id:=domain.id;
        entry.domain_prefix:=domain.prefix;
        entry.title:=coalesce(current->>'title',domain.prefix||'-'||document.ordinal);
        PERFORM {s}.mnemonic_append_project_change(entry); RETURN NULL;
    END $f$;
    """)


def _transcript_source(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_change_transcript_source() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE entry {s}.project_changes;
    BEGIN
        IF TG_OP='UPDATE' AND (NEW.copy_sha256 IS NULL OR
            NEW.copy_sha256 IS NOT DISTINCT FROM OLD.copy_sha256) THEN RETURN NULL; END IF;
        entry.project_id:=NEW.import_project_id;
        IF entry.project_id IS NULL THEN
            SELECT project_id INTO STRICT entry.project_id FROM {s}.work_items
                WHERE id=NEW.work_item_id;
        END IF;
        entry.resource_id:=NEW.id; entry.resource_type:='transcripts';
        entry.operation:=CASE WHEN TG_OP='INSERT' THEN 'transcript_registered'
                              ELSE 'transcript_content_updated' END;
        entry.title:=NEW.client||' transcript'; entry.revision:=NEW.generation::text;
        entry.changed_fields:=CASE WHEN TG_OP='INSERT' THEN ARRAY['registration']
                                  ELSE ARRAY['content'] END;
        entry.after_state:=jsonb_build_object('client',NEW.client,'kind',NEW.kind,
            'sha256',NEW.copy_sha256,'size_bytes',NEW.copy_size_bytes);
        IF TG_OP='UPDATE' THEN
            entry.before_state:=jsonb_build_object('client',OLD.client,'kind',OLD.kind,
                'sha256',OLD.copy_sha256,'size_bytes',OLD.copy_size_bytes);
        END IF;
        PERFORM {s}.mnemonic_append_project_change(entry); RETURN NULL;
    END $f$;
    """)


def _report_source(s: str) -> None:
    op.execute(f"""
    CREATE FUNCTION {s}.mnemonic_change_report_source() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
    DECLARE entry {s}.project_changes; item {s}.work_items;
    BEGIN
        entry.project_id:=NEW.project_id; entry.resource_type:='work_items';
        IF TG_TABLE_NAME='job_completion_report_reviews' THEN
            IF NEW.dismissal_id IS NULL OR NEW.dismissal_id IS NOT DISTINCT FROM OLD.dismissal_id
                THEN RETURN NULL; END IF;
            entry.resource_id:=NEW.work_item_id;
            entry.operation:='job_completion_report_dismissed';
            entry.occurred_at:=NEW.dismissed_at;
            entry.after_state:=jsonb_build_object('report_id',NEW.report_id,
                'dismissal_id',NEW.dismissal_id);
            entry.changed_fields:=ARRAY['report_dismissal'];
        ELSE
            entry.resource_id:=NEW.source_work_item_id;
            entry.operation:='job_completion_report_follow_up_created';
            entry.occurred_at:=NEW.created_at;
            entry.after_state:=jsonb_build_object('report_id',NEW.report_id,
                'follow_up_id',NEW.id,'follow_up_work_item_id',NEW.follow_up_work_item_id);
            entry.changed_fields:=ARRAY['report_follow_up'];
        END IF;
        SELECT * INTO STRICT item FROM {s}.work_items WHERE id=entry.resource_id;
        entry.title:=item.title; entry.tags:=item.tags; entry.work_status:=item.status;
        entry.revision:=item.version::text;
        entry.before_state:={s}.mnemonic_change_pick(to_jsonb(item),
            ARRAY['title','status','priority','tags']);
        entry.after_state:=entry.before_state||entry.after_state;
        PERFORM {s}.mnemonic_append_project_change(entry); RETURN NULL;
    END $f$;
    """)


def _backfill(s: str) -> None:
    # Missing historical scalar values stay absent; current state cannot establish them.
    op.execute(f"""
    WITH history AS (
        SELECT e.project_id,'work_items'::text resource_type,e.work_item_id resource_id,
            e.event_type::text operation,e.created_at occurred_at,
            coalesce(e.facts#>>'{{initial,title}}',e.facts#>>'{{changes,title,after}}',
                     'Work item')::text title,
            coalesce(e.facts->>'work_version',e.facts#>>'{{initial,version}}') revision,
            ARRAY(SELECT jsonb_object_keys(CASE WHEN jsonb_typeof(e.facts->'changes')='object'
                THEN e.facts->'changes' ELSE '{{}}'::jsonb END)) changed_fields,
            {s}.mnemonic_change_work_state(e.facts,'before') before_state,
            {s}.mnemonic_change_work_state(e.facts,'after') after_state,
            NULL::uuid domain_id,NULL::text domain_prefix,NULL::varchar(50)[] tags,
            coalesce(e.facts->>'to_status',e.facts#>>'{{initial,status}}',
                     e.facts#>>'{{changes,status,after}}') work_status,
            'work:'||e.id::text source
        FROM (SELECT *, CASE WHEN event_type IN ('work_created','work_updated',
            'work_status_changed','work_reopened','work_completed')
            THEN metadata ELSE '{{}}'::jsonb END AS facts FROM {s}.work_events) e
        UNION ALL
        SELECT a.project_id,'artifacts',a.id,e.action,e.created_at,e.filename,e.revision::text,
            CASE WHEN e.action='deleted' THEN ARRAY['deleted_at']
                 WHEN e.action='linked' THEN ARRAY['links'] ELSE ARRAY['revision'] END,
            CASE WHEN e.action='deleted' THEN
                {s}.mnemonic_change_pick(to_jsonb(r),ARRAY['filename','revision','size_bytes',
                    'sha256','mime_type','tags','sensitive']) ELSE '{{}}'::jsonb END,
            CASE WHEN e.action='deleted' THEN '{{}}'::jsonb ELSE
                {s}.mnemonic_change_pick(to_jsonb(r),ARRAY['filename','revision','size_bytes',
                    'sha256','mime_type','tags','sensitive']) END,
            NULL,NULL,r.tags,NULL,'artifact:'||e.id::text
        FROM {s}.artifact_audit e JOIN {s}.artifacts a ON a.id=e.artifact_id
        JOIN {s}.artifact_revisions r ON r.artifact_id=e.artifact_id AND r.revision=e.revision
        WHERE e.action IN ('uploaded','replaced','deleted','metadata_updated','linked')
        UNION ALL
        SELECT r.project_id,'decisions',r.document_id,
            CASE WHEN r.revision=0 THEN 'decision_created' ELSE 'decision_revised' END,
            r.created_at,r.title,r.revision::text,ARRAY['revision'],'{{}}'::jsonb,
            {s}.mnemonic_change_pick(to_jsonb(r),ARRAY['title','state','revision','sha256','media_type']),
            d.domain_id,k.prefix,NULL,NULL,'decision:'||r.id::text
        FROM {s}.decision_revisions r JOIN {s}.decision_documents d ON d.id=r.document_id
        JOIN {s}.knowledge_domains k ON k.id=d.domain_id
        UNION ALL
        SELECT d.project_id,'decisions',d.id,'decision_deleted',d.deleted_at,
            k.prefix||'-'||d.ordinal,d.current_revision::text,ARRAY['deleted_at'],
            '{{}}'::jsonb,'{{}}'::jsonb,d.domain_id,k.prefix,NULL,NULL,'delete:'||d.id::text
        FROM {s}.decision_documents d JOIN {s}.knowledge_domains k ON k.id=d.domain_id
        WHERE d.deleted_at IS NOT NULL
        UNION ALL
        SELECT r.project_id,'decisions',r.document_id,'scope_added',r.created_at,
            k.prefix||'-'||d.ordinal,r.revision::text,ARRAY['implementation_scope'],'{{}}'::jsonb,
            jsonb_build_object('scope_id',r.id,'commit_hash',r.commit_hash),d.domain_id,k.prefix,
            NULL,NULL,'scope:'||r.id::text
        FROM {s}.decision_scopes r JOIN {s}.decision_documents d ON d.id=r.document_id
        JOIN {s}.knowledge_domains k ON k.id=d.domain_id
        UNION ALL
        SELECT a.project_id,'decisions',d.id,'scope_added',
            (a.row_data->>'created_at')::timestamptz,k.prefix||'-'||d.ordinal,
            a.row_data->>'revision',ARRAY['implementation_scope'],'{{}}'::jsonb,
            jsonb_build_object('scope_id',a.row_data->'id','commit_hash',a.row_data->'commit_hash'),
            d.domain_id,k.prefix,NULL,NULL,'archived_scope:'||(a.row_data->>'id')
        FROM {s}.knowledge_deleted_edges a
        JOIN {s}.decision_documents d ON d.id=(a.row_data->>'document_id')::uuid
        JOIN {s}.knowledge_domains k ON k.id=d.domain_id
        WHERE a.table_name='decision_scopes'
        UNION ALL
        SELECT r.project_id,'work_items',r.work_item_id,'job_completion_report_dismissed',
            r.dismissed_at,'Work item',NULL,ARRAY['report_dismissal'],'{{}}'::jsonb,
            jsonb_build_object('report_id',r.report_id,'dismissal_id',r.dismissal_id),
            NULL,NULL,NULL,NULL,'report_dismissal:'||r.dismissal_id::text
        FROM {s}.job_completion_report_reviews r WHERE r.dismissal_id IS NOT NULL
        UNION ALL
        SELECT f.project_id,'work_items',f.source_work_item_id,
            'job_completion_report_follow_up_created',f.created_at,'Work item',NULL,
            ARRAY['report_follow_up'],'{{}}'::jsonb,jsonb_build_object('report_id',f.report_id,
                'follow_up_id',f.id,'follow_up_work_item_id',f.follow_up_work_item_id),
            NULL,NULL,NULL,NULL,'report_follow_up:'||f.id::text
        FROM {s}.job_completion_report_follow_ups f
    )
    INSERT INTO {s}.project_changes(project_id,sequence,resource_type,resource_id,operation,
        occurred_at,title,revision,changed_fields,before_state,after_state,domain_id,
        domain_prefix,tags,work_status,origin)
    SELECT project_id,row_number() OVER (PARTITION BY project_id ORDER BY occurred_at,source),
        resource_type,resource_id,operation,occurred_at,left(title,500),revision,changed_fields,
        before_state,after_state,domain_id,domain_prefix,tags,work_status,'history_import'
    FROM history;
    UPDATE {s}.project_change_heads h SET last_sequence=coalesce(
        (SELECT max(c.sequence) FROM {s}.project_changes c WHERE c.project_id=h.project_id),0);
    """)


def _triggers(s: str) -> None:
    for table, arguments in GENERIC.items():
        args = ",".join("'" + value + "'" for value in arguments)
        op.execute(f"CREATE TRIGGER project_change_{table}_source AFTER INSERT OR UPDATE OR DELETE "
                   f"ON {s}.{table} FOR EACH ROW EXECUTE FUNCTION "
                   f"{s}.mnemonic_change_generic_source({args})")
    for table, events, function in (
        ("work_events", "INSERT", "work"), ("artifact_audit", "INSERT", "artifact"),
        ("decision_revisions", "INSERT", "decision"), ("decision_scopes", "INSERT", "decision"),
        ("decision_documents", "UPDATE", "decision"),
        ("transcripts", "INSERT OR UPDATE", "transcript"),
        ("project_prompt_observations", "UPDATE", "prompt"),
        ("job_completion_report_reviews", "UPDATE", "report"),
        ("job_completion_report_follow_ups", "INSERT", "report"),
    ):
        op.execute(f"CREATE TRIGGER project_change_{table}_source AFTER {events} ON {s}.{table} "
                   f"FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_change_{function}_source()")
    for table in ("project_changes", "project_change_heads"):
        op.execute(f"CREATE TRIGGER {table}_guard BEFORE INSERT OR UPDATE OR DELETE ON {s}.{table} "
                   f"FOR EACH ROW EXECUTE FUNCTION {s}.mnemonic_change_guard()")
        op.execute(f"CREATE TRIGGER {table}_truncate_guard BEFORE TRUNCATE ON {s}.{table} "
                   f"FOR EACH STATEMENT EXECUTE FUNCTION {s}.mnemonic_change_guard()")


def upgrade() -> None:
    s = _schema()
    op.execute("LOCK TABLE " + ",".join(f"{s}.{table}" for table in SOURCES)
               + " IN ACCESS EXCLUSIVE MODE")
    op.create_table("project_change_heads", *head_elements())
    op.create_table("project_changes", *change_elements())
    op.create_table("project_prompt_observations",
        sa.Column("project_id", sa.UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("prompt_id", sa.String(80), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()))
    op.execute(f"INSERT INTO {s}.project_change_heads(project_id) SELECT id FROM {s}.projects")
    _allocator(s)
    _generic_source(s)
    _work_source(s)
    _artifact_source(s)
    _decision_source(s)
    _transcript_source(s)
    _report_source(s)
    _backfill(s)
    _triggers(s)


def downgrade() -> None:
    s = _schema()
    op.execute("LOCK TABLE " + ",".join(f"{s}.{table}" for table in SOURCES)
               + " IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM project_changes "
                                    "WHERE origin='live')")):
        raise RuntimeError("Project change history exists; export it before downgrading")
    for table in (*GENERIC, "work_events", "artifact_audit", "decision_revisions",
                  "decision_scopes", "decision_documents", "transcripts",
                  "project_prompt_observations", "job_completion_report_reviews",
                  "job_completion_report_follow_ups"):
        op.execute(f"DROP TRIGGER project_change_{table}_source ON {s}.{table}")
    for function in ("generic", "work", "artifact", "decision", "transcript", "prompt", "report"):
        op.execute(f"DROP FUNCTION {s}.mnemonic_change_{function}_source()")
    op.execute(f"DROP FUNCTION {s}.mnemonic_append_project_change({s}.project_changes)")
    op.drop_table("project_prompt_observations")
    op.drop_table("project_changes")
    op.drop_table("project_change_heads")
    op.execute(f"DROP FUNCTION {s}.mnemonic_change_guard()")
    op.execute(f"DROP FUNCTION {s}.mnemonic_change_work_state(jsonb,text)")
    op.execute(f"DROP FUNCTION {s}.mnemonic_change_pick(jsonb,text[])")
