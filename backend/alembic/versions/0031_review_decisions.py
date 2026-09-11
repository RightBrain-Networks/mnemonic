"""Retain human review dispositions independently of immutable agent review results."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from mnemonic_api.code_review_db_sql import RESOURCE_GUARDS, WORK_GUARDS

revision: str = "0031_review_decisions"
down_revision: str | None = "0030_question_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _function(sql: str, name: str) -> str:
    start = sql.index(f"CREATE FUNCTION SCHEMA.{name}(")
    end = sql.index("END $f$;", start) + len("END $f$;")
    return sql[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


DECISION_GUARD = """
    IF TG_TABLE_NAME IN ('code_reviews', 'work_agent_follow_ups') AND
       new_data->'human_decisions' IS DISTINCT FROM old_data->'human_decisions' THEN
        IF (old_data-'human_decisions') IS DISTINCT FROM (new_data-'human_decisions')
           OR OLD.state NOT IN ('requested', 'pending')
           OR jsonb_typeof(NEW.human_decisions) <> 'array'
           OR jsonb_array_length(NEW.human_decisions) <> jsonb_array_length(OLD.human_decisions)+1
           OR NEW.human_decisions - (jsonb_array_length(NEW.human_decisions)-1)
              IS DISTINCT FROM OLD.human_decisions THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review decisions are append only';
        END IF;
        PERFORM SCHEMA.mnemonic_validate_review_decision(NEW.work_item_id, NEW.id,
            NEW.completion_checkpoint_id, NEW.human_decisions);
        RETURN NEW;
    END IF;
