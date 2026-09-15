"""Human review earmarks, attribution, and first-claim scope preparation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from mnemonic_api.manual_review_db import REASON_CHECK

revision: str = "0039_manual_review_requests"
down_revision: str | None = "0038_transcript_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Patch the current definitions, retaining the human disposition guards from 0031.
PATCHES = [
    (
        "mnemonic_code_review_assert_question",
        "WHERE policy_decision_id::text=question.kind_data->>'policy_decision_id')) THEN",
        "WHERE policy_decision_id::text=question.kind_data->>'policy_decision_id' "
        "AND request_reason<>'manual')) THEN",
    ),
    (
        "mnemonic_code_review_current_work",
        "work.completion_review_checkpoint_id=checkpoint",
        """
        EXISTS(SELECT 1 FROM SCHEMA.checkpoints c WHERE c.id=checkpoint
            AND c.work_item_id=work.id AND c.kind='completion'
            AND c.completion_generation=work.completion_generation)""",
    ),
    (
        "mnemonic_code_review_work_sealed",
        "retained.completion_review_checkpoint_id IS DISTINCT FROM review.completion_checkpoint_id",
        "NOT SCHEMA.mnemonic_code_review_current_work(retained.id,review.completion_checkpoint_id)",
    ),
    (
        "mnemonic_code_review_lease_guard",
        "work.completion_review_checkpoint_id IS DISTINCT FROM review.completion_checkpoint_id",
        "NOT SCHEMA.mnemonic_code_review_current_work(work.id,review.completion_checkpoint_id)",
    ),
    (
        "mnemonic_code_review_policy_sealed",
        "WHEN 'ask_recommendation' THEN\n",
        """
        WHEN 'ask_recommendation' THEN
            EXISTS(SELECT 1 FROM SCHEMA.code_reviews review
                WHERE review.policy_decision_id=policy.id AND review.request_reason='manual') OR
""",
    ),
    (
        "mnemonic_code_review_policy_sealed",
        "ELSE NOT EXISTS(SELECT 1 FROM SCHEMA.code_reviews review WHERE\n"
        "            review.policy_decision_id=policy.id)",
        "ELSE NOT EXISTS(SELECT 1 FROM SCHEMA.code_reviews review WHERE\n"
        "            review.policy_decision_id=policy.id AND review.request_reason<>'manual')",
    ),
    (
        "mnemonic_code_review_resource_insert",
        "    ELSIF TG_TABLE_NAME='code_reviews' THEN\n",
        """
    ELSIF TG_TABLE_NAME='code_reviews' AND to_jsonb(NEW)->>'request_reason'='manual' THEN
        SELECT * INTO STRICT work FROM SCHEMA.work_items WHERE id=NEW.work_item_id;
        IF NEW.manual_request IS DISTINCT FROM work.manual_review_request
           OR NEW.manual_request IS NULL OR NEW.state<>'requested' OR NEW.version<>1
           OR NEW.result_id IS NOT NULL OR NEW.superseded_by_event_id IS NOT NULL
           OR NEW.scope_preparation IS NOT NULL OR NEW.answer_id IS NOT NULL
           OR NOT SCHEMA.mnemonic_code_review_current_work(work.id,NEW.completion_checkpoint_id)
           OR ROW(NEW.requesting_client,NEW.requesting_session_id,NEW.requesting_model)
              IS DISTINCT FROM ROW('dashboard',NEW.manual_request->>'actor_session_id',NULL::text)
           OR NOT EXISTS(SELECT 1 FROM SCHEMA.work_events e WHERE e.id=NEW.completion_event_id
               AND e.checkpoint_id=NEW.completion_checkpoint_id AND e.work_item_id=work.id
               AND e.project_id=NEW.project_id AND e.event_type='work_completed')
           OR NEW.policy_decision_id IS DISTINCT FROM (SELECT id
               FROM SCHEMA.work_completion_review_policies
               WHERE completion_checkpoint_id=NEW.completion_checkpoint_id)
           OR EXISTS(SELECT 1 FROM SCHEMA.work_agent_follow_ups q
               WHERE q.completion_checkpoint_id=NEW.completion_checkpoint_id AND q.state='pending')
        THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='manual review requires human request';
        END IF;
    ELSIF TG_TABLE_NAME='code_reviews' THEN
