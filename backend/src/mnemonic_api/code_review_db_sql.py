"""Frozen SQL contracts for the 0024 code-review migration.

SQL uses an explicitly quoted schema substituted by the migration. All guards
run with pg_catalog search_path and never trust session settings as witnesses.
"""

VALIDATORS = r"""
CREATE FUNCTION SCHEMA.mnemonic_code_review_content_bytes(value jsonb)
RETURNS integer LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $f$
BEGIN
    RETURN octet_length(SCHEMA.mnemonic_code_review_canonical_json(value));
END $f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_canonical_json(value jsonb)
RETURNS text LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $f$
DECLARE output text;
BEGIN
    IF jsonb_typeof(value)='object' THEN
        SELECT '{' || coalesce(string_agg(to_json(key)::text || ':' ||
               SCHEMA.mnemonic_code_review_canonical_json(item), ',' ORDER BY key COLLATE "C"),'')
               || '}' INTO output FROM jsonb_each(value) AS entries(key,item);
    ELSIF jsonb_typeof(value)='array' THEN
        SELECT '[' || coalesce(string_agg(SCHEMA.mnemonic_code_review_canonical_json(item),
                  ',' ORDER BY ordinal),'') || ']' INTO output
        FROM jsonb_array_elements(value) WITH ORDINALITY AS entries(item,ordinal);
    ELSE output:=value::text;
    END IF;
    RETURN output;
END $f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_text_array_valid(value jsonb, count_max int, chars int)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $f$
DECLARE item jsonb;
BEGIN
    IF value IS NULL OR jsonb_typeof(value)<>'array' OR jsonb_array_length(value)>count_max THEN
        RETURN false;
    END IF;
    FOR item IN SELECT * FROM jsonb_array_elements(value) LOOP
        IF jsonb_typeof(item)<>'string' OR NOT SCHEMA.mnemonic_job_report_text_valid_v1(
            item #>> '{}',chars,chars*4,true) THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
END $f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_scope_valid(value jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $f$
DECLARE item jsonb; keys text[]; seen text[] := '{}'; ranges jsonb := '[]'; locator text;
BEGIN
    IF value IS NULL OR jsonb_typeof(value)<>'array' OR jsonb_array_length(value) NOT BETWEEN 1 AND
        10
       OR SCHEMA.mnemonic_code_review_content_bytes(jsonb_build_object('repositories',value))>65536
       THEN RETURN false; END IF;
    FOR item IN SELECT * FROM jsonb_array_elements(value) LOOP
        IF jsonb_typeof(item)<>'object' THEN RETURN false; END IF;
        SELECT array_agg(key) INTO keys FROM jsonb_object_keys(item) key;
        IF EXISTS(SELECT 1 FROM jsonb_each(item) fields WHERE jsonb_typeof(fields.value)<>'string')
            THEN RETURN false; END IF;
        IF NOT (keys @> ARRAY['repository_key','object_format','base_commit','head_commit'])
           OR NOT (keys <@ ARRAY['repository_key','object_format','base_commit','head_commit',
                                'repository_url','checkout_path'])
           OR NOT (item ? 'repository_url' OR item ? 'checkout_path')
           OR jsonb_typeof(item->'repository_key')<>'string'
           OR item->>'repository_key' !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'
           OR item->>'repository_key'=ANY(seen)
           OR item->>'object_format' NOT IN ('sha1','sha256')
           OR jsonb_typeof(item->'base_commit')<>'string'
           OR jsonb_typeof(item->'head_commit')<>'string' THEN RETURN false; END IF;
        IF item->>'object_format'='sha1' THEN
            IF item->>'base_commit' !~ '^[0-9a-f]{40}$'
               OR item->>'head_commit' !~ '^[0-9a-f]{40}$' THEN RETURN false; END IF;
        ELSE
            IF item->>'base_commit' !~ '^[0-9a-f]{64}$'
               OR item->>'head_commit' !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
        END IF;
        seen:=array_append(seen,item->>'repository_key');
        IF ranges @> jsonb_build_array(item-'repository_key'-'object_format') THEN
            RETURN false;
        END IF;
        ranges:=ranges || jsonb_build_array(item-'repository_key'-'object_format');
        FOREACH locator IN ARRAY ARRAY['repository_url','checkout_path'] LOOP
            IF item ? locator AND (jsonb_typeof(item->locator)<>'string' OR NOT
                SCHEMA.mnemonic_job_report_text_valid_v1(item->>locator,
                    CASE WHEN locator='repository_url' THEN 2000 ELSE 4096 END,
                    CASE WHEN locator='repository_url' THEN 8000 ELSE 16384 END,false)
                OR item->>locator ~ '[`$;|<>\\]') THEN RETURN false; END IF;
        END LOOP;
        IF item ? 'repository_url' AND (item->>'repository_url' !~
            '^https://[^/?#@[:space:]]+(/[^?#]*)?$'
            OR NOT SCHEMA.mnemonic_external_url_is_valid(item->>'repository_url')) THEN
            RETURN false;
        END IF;
        IF item ? 'checkout_path' AND left(item->>'checkout_path',1)<>'/' THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
END $f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_handoff_valid(
    summary text, decisions jsonb, focus jsonb, traps jsonb, validation text)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $f$
SELECT SCHEMA.mnemonic_job_report_text_valid_v1(summary,4000,16000,true)
   AND SCHEMA.mnemonic_job_report_text_valid_v1(validation,4000,16000,true)
   AND SCHEMA.mnemonic_code_review_text_array_valid(decisions,20,2000)
   AND SCHEMA.mnemonic_code_review_text_array_valid(focus,20,2000)
   AND SCHEMA.mnemonic_code_review_text_array_valid(traps,20,2000)
   AND SCHEMA.mnemonic_code_review_content_bytes(jsonb_build_object(
       'change_summary',summary,'decisions',decisions,'focus_areas',focus,'traps',traps,
       'validation_summary',validation))<=65536
$f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_finding_valid(value jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $f$
DECLARE keys text[]; field text;
BEGIN
    IF value IS NULL OR jsonb_typeof(value)<>'object' OR
       SCHEMA.mnemonic_code_review_content_bytes(value)>8192 THEN RETURN false; END IF;
    SELECT array_agg(key) INTO keys FROM jsonb_object_keys(value) key;
    IF EXISTS(SELECT 1 FROM jsonb_each(value) fields
        WHERE fields.key NOT IN ('start_line','end_line')
          AND jsonb_typeof(fields.value)<>'string') THEN RETURN false; END IF;
    IF NOT (keys @> ARRAY['finding_key','severity','title','repository_key','path','location_side',
       'problem','triggering_conditions','impact','evidence','recommended_verification'])
       OR NOT (keys <@ ARRAY['finding_key','severity','title','repository_key','path',
           'location_side',
       'problem','triggering_conditions','impact','evidence','recommended_verification',
       'start_line','end_line']) THEN RETURN false; END IF;
    IF value->>'finding_key' !~ '^F[0-9]{3}$'
       OR value->>'severity' NOT IN ('critical','high','medium','low')
       OR value->>'repository_key' !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'
       OR value->>'location_side' NOT IN ('base','head')
       OR NOT SCHEMA.mnemonic_job_report_text_valid_v1(value->>'title',200,800,false)
       OR NOT SCHEMA.mnemonic_job_report_text_valid_v1(value->>'path',4096,16384,false)
       OR value->>'path' ~ '(^/|\\|(^|/)\.\.(/|$))' THEN RETURN false; END IF;
    FOREACH field IN ARRAY ARRAY['problem','triggering_conditions','impact','evidence',
                                'recommended_verification'] LOOP
        IF jsonb_typeof(value->field)<>'string' OR NOT
           SCHEMA.mnemonic_job_report_text_valid_v1(value->>field,2000,8000,true) THEN
            RETURN false;
        END IF;
    END LOOP;
    FOREACH field IN ARRAY ARRAY['start_line','end_line'] LOOP
        IF value ? field AND value->field<>'null'::jsonb AND (
            jsonb_typeof(value->field)<>'number' OR value->>field !~ '^[0-9]+$' OR
            (value->>field)::numeric NOT BETWEEN 1 AND 2147483647) THEN RETURN false; END IF;
    END LOOP;
    IF value->>'end_line' IS NOT NULL AND (value->>'start_line' IS NULL OR
       (value->>'end_line')::integer<(value->>'start_line')::integer) THEN RETURN false; END IF;
    RETURN true;
END $f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_decision(
    priority int, depth int, required_threshold int, optional_threshold int, allow_remediation bool)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $f$
SELECT CASE WHEN depth=2 THEN 'ineligible_depth_limit'
    WHEN depth=1 AND NOT allow_remediation THEN 'ineligible_remediation_disabled'
    WHEN required_threshold<>100 AND priority>=required_threshold THEN 'mandatory'
    WHEN optional_threshold<>100 AND priority>=optional_threshold THEN 'ask_recommendation'
    ELSE 'not_requested' END
$f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_event_references_valid(
    kind text, review uuid, question uuid, answer uuid, result uuid)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $f$
SELECT CASE
    WHEN kind IN ('work_follow_up_requested','work_follow_up_superseded') THEN
        question IS NOT NULL AND review IS NULL AND answer IS NULL AND result IS NULL
    WHEN kind='work_follow_up_answered' THEN
        question IS NOT NULL AND answer IS NOT NULL AND result IS NULL
    WHEN kind IN ('code_review_requested','code_review_superseded') THEN
        review IS NOT NULL AND question IS NULL AND answer IS NULL AND result IS NULL
    WHEN kind='code_review_completed' THEN
        review IS NOT NULL AND result IS NOT NULL AND question IS NULL AND answer IS NULL
    WHEN kind IN ('work_claimed','work_released') THEN
        question IS NULL AND answer IS NULL AND result IS NULL
    ELSE review IS NULL AND question IS NULL AND answer IS NULL AND result IS NULL END
$f$;

CREATE FUNCTION SCHEMA.mnemonic_work_event_metadata_v3_is_valid(
    kind text, origin text, work uuid, checkpoint uuid, generation uuid, release uuid,
    relationship uuid, source uuid, target uuid, context_work uuid, context_checkpoint uuid,
    revision smallint, metadata jsonb, review uuid, question uuid, answer uuid, result uuid)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $f$
DECLARE expected jsonb;
BEGIN
    IF kind IN ('work_follow_up_requested','work_follow_up_answered','work_follow_up_superseded',
                'code_review_requested','code_review_completed','code_review_superseded') THEN
        expected:=jsonb_strip_nulls(jsonb_build_object('code_review_id',review,
            'work_follow_up_id',question,'work_follow_up_answer_id',answer,
            'code_review_result_id',result));
        RETURN origin='live' AND revision=1 AND metadata=expected;
    END IF;
    IF review IS NOT NULL AND kind IN ('work_claimed','work_released') THEN
        IF metadata->>'purpose' IS DISTINCT FROM 'code_review' OR
           metadata->>'code_review_id' IS DISTINCT FROM review::text OR
           metadata->>'mode' NOT IN ('cold','warm') THEN RETURN false; END IF;
        metadata:=metadata-'purpose'-'code_review_id'-'mode';
    END IF;
    RETURN SCHEMA.mnemonic_work_event_metadata_v2_is_valid(kind,origin,work,checkpoint,generation,
        release,relationship,source,target,context_work,context_checkpoint,revision,metadata);
END $f$;
"""

