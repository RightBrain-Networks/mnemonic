"""Read-only code-review integrity audit for supported schemas 0024 through 0035.

Run with the backend virtual environment and private database access. Output
contains counts only: no repository locators, prompts, findings, actors, tokens,
receipt bodies or database connection strings. Nothing is repaired or inferred.
"""

import argparse
import json
import os

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

HEAD = "0035_prompt_library"
REVIEW_HEAD = "0024_code_reviews"
SUPPORTED_HEADS = (REVIEW_HEAD, "0025_cross_project_relationships", "0026_artifact_library",
                   "0027_artifact_fulltext", "0028_work_summary_limit",
                   "0029_artifact_links_sensitive", "0030_question_versions",
                   "0031_review_decisions", "0032_agent_transcripts", "0033_transcript_imports",
                   "0034_variable_work_leases", HEAD)
CHECKS = {
    "lifecycle_event_witness_mismatch": """
        SELECT count(*) FROM work_events event
        WHERE event.event_type IN ('work_follow_up_requested','work_follow_up_answered',
            'work_follow_up_superseded','code_review_requested','code_review_completed',
            'code_review_superseded') AND NOT mnemonic_code_review_event_is_sealed(event.id)
    """,
    "review_work_ownership_mismatch": """
        SELECT count(*) FROM (
            SELECT project_id,work_item_id FROM work_completion_review_policies
            UNION ALL SELECT project_id,work_item_id FROM work_agent_follow_ups
            UNION ALL SELECT project_id,work_item_id FROM code_reviews
        ) history LEFT JOIN work_items work ON work.id=history.work_item_id
        WHERE work.id IS NULL OR work.project_id<>history.project_id
    """,
    "missing_episode_policies": """
        SELECT count(*) FROM checkpoints checkpoint
        LEFT JOIN work_completion_review_policies policy
          ON policy.completion_checkpoint_id=checkpoint.id
        WHERE checkpoint.requires_code_review_policy AND policy.id IS NULL
    """,
    "historical_policy_attachment": """
        SELECT count(*) FROM work_completion_review_policies policy
        JOIN checkpoints checkpoint ON checkpoint.id=policy.completion_checkpoint_id
        WHERE NOT checkpoint.requires_code_review_policy OR checkpoint.kind<>'completion'
    """,
    "policy_event_mismatch": """
        SELECT count(*) FROM work_completion_review_policies policy
        LEFT JOIN work_events event ON event.id=policy.completion_event_id
        WHERE event.id IS NULL OR event.event_type<>'work_completed' OR
          ROW(event.project_id,event.work_item_id,event.checkpoint_id) IS DISTINCT FROM
          ROW(policy.project_id,policy.work_item_id,policy.completion_checkpoint_id)
          OR NOT mnemonic_code_review_policy_sealed(policy.completion_checkpoint_id)
          OR policy.decision<>mnemonic_code_review_decision(policy.priority_at_closeout,
            policy.remediation_depth,policy.required_min_priority,policy.optional_min_priority,
            policy.allow_remediation_code_reviews)
    """,
    "question_answer_mismatch": """
        SELECT count(*) FROM work_agent_follow_ups question
        LEFT JOIN work_agent_follow_up_answers answer ON answer.follow_up_id=question.id
        WHERE (question.state='answered')<>(answer.id IS NOT NULL) OR
          question.answer_id IS DISTINCT FROM answer.id OR
          (answer.id IS NOT NULL AND ROW(answer.actor_client,answer.actor_session_id)
            IS DISTINCT FROM ROW(question.origin_client,question.origin_session_id))
    """,
    "answer_review_mismatch": """
        SELECT count(*) FROM work_agent_follow_up_answers answer
        LEFT JOIN code_reviews review ON review.answer_id=answer.id
        WHERE answer.recommend_review<>(review.id IS NOT NULL)
          OR answer.code_review_id IS DISTINCT FROM review.id
    """,
    "review_resource_mismatch": """
        SELECT count(*) FROM code_reviews review
        LEFT JOIN code_review_scopes scope ON scope.review_id=review.id
        LEFT JOIN code_review_handoffs handoff ON handoff.review_id=review.id
        LEFT JOIN code_review_results result ON result.review_id=review.id
        WHERE scope.review_id IS NULL OR handoff.review_id IS NULL
          OR (review.state='completed')<>(result.id IS NOT NULL)
          OR review.result_id IS DISTINCT FROM result.id
          OR review.scope_sha256<>encode(sha256(convert_to(mnemonic_code_review_canonical_json(
               jsonb_build_object('repositories',scope.repositories)),'UTF8')),'hex')
    """,
    "depth_two_review": """
        SELECT count(*) FROM code_reviews review JOIN work_items work ON work.id=review.work_item_id
        WHERE work.remediation_depth>=2
    """,
    "result_findings_mismatch": """
        SELECT count(*) FROM code_review_results result LEFT JOIN (
          SELECT result_id,count(*) amount,min(position) first,max(position) last
          FROM code_review_findings GROUP BY result_id
        ) findings ON findings.result_id=result.id
        WHERE result.findings_count<>coalesce(findings.amount,0)
          OR (result.findings_count>0 AND (findings.first<>0
                                         OR findings.last<>result.findings_count-1))
    """,
    "result_remediation_mismatch": """
        SELECT count(*) FROM code_review_results result
        LEFT JOIN code_review_remediations lineage ON lineage.result_id=result.id
        WHERE (result.findings_count>0)<>(lineage.id IS NOT NULL)
          OR (lineage.id IS NOT NULL AND lineage.review_id<>result.review_id)
    """,
    "lineage_mismatch": """
        SELECT count(*) FROM code_review_remediations lineage
        LEFT JOIN work_items source ON source.id=lineage.source_work_item_id
        LEFT JOIN work_items child ON child.id=lineage.remediation_work_item_id
        LEFT JOIN code_review_remediations parent ON parent.id=lineage.parent_remediation_id
        LEFT JOIN work_relationships edge ON edge.id=lineage.relationship_id
        WHERE child.id IS NULL OR source.id IS NULL OR edge.id IS NULL
          OR child.remediation_id IS DISTINCT FROM lineage.id
          OR child.remediation_depth<>lineage.depth
          OR lineage.depth<>source.remediation_depth+1 OR lineage.depth NOT IN (1,2)
          OR (lineage.depth=1 AND lineage.root_work_item_id<>source.id)
          OR (lineage.depth=2 AND (parent.id IS DISTINCT FROM source.remediation_id
                                  OR parent.root_work_item_id<>lineage.root_work_item_id))
          OR ROW(edge.source_work_item_id,edge.target_work_item_id,edge.context_checkpoint_id,
                 edge.context_checkpoint_work_item_id,edge.relationship_type)
             IS DISTINCT FROM ROW(child.id,source.id,lineage.completion_checkpoint_id,
                                  source.id,'discovered-from')
    """,
    "work_provenance_mismatch": """
        SELECT count(*) FROM work_items work LEFT JOIN code_review_remediations lineage
          ON lineage.remediation_work_item_id=work.id
        WHERE work.remediation_id IS DISTINCT FROM lineage.id
          OR work.remediation_depth<>coalesce(lineage.depth,0)
    """,
    "review_lease_mismatch": """
        SELECT count(*) FROM work_leases lease
        LEFT JOIN code_reviews review ON review.id=lease.code_review_id
        LEFT JOIN work_items work ON work.id=lease.work_item_id
        WHERE lease.purpose='code_review' AND (review.id IS NULL OR review.state<>'requested'
          OR work.status<>'done' OR work.deleted_at IS NOT NULL OR work.remediation_depth>=2
          OR review.work_item_id<>work.id OR lease.mode IS NULL OR lease.mode NOT IN ('cold','warm')
          OR review.completion_checkpoint_id IS DISTINCT FROM work.completion_review_checkpoint_id)
    """,
    "result_claim_witness_mismatch": """
        SELECT count(*) FROM code_review_results result
        LEFT JOIN work_events event ON event.id=result.claim_event_id
        WHERE event.id IS NULL OR ROW(event.project_id,event.work_item_id,event.code_review_id,
            event.lease_generation_id,event.event_type,event.actor_client,event.actor_session_id,
            event.metadata->>'mode') IS DISTINCT FROM ROW(result.project_id,result.work_item_id,
            result.review_id,result.lease_generation_id,'work_claimed',result.actor_client,
            result.actor_session_id,result.mode)
    """,
    "missing_review_activity": """
        SELECT count(*) FROM work_events event LEFT JOIN project_activity activity
          ON activity.work_event_id=event.id
        WHERE (event.code_review_id IS NOT NULL OR event.work_follow_up_id IS NOT NULL)
          AND (activity.sequence IS NULL OR activity.project_id<>event.project_id
               OR activity.work_item_id<>event.work_item_id)
    """,
    "missing_review_result_receipts": """
        SELECT count(*) FROM code_review_results result WHERE NOT EXISTS(
          SELECT 1 FROM client_operations receipt WHERE receipt.project_id=result.project_id
          AND receipt.state='completed' AND receipt.operation_kind='complete_code_review'
          AND receipt.response_body#>>'{result,id}'=result.id::text)
    """,
    "missing_question_answer_receipts": """
        SELECT count(*) FROM work_agent_follow_up_answers answer WHERE NOT EXISTS(
          SELECT 1 FROM client_operations receipt WHERE receipt.project_id=answer.project_id
          AND receipt.state='completed' AND receipt.operation_kind='respond_to_work_follow_up'
          AND receipt.response_body#>>'{answer,id}'=answer.id::text)
    """,
}