""",
    ),
    (
        "mnemonic_code_review_resource_insert",
        "IF review.created_event_id IS NOT NULL OR review.state<>'requested' OR",
        """IF (review.created_event_id IS NOT NULL AND NOT
               (review.request_reason='manual' AND review.scope_sha256 IS NULL
                AND review.scope_preparation IS NULL AND EXISTS(
                    SELECT 1 FROM SCHEMA.work_leases l WHERE l.code_review_id=review.id
                      AND l.mode='warm' AND l.expires_at>clock_timestamp())))
           OR review.state<>'requested' OR""",
    ),
    (
        "mnemonic_code_review_resource_mutation",
        "    allowed:=ARRAY['created_event_id','created_sequence'];",
        """
    IF TG_TABLE_NAME='code_reviews' AND old_data->>'request_reason'='manual'
       AND old_data->>'scope_sha256' IS NULL AND new_data->>'scope_sha256' IS NOT NULL THEN
        IF OLD.state<>'requested' OR OLD.scope_preparation IS NOT NULL
           OR (old_data-ARRAY['scope_sha256','scope_preparation']) IS DISTINCT FROM
              (new_data-ARRAY['scope_sha256','scope_preparation'])
           OR NOT EXISTS(SELECT 1 FROM SCHEMA.work_leases l JOIN SCHEMA.work_events e
               ON e.lease_generation_id=l.lease_generation_id AND e.event_type='work_claimed'
               WHERE l.code_review_id=OLD.id AND l.mode='warm' AND l.expires_at>clock_timestamp()
                 AND NEW.scope_preparation=jsonb_build_object(
                    'claim_request_id',l.claim_request_id,'event_id',e.id::text)) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='manual scope requires its first claim';
        END IF;
        RETURN NEW;
    END IF;
    allowed:=ARRAY['created_event_id','created_sequence'];""",
    ),
    (
        "mnemonic_code_review_assert_review",
        "SELECT * INTO STRICT scope FROM SCHEMA.code_review_scopes WHERE",
        "SELECT * INTO scope FROM SCHEMA.code_review_scopes WHERE",
    ),
    (
        "mnemonic_code_review_assert_review",
        "IF NOT EXISTS(SELECT 1 FROM SCHEMA.code_review_handoffs WHERE\n"
        "        code_review_handoffs.review_id=review.id)\n"
        "       OR review.scope_sha256<>encode(sha256(convert_to(canonical,'UTF8')),'hex') OR",
        """IF (review.scope_sha256 IS NULL AND (review.request_reason<>'manual'
            OR scope.review_id IS NOT NULL OR review.scope_preparation IS NOT NULL
            OR EXISTS(SELECT 1 FROM SCHEMA.code_review_handoffs h WHERE h.review_id=review.id)
            OR review.state='completed'))
       OR (review.scope_sha256 IS NOT NULL AND (scope.review_id IS NULL
            OR NOT EXISTS(SELECT 1 FROM SCHEMA.code_review_handoffs h WHERE h.review_id=review.id)
            OR review.scope_sha256<>encode(sha256(convert_to(canonical,'UTF8')),'hex')))
       OR""",
    ),
]

GUARDS = """
CREATE FUNCTION SCHEMA.mnemonic_manual_review_work_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE entry jsonb:=NEW.manual_review_request; event SCHEMA.work_events;
BEGIN
    IF TG_OP='INSERT' THEN
        IF entry IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='manual review requests require an existing work item'; END IF;
        RETURN NEW;
    END IF;
    IF OLD.manual_review_request IS NOT NULL AND NEW.project_id<>OLD.project_id THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='manual review history cannot move projects';
    END IF;
    IF entry IS NOT DISTINCT FROM OLD.manual_review_request THEN RETURN NEW; END IF;
    IF entry IS NULL AND OLD.status='done' AND NEW.status='pending' THEN RETURN NEW; END IF;
    IF OLD.manual_review_request IS NOT NULL OR entry IS NULL OR NEW.remediation_depth>=2
       OR NEW.deleted_at IS NOT NULL OR NEW.status<>OLD.status
       OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(entry) key)
          IS DISTINCT FROM ARRAY['actor_client','actor_model','actor_session_id','created_at',
                                  'event_id','id','priority','work_version']
       OR entry->>'actor_client' IS DISTINCT FROM 'dashboard'
       OR entry->'actor_model' IS DISTINCT FROM 'null'::jsonb
       OR entry->'priority' IS DISTINCT FROM to_jsonb(NEW.priority)
       OR entry->'work_version' IS DISTINCT FROM to_jsonb(NEW.version)
       OR (entry->>'id')::uuid IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid manual review request';
    END IF;
    SELECT * INTO STRICT event FROM SCHEMA.work_events WHERE id=(entry->>'event_id')::bigint;
    IF event.event_type<>'progress' OR event.work_item_id<>NEW.id
       OR event.project_id<>NEW.project_id
       OR event.actor_client IS DISTINCT FROM 'dashboard' OR event.actor_model IS NOT NULL
       OR event.actor_session_id IS DISTINCT FROM entry->>'actor_session_id'
       OR event.created_at IS DISTINCT FROM (entry->>'created_at')::timestamptz
       OR event.metadata IS DISTINCT FROM jsonb_build_object(
           'manual_review_request_id',entry->>'id','work_version',NEW.version) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='manual review requires human event';
    END IF;
    RETURN NEW;
END $f$;
CREATE TRIGGER manual_review_work_guard BEFORE INSERT OR UPDATE ON SCHEMA.work_items
FOR EACH ROW EXECUTE FUNCTION SCHEMA.mnemonic_manual_review_work_guard();