WORK_GUARDS = r"""
CREATE FUNCTION SCHEMA.mnemonic_code_review_checkpoint_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
BEGIN
    IF NEW.requires_code_review_policy THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='checkpoint review witness is database managed';
    END IF;
    NEW.requires_code_review_policy:=(NEW.kind='completion');
    RETURN NEW;
END $f$;
CREATE TRIGGER code_review_checkpoint_guard BEFORE INSERT ON SCHEMA.checkpoints
FOR EACH ROW EXECUTE FUNCTION SCHEMA.mnemonic_code_review_checkpoint_guard();

CREATE FUNCTION SCHEMA.mnemonic_code_review_policy_sealed(checkpoint uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $f$
SELECT checkpoint IS NULL OR EXISTS (
    SELECT 1 FROM SCHEMA.work_completion_review_policies policy
    WHERE policy.completion_checkpoint_id=checkpoint AND CASE policy.decision
        WHEN 'mandatory' THEN
            (SELECT count(*) FROM SCHEMA.code_reviews review WHERE
                review.policy_decision_id=policy.id)=1
            AND NOT EXISTS(SELECT 1 FROM SCHEMA.work_agent_follow_ups question
                           WHERE question.kind_data->>'policy_decision_id'=policy.id::text)
        WHEN 'ask_recommendation' THEN
            (SELECT count(*) FROM SCHEMA.work_agent_follow_ups question
             WHERE question.kind_data->>'policy_decision_id'=policy.id::text)=1
        ELSE NOT EXISTS(SELECT 1 FROM SCHEMA.code_reviews review WHERE
            review.policy_decision_id=policy.id)
             AND NOT EXISTS(SELECT 1 FROM SCHEMA.work_agent_follow_ups question
                            WHERE question.kind_data->>'policy_decision_id'=policy.id::text)
    END)
$f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_work_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE settings SCHEMA.project_settings; snapshot jsonb; checkpoint uuid;
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.completion_review_checkpoint_id IS NOT NULL OR
           NEW.completion_review_policy_snapshot IS NOT NULL THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='review completion witness is database managed';
        END IF;
        RETURN NEW;
    END IF;
    IF NOT SCHEMA.mnemonic_job_report_slot_sealed(OLD.id,OLD.last_reportable_closeout_version) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='closeout report must seal before departure';
    END IF;
    IF NOT SCHEMA.mnemonic_code_review_policy_sealed(OLD.completion_review_checkpoint_id) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review policy must seal before departure';
    END IF;
    IF OLD.remediation_id IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM SCHEMA.code_review_remediations WHERE id=OLD.remediation_id
          AND remediation_work_item_id=OLD.id AND project_id=OLD.project_id
          AND depth=OLD.remediation_depth) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='remediation must seal before departure';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    IF ROW(NEW.remediation_id,NEW.remediation_depth,NEW.completion_review_checkpoint_id,
           NEW.completion_review_policy_snapshot) IS DISTINCT FROM
       ROW(OLD.remediation_id,OLD.remediation_depth,OLD.completion_review_checkpoint_id,
           OLD.completion_review_policy_snapshot) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review lineage and witness are immutable';
    END IF;
    IF NEW.project_id IS DISTINCT FROM OLD.project_id AND (
        OLD.remediation_depth>0 OR OLD.completion_review_checkpoint_id IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review history cannot change projects';
    END IF;
    IF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL AND EXISTS(
        SELECT 1 FROM SCHEMA.work_relationships edge
        WHERE OLD.id IN (edge.source_work_item_id,edge.target_work_item_id)
          AND NOT EXISTS(SELECT 1 FROM SCHEMA.code_review_remediations lineage
                         WHERE lineage.relationship_id=edge.id)) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='ordinary relationships prevent deletion';
    END IF;
    IF NEW.status='done' AND OLD.status IS DISTINCT FROM 'done' THEN
        IF OLD.status<>'pending' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='only pending work can become done';
        END IF;
        SELECT id INTO STRICT checkpoint FROM SCHEMA.checkpoints
        WHERE work_item_id=NEW.id AND kind='completion'
          AND completion_generation=OLD.completion_generation;
        SELECT * INTO STRICT settings FROM SCHEMA.project_settings WHERE project_id=NEW.project_id;
        snapshot:=jsonb_build_object('settings_revision',settings.revision,
            'required_min_priority',settings.code_review_required_min_priority,
            'optional_min_priority',settings.code_review_optional_min_priority,
            'allow_remediation_code_reviews',settings.allow_remediation_code_reviews,
            'priority_at_closeout',NEW.priority,'remediation_depth',NEW.remediation_depth,
            'decision',SCHEMA.mnemonic_code_review_decision(NEW.priority,NEW.remediation_depth,
                settings.code_review_required_min_priority,
                    settings.code_review_optional_min_priority,
                settings.allow_remediation_code_reviews));
        NEW.completion_review_checkpoint_id:=checkpoint;
        NEW.completion_review_policy_snapshot:=snapshot;
    END IF;
    RETURN NEW;
END $f$;
CREATE TRIGGER review_work_guard BEFORE INSERT OR UPDATE OR DELETE ON SCHEMA.work_items
FOR EACH ROW EXECUTE FUNCTION SCHEMA.mnemonic_code_review_work_guard();

CREATE FUNCTION SCHEMA.mnemonic_code_review_move_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE work SCHEMA.work_items;
BEGIN
    SELECT * INTO work FROM SCHEMA.work_items WHERE id=NEW.work_item_id FOR UPDATE;
    IF work.remediation_depth>0 OR work.completion_review_checkpoint_id IS NOT NULL OR EXISTS(
        SELECT 1 FROM SCHEMA.work_completion_review_policies policy
        WHERE policy.project_id=work.project_id AND policy.work_item_id=work.id
    ) OR EXISTS(
        SELECT 1 FROM SCHEMA.code_reviews review
        WHERE review.project_id=work.project_id AND review.work_item_id=work.id
    ) OR EXISTS(
        SELECT 1 FROM SCHEMA.work_agent_follow_ups question
        WHERE question.project_id=work.project_id AND question.work_item_id=work.id
    ) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review history cannot change projects';
    END IF;
    RETURN NEW;
END $f$;
CREATE TRIGGER code_review_move_guard BEFORE INSERT ON SCHEMA.work_item_moves
FOR EACH ROW EXECUTE FUNCTION SCHEMA.mnemonic_code_review_move_guard();

CREATE FUNCTION SCHEMA.mnemonic_code_review_work_sealed()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE retained SCHEMA.work_items;
BEGIN
    IF NOT SCHEMA.mnemonic_code_review_policy_sealed(NEW.completion_review_checkpoint_id) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Done requires its exact review policy';
    END IF;
    SELECT * INTO retained FROM SCHEMA.work_items WHERE id=NEW.id;
    IF FOUND AND EXISTS(SELECT 1 FROM SCHEMA.code_reviews review
        WHERE review.work_item_id=retained.id AND review.state='requested' AND
        (retained.status<>'done' OR retained.deleted_at IS NOT NULL OR
         retained.completion_review_checkpoint_id IS DISTINCT FROM review.completion_checkpoint_id))
       OR EXISTS(SELECT 1 FROM SCHEMA.work_agent_follow_ups question
        WHERE question.work_item_id=NEW.id AND question.state='pending' AND
        (retained.status<>'done' OR retained.deleted_at IS NOT NULL OR
         retained.completion_review_checkpoint_id IS DISTINCT FROM
             question.completion_checkpoint_id))
       THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='outstanding review must be superseded';
    END IF;
    RETURN NULL;
END $f$;
CREATE CONSTRAINT TRIGGER code_review_work_sealed AFTER INSERT OR UPDATE ON SCHEMA.work_items
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
    SCHEMA.mnemonic_code_review_work_sealed();

CREATE FUNCTION SCHEMA.mnemonic_code_review_policy_insert()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE work SCHEMA.work_items; event SCHEMA.work_events; report SCHEMA.job_completion_reports;
BEGIN
    SELECT * INTO STRICT work FROM SCHEMA.work_items WHERE id=NEW.work_item_id;
    SELECT * INTO STRICT event FROM SCHEMA.work_events WHERE id=NEW.completion_event_id;
    SELECT * INTO STRICT report FROM SCHEMA.job_completion_reports
        WHERE completion_checkpoint_id=NEW.completion_checkpoint_id;
    IF work.status<>'done' OR work.deleted_at IS NOT NULL OR
       work.completion_review_checkpoint_id IS DISTINCT FROM NEW.completion_checkpoint_id OR
       work.completion_review_policy_snapshot IS DISTINCT FROM
          (to_jsonb(NEW)-ARRAY['id','project_id','work_item_id','completion_checkpoint_id',
                              'completion_event_id','created_at']) OR
       ROW(event.project_id,event.work_item_id,event.checkpoint_id,event.event_type)
       IS DISTINCT FROM ROW(NEW.project_id,NEW.work_item_id,NEW.completion_checkpoint_id,
           'work_completed')
       OR report.prompt_revision<>NEW.settings_revision THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review policy requires actual Done witness';
    END IF;
    RETURN NEW;
END $f$;
CREATE TRIGGER code_review_policy_insert BEFORE INSERT ON SCHEMA.work_completion_review_policies
FOR EACH ROW EXECUTE FUNCTION SCHEMA.mnemonic_code_review_policy_insert();

CREATE FUNCTION SCHEMA.mnemonic_code_review_edge_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
BEGIN
    IF EXISTS(SELECT 1 FROM SCHEMA.code_review_remediations WHERE relationship_id=OLD.id) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review provenance edge is immutable';
    END IF;
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $f$;
CREATE TRIGGER code_review_edge_guard BEFORE UPDATE OR DELETE ON SCHEMA.work_relationships
FOR EACH ROW EXECUTE FUNCTION SCHEMA.mnemonic_code_review_edge_guard();

CREATE FUNCTION SCHEMA.mnemonic_code_review_merge_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
BEGIN
    IF EXISTS(SELECT 1 FROM SCHEMA.work_items WHERE id IN
        (NEW.source_work_item_id,NEW.destination_work_item_id) AND remediation_depth>0) OR
       EXISTS(SELECT 1 FROM SCHEMA.code_reviews WHERE work_item_id IN
        (NEW.source_work_item_id,NEW.destination_work_item_id) AND state='requested') OR
       EXISTS(SELECT 1 FROM SCHEMA.work_agent_follow_ups WHERE work_item_id IN
        (NEW.source_work_item_id,NEW.destination_work_item_id) AND state='pending') THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review provenance forbids duplicate merge';
    END IF;
    RETURN NEW;
END $f$;
CREATE TRIGGER code_review_merge_guard BEFORE INSERT ON SCHEMA.work_duplicate_merges
FOR EACH ROW EXECUTE FUNCTION SCHEMA.mnemonic_code_review_merge_guard();

CREATE FUNCTION SCHEMA.mnemonic_code_review_lease_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE work SCHEMA.work_items; review SCHEMA.code_reviews;
BEGIN
    IF TG_OP='UPDATE' AND NEW.lease_generation_id=OLD.lease_generation_id AND
       ROW(NEW.purpose,NEW.code_review_id,NEW.mode) IS DISTINCT FROM
       ROW(OLD.purpose,OLD.code_review_id,OLD.mode) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review lease purpose is generation-bound';
    END IF;
    IF NEW.purpose<>'code_review' THEN RETURN NEW; END IF;
    IF TG_OP='UPDATE' AND NEW.lease_generation_id=OLD.lease_generation_id
       AND NEW.expires_at>OLD.expires_at AND OLD.expires_at<=clock_timestamp() THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='expired review lease cannot be revived';
    END IF;
    SELECT * INTO STRICT work FROM SCHEMA.work_items WHERE id=NEW.work_item_id;
    SELECT * INTO STRICT review FROM SCHEMA.code_reviews WHERE id=NEW.code_review_id;
    -- A release marker is a final lifecycle fact; the request may already be superseded/completed.
    IF TG_OP='UPDATE' AND NEW.pending_release_id IS NOT NULL AND OLD.pending_release_id IS NULL
       AND (to_jsonb(NEW)-'pending_release_id')=(to_jsonb(OLD)-'pending_release_id') THEN RETURN
           NEW;
    END IF;
    IF work.status<>'done' OR work.deleted_at IS NOT NULL OR work.remediation_depth=2 OR
       review.state<>'requested' OR review.work_item_id<>work.id OR
       work.completion_review_checkpoint_id IS DISTINCT FROM review.completion_checkpoint_id THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='review lease requires eligible current request';
    END IF;
    RETURN NEW;
END $f$;
CREATE TRIGGER code_review_lease_guard BEFORE INSERT OR UPDATE ON SCHEMA.work_leases
FOR EACH ROW EXECUTE FUNCTION SCHEMA.mnemonic_code_review_lease_guard();
"""