# PostgreSQL renders catalog definitions through session settings, so an audit that
# does not pin them reports the connecting session as if it were the schema. This
# audit's own checks are counts today and are not sensitive to any of these, so
# pinning makes that a structural property rather than an incidental one. Kept
# identical to the Phase 12 audit's map; ``test_project_activity_audit_postgres.py``
# asserts they never drift.
DETERMINISTIC_SESSION_SETTINGS = {
    "client_encoding": "UTF8",
    "bytea_output": "hex",
    "DateStyle": "ISO, MDY",
    "TimeZone": "UTC",
    "quote_all_identifiers": "off",
}


def pin_session_settings(connection: Connection) -> None:
    """Pin the settings the catalog is read under for the caller's transaction.

    Transaction-local, so the audit never mutates a session it was handed, and read
    back afterwards: a ``SET LOCAL`` outside a transaction block is a silent no-op,
    and a determinism guarantee that can quietly fail to apply is not one.
    """
    for name, value in DETERMINISTIC_SESSION_SETTINGS.items():
        connection.execute(
            text("SELECT pg_catalog.set_config(:name, :value, true)"),
            {"name": name, "value": value},
        )
    applied = {
        name: connection.scalar(
            text("SELECT pg_catalog.current_setting(:name)"), {"name": name}
        )
        for name in DETERMINISTIC_SESSION_SETTINGS
    }
    if applied != DETERMINISTIC_SESSION_SETTINGS:
        raise RuntimeError("Audit could not pin its deterministic session settings")


