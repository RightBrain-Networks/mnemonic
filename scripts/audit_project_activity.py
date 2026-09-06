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

HEAD = "0022_external_references"
REPORT_HEAD = "0021_job_completion_reports"
REPORT_HEADS = (REPORT_HEAD, HEAD)
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
    "missing_activity_heads": "SELECT count(*) FROM projects p LEFT JOIN project_activity_heads h ON h.project_id=p.id WHERE h.project_id IS NULL",
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
        SELECT count(*) FROM (SELECT work_event_id FROM project_activity WHERE work_event_id IS NOT NULL
                              GROUP BY work_event_id HAVING count(*)<>1) duplicate
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
        SELECT count(*) FROM job_completion_reports r LEFT JOIN work_events e ON e.id=r.closeout_event_id
        LEFT JOIN work_items w ON w.id=r.work_item_id
        WHERE e.id IS NULL OR w.id IS NULL OR e.job_completion_report_id IS DISTINCT FROM r.id
           OR ROW(e.project_id,e.work_item_id,e.actor_client,e.actor_session_id,e.actor_model)
              IS DISTINCT FROM ROW(r.project_id,r.work_item_id,r.actor_client,r.actor_session_id,r.actor_model)
           OR e.metadata->>'work_version' IS DISTINCT FROM r.closeout_work_version::text
           OR w.last_reportable_closeout_version IS NULL OR w.last_reportable_closeout_version<r.closeout_work_version
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
           OR prompt_revision<1 OR prompt_sha256<>encode(sha256(convert_to(prompt_text,'UTF8')),'hex')
    """,
    "invalid_settings_text": """
        SELECT count(*) FROM project_settings WHERE revision<1
          OR NOT mnemonic_job_report_text_valid_v1(job_completion_report_prompt,8000,16384,true)
    """,
    "missing_or_mismatched_review": """
        SELECT count(*) FROM job_completion_reports r LEFT JOIN job_completion_report_reviews v ON v.report_id=r.id
        LEFT JOIN project_activity a ON a.project_id=v.project_id AND a.sequence=v.created_sequence
        WHERE v.report_id IS NULL OR ROW(v.project_id,v.work_item_id) IS DISTINCT FROM ROW(r.project_id,r.work_item_id)
           OR a.kind IS DISTINCT FROM 'job_completion_report_created' OR a.job_completion_report_id IS DISTINCT FROM r.id
    """,
    "report_count_drift": """
        SELECT count(*) FROM project_job_completion_report_counts c LEFT JOIN (
            SELECT project_id,count(*) amount FROM job_completion_report_reviews
            WHERE dismissal_id IS NULL GROUP BY project_id
        ) v USING(project_id) WHERE c.undismissed_count<>coalesce(v.amount,0)
    """,
    "follow_up_count_drift": """
        SELECT count(*) FROM job_completion_report_reviews r LEFT JOIN (
            SELECT report_id,count(*) amount FROM job_completion_report_follow_ups GROUP BY report_id
        ) f USING(report_id) WHERE r.follow_up_count<>coalesce(f.amount,0)
    """,
    "invalid_follow_up_provenance": """
        SELECT count(*) FROM job_completion_report_follow_ups f
        LEFT JOIN job_completion_reports r ON r.id=f.report_id
        LEFT JOIN work_items w ON w.id=f.follow_up_work_item_id
        LEFT JOIN project_activity a ON a.project_id=f.project_id AND a.sequence=f.created_sequence
        WHERE r.id IS NULL OR w.id IS NULL OR f.project_id<>r.project_id OR f.project_id<>w.project_id
           OR f.source_work_item_id<>r.work_item_id OR f.source_work_item_id=f.follow_up_work_item_id
           OR a.kind IS DISTINCT FROM 'job_completion_report_follow_up_created'
           OR a.follow_up_id IS DISTINCT FROM f.id OR a.work_item_id IS DISTINCT FROM w.id
           OR a.job_completion_report_id IS DISTINCT FROM r.id
    """,
    "missing_dismissal_activity": """
        SELECT count(*) FROM job_completion_report_reviews r
        LEFT JOIN project_activity a ON a.human_dismissal_id=r.dismissal_id
        WHERE r.dismissal_id IS NOT NULL AND (a.kind IS DISTINCT FROM 'job_completion_report_dismissed'
           OR a.project_id IS DISTINCT FROM r.project_id OR a.work_item_id IS DISTINCT FROM r.work_item_id
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
    findings = previous._blocking_counts(counts)
    if expected_head == PREVIOUS_HEAD:
        findings.update(
            previous._catalog_blocking_counts(
                previous._catalog(connection, expected_head)
            )
        )
    else:
        findings.update(_catalog_drift(connection, expected_head))
        checks = dict(_ACTIVITY_FINDINGS)
        if expected_head in REPORT_HEADS:
            checks.update(_REPORT_FINDINGS)
        if expected_head == HEAD:
            checks.update(_REFERENCE_FINDINGS)
        findings.update(
            {key: connection.scalar(text(sql)) for key, sql in checks.items()}
        )
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
        "--expected-head", choices=(PREVIOUS_HEAD, ACTIVITY_HEAD, REPORT_HEAD, HEAD), default=HEAD
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