RESOURCE_GUARDS = r"""
CREATE FUNCTION SCHEMA.mnemonic_code_review_current_work(work_id uuid, checkpoint uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $f$
SELECT EXISTS(SELECT 1 FROM SCHEMA.work_items work WHERE work.id=work_id AND work.status='done'
    AND work.deleted_at IS NULL AND work.remediation_depth<2
    AND work.completion_review_checkpoint_id=checkpoint AND NOT EXISTS(
        SELECT 1 FROM SCHEMA.work_duplicate_merges WHERE source_work_item_id=work.id))
$f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_resource_insert()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE policy SCHEMA.work_completion_review_policies; question SCHEMA.work_agent_follow_ups;
    review SCHEMA.code_reviews; answer SCHEMA.work_agent_follow_up_answers;
    result SCHEMA.code_review_results; lease SCHEMA.work_leases; claim SCHEMA.work_events;
    checkpoint SCHEMA.checkpoints; work SCHEMA.work_items; child SCHEMA.work_items;
    parent SCHEMA.code_review_remediations; edge SCHEMA.work_relationships;
BEGIN
    IF TG_TABLE_NAME='work_agent_follow_ups' THEN
        SELECT * INTO STRICT policy FROM SCHEMA.work_completion_review_policies
            WHERE id::text=NEW.kind_data->>'policy_decision_id';
        SELECT * INTO STRICT checkpoint FROM SCHEMA.checkpoints WHERE
            id=NEW.completion_checkpoint_id;
        IF NEW.state<>'pending' OR NEW.version<>1 OR NEW.answer_id IS NOT NULL OR
           NEW.superseded_by_event_id IS NOT NULL OR policy.decision<>'ask_recommendation' OR
           NEW.kind_data<>jsonb_build_object('policy_decision_id',policy.id::text) OR
           ROW(NEW.project_id,NEW.work_item_id,NEW.completion_checkpoint_id,NEW.trigger_event_id)
           IS DISTINCT FROM ROW(policy.project_id,policy.work_item_id,
                                policy.completion_checkpoint_id,policy.completion_event_id) OR
           ROW(NEW.origin_client,NEW.origin_session_id,NEW.origin_model) IS DISTINCT FROM
           ROW(checkpoint.source_client,checkpoint.source_session_id,checkpoint.source_model) OR
           NEW.audience IS DISTINCT FROM (CASE WHEN NEW.origin_client='dashboard'
               AND NEW.origin_model IS NULL THEN 'origin_human' ELSE 'origin_agent' END) OR
           NOT SCHEMA.mnemonic_code_review_current_work(NEW.work_item_id,
               NEW.completion_checkpoint_id)
           THEN RAISE EXCEPTION USING ERRCODE='23514',
               MESSAGE='question requires optional closeout';
        END IF;
    ELSIF TG_TABLE_NAME='code_reviews' THEN
        SELECT * INTO STRICT policy FROM SCHEMA.work_completion_review_policies
            WHERE id=NEW.policy_decision_id;
        SELECT * INTO STRICT checkpoint FROM SCHEMA.checkpoints WHERE
            id=NEW.completion_checkpoint_id;
        IF NEW.state<>'requested' OR NEW.version<>1 OR NEW.result_id IS NOT NULL OR
           NEW.superseded_by_event_id IS NOT NULL OR
           ROW(NEW.project_id,NEW.work_item_id,NEW.completion_checkpoint_id,NEW.completion_event_id)
           IS DISTINCT FROM ROW(policy.project_id,policy.work_item_id,
                                policy.completion_checkpoint_id,policy.completion_event_id) OR
           NOT SCHEMA.mnemonic_code_review_current_work(NEW.work_item_id,
               NEW.completion_checkpoint_id)
           THEN RAISE EXCEPTION USING ERRCODE='23514',
               MESSAGE='review requires eligible Done episode';
        END IF;
        IF NEW.request_reason='mandatory' THEN
            IF policy.decision<>'mandatory' OR
               ROW(NEW.requesting_client,NEW.requesting_session_id,NEW.requesting_model)
               IS DISTINCT FROM
               ROW(checkpoint.source_client,checkpoint.source_session_id,checkpoint.source_model)
                   THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='mandatory review must match origin';
            END IF;
        ELSE
            SELECT * INTO STRICT answer FROM SCHEMA.work_agent_follow_up_answers WHERE
                id=NEW.answer_id;
            SELECT * INTO STRICT question FROM SCHEMA.work_agent_follow_ups WHERE
                id=answer.follow_up_id;
            IF policy.decision<>'ask_recommendation' OR NOT answer.recommend_review OR
               answer.code_review_id<>NEW.id OR
                   question.kind_data->>'policy_decision_id'<>policy.id::text
               OR ROW(NEW.requesting_client,NEW.requesting_session_id,NEW.requesting_model)
               IS DISTINCT FROM ROW(answer.actor_client,answer.actor_session_id,answer.actor_model)
                   THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='recommended review requires its answer';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='work_agent_follow_up_answers' THEN
        SELECT * INTO STRICT question FROM SCHEMA.work_agent_follow_ups WHERE id=NEW.follow_up_id;
        IF question.state<>'pending' OR
           ROW(NEW.project_id,NEW.work_item_id,NEW.actor_client,NEW.actor_session_id) IS DISTINCT
               FROM
           ROW(question.project_id,question.work_item_id,question.origin_client,
               question.origin_session_id)
           OR NOT SCHEMA.mnemonic_code_review_current_work(NEW.work_item_id,
               question.completion_checkpoint_id)
           THEN RAISE EXCEPTION USING ERRCODE='23514',
               MESSAGE='answer requires pending origin question';
        END IF;
    ELSIF TG_TABLE_NAME IN ('code_review_scopes','code_review_handoffs') THEN
        SELECT * INTO STRICT review FROM SCHEMA.code_reviews WHERE id=NEW.review_id;
        IF review.created_event_id IS NOT NULL OR review.state<>'requested' OR
           ROW(review.project_id,review.work_item_id) IS DISTINCT FROM ROW(NEW.project_id,
               NEW.work_item_id)
           THEN RAISE EXCEPTION USING ERRCODE='23514',
               MESSAGE='scope and handoff seal at request creation';
        END IF;
    ELSIF TG_TABLE_NAME='code_review_results' THEN
        SELECT * INTO STRICT review FROM SCHEMA.code_reviews WHERE id=NEW.review_id;
        SELECT * INTO STRICT lease FROM SCHEMA.work_leases WHERE work_item_id=NEW.work_item_id;
        SELECT * INTO STRICT claim FROM SCHEMA.work_events WHERE id=NEW.claim_event_id;
        IF review.state<>'requested' OR NOT SCHEMA.mnemonic_code_review_current_work(
            NEW.work_item_id,review.completion_checkpoint_id) OR
           ROW(NEW.project_id,NEW.work_item_id,NEW.scope_sha256) IS DISTINCT FROM
           ROW(review.project_id,review.work_item_id,review.scope_sha256) OR
           lease.expires_at<=clock_timestamp() OR lease.purpose<>'code_review' OR
           ROW(lease.code_review_id,lease.mode,lease.holder_client,lease.holder_session_id,
               lease.lease_generation_id) IS DISTINCT FROM
           ROW(NEW.review_id,NEW.mode,NEW.actor_client,NEW.actor_session_id,
               NEW.lease_generation_id) OR
           ROW(claim.project_id,claim.work_item_id,claim.code_review_id,claim.lease_generation_id,
               claim.event_type,claim.actor_client,claim.actor_session_id,claim.metadata->>'mode')
           IS DISTINCT FROM ROW(NEW.project_id,NEW.work_item_id,NEW.review_id,
               NEW.lease_generation_id,
               'work_claimed',NEW.actor_client,NEW.actor_session_id,NEW.mode) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='review result requires exact live review lease';
        END IF;
    ELSIF TG_TABLE_NAME='code_review_findings' THEN
        SELECT * INTO STRICT result FROM SCHEMA.code_review_results WHERE id=NEW.result_id;
        IF result.created_event_id IS NOT NULL OR NEW.position>=result.findings_count OR
           NOT EXISTS(SELECT 1 FROM jsonb_array_elements(result.coverage) repo
               WHERE repo->>'repository_key'=NEW.data->>'repository_key') THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='findings must seal with their result';
        END IF;
    ELSIF TG_TABLE_NAME='code_review_remediations' THEN
        SELECT * INTO STRICT result FROM SCHEMA.code_review_results WHERE id=NEW.result_id;
        SELECT * INTO STRICT review FROM SCHEMA.code_reviews WHERE id=NEW.review_id;
        SELECT * INTO STRICT work FROM SCHEMA.work_items WHERE id=NEW.source_work_item_id;
        SELECT * INTO STRICT child FROM SCHEMA.work_items WHERE id=NEW.remediation_work_item_id;
        SELECT * INTO STRICT edge FROM SCHEMA.work_relationships WHERE id=NEW.relationship_id;
        IF result.created_event_id IS NOT NULL OR result.findings_count=0 OR
            result.review_id<>review.id OR
           NEW.completion_checkpoint_id<>review.completion_checkpoint_id OR
               work.remediation_depth>=2 OR
           NEW.depth<>work.remediation_depth+1 OR child.status<>'pending' OR child.deleted_at IS
               NOT NULL OR
           child.remediation_id IS DISTINCT FROM NEW.id OR child.remediation_depth<>NEW.depth OR
           ROW(edge.project_id,edge.source_work_item_id,edge.target_work_item_id,
               edge.context_checkpoint_work_item_id,edge.context_checkpoint_id,
                   edge.relationship_type)
           IS DISTINCT FROM ROW(NEW.project_id,NEW.remediation_work_item_id,NEW.source_work_item_id,
               NEW.source_work_item_id,NEW.completion_checkpoint_id,'discovered-from') THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='remediation requires exact fresh result lineage';
        END IF;
        IF work.remediation_depth=0 THEN
            IF NEW.root_work_item_id<>work.id OR NEW.parent_remediation_id IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='first remediation root must be source';
            END IF;
        ELSE
            SELECT * INTO STRICT parent FROM SCHEMA.code_review_remediations WHERE
                id=work.remediation_id;
            IF NEW.parent_remediation_id IS DISTINCT FROM parent.id OR
               NEW.root_work_item_id<>parent.root_work_item_id THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='second remediation must retain its root';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_resource_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE old_data jsonb; new_data jsonb; allowed text[];
BEGIN
    IF TG_OP IN ('DELETE','TRUNCATE') THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='code-review history is immutable';
    END IF;
    old_data:=to_jsonb(OLD); new_data:=to_jsonb(NEW);
    allowed:=ARRAY['created_event_id','created_sequence'];
    IF TG_TABLE_NAME IN ('code_reviews','work_agent_follow_ups') THEN
        allowed:=allowed || ARRAY['state','version','superseded_by_event_id'] ||
            CASE WHEN TG_TABLE_NAME='code_reviews' THEN ARRAY['result_id'] ELSE ARRAY['answer_id']
                END;
        IF NEW.state IS DISTINCT FROM OLD.state THEN
            IF OLD.version<>1 OR NEW.version<>2 OR
               (TG_TABLE_NAME='code_reviews' AND (OLD.state<>'requested' OR
                                                NEW.state NOT IN ('completed','superseded'))) OR
               (TG_TABLE_NAME='work_agent_follow_ups' AND (OLD.state<>'pending' OR
                                                NEW.state NOT IN ('answered','superseded'))) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid review resource transition';
            END IF;
        ELSIF NEW.version<>OLD.version OR (new_data-ARRAY['created_event_id','created_sequence'])
            IS DISTINCT FROM (old_data-ARRAY['created_event_id','created_sequence']) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='review state references require transition';
        END IF;
    ELSIF TG_TABLE_NAME NOT IN ('work_agent_follow_up_answers','code_review_results') THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='code-review history is immutable';
    END IF;
    IF (new_data-allowed) IS DISTINCT FROM (old_data-allowed) OR
       (old_data->>'created_event_id' IS NOT NULL AND
        new_data->'created_event_id' IS DISTINCT FROM old_data->'created_event_id') OR
       (old_data->>'created_sequence' IS NOT NULL AND
        new_data->'created_sequence' IS DISTINCT FROM old_data->'created_sequence') THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review creation facts cannot be rewritten';
    END IF;
    RETURN NEW;
END $f$;
"""

