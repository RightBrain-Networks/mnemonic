"""Read-only aggregate integrity audit for Phase 12 and its 0019/0020 preflights.

Run with the backend environment and writers quiesced. This audit never emits
report/prompt/checkpoint prose, credentials, IDs or receipt bodies. Guard catalog
hashes are compared with independently frozen original and pg_dump/restore
representations of the same migrated schema. No semantic text heuristic is used. Historical
Phase 9–11 data checks are composed from the existing read-only audit.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

HEAD = "0025_cross_project_relationships"
REVIEW_HEAD = "0024_code_reviews"
REVIEW_HEADS = (REVIEW_HEAD, HEAD)
MOVE_HEAD = "0023_work_item_moves"
MOVE_HEADS = (MOVE_HEAD, *REVIEW_HEADS)
REFERENCE_HEAD = "0022_external_references"
REPORT_HEAD = "0021_job_completion_reports"
REPORT_HEADS = (REPORT_HEAD, REFERENCE_HEAD, *MOVE_HEADS)
REFERENCE_HEADS = (REFERENCE_HEAD, *MOVE_HEADS)
ACTIVITY_HEAD = "0020_project_activity"
PREVIOUS_HEAD = "0019_structured_completion_evidence"
CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/project-activity-catalog-v1.json"
)


def _legacy():
    path = Path(__file__).with_name("audit_duplicate_handling.py")
    spec = importlib.util.spec_from_file_location("mnemonic_prior_phase_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Prior-phase audit is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _review_checks() -> dict[str, str]:
    path = Path(__file__).with_name("audit_code_reviews.py")
    spec = importlib.util.spec_from_file_location("mnemonic_review_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Code-review audit is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {f"code_review_{name}": sql for name, sql in module.CHECKS.items()}


def _digest(value: str, schema: str) -> str:
    quoted = '"' + schema.replace('"', '""') + '"'
    normalized = value.replace(quoted, "<schema>")
    normalized = re.sub(
        rf"(?<![A-Za-z0-9_]){re.escape(schema)}(?![A-Za-z0-9_])", "<schema>", normalized
    )
    return hashlib.sha256(normalized.encode()).hexdigest()


def catalog_snapshot(connection: Connection) -> dict[str, dict[str, str]]:
    """Capture deterministic definitions, including enabled modes and exact function bodies."""
    schema = connection.scalar(text("SELECT current_schema()"))
    statements = {
        "functions": """
            SELECT p.proname||'('||pg_get_function_identity_arguments(p.oid)||')',
                   pg_get_functiondef(p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=:schema AND p.prokind='f'
        """,
        "triggers": """
            SELECT c.relname||'.'||t.tgname,
                   pg_get_triggerdef(t.oid)||' enabled='||t.tgenabled::text
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=:schema AND NOT t.tgisinternal
        """,
        "constraints": """
            SELECT c.relname||'.'||k.conname,
                   pg_get_constraintdef(k.oid,true)||' validated='||k.convalidated::text
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=:schema AND k.contype<>'t' AND c.relname<>'alembic_version'
        """,
        "indexes": """
            SELECT indexname,indexdef FROM pg_indexes
            WHERE schemaname=:schema AND tablename<>'alembic_version'
        """,
        "function_permissions": """
            SELECT p.proname||'('||pg_get_function_identity_arguments(p.oid)||')',
                   (p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user))::text
                   ||'|'||coalesce((
                       SELECT jsonb_agg(jsonb_build_array(
                           a.grantee=0,a.grantee=p.proowner,a.grantor=p.proowner,
                           a.privilege_type,a.is_grantable)
                           ORDER BY a.grantee=0,a.grantee=p.proowner,a.grantor=p.proowner,
                                    a.privilege_type,a.is_grantable)::text
                       FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                   ),'[]')
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=:schema AND p.prokind='f'
        """,
        "relation_state": """
            SELECT c.relname,jsonb_build_array(c.relkind,c.relpersistence,c.relispartition,
                   c.relrowsecurity,c.relforcerowsecurity,c.relreplident,c.reloptions,
                   c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user),
                   (SELECT jsonb_agg(jsonb_build_array(
                       a.grantee=0,a.grantee=c.relowner,a.grantor=c.relowner,
                       a.privilege_type,a.is_grantable)
                       ORDER BY a.grantee=0,a.grantee=c.relowner,a.grantor=c.relowner,
                                a.privilege_type,a.is_grantable)
                    FROM aclexplode(coalesce(c.relacl,acldefault(
                        CASE WHEN c.relkind='S' THEN 's' ELSE 'r' END::"char",c.relowner))) a)
                   )::text
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=:schema AND c.relkind IN ('r','S','v','m','p')
              AND c.relname<>'alembic_version'
        """,
        "column_permissions": """
            SELECT c.relname||'.'||a.attname,(a.attacl IS NULL)::text
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=:schema AND c.relkind='r' AND c.relname<>'alembic_version'
              AND a.attnum>0 AND NOT a.attisdropped
        """,
        "foreign_key_triggers": """
            SELECT c.relname||'.'||k.conname||'.'||t.tgtype::text,
                   jsonb_build_array(t.tgenabled,t.tgdeferrable,t.tginitdeferred,
                       t.tgconstrrelid::regclass::text,t.tgfoid::regprocedure::text,
                       t.tgargs::text)::text
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_constraint k ON k.oid=t.tgconstraint
            WHERE n.nspname=:schema AND t.tgisinternal AND k.contype='f'
        """,
        "columns": """
            SELECT c.relname||'.'||a.attname,
                   format_type(a.atttypid,a.atttypmod)||'|'||a.attnotnull::text||'|'||a.attidentity::text
                   ||'|'||a.attgenerated::text||'|'||coalesce(pg_get_expr(d.adbin,d.adrelid),'')
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE n.nspname=:schema AND c.relkind='r' AND c.relname<>'alembic_version'
              AND a.attnum>0 AND NOT a.attisdropped
        """,
    }
    return {
        category: {
            name: _digest(definition, schema)
            for name, definition in connection.execute(text(sql), {"schema": schema})
        }
        for category, sql in statements.items()
    }


def _catalog_drift(connection: Connection, expected_head: str) -> dict[str, int]:
    frozen = json.loads(CATALOG_PATH.read_text())[expected_head]
    current = catalog_snapshot(connection)
    return {
        f"catalog_{category}_drift": sum(
            current[category].get(name) not in expected.get(name, [])
            for name in expected.keys() | current[category].keys()
        )
        for category, expected in frozen.items()
    }


_ACTIVITY_FINDINGS = {
    "missing_activity_heads": """
        SELECT count(*) FROM projects p
        LEFT JOIN project_activity_heads h ON h.project_id=p.id
        WHERE h.project_id IS NULL
    """,
    "activity_prefix_mismatch": """
        SELECT count(*) FROM project_activity_heads h LEFT JOIN (
            SELECT project_id,count(*) amount,min(sequence) first,max(sequence) last
            FROM project_activity GROUP BY project_id
        ) a USING(project_id)
        WHERE coalesce(a.amount,0)<>h.last_sequence
           OR (h.last_sequence>0 AND (a.first<>1 OR a.last<>h.last_sequence))
           OR h.historical_through_sequence<0 OR h.historical_through_sequence>h.last_sequence
    """,
    "activity_import_boundary_mismatch": """
        SELECT count(*) FROM project_activity a JOIN project_activity_heads h USING(project_id)
        WHERE (a.sequence<=h.historical_through_sequence)<>(a.origin='history_import')
    """,
    "missing_work_event_activity": """
        SELECT count(*) FROM work_events e LEFT JOIN project_activity a ON a.work_event_id=e.id
        WHERE a.sequence IS NULL OR a.kind<>'work_event'
           OR a.project_id<>e.project_id OR a.work_item_id<>e.work_item_id
    """,
    "duplicate_activity_event_source": """
        SELECT count(*) FROM (
            SELECT work_event_id FROM project_activity
            WHERE work_event_id IS NOT NULL
            GROUP BY work_event_id HAVING count(*)<>1
        ) duplicate
    """,
    "invalid_lease_renewal_source": """
        SELECT count(*) FROM project_activity activity
        WHERE activity.kind='lease_renewed'
          AND NOT EXISTS (
              SELECT 1 FROM work_events claim
              WHERE claim.event_type='work_claimed'
                AND claim.work_item_id=activity.work_item_id
                AND claim.lease_generation_id=activity.lease_generation_id
                AND claim.project_id=activity.project_id
          )
    """,
}
_REPORT_FINDINGS = {
    "missing_live_closeout_reports": """
        SELECT count(*) FROM project_activity a JOIN project_activity_heads h USING(project_id)
        JOIN work_events e ON e.id=a.work_event_id
        JOIN work_items w ON w.id=e.work_item_id
        LEFT JOIN job_completion_reports r ON r.id=e.job_completion_report_id
        WHERE a.sequence>h.historical_through_sequence
          AND (e.event_type='work_completed' OR (e.event_type='work_status_changed'
               AND e.metadata->>'to_status' IN ('done','wont-do','promoted')))
          AND (r.id IS NULL OR r.closeout_event_id<>e.id
               OR r.project_id<>e.project_id OR r.work_item_id<>e.work_item_id
               OR w.last_reportable_closeout_version IS NULL
               OR w.last_reportable_closeout_version<r.closeout_work_version)
    """,
    "terminal_live_work_creation": """
        SELECT count(*) FROM project_activity a JOIN project_activity_heads h USING(project_id)
        JOIN work_events e ON e.id=a.work_event_id
        WHERE a.sequence>h.historical_through_sequence AND e.event_type='work_created'
          AND e.metadata->'initial'->>'status' IS DISTINCT FROM 'pending'
    """,
    "missing_settings_or_report_counts": """
        SELECT count(*) FROM projects p LEFT JOIN project_settings s ON s.project_id=p.id
        LEFT JOIN project_job_completion_report_counts c ON c.project_id=p.id
        WHERE s.project_id IS NULL OR c.project_id IS NULL
    """,
    "unsealed_closeout_witnesses": """
        SELECT count(*) FROM work_items w WHERE w.last_reportable_closeout_version IS NOT NULL
          AND NOT mnemonic_job_report_slot_sealed(w.id,w.last_reportable_closeout_version)
    """,
    "invalid_report_event_binding": """
        SELECT count(*) FROM job_completion_reports r
        LEFT JOIN work_events e ON e.id=r.closeout_event_id
        LEFT JOIN work_items w ON w.id=r.work_item_id
        WHERE e.id IS NULL OR w.id IS NULL OR e.job_completion_report_id IS DISTINCT FROM r.id
           OR ROW(e.project_id,e.work_item_id,e.actor_client,e.actor_session_id,e.actor_model)
              IS DISTINCT FROM ROW(
                  r.project_id,r.work_item_id,r.actor_client,r.actor_session_id,r.actor_model
              )
           OR e.metadata->>'work_version' IS DISTINCT FROM r.closeout_work_version::text
           OR w.last_reportable_closeout_version IS NULL
           OR w.last_reportable_closeout_version<r.closeout_work_version
           OR (r.closeout_status='done' AND (e.event_type<>'work_completed'
                  OR r.completion_checkpoint_id IS DISTINCT FROM e.checkpoint_id))
           OR (r.closeout_status IN ('wont-do','promoted') AND (e.event_type<>'work_status_changed'
                  OR e.metadata->>'from_status' IS DISTINCT FROM 'pending'
                  OR e.metadata->>'to_status' IS DISTINCT FROM r.closeout_status))
    """,
    "invalid_report_text": """
        SELECT count(*) FROM job_completion_reports
        WHERE NOT mnemonic_job_report_text_valid_v1(summary,2000,8000,false)
           OR NOT mnemonic_job_report_fyis_valid_v1(summary,fyi_items)
           OR NOT mnemonic_job_report_text_valid_v1(prompt_text,8000,16384,true)
           OR prompt_revision<1
           OR prompt_sha256<>encode(sha256(convert_to(prompt_text,'UTF8')),'hex')
    """,
    "invalid_settings_text": """
        SELECT count(*) FROM project_settings WHERE revision<1
          OR NOT mnemonic_job_report_text_valid_v1(job_completion_report_prompt,8000,16384,true)
    """,
    "missing_or_mismatched_review": """
        SELECT count(*) FROM job_completion_reports r
        LEFT JOIN job_completion_report_reviews v ON v.report_id=r.id
        LEFT JOIN project_activity a ON a.project_id=v.project_id AND a.sequence=v.created_sequence
        WHERE v.report_id IS NULL
           OR ROW(v.project_id,v.work_item_id)
              IS DISTINCT FROM ROW(r.project_id,r.work_item_id)
           OR a.kind IS DISTINCT FROM 'job_completion_report_created'
           OR a.job_completion_report_id IS DISTINCT FROM r.id
           OR a.work_item_id IS DISTINCT FROM r.work_item_id
    """,
    "report_count_drift": """
        SELECT count(*) FROM project_job_completion_report_counts c LEFT JOIN (
            SELECT project_id,count(*) amount FROM job_completion_report_reviews
            WHERE dismissal_id IS NULL GROUP BY project_id
        ) v USING(project_id) WHERE c.undismissed_count<>coalesce(v.amount,0)
    """,
    "follow_up_count_drift": """
        SELECT count(*) FROM job_completion_report_reviews r LEFT JOIN (
            SELECT report_id,count(*) amount
            FROM job_completion_report_follow_ups GROUP BY report_id
        ) f USING(report_id) WHERE r.follow_up_count<>coalesce(f.amount,0)
    """,
    "invalid_follow_up_provenance": """
        SELECT count(*) FROM job_completion_report_follow_ups f
        LEFT JOIN job_completion_reports r ON r.id=f.report_id
        LEFT JOIN work_items w ON w.id=f.follow_up_work_item_id
        LEFT JOIN work_events creation
          ON creation.work_item_id=f.follow_up_work_item_id
         AND creation.event_type='work_created'
        LEFT JOIN project_activity a ON a.project_id=f.project_id AND a.sequence=f.created_sequence
        WHERE r.id IS NULL OR w.id IS NULL OR f.project_id<>r.project_id
           OR f.source_work_item_id<>r.work_item_id
           OR f.source_work_item_id=f.follow_up_work_item_id
           OR creation.id IS NULL OR creation.project_id<>f.project_id
           OR a.kind IS DISTINCT FROM 'job_completion_report_follow_up_created'
           OR a.follow_up_id IS DISTINCT FROM f.id OR a.work_item_id IS DISTINCT FROM w.id
           OR a.job_completion_report_id IS DISTINCT FROM r.id
    """,
    "missing_dismissal_activity": """
        SELECT count(*) FROM job_completion_report_reviews r
        LEFT JOIN project_activity a ON a.human_dismissal_id=r.dismissal_id
        WHERE r.dismissal_id IS NOT NULL
          AND (a.kind IS DISTINCT FROM 'job_completion_report_dismissed'
           OR a.project_id IS DISTINCT FROM r.project_id
           OR a.work_item_id IS DISTINCT FROM r.work_item_id
           OR a.job_completion_report_id IS DISTINCT FROM r.report_id)
    """,
}


_REFERENCE_FINDINGS = {
    "invalid_external_reference_lists": """
        SELECT count(*) FROM work_items
        WHERE NOT mnemonic_external_references_is_valid(external_references)
    """,
    "invalid_external_event_envelopes": """
        SELECT count(*) FROM work_events
        WHERE octet_length(metadata::text) > CASE WHEN event_type IN (
            'work_created','work_updated','work_status_changed','work_reopened'
        ) THEN 131072 ELSE 16384 END
    """,
    "invalid_external_creation_snapshots": """
        SELECT count(*) FROM work_events
        WHERE event_type='work_created' AND metadata->'initial' ? 'external_references'
          AND (NOT mnemonic_external_references_is_valid(
              metadata->'initial'->'external_references'
          ) OR metadata->'initial'->'external_references'='[]'::jsonb)
    """,
    "invalid_external_event_metadata": """
        SELECT count(*) FROM work_events
        WHERE (metadata->'initial' ? 'external_references'
               OR metadata->'changes' ? 'external_references')
          AND (event_type NOT IN (
              'work_created','work_updated','work_status_changed','work_reopened'
          ) OR mnemonic_work_event_metadata_v2_is_valid(
              event_type, origin, work_item_id, checkpoint_id, lease_generation_id,
              lease_release_id, relationship_id, relationship_source_work_item_id,
              relationship_target_work_item_id, relationship_context_checkpoint_work_item_id,
              relationship_context_checkpoint_id, metadata_version, metadata
          ) IS DISTINCT FROM true)
    """,
}


_MOVE_FINDINGS = {
    "work_provenance_prefix_mismatch": """
        WITH facts AS (
            SELECT source_work_item_id AS work_item_id,
                   source_work_sequence AS sequence
            FROM job_completion_report_follow_ups
            UNION ALL
            SELECT follow_up_work_item_id AS work_item_id,
                   follow_up_work_sequence AS sequence
            FROM job_completion_report_follow_ups
        ), prefixes AS (
            SELECT work_item_id,count(*) AS amount,
                   count(DISTINCT sequence) AS distinct_amount,
                   min(sequence) AS first,max(sequence) AS last
            FROM facts
            GROUP BY work_item_id
        )
        SELECT count(*)
        FROM work_report_provenance_heads head
        FULL OUTER JOIN prefixes USING(work_item_id)
        WHERE head.work_item_id IS NULL OR prefixes.work_item_id IS NULL
           OR prefixes.amount<>head.last_sequence
           OR prefixes.distinct_amount<>prefixes.amount
           OR prefixes.first<>1 OR prefixes.last<>head.last_sequence
    """,
    "work_provenance_order_mismatch": """
        WITH facts AS (
            SELECT source_work_item_id AS work_item_id,
                   source_work_sequence AS sequence,id,created_at
            FROM job_completion_report_follow_ups
            UNION ALL
            SELECT follow_up_work_item_id AS work_item_id,
                   follow_up_work_sequence AS sequence,id,created_at
            FROM job_completion_report_follow_ups
        ), ordered AS (
            SELECT work_item_id,sequence,id,created_at,
                   lag(id) OVER (
                       PARTITION BY work_item_id ORDER BY sequence
                   ) AS prior_id,
                   lag(created_at) OVER (
                       PARTITION BY work_item_id ORDER BY sequence
                   ) AS prior_created_at
            FROM facts
        )
        SELECT count(*) FROM ordered
        WHERE prior_created_at IS NOT NULL
          AND ROW(prior_created_at,prior_id)>=ROW(created_at,id)
    """,
    "invalid_move_event_pairs": """
        SELECT count(*) FROM work_item_moves m
        LEFT JOIN LATERAL (
            SELECT count(*) AS total,
                   count(*) FILTER (
                       WHERE event.project_id=m.source_project_id
                         AND event.metadata->>'role'='source'
                   ) AS sources,
                   count(*) FILTER (
                       WHERE event.project_id=m.target_project_id
                         AND event.metadata->>'role'='target'
                   ) AS targets,
                   min(event.id) FILTER (
                       WHERE event.project_id=m.source_project_id
                         AND event.metadata->>'role'='source'
                   ) AS source_event_id,
                   min(event.id) FILTER (
                       WHERE event.project_id=m.target_project_id
                         AND event.metadata->>'role'='target'
                   ) AS target_event_id
            FROM work_events event
            WHERE event.work_move_id=m.id
              AND event.event_type='work_moved'
              AND event.work_item_id=m.work_item_id
              AND event.created_at=m.created_at
              AND ROW(event.actor_kind,event.actor_client,event.actor_session_id,event.actor_model)
                  IS NOT DISTINCT FROM
                  ROW(m.actor_kind,m.actor_client,m.actor_session_id,m.actor_model)
              AND mnemonic_work_moved_metadata_v1_is_valid(
                  event.work_item_id,event.project_id,event.work_move_id,
                  event.metadata_version,event.metadata
              )
              AND event.metadata->>'move_id'=m.id::text
              AND event.metadata->>'source_project_id'=m.source_project_id::text
              AND event.metadata->>'target_project_id'=m.target_project_id::text
              AND event.metadata->>'work_version'=m.resulting_work_version::text
        ) evidence ON true
        WHERE evidence.total<>2 OR evidence.sources<>1 OR evidence.targets<>1
           OR evidence.source_event_id>=evidence.target_event_id
    """,
    "invalid_move_chain": """
        SELECT count(*) FROM (
            SELECT m.id
            FROM (
                SELECT m.*,
                       lag(target_project_id) OVER (
                           PARTITION BY work_item_id
                           ORDER BY resulting_work_version,id
                       ) AS prior_target,
                       lag(resulting_work_version) OVER (
                           PARTITION BY work_item_id
                           ORDER BY resulting_work_version,id
                       ) AS prior_resulting_work_version
                FROM work_item_moves m
            ) m
            WHERE m.prior_target IS NOT NULL
              AND (m.source_project_id<>m.prior_target
                   OR m.source_work_version<m.prior_resulting_work_version)
            UNION ALL
            SELECT latest.id
            FROM (
                SELECT DISTINCT ON (work_item_id) *
                FROM work_item_moves
                ORDER BY work_item_id,resulting_work_version DESC,id DESC
            ) latest
            LEFT JOIN work_items work ON work.id=latest.work_item_id
            WHERE work.id IS NULL OR work.project_id<>latest.target_project_id
               OR work.version<latest.resulting_work_version
            UNION ALL
            SELECT first_move.id
            FROM (
                SELECT DISTINCT ON (work_item_id) *
                FROM work_item_moves
                ORDER BY work_item_id,resulting_work_version,id
            ) first_move
            LEFT JOIN work_events creation
              ON creation.work_item_id=first_move.work_item_id
             AND creation.event_type='work_created'
            WHERE creation.id IS NULL
               OR creation.project_id<>first_move.source_project_id
        ) invalid
    """,
    "invalid_move_receipts": """
        SELECT count(*) FROM client_operations receipt
        WHERE receipt.operation_kind='move_work' AND receipt.state='completed'
          AND NOT EXISTS (
              SELECT 1 FROM work_item_moves m
              WHERE m.source_project_id=receipt.project_id
                AND receipt.response_status=200
                AND receipt.mutation_applied=true
                AND receipt.response_body#>>'{work_item,id}'=m.work_item_id::text
                AND receipt.response_body->>'target_project_id'=m.target_project_id::text
                AND receipt.response_body#>>'{work_item,version}'
                    =m.resulting_work_version::text
                AND m.preserved_status=receipt.response_body->>'preserved_status'
                AND receipt.response_body->>'source_project_id'=m.source_project_id::text
                AND receipt.response_body#>>'{work_item,project_id}'
                    =m.target_project_id::text
                AND receipt.response_body#>>'{work_item,status}'=m.preserved_status
                AND CASE
                    WHEN pg_input_is_valid(
                        receipt.response_body#>>'{work_item,updated_at}',
                        'timestamp with time zone'
                    )
                    THEN (receipt.response_body#>>'{work_item,updated_at}')::timestamptz
                        =m.created_at
                    ELSE false
                END
          )
    """,
}


_CROSS_PROJECT_RELATIONSHIP_FINDINGS = {
    "invalid_retained_relationship_facts": """
        SELECT count(*)
        FROM work_relationships relationship
        LEFT JOIN LATERAL (
            SELECT count(*) AS total,
                   count(DISTINCT event.work_item_id) AS endpoints,
                   count(*) FILTER (
                       WHERE event.project_id=relationship.project_id
                   ) AS authority_witnesses
            FROM work_events event
            WHERE event.relationship_id=relationship.id
              AND event.event_type=CASE
                  WHEN relationship.relationship_type='blocks'
                      THEN 'dependency_added'
                  ELSE 'relationship_added'
              END
              AND event.relationship_source_work_item_id
                  =relationship.source_work_item_id
              AND event.relationship_target_work_item_id
                  =relationship.target_work_item_id
              AND event.relationship_context_checkpoint_work_item_id
                  IS NOT DISTINCT FROM relationship.context_checkpoint_work_item_id
              AND event.relationship_context_checkpoint_id
                  IS NOT DISTINCT FROM relationship.context_checkpoint_id
              AND event.metadata->>'relationship_type'
                  =relationship.relationship_type
              AND event.created_at=relationship.created_at
        ) evidence ON true
        WHERE evidence.total<>2 OR evidence.endpoints<>2
           OR evidence.authority_witnesses<1
    """,
    "invalid_relationship_event_pairs": """
        SELECT count(*) FROM (
            SELECT event.relationship_id,
                   CASE
                       WHEN event.event_type IN (
                           'dependency_added','relationship_added'
                       ) THEN 'added'
                       ELSE 'removed'
                   END AS action
            FROM work_events event
            WHERE event.event_type IN (
                'dependency_added','dependency_removed',
                'relationship_added','relationship_removed'
            )
            GROUP BY event.relationship_id,action
            HAVING count(*)<>2
                OR count(DISTINCT event.work_item_id)<>2
                OR count(DISTINCT event.event_type)<>1
                OR count(DISTINCT ROW(
                    event.relationship_source_work_item_id,
                    event.relationship_target_work_item_id,
                    event.relationship_context_checkpoint_work_item_id,
                    event.relationship_context_checkpoint_id,
                    event.metadata,
                    event.created_at,
                    event.actor_kind,
                    event.actor_client,
                    event.actor_session_id,
                    event.actor_model,
                    event.origin
                ))<>1
        ) invalid
    """,
}


def _event_project_matches_placement(
    work_item_id: str,
    event_id: str,
    project_id: str,
) -> str:
    """Return SQL binding one event endpoint to its serialized move interval.

    Work writers lock the stable work row and stage each move's source witness
    before its target witness. The source event ID is therefore the durable
    ownership boundary even when both witnesses share one timestamp.
    """
    return f"""
        EXISTS (
            SELECT 1 FROM work_items current_work
            WHERE current_work.id={work_item_id}
        )
        AND EXISTS (
            SELECT 1 FROM work_events creation
            WHERE creation.work_item_id={work_item_id}
              AND creation.event_type='work_created'
              AND creation.id<={event_id}
        )
        AND {project_id} IS NOT DISTINCT FROM COALESCE(
            (
                SELECT move.target_project_id
                FROM work_item_moves move
                JOIN work_events source_event
                  ON source_event.work_move_id=move.id
                 AND source_event.event_type='work_moved'
                 AND source_event.project_id=move.source_project_id
                 AND source_event.metadata->>'role'='source'
                WHERE move.work_item_id={work_item_id}
                  AND source_event.id<{event_id}
                ORDER BY source_event.id DESC
                LIMIT 1
            ),
            (
                SELECT first_move.source_project_id
                FROM work_item_moves first_move
                WHERE first_move.work_item_id={work_item_id}
                ORDER BY first_move.resulting_work_version,first_move.id
                LIMIT 1
            ),
            (
                SELECT current_work.project_id FROM work_items current_work
                WHERE current_work.id={work_item_id}
            )
        )
    """


def _work_existed_by_event(work_item_id: str, event_id: str) -> str:
    """Return SQL proving a referenced endpoint already had its creation fact."""
    return f"""
        EXISTS (
            SELECT 1 FROM work_items current_work
            WHERE current_work.id={work_item_id}
        )
        AND EXISTS (
            SELECT 1 FROM work_events creation
            WHERE creation.work_item_id={work_item_id}
              AND creation.event_type='work_created'
              AND creation.id<={event_id}
        )
    """


def _move_aware_prior_counts(
    connection: Connection,
    counts: dict[str, int],
    previous: Any,
    *,
    cross_project_relationships: bool,
) -> None:
    """Replace old same-project owner checks with stable-identity checks at 0023+."""
    primary_owner = _event_project_matches_placement(
        "event.work_item_id", "event.id", "event.project_id"
    )
    if cross_project_relationships:
        source_owner = _work_existed_by_event(
            "event.relationship_source_work_item_id", "event.id"
        )
        target_owner = _work_existed_by_event(
            "event.relationship_target_work_item_id", "event.id"
        )
    else:
        source_owner = _event_project_matches_placement(
            "event.relationship_source_work_item_id", "event.id", "event.project_id"
        )
        target_owner = _event_project_matches_placement(
            "event.relationship_target_work_item_id", "event.id", "event.project_id"
        )
    event_owner_query = """
        SELECT count(*) FROM work_events event
        WHERE NOT (__PRIMARY_OWNER__)
           OR (event.relationship_source_work_item_id IS NOT NULL
               AND NOT (__SOURCE_OWNER__))
           OR (event.relationship_target_work_item_id IS NOT NULL
               AND NOT (__TARGET_OWNER__))
    """
    event_owner_query = event_owner_query.replace("__PRIMARY_OWNER__", primary_owner)
    event_owner_query = event_owner_query.replace("__SOURCE_OWNER__", source_owner)
    event_owner_query = event_owner_query.replace("__TARGET_OWNER__", target_owner)
    counts["event_owner_violations"] = connection.scalar(text(event_owner_query))
    if cross_project_relationships:
        counts["relationship_scope_violations"] = connection.scalar(text("""
            SELECT count(*)
            FROM work_relationships relationship
            LEFT JOIN work_items source
              ON source.id=relationship.source_work_item_id
            LEFT JOIN work_items target
              ON target.id=relationship.target_work_item_id
            WHERE source.id IS NULL OR target.id IS NULL
        """))
    counts["gate_owner_violations"] = connection.scalar(text("""
        SELECT count(*) FROM work_gates gate
        LEFT JOIN work_items work ON work.id=gate.work_item_id
        WHERE work.id IS NULL
           OR (gate.resolved_at IS NULL
               AND gate.project_id IS DISTINCT FROM work.project_id)
           OR NOT EXISTS (
                SELECT 1 FROM work_events request
                WHERE request.work_item_id=gate.work_item_id
                  AND request.gate_id=gate.id
                  AND request.event_type='human_attention_requested'
                  AND request.project_id=gate.project_id
           )
           OR (gate.resolved_at IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM work_events resolution
                WHERE resolution.work_item_id=gate.work_item_id
                  AND resolution.gate_id=gate.id
                  AND resolution.event_type='human_attention_resolved'
                  AND resolution.project_id=gate.project_id
           ))
    """))
    counts["reopen_binding_violation_count"] = (
        previous._phase11_batched_reopen_binding_violation_count(
            connection,
            movable_work=True,
        )
    )
    counts["evidence_owner_violation_count"] = connection.scalar(text("""
        SELECT count(*) FROM (
            SELECT result.work_item_id,result.completion_checkpoint_id
            FROM verification_results result
            LEFT JOIN work_items work ON work.id=result.work_item_id
            LEFT JOIN checkpoints checkpoint
              ON checkpoint.work_item_id=result.work_item_id
             AND checkpoint.id=result.completion_checkpoint_id
            WHERE work.id IS NULL OR checkpoint.id IS NULL
               OR checkpoint.kind<>'completion'
               OR result.created_at IS DISTINCT FROM checkpoint.created_at
               OR NOT EXISTS (
                   SELECT 1 FROM work_events completion
                   WHERE completion.work_item_id=result.work_item_id
                     AND completion.checkpoint_id=result.completion_checkpoint_id
                     AND completion.event_type='work_completed'
                     AND completion.project_id=result.project_id
               )
            UNION ALL
            SELECT artifact.work_item_id,artifact.completion_checkpoint_id
            FROM artifact_references artifact
            LEFT JOIN work_items work ON work.id=artifact.work_item_id
            LEFT JOIN checkpoints checkpoint
              ON checkpoint.work_item_id=artifact.work_item_id
             AND checkpoint.id=artifact.completion_checkpoint_id
            WHERE work.id IS NULL OR checkpoint.id IS NULL
               OR checkpoint.kind<>'completion'
               OR artifact.created_at IS DISTINCT FROM checkpoint.created_at
               OR NOT EXISTS (
                   SELECT 1 FROM work_events completion
                   WHERE completion.work_item_id=artifact.work_item_id
                     AND completion.checkpoint_id=artifact.completion_checkpoint_id
                     AND completion.event_type='work_completed'
                     AND completion.project_id=artifact.project_id
               )
        ) invalid
    """))


def _head_findings(
    connection: Connection, expected_head: str, previous: Any
) -> dict[str, int]:
    if expected_head == PREVIOUS_HEAD:
        return previous._catalog_blocking_counts(
            previous._catalog(connection, expected_head)
        )
    findings = _catalog_drift(connection, expected_head)
    checks = dict(_ACTIVITY_FINDINGS)
    if expected_head in REPORT_HEADS:
        checks.update(_REPORT_FINDINGS)
    if expected_head in REFERENCE_HEADS:
        checks.update(_REFERENCE_FINDINGS)
    if expected_head in MOVE_HEADS:
        checks.update(_MOVE_FINDINGS)
    if expected_head in REVIEW_HEADS:
        checks.update(_review_checks())
    if expected_head == HEAD:
        checks.update(_CROSS_PROJECT_RELATIONSHIP_FINDINGS)
    findings.update(
        {key: connection.scalar(text(sql)) for key, sql in checks.items()}
    )
    return findings


def audit_snapshot(connection: Connection, expected_head: str = HEAD) -> dict[str, Any]:
    if connection.scalar(text("SHOW transaction_read_only")) != "on":
        raise RuntimeError("Audit requires a read-only transaction")
    actual = (
        connection.execute(text("SELECT version_num FROM alembic_version"))
        .scalars()
        .all()
    )
    if actual != [expected_head]:
        return {
            "result": "blocked",
            "blocking_findings": {"migration_head_mismatch": 1},
        }
    previous = _legacy()
    schema = connection.scalar(text("SELECT current_schema()"))
    counts = previous._base_counts(connection, schema)
    counts.update(previous._core_counts(connection))
    counts.update(previous._repository_freshness_counts(connection))
    counts.update(previous._completion_evidence_counts(connection, schema))
    if expected_head in MOVE_HEADS:
        _move_aware_prior_counts(
            connection,
            counts,
            previous,
            cross_project_relationships=expected_head == HEAD,
        )
    findings = previous._blocking_counts(counts)
    findings.update(_head_findings(connection, expected_head, previous))
    findings = {key: value for key, value in findings.items() if value}
    inventory = {"projects": connection.scalar(text("SELECT count(*) FROM projects"))}
    if expected_head != PREVIOUS_HEAD:
        inventory["activity"] = connection.scalar(
            text("SELECT count(*) FROM project_activity")
        )
    if expected_head in REPORT_HEADS:
        inventory["reports"] = connection.scalar(
            text("SELECT count(*) FROM job_completion_reports")
        )
        inventory["follow_ups"] = connection.scalar(
            text("SELECT count(*) FROM job_completion_report_follow_ups")
        )
    if expected_head in MOVE_HEADS:
        inventory["moves"] = connection.scalar(text("SELECT count(*) FROM work_item_moves"))
        inventory["work_provenance_heads"] = connection.scalar(
            text("SELECT count(*) FROM work_report_provenance_heads")
        )
    if expected_head == HEAD:
        inventory["relationships"] = connection.scalar(
            text("SELECT count(*) FROM work_relationships")
        )
        inventory["cross_project_relationships"] = connection.scalar(text("""
            SELECT count(*) FROM work_relationships relationship
            JOIN work_items source ON source.id=relationship.source_work_item_id
            JOIN work_items target ON target.id=relationship.target_work_item_id
            WHERE source.project_id<>target.project_id
        """))
    return {
        "audit_version": "project-activity-v1",
        "expected_head": expected_head,
        "result": "blocked" if findings else "pass",
        "inventory": inventory,
        "blocking_findings": findings,
        "prior_phase_counts": counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--expected-head",
        choices=(PREVIOUS_HEAD, ACTIVITY_HEAD, REPORT_HEAD, REFERENCE_HEAD, *MOVE_HEADS),
        default=HEAD,
    )
    args = parser.parse_args()
    engine = None
    try:
        if not args.database_url:
            raise RuntimeError("DATABASE_URL is required")
        engine = create_engine(
            args.database_url, hide_parameters=True, connect_args={"connect_timeout": 5}
        )
        with engine.connect() as connection:
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            connection.execute(text("SET LOCAL statement_timeout='60s'"))
            report = audit_snapshot(connection, args.expected_head)
            connection.rollback()
    except (OSError, RuntimeError, ValueError, TypeError, LookupError, SQLAlchemyError):
        report = {
            "audit_version": "project-activity-v1",
            "result": "blocked",
            "audit_runtime_failure": True,
        }
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