HUMAN_DECISION_CHECKS = {
    "human_decision_event_mismatch": """
        WITH resources AS (
            SELECT id,project_id,work_item_id,human_decisions FROM code_reviews
            UNION ALL
            SELECT id,project_id,work_item_id,human_decisions FROM work_agent_follow_ups
        )
        SELECT count(*) FROM resources resource
        CROSS JOIN LATERAL jsonb_array_elements(resource.human_decisions)
            WITH ORDINALITY AS decision(entry,position)
        LEFT JOIN work_events event ON event.id::text=entry->>'event_id'
        WHERE event.id IS NULL OR event.work_item_id<>resource.work_item_id
           OR event.project_id<>resource.project_id OR event.event_type<>'progress'
           OR entry->'version' IS DISTINCT FROM to_jsonb(position)
           OR entry->>'actor_client' IS DISTINCT FROM 'dashboard'
           OR entry->'actor_model' IS DISTINCT FROM 'null'::jsonb
           OR event.actor_client IS DISTINCT FROM entry->>'actor_client'
           OR event.actor_session_id IS DISTINCT FROM entry->>'actor_session_id'
           OR event.actor_model IS NOT NULL
           OR event.metadata->>'review_resource_id' IS DISTINCT FROM resource.id::text
           OR event.metadata->'review_status' IS DISTINCT FROM entry->'status'
           OR event.metadata->'decision_version' IS DISTINCT FROM entry->'version'
           OR event.metadata->'work_version' IS DISTINCT FROM entry->'work_version'
           OR event.created_at IS DISTINCT FROM (entry->>'created_at')::timestamptz
           OR NOT COALESCE(entry->>'status' IN
                ('to-review','deferred','done','wont-do','promoted'), false)
           OR ((entry->>'status' IN ('done','wont-do','promoted')) IS DISTINCT FROM
               (jsonb_typeof(entry->'job_completion_report')='object'))
    """,
    "human_closed_review_has_lease": """
        SELECT count(*) FROM code_reviews review JOIN work_leases lease
            ON lease.code_review_id=review.id
        WHERE COALESCE(review.human_decisions->-1->>'status','to-review')<>'to-review'
    """,
}


def checks_for_head(schema_head: str) -> dict[str, str]:
    human_decision_heads = {
        "0031_review_decisions", "0032_agent_transcripts", "0033_transcript_imports", HEAD,
    }
    return {**CHECKS, **(HUMAN_DECISION_CHECKS if schema_head in human_decision_heads else {})}


def audit(connection: Connection) -> dict:
    """Read counts within the caller's read-only coherent transaction."""
    pin_session_settings(connection)
    schema_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
    if schema_head not in SUPPORTED_HEADS:
        raise RuntimeError("Code-review audit requires a supported schema head")
    findings = {
        name: int(connection.scalar(text(query)) or 0) for name, query in checks_for_head(schema_head).items()
    }
    counts = dict(
        connection.execute(
            text("""
        SELECT 'reviews_requested',count(*) FROM code_reviews WHERE state='requested'
        UNION ALL SELECT 'questions_pending',count(*) FROM work_agent_follow_ups
            WHERE state='pending'
        UNION ALL SELECT 'reviews_completed',count(*) FROM code_reviews WHERE state='completed'
        UNION ALL SELECT 'remediation_items',count(*) FROM code_review_remediations
        UNION ALL SELECT 'expired_review_leases',count(*) FROM work_leases
          WHERE purpose='code_review' AND expires_at<=transaction_timestamp()
    """)
        ).all()
    )
    return {
        "schema_head": schema_head,
        "ok": not any(findings.values()),
        "findings": findings,
        "operational_counts": counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error(
            "Set DATABASE_URL or provide --database-url in a private environment"
        )
    engine = create_engine(
        args.database_url, hide_parameters=True, connect_args={"connect_timeout": 5}
    )
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            connection.execute(text("SET LOCAL statement_timeout='30s'"))
            connection.execute(text("SET LOCAL lock_timeout='2s'"))
            report = audit(connection)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1
    except (SQLAlchemyError, RuntimeError):
        print(
            json.dumps({"ok": False, "error": "Code-review audit could not complete"})
        )
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
