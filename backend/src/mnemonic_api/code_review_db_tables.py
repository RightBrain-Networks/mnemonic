"""Frozen code-review v1 table definitions shared by migration and ORM metadata."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import SchemaItem


def _fk(columns: list[str], targets: list[str], name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        columns,
        targets,
        name=name,
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
        use_alter=True,
    )


def _identity(table: str) -> list[SchemaItem]:
    return [
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint("project_id", "work_item_id", "id", name=f"uq_{table}_owner"),
        _fk(
            ["project_id", "work_item_id"],
            ["work_items.project_id", "work_items.id"],
            f"fk_{table}_work",
        ),
    ]


def _actor(prefix: str) -> list[SchemaItem]:
    return [
        sa.Column(f"{prefix}_client", sa.String(80), nullable=False),
        sa.Column(f"{prefix}_session_id", sa.String(200), nullable=False),
        sa.Column(f"{prefix}_model", sa.String(120)),
        sa.CheckConstraint(
            f"mnemonic_has_non_whitespace({prefix}_client) AND "
            f"mnemonic_has_non_whitespace({prefix}_session_id) AND "
            f"({prefix}_model IS NULL OR mnemonic_has_non_whitespace({prefix}_model))",
            name=f"{prefix}_valid",
        ),
    ]


def _event(column: str, table: str) -> list[SchemaItem]:
    return [
        sa.Column(column, sa.BigInteger()),
        _fk(
            ["project_id", "work_item_id", column],
            ["work_events.project_id", "work_events.work_item_id", "work_events.id"],
            f"fk_{table}_{column}",
        ),
        sa.UniqueConstraint(column, name=f"uq_{table}_{column}"),
    ]


def _created_event(table: str) -> list[SchemaItem]:
    return [
        *_event("created_event_id", table),
        sa.Column("created_sequence", sa.BigInteger()),
        sa.CheckConstraint(
            "created_sequence IS NULL OR created_sequence > 0", name="created_sequence_positive"
        ),
    ]


def _queue_indexes(table: str) -> list[SchemaItem]:
    return [
        sa.Index(f"ix_{table}_project_state_sequence", "project_id", "state", "created_sequence"),
        sa.Index(f"ix_{table}_work_sequence", "work_item_id", "created_sequence"),
    ]


def policy_elements() -> list[SchemaItem]:
    table = "work_completion_review_policies"
    return [
        *_identity(table),
        sa.Column("completion_checkpoint_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("completion_event_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("settings_revision", sa.BigInteger(), nullable=False),
        sa.Column("required_min_priority", sa.SmallInteger(), nullable=False),
        sa.Column("optional_min_priority", sa.SmallInteger(), nullable=False),
        sa.Column("allow_remediation_code_reviews", sa.Boolean(), nullable=False),
        sa.Column("priority_at_closeout", sa.SmallInteger(), nullable=False),
        sa.Column("remediation_depth", sa.SmallInteger(), nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.CheckConstraint(
            "settings_revision > 0 AND priority_at_closeout BETWEEN 0 AND 100 "
            "AND remediation_depth BETWEEN 0 AND 2",
            name="snapshot_ranges",
        ),
        sa.CheckConstraint(
            "required_min_priority BETWEEN 0 AND 100 AND "
            "required_min_priority % 5 = 0 AND "
            "optional_min_priority BETWEEN 0 AND 100 AND "
            "optional_min_priority % 5 = 0",
            name="threshold_ranges",
        ),
        sa.CheckConstraint(
            "decision IN ('mandatory', 'ask_recommendation', 'not_requested', "
            "'ineligible_depth_limit', 'ineligible_remediation_disabled')",
            name="decision_valid",
        ),
        _fk(
            ["work_item_id", "completion_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            f"fk_{table}_checkpoint",
        ),
        _fk(
            ["project_id", "work_item_id", "completion_event_id"],
            ["work_events.project_id", "work_events.work_item_id", "work_events.id"],
            f"fk_{table}_event",
        ),
    ]


def follow_up_elements() -> list[SchemaItem]:
    table = "work_agent_follow_ups"
    return [
        *_identity(table),
        *_actor("origin"),
        *_created_event(table),
        sa.Column("trigger_event_id", sa.BigInteger(), nullable=False),
        sa.Column("completion_checkpoint_id", sa.UUID()),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("audience", sa.String(24), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("allowed_answers", JSONB(), nullable=False),
        sa.Column("required_answer_fields", JSONB(), nullable=False),
        sa.Column("kind_data", JSONB(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("answer_id", sa.UUID()),
        *_event("superseded_by_event_id", table),
        sa.UniqueConstraint("trigger_event_id", "kind", name=f"uq_{table}_trigger_kind"),
        sa.CheckConstraint("schema_version = 1 AND version IN (1, 2)", name="version_valid"),
        sa.CheckConstraint("kind = 'code_review_recommendation'", name="kind_valid"),
        sa.CheckConstraint("audience IN ('origin_agent','origin_human')", name="audience_valid"),
        sa.CheckConstraint("state IN ('pending','answered','superseded')", name="state_valid"),
        sa.CheckConstraint(
            "mnemonic_job_report_text_valid_v1(question, 8000, 8192, true)", name="question_valid"
        ),
        sa.CheckConstraint(
            'allowed_answers = \'["yes","no"]\'::jsonb AND '
            "jsonb_typeof(required_answer_fields) = 'array' AND "
            "octet_length(required_answer_fields::text) <= 2048 AND "
            "jsonb_typeof(kind_data) = 'object' AND "
            "octet_length(kind_data::text) <= 2048",
            name="kind_data_valid",
        ),
        _fk(
            ["project_id", "work_item_id", "trigger_event_id"],
            ["work_events.project_id", "work_events.work_item_id", "work_events.id"],
            f"fk_{table}_trigger",
        ),
        _fk(
            ["work_item_id", "completion_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            f"fk_{table}_checkpoint",
        ),
        _fk(
            ["project_id", "work_item_id", "answer_id"],
            [
                "work_agent_follow_up_answers.project_id",
                "work_agent_follow_up_answers.work_item_id",
                "work_agent_follow_up_answers.id",
            ],
            f"fk_{table}_answer",
        ),
        *_queue_indexes(table),
    ]


def answer_elements() -> list[SchemaItem]:
    table = "work_agent_follow_up_answers"
    return [
        *_identity(table),
        *_actor("actor"),
        *_event("created_event_id", table),
        sa.Column("follow_up_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("recommend_review", sa.Boolean(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("code_review_id", sa.UUID(), unique=True),
        sa.CheckConstraint(
            "mnemonic_job_report_text_valid_v1(rationale, 2000, 8000, true)", name="rationale_valid"
        ),
        sa.CheckConstraint(
            "recommend_review = (code_review_id IS NOT NULL)", name="recommendation_reference"
        ),
        _fk(
            ["project_id", "work_item_id", "follow_up_id"],
            [
                "work_agent_follow_ups.project_id",
                "work_agent_follow_ups.work_item_id",
                "work_agent_follow_ups.id",
            ],
            f"fk_{table}_follow_up",
        ),
        _fk(
            ["project_id", "work_item_id", "code_review_id"],
            ["code_reviews.project_id", "code_reviews.work_item_id", "code_reviews.id"],
            f"fk_{table}_review",
        ),
    ]


def review_elements() -> list[SchemaItem]:
    table = "code_reviews"
    return [
        *_identity(table),
        *_actor("requesting"),
        *_created_event(table),
        sa.UniqueConstraint("work_item_id", "id", name="uq_code_reviews_work_id"),
        sa.Column("completion_checkpoint_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("completion_event_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("policy_decision_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("answer_id", sa.UUID(), unique=True),
        sa.Column("request_reason", sa.String(16), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(16), nullable=False, server_default="requested"),
        sa.Column("scope_sha256", sa.String(64), nullable=False),
        sa.Column("result_id", sa.UUID(), unique=True),
        *_event("superseded_by_event_id", table),
        sa.CheckConstraint("schema_version = 1 AND version IN (1,2)", name="version_valid"),
        sa.CheckConstraint("state IN ('requested','completed','superseded')", name="state_valid"),
        sa.CheckConstraint("scope_sha256 ~ '^[0-9a-f]{64}$'", name="scope_hash_valid"),
        sa.CheckConstraint(
            "(request_reason = 'mandatory' AND answer_id IS NULL) OR "
            "(request_reason = 'recommended' AND answer_id IS NOT NULL)",
            name="reason_valid",
        ),
        _fk(
            ["work_item_id", "completion_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            f"fk_{table}_checkpoint",
        ),
        _fk(
            ["project_id", "work_item_id", "completion_event_id"],
            ["work_events.project_id", "work_events.work_item_id", "work_events.id"],
            f"fk_{table}_completion_event",
        ),
        _fk(
            ["project_id", "work_item_id", "policy_decision_id"],
            [
                "work_completion_review_policies.project_id",
                "work_completion_review_policies.work_item_id",
                "work_completion_review_policies.id",
            ],
            f"fk_{table}_policy",
        ),
        _fk(
            ["project_id", "work_item_id", "answer_id"],
            [
                "work_agent_follow_up_answers.project_id",
                "work_agent_follow_up_answers.work_item_id",
                "work_agent_follow_up_answers.id",
            ],
            f"fk_{table}_answer",
        ),
        _fk(
            ["project_id", "work_item_id", "result_id"],
            [
                "code_review_results.project_id",
                "code_review_results.work_item_id",
                "code_review_results.id",
            ],
            f"fk_{table}_result",
        ),
        *_queue_indexes(table),
    ]


def _review_owned(table: str) -> list[SchemaItem]:
    return [
        sa.Column("review_id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        _fk(
            ["project_id", "work_item_id", "review_id"],
            ["code_reviews.project_id", "code_reviews.work_item_id", "code_reviews.id"],
            f"fk_{table}_review",
        ),
    ]


def scope_elements() -> list[SchemaItem]:
    return [
        *_review_owned("code_review_scopes"),
        sa.Column("repositories", JSONB(), nullable=False),
        sa.CheckConstraint(
            "mnemonic_code_review_scope_valid(repositories)", name="repositories_valid"
        ),
    ]


def handoff_elements() -> list[SchemaItem]:
    return [
        *_review_owned("code_review_handoffs"),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("decisions", JSONB(), nullable=False),
        sa.Column("focus_areas", JSONB(), nullable=False),
        sa.Column("traps", JSONB(), nullable=False),
        sa.Column("validation_summary", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "mnemonic_code_review_handoff_valid(change_summary, decisions, focus_areas, traps, "
            "validation_summary)",
            name="handoff_valid",
        ),
    ]


def result_elements() -> list[SchemaItem]:
    table = "code_review_results"
    return [
        *_identity(table),
        *_actor("actor"),
        *_event("created_event_id", table),
        sa.Column("review_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("scope_sha256", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("coverage", JSONB(), nullable=False),
        sa.Column("limitations", JSONB(), nullable=False),
        sa.Column("findings_count", sa.SmallInteger(), nullable=False),
        sa.Column("lease_generation_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("claim_event_id", sa.BigInteger(), nullable=False, unique=True),
        sa.CheckConstraint("mode IN ('cold','warm')", name="mode_valid"),
        sa.CheckConstraint("scope_sha256 ~ '^[0-9a-f]{64}$'", name="scope_hash_valid"),
        sa.CheckConstraint("findings_count BETWEEN 0 AND 100", name="findings_count_valid"),
        sa.CheckConstraint(
            "mnemonic_job_report_text_valid_v1(summary,4000,16000,true) AND "
            "jsonb_typeof(coverage) = 'array' AND "
            "jsonb_array_length(coverage) BETWEEN 1 AND 10 AND "
            "mnemonic_code_review_text_array_valid(limitations,20,1000) AND "
            "octet_length(coverage::text) <= 65536",
            name="content_valid",
        ),
        _fk(
            ["project_id", "work_item_id", "review_id"],
            ["code_reviews.project_id", "code_reviews.work_item_id", "code_reviews.id"],
            f"fk_{table}_review",
        ),
        _fk(
            ["project_id", "work_item_id", "claim_event_id"],
            ["work_events.project_id", "work_events.work_item_id", "work_events.id"],
            f"fk_{table}_claim_event",
        ),
    ]


def finding_elements() -> list[SchemaItem]:
    return [
        sa.Column("result_id", sa.UUID(), primary_key=True),
        sa.Column("position", sa.SmallInteger(), primary_key=True),
        sa.Column("finding_key", sa.String(8), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        _fk(["result_id"], ["code_review_results.id"], "fk_code_review_findings_result"),
        sa.UniqueConstraint("result_id", "finding_key", name="uq_code_review_findings_key"),
        sa.CheckConstraint("position BETWEEN 0 AND 99", name="position_valid"),
        sa.CheckConstraint(
            "finding_key ~ '^F[0-9]{3}$' AND "
            "data->>'finding_key' = finding_key AND "
            "mnemonic_code_review_finding_valid(data)",
            name="data_valid",
        ),
    ]


def remediation_elements() -> list[SchemaItem]:
    return [
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("review_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("result_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("source_work_item_id", sa.UUID(), nullable=False),
        sa.Column("completion_checkpoint_id", sa.UUID(), nullable=False),
        sa.Column("remediation_work_item_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("relationship_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("parent_remediation_id", sa.UUID()),
        sa.Column("root_work_item_id", sa.UUID(), nullable=False),
        sa.Column("depth", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint(
            "project_id",
            "remediation_work_item_id",
            "id",
            "depth",
            name="uq_code_review_remediations_work_provenance",
        ),
        sa.CheckConstraint(
            "depth IN (1,2) AND source_work_item_id <> remediation_work_item_id "
            "AND ((depth=1 AND parent_remediation_id IS NULL) OR "
            "(depth=2 AND parent_remediation_id IS NOT NULL))",
            name="depth_valid",
        ),
        *[
            _fk(
                ["project_id", name],
                ["work_items.project_id", "work_items.id"],
                f"fk_code_review_remediations_{name}",
            )
            for name in ("source_work_item_id", "remediation_work_item_id", "root_work_item_id")
        ],
        _fk(
            ["project_id", "source_work_item_id", "review_id"],
            ["code_reviews.project_id", "code_reviews.work_item_id", "code_reviews.id"],
            "fk_code_review_remediations_review",
        ),
        _fk(
            ["project_id", "source_work_item_id", "result_id"],
            [
                "code_review_results.project_id",
                "code_review_results.work_item_id",
                "code_review_results.id",
            ],
            "fk_code_review_remediations_result",
        ),
        _fk(
            ["source_work_item_id", "completion_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            "fk_code_review_remediations_checkpoint",
        ),
        _fk(["relationship_id"], ["work_relationships.id"], "fk_code_review_remediations_edge"),
        _fk(
            ["parent_remediation_id"],
            ["code_review_remediations.id"],
            "fk_code_review_remediations_parent",
        ),
    ]


TABLE_ELEMENTS = {
    "work_completion_review_policies": policy_elements,
    "work_agent_follow_ups": follow_up_elements,
    "work_agent_follow_up_answers": answer_elements,
    "code_reviews": review_elements,
    "code_review_scopes": scope_elements,
    "code_review_handoffs": handoff_elements,
    "code_review_results": result_elements,
    "code_review_findings": finding_elements,
    "code_review_remediations": remediation_elements,
}


def event_extension_elements() -> list[SchemaItem]:
    return [
        *[
            _fk(
                ["project_id", "work_item_id", column],
                [f"{table}.project_id", f"{table}.work_item_id", f"{table}.id"],
                f"fk_work_events_{column}",
            )
            for column, table in (
                ("code_review_id", "code_reviews"),
                ("work_follow_up_id", "work_agent_follow_ups"),
                ("work_follow_up_answer_id", "work_agent_follow_up_answers"),
                ("code_review_result_id", "code_review_results"),
            )
        ],
        sa.CheckConstraint(
            "mnemonic_code_review_event_references_valid(event_type,code_review_id,"
            "work_follow_up_id,work_follow_up_answer_id,code_review_result_id)",
            name="code_review_references_valid",
        ),
    ]