SEAL_GUARDS = r"""
CREATE FUNCTION SCHEMA.mnemonic_code_review_event_matches(
    event_id bigint, kind text, project uuid, work uuid, resource_key text, resource uuid,
    client text, session_id text, model text)
RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $f$
SELECT EXISTS(SELECT 1 FROM SCHEMA.work_events event WHERE event.id=event_id
    AND event.project_id=project AND event.work_item_id=work AND event.event_type=kind
    AND event.metadata->>resource_key=resource::text AND event.actor_kind='client'
    AND ROW(event.actor_client,event.actor_session_id,event.actor_model)
        IS NOT DISTINCT FROM ROW(client,session_id,model))
$f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_assert_question(question_id uuid)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE question SCHEMA.work_agent_follow_ups; answer SCHEMA.work_agent_follow_up_answers;
BEGIN
    SELECT * INTO STRICT question FROM SCHEMA.work_agent_follow_ups WHERE id=question_id;
    IF NOT SCHEMA.mnemonic_code_review_event_matches(question.created_event_id,
        'work_follow_up_requested',question.project_id,question.work_item_id,'work_follow_up_id',
        question.id,question.origin_client,question.origin_session_id,question.origin_model)
       OR NOT EXISTS(SELECT 1 FROM SCHEMA.project_activity WHERE project_id=question.project_id
           AND sequence=question.created_sequence AND work_event_id=question.created_event_id) THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='question requires exact creation event and activity';
    END IF;
    IF question.state='pending' THEN
        IF question.version<>1 OR question.answer_id IS NOT NULL OR
           question.superseded_by_event_id IS NOT NULL OR
           EXISTS(SELECT 1 FROM SCHEMA.work_agent_follow_up_answers WHERE follow_up_id=question.id)
           OR NOT SCHEMA.mnemonic_code_review_current_work(question.work_item_id,
                                                          question.completion_checkpoint_id) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='pending question state is incoherent';
        END IF;
    ELSIF question.state='answered' THEN
        SELECT * INTO STRICT answer FROM SCHEMA.work_agent_follow_up_answers WHERE
            id=question.answer_id;
        IF question.version<>2 OR question.superseded_by_event_id IS NOT NULL OR
           answer.follow_up_id<>question.id OR NOT SCHEMA.mnemonic_code_review_event_matches(
            answer.created_event_id,'work_follow_up_answered',answer.project_id,answer.work_item_id,
            'work_follow_up_answer_id',answer.id,answer.actor_client,answer.actor_session_id,
                answer.actor_model)
           OR NOT EXISTS(SELECT 1 FROM SCHEMA.work_events WHERE id=answer.created_event_id
                         AND work_follow_up_id=question.id) OR
           (answer.recommend_review AND NOT EXISTS(SELECT 1 FROM SCHEMA.code_reviews
               WHERE id=answer.code_review_id AND answer_id=answer.id
                 AND policy_decision_id::text=question.kind_data->>'policy_decision_id')) OR
           (NOT answer.recommend_review AND EXISTS(SELECT 1 FROM SCHEMA.code_reviews
               WHERE policy_decision_id::text=question.kind_data->>'policy_decision_id')) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='question answer is not sealed';
        END IF;
    ELSE
        IF question.version<>2 OR question.answer_id IS NOT NULL OR
           NOT EXISTS(SELECT 1 FROM SCHEMA.work_events event WHERE
               id=question.superseded_by_event_id
                      AND event_type='work_follow_up_superseded' AND work_follow_up_id=question.id)
           OR NOT EXISTS(SELECT 1 FROM SCHEMA.work_events event WHERE
               event.work_item_id=question.work_item_id
                 AND event.event_type='work_reopened' AND event.id>question.superseded_by_event_id)
           OR EXISTS(SELECT 1 FROM SCHEMA.work_agent_follow_up_answers WHERE
               follow_up_id=question.id) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='question supersession requires actual reopen';
        END IF;
    END IF;
END $f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_assert_review(review_id uuid)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE review SCHEMA.code_reviews; scope SCHEMA.code_review_scopes; canonical text;
BEGIN
    SELECT * INTO STRICT review FROM SCHEMA.code_reviews WHERE id=review_id;
    SELECT * INTO STRICT scope FROM SCHEMA.code_review_scopes WHERE
        code_review_scopes.review_id=review.id;
    canonical:=SCHEMA.mnemonic_code_review_canonical_json(jsonb_build_object('repositories',
        scope.repositories));
    IF NOT EXISTS(SELECT 1 FROM SCHEMA.code_review_handoffs WHERE
        code_review_handoffs.review_id=review.id)
       OR review.scope_sha256<>encode(sha256(convert_to(canonical,'UTF8')),'hex') OR
       NOT SCHEMA.mnemonic_code_review_event_matches(review.created_event_id,
           'code_review_requested',
           review.project_id,review.work_item_id,'code_review_id',review.id,
               review.requesting_client,
           review.requesting_session_id,review.requesting_model) OR
       NOT EXISTS(SELECT 1 FROM SCHEMA.project_activity WHERE project_id=review.project_id
                  AND sequence=review.created_sequence AND work_event_id=review.created_event_id)
                      THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='review requires exact scope handoff and creation event';
    END IF;
    IF review.state='requested' THEN
        IF review.version<>1 OR review.result_id IS NOT NULL OR review.superseded_by_event_id IS
            NOT NULL OR
           EXISTS(SELECT 1 FROM SCHEMA.code_review_results WHERE
               code_review_results.review_id=review.id)
           OR NOT SCHEMA.mnemonic_code_review_current_work(review.work_item_id,
               review.completion_checkpoint_id)
           THEN RAISE EXCEPTION USING ERRCODE='23514',
               MESSAGE='requested review state is incoherent';
        END IF;
    ELSIF review.state='completed' THEN
        IF review.version<>2 OR review.superseded_by_event_id IS NOT NULL OR
           NOT EXISTS(SELECT 1 FROM SCHEMA.code_review_results WHERE id=review.result_id AND
                      code_review_results.review_id=review.id) OR
           EXISTS(SELECT 1 FROM SCHEMA.work_leases WHERE code_review_id=review.id) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='completed review requires result and consumed lease';
        END IF;
    ELSE
        IF review.version<>2 OR review.result_id IS NOT NULL OR
           NOT EXISTS(SELECT 1 FROM SCHEMA.work_events event WHERE id=review.superseded_by_event_id
                      AND event_type='code_review_superseded' AND code_review_id=review.id) OR
           NOT EXISTS(SELECT 1 FROM SCHEMA.work_events event WHERE
               event.work_item_id=review.work_item_id
                 AND event.event_type='work_reopened' AND event.id>review.superseded_by_event_id) OR
           EXISTS(SELECT 1 FROM SCHEMA.code_review_results WHERE
               code_review_results.review_id=review.id) OR
           EXISTS(SELECT 1 FROM SCHEMA.work_leases WHERE code_review_id=review.id) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='review supersession requires actual reopen';
        END IF;
    END IF;
END $f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_assert_result(result_id uuid)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE result SCHEMA.code_review_results; expected jsonb; findings jsonb; positions int;
    lineage SCHEMA.code_review_remediations; prompt text; finding jsonb; field record;
BEGIN
    SELECT * INTO STRICT result FROM SCHEMA.code_review_results WHERE id=result_id;
    SELECT jsonb_agg(jsonb_build_object('repository_key',repo->>'repository_key',
        'base_commit',repo->>'base_commit','head_commit',repo->>'head_commit') ORDER BY ordinal)
      INTO expected FROM SCHEMA.code_review_scopes,
        jsonb_array_elements(repositories) WITH ORDINALITY AS entries(repo,ordinal)
      WHERE review_id=result.review_id;
    SELECT coalesce(jsonb_agg(data ORDER BY position),'[]'::jsonb),count(*)
      INTO findings,positions FROM SCHEMA.code_review_findings WHERE
          code_review_findings.result_id=result.id;
    IF result.coverage IS DISTINCT FROM expected OR positions<>result.findings_count OR
       (positions>0 AND NOT EXISTS(SELECT 1 FROM SCHEMA.code_review_findings
           WHERE code_review_findings.result_id=result.id AND position=result.findings_count-1)) OR
       SCHEMA.mnemonic_code_review_content_bytes(jsonb_build_object('mode',result.mode,
         'summary',result.summary,'coverage',result.coverage,'limitations',result.limitations,
         'findings',findings))>65536 OR
       NOT SCHEMA.mnemonic_code_review_event_matches(result.created_event_id,
           'code_review_completed',
           result.project_id,result.work_item_id,'code_review_result_id',result.id,
               result.actor_client,
           result.actor_session_id,result.actor_model) OR
       NOT EXISTS(SELECT 1 FROM SCHEMA.code_reviews WHERE id=result.review_id AND state='completed'
           AND code_reviews.result_id=result.id) THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='review result coverage findings and event must seal';
    END IF;
    SELECT * INTO lineage FROM SCHEMA.code_review_remediations WHERE
        code_review_remediations.result_id=result.id;
    IF (result.findings_count>0)<>FOUND THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='findings require exactly one remediation';
    END IF;
    IF result.findings_count>0 THEN
        SELECT checkpoint.prompt INTO STRICT prompt FROM SCHEMA.work_items work
            JOIN SCHEMA.checkpoints checkpoint ON checkpoint.id=work.initial_checkpoint_id
            WHERE work.id=lineage.remediation_work_item_id;
        IF strpos(prompt,result.id::text)=0 THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='remediation must retain result pointer';
        END IF;
        FOR finding IN SELECT * FROM jsonb_array_elements(findings) LOOP
            FOR field IN SELECT key,value FROM jsonb_each_text(finding) LOOP
                IF field.value IS NOT NULL AND strpos(prompt,field.value)=0 THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='remediation must retain every finding field';
                END IF;
            END LOOP;
        END LOOP;
    END IF;
END $f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_resource_sealed()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
BEGIN
    IF TG_TABLE_NAME='work_completion_review_policies' THEN
        IF NOT SCHEMA.mnemonic_code_review_policy_sealed(NEW.completion_checkpoint_id) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review policy resources are missing';
        END IF;
    ELSIF TG_TABLE_NAME='work_agent_follow_ups' THEN
        PERFORM SCHEMA.mnemonic_code_review_assert_question(NEW.id);
    ELSIF TG_TABLE_NAME='work_agent_follow_up_answers' THEN
        PERFORM SCHEMA.mnemonic_code_review_assert_question(NEW.follow_up_id);
    ELSIF TG_TABLE_NAME='code_reviews' THEN
        PERFORM SCHEMA.mnemonic_code_review_assert_review(NEW.id);
    ELSIF TG_TABLE_NAME IN ('code_review_scopes','code_review_handoffs') THEN
        PERFORM SCHEMA.mnemonic_code_review_assert_review(NEW.review_id);
    ELSIF TG_TABLE_NAME='code_review_results' THEN
        PERFORM SCHEMA.mnemonic_code_review_assert_result(NEW.id);
    ELSE
        PERFORM SCHEMA.mnemonic_code_review_assert_result(NEW.result_id);
    END IF;
    RETURN NULL;
END $f$;

CREATE FUNCTION SCHEMA.mnemonic_code_review_event_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE review SCHEMA.code_reviews; question SCHEMA.work_agent_follow_ups;
    answer SCHEMA.work_agent_follow_up_answers; result SCHEMA.code_review_results;
    lease SCHEMA.work_leases; client text; session_id text; model text;
BEGIN
    IF NEW.event_type IN ('work_claimed','work_released') THEN
        SELECT * INTO STRICT lease FROM SCHEMA.work_leases WHERE work_item_id=NEW.work_item_id;
        IF NEW.code_review_id IS DISTINCT FROM lease.code_review_id OR
           (lease.purpose='code_review' AND
            (NEW.metadata->>'mode' IS DISTINCT FROM lease.mode OR
             NEW.metadata->>'purpose' IS DISTINCT FROM lease.purpose)) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='lease event must retain exact review purpose';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.event_type NOT IN ('work_follow_up_requested','work_follow_up_answered',
        'work_follow_up_superseded',
        'code_review_requested','code_review_completed','code_review_superseded') THEN RETURN NEW;
            END IF;
    IF NEW.event_type IN ('work_follow_up_requested','work_follow_up_superseded') THEN
        SELECT * INTO STRICT question FROM SCHEMA.work_agent_follow_ups WHERE
            id=NEW.work_follow_up_id;
        IF question.state<>'pending' OR (NEW.event_type='work_follow_up_requested' AND
            question.created_event_id IS NOT NULL) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='question event requires fresh source fact';
        END IF;
        client:=question.origin_client; session_id:=question.origin_session_id;
            model:=question.origin_model;
    ELSIF NEW.event_type='work_follow_up_answered' THEN
        SELECT * INTO STRICT answer FROM SCHEMA.work_agent_follow_up_answers WHERE
            id=NEW.work_follow_up_answer_id;
        IF answer.created_event_id IS NOT NULL OR answer.follow_up_id<>NEW.work_follow_up_id THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='answer event requires fresh source fact';
        END IF;
        client:=answer.actor_client; session_id:=answer.actor_session_id; model:=answer.actor_model;
    ELSIF NEW.event_type IN ('code_review_requested','code_review_superseded') THEN
        SELECT * INTO STRICT review FROM SCHEMA.code_reviews WHERE id=NEW.code_review_id;
        IF review.state<>'requested' OR (NEW.event_type='code_review_requested' AND
            review.created_event_id IS NOT NULL) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='review event requires fresh source fact';
        END IF;
        client:=review.requesting_client; session_id:=review.requesting_session_id;
            model:=review.requesting_model;
    ELSE
        SELECT * INTO STRICT result FROM SCHEMA.code_review_results WHERE
            id=NEW.code_review_result_id;
        IF result.created_event_id IS NOT NULL OR result.review_id<>NEW.code_review_id THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='result event requires fresh source fact';
        END IF;
        client:=result.actor_client; session_id:=result.actor_session_id; model:=result.actor_model;
    END IF;
    IF NEW.actor_kind<>'client' OR NEW.origin<>'live' OR (NEW.event_type NOT IN
        ('code_review_superseded','work_follow_up_superseded') AND
        ROW(NEW.actor_client,NEW.actor_session_id,NEW.actor_model)
        IS DISTINCT FROM ROW(client,session_id,model)) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review event actor must match source fact';
    END IF;
    RETURN NEW;
END $f$;
CREATE TRIGGER code_review_event_guard BEFORE INSERT ON SCHEMA.work_events
FOR EACH ROW EXECUTE FUNCTION SCHEMA.mnemonic_code_review_event_guard();
"""