"""


def upgrade() -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_identifier(
        bind.scalar(sa.text("SELECT current_schema()"))
    )
    for table in ("code_reviews", "work_agent_follow_ups"):
        op.add_column(
            table,
            sa.Column(
                "human_decisions", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
            ),
        )
    op.execute(f"""
        CREATE FUNCTION {schema}.mnemonic_validate_review_decision(
            work_id uuid, resource_id uuid, checkpoint_id uuid, decisions jsonb)
        RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
        DECLARE entry jsonb := decisions->-1; event {schema}.work_events;
            work {schema}.work_items; report jsonb := entry->'job_completion_report';
        BEGIN
            SELECT * INTO STRICT work FROM {schema}.work_items WHERE id=work_id FOR UPDATE;
            SELECT * INTO STRICT event FROM {schema}.work_events
                WHERE id=(entry->>'event_id')::bigint;
            IF NOT {schema}.mnemonic_code_review_current_work(work_id, checkpoint_id)
               OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(entry) key)
                  IS DISTINCT FROM ARRAY['actor_client','actor_model','actor_session_id',
                    'created_at','event_id','job_completion_report','status','version','work_version']
               OR NOT COALESCE(entry->>'status' IN
                   ('to-review','deferred','done','wont-do','promoted'), false)
               OR entry->>'status'=COALESCE(decisions->-2->>'status','to-review')
               OR entry->'version' IS DISTINCT FROM to_jsonb(jsonb_array_length(decisions))
               OR entry->'work_version' IS DISTINCT FROM to_jsonb(work.version)
               OR work.version<=COALESCE((decisions->-2->>'work_version')::integer, 0)
               OR entry->>'actor_client' IS DISTINCT FROM 'dashboard'
               OR entry->'actor_model' IS DISTINCT FROM 'null'::jsonb
               OR event.event_type<>'progress' OR event.work_item_id<>work_id
               OR event.actor_client IS DISTINCT FROM 'dashboard' OR event.actor_model IS NOT NULL
               OR event.actor_session_id IS DISTINCT FROM entry->>'actor_session_id'
               OR event.created_at IS DISTINCT FROM (entry->>'created_at')::timestamptz
               OR event.metadata IS DISTINCT FROM jsonb_build_object(
                   'review_resource_id',resource_id::text,'review_status',entry->>'status',
                   'decision_version',jsonb_array_length(decisions),'work_version',work.version)
               OR EXISTS(SELECT 1 FROM {schema}.work_leases WHERE work_item_id=work_id)
               OR ((entry->>'status' IN ('done','wont-do','promoted'))
                   IS DISTINCT FROM (report <> 'null'::jsonb)) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid human review decision';
            END IF;
            IF report <> 'null'::jsonb AND (
                jsonb_typeof(report) IS DISTINCT FROM 'object'
                OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(report) key)
                   IS DISTINCT FROM ARRAY['fyi_items','prompt_revision','summary']
                OR NOT COALESCE({schema}.mnemonic_job_report_text_valid_v1(
                    report->>'summary',2000,8000,false),false)
                OR jsonb_typeof(report->'fyi_items') IS DISTINCT FROM 'array'
                OR jsonb_array_length(report->'fyi_items')>10
                OR EXISTS(SELECT 1 FROM jsonb_array_elements(report->'fyi_items') item
                    WHERE jsonb_typeof(item)<>'string' OR NOT
                    {schema}.mnemonic_job_report_text_valid_v1(item#>>'{{}}',600,2400,false))
                OR octet_length(report->>'summary')+(SELECT COALESCE(sum(
                    octet_length(item#>>'{{}}')),0) FROM
                    jsonb_array_elements(report->'fyi_items') item)>16384
                OR NOT EXISTS(SELECT 1 FROM {schema}.project_settings
                    WHERE project_id=work.project_id
                      AND revision=(report->>'prompt_revision')::bigint)) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='review closeout report required';
            END IF;
        END $f$;
    """)
    mutation = _function(RESOURCE_GUARDS, "mnemonic_code_review_resource_mutation")
    mutation = mutation.replace(
        "    allowed:=ARRAY['created_event_id','created_sequence'];",
        DECISION_GUARD + "    allowed:=ARRAY['created_event_id','created_sequence'];",
    )
    op.execute(mutation.replace("SCHEMA", schema))
    insert = _function(RESOURCE_GUARDS, "mnemonic_code_review_resource_insert")
    insert = insert.replace(
        "BEGIN\n",
        """BEGIN
    IF TG_TABLE_NAME IN ('code_reviews','work_agent_follow_ups') AND
       to_jsonb(NEW)->'human_decisions' IS DISTINCT FROM '[]'::jsonb THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='new reviews have no human decisions';
    END IF;
""",
        1,
    )
    insert = insert.replace(
        "IF review.state<>'requested' OR NOT",
        """IF review.state<>'requested' OR
           COALESCE(review.human_decisions->-1->>'status','to-review')<>'to-review' OR NOT""",
    )
    insert = insert.replace("IF question.state<>'pending' OR", """IF question.state<>'pending' OR
           COALESCE(question.human_decisions->-1->>'status','to-review')<>'to-review' OR""")
    op.execute(insert.replace("SCHEMA", schema))
    lease = _function(WORK_GUARDS, "mnemonic_code_review_lease_guard")
    lease = lease.replace(
        "review.state<>'requested' OR",
        """review.state<>'requested' OR
       COALESCE(review.human_decisions->-1->>'status','to-review')<>'to-review' OR""",
    )
    op.execute(lease.replace("SCHEMA", schema))


def downgrade() -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_identifier(
        bind.scalar(sa.text("SELECT current_schema()"))
    )
    op.execute(
        f"LOCK TABLE {schema}.code_reviews, {schema}.work_agent_follow_ups IN ACCESS EXCLUSIVE MODE"
    )
    if bind.scalar(
        sa.text(f"""SELECT EXISTS(SELECT 1 FROM {schema}.code_reviews
        WHERE human_decisions<>'[]'::jsonb) OR EXISTS(SELECT 1 FROM {schema}.work_agent_follow_ups
        WHERE human_decisions<>'[]'::jsonb)""")
    ):
        raise RuntimeError("Human review decisions exist; restore a pre-upgrade backup")
    for sql, name in (
        (RESOURCE_GUARDS, "mnemonic_code_review_resource_mutation"),
        (RESOURCE_GUARDS, "mnemonic_code_review_resource_insert"),
        (WORK_GUARDS, "mnemonic_code_review_lease_guard"),
    ):
        op.execute(_function(sql, name).replace("SCHEMA", schema))
    op.execute(f"DROP FUNCTION {schema}.mnemonic_validate_review_decision(uuid,uuid,uuid,jsonb)")
    for table in ("code_reviews", "work_agent_follow_ups"):
        op.drop_column(table, "human_decisions")