CREATE FUNCTION SCHEMA.mnemonic_manual_review_work_sealed()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
DECLARE work SCHEMA.work_items;
BEGIN
    SELECT * INTO STRICT work FROM SCHEMA.work_items WHERE id=NEW.id;
    IF work.status='done' AND work.deleted_at IS NULL
       AND work.manual_review_request IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM SCHEMA.code_reviews r WHERE r.work_item_id=work.id
          AND r.manual_request=work.manual_review_request
          AND EXISTS(SELECT 1 FROM SCHEMA.checkpoints c WHERE c.id=r.completion_checkpoint_id
              AND c.work_item_id=work.id
              AND c.completion_generation=work.completion_generation)) THEN
        RAISE EXCEPTION USING ERRCODE='23514',
            MESSAGE='Done must fulfill its manual review request';
    END IF;
    RETURN NULL;
END $f$;
CREATE CONSTRAINT TRIGGER manual_review_work_sealed AFTER INSERT OR UPDATE ON SCHEMA.work_items
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION SCHEMA.mnemonic_manual_review_work_sealed();

CREATE FUNCTION SCHEMA.mnemonic_manual_review_lease_sealed()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
BEGIN
    IF EXISTS(SELECT 1 FROM SCHEMA.work_leases l JOIN SCHEMA.code_reviews r ON r.id=l.code_review_id
              WHERE l.work_item_id=NEW.work_item_id AND r.scope_sha256 IS NULL) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='a review claim must pin its scope';
    END IF;
    RETURN NULL;
END $f$;
CREATE CONSTRAINT TRIGGER manual_review_lease_sealed AFTER INSERT OR UPDATE ON SCHEMA.work_leases
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION SCHEMA.mnemonic_manual_review_lease_sealed();
"""


def _patch(schema: str, name: str, old: str, new: str) -> None:
    bind = op.get_bind()
    definition = bind.scalar(
        sa.text("""SELECT pg_get_functiondef(p.oid) FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=current_schema() AND p.proname=:name"""),
        {"name": name},
    )
    old, new = old.replace("SCHEMA", schema), new.replace("SCHEMA", schema)
    if old not in definition:
        raise RuntimeError(f"Unexpected definition for {name}")
    op.execute(definition.replace(old, new))


def upgrade() -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_identifier(
        bind.scalar(sa.text("SELECT current_schema()"))
    )
    op.add_column("work_items", sa.Column("manual_review_request", JSONB()))
    op.add_column("code_reviews", sa.Column("manual_request", JSONB()))
    op.add_column("code_reviews", sa.Column("scope_preparation", JSONB()))
    op.alter_column("code_reviews", "policy_decision_id", nullable=True)
    op.alter_column("code_reviews", "scope_sha256", nullable=True)
    op.drop_constraint(op.f("ck_code_reviews_reason_valid"), "code_reviews", type_="check")
    op.create_check_constraint("reason_valid", "code_reviews", REASON_CHECK)
    op.create_check_constraint(
        "automatic_review_scope",
        "code_reviews",
        "request_reason = 'manual' OR "
        "(policy_decision_id IS NOT NULL AND scope_sha256 IS NOT NULL)",
    )
    for name, old, new in PATCHES:
        _patch(schema, name, old, new)
    op.execute(GUARDS.replace("SCHEMA", schema))


def downgrade() -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_identifier(
        bind.scalar(sa.text("SELECT current_schema()"))
    )
    op.execute(f"LOCK TABLE {schema}.work_items, {schema}.code_reviews IN ACCESS EXCLUSIVE MODE")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM work_items WHERE manual_review_request "
            "IS NOT NULL) OR EXISTS(SELECT 1 FROM code_reviews "
            "WHERE request_reason='manual')"
        )
    ):
        raise RuntimeError("Manual review history exists; restore a pre-upgrade backup")
    for table, trigger in (
        ("work_items", "manual_review_work_guard"),
        ("work_items", "manual_review_work_sealed"),
        ("work_leases", "manual_review_lease_sealed"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {schema}.{table}")
        op.execute(f"DROP FUNCTION {schema}.mnemonic_{trigger}()")
    for name, old, new in reversed(PATCHES):
        _patch(schema, name, new, old)
    op.drop_constraint(
        op.f("ck_code_reviews_automatic_review_scope"), "code_reviews", type_="check"
    )
    op.drop_constraint(op.f("ck_code_reviews_reason_valid"), "code_reviews", type_="check")
    op.create_check_constraint(
        "reason_valid",
        "code_reviews",
        "(request_reason = 'mandatory' AND answer_id IS NULL) OR "
        "(request_reason = 'recommended' AND answer_id IS NOT NULL)",
    )
    op.alter_column("code_reviews", "policy_decision_id", nullable=False)
    op.alter_column("code_reviews", "scope_sha256", nullable=False)
    op.drop_column("code_reviews", "scope_preparation")
    op.drop_column("code_reviews", "manual_request")
    op.drop_column("work_items", "manual_review_request")
