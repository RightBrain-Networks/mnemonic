"""Frozen Phase 12 v1 table definitions shared by migrations and ORM metadata.

Do not change these definitions for a later schema revision; add a new migration.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.schema import SchemaItem


def _fk(
    columns: list[str],
    targets: list[str],
    name: str,
    *,
    deferrable: bool | None = None,
    initially: str | None = None,
    use_alter: bool = False,
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        columns,
        targets,
        name=name,
        ondelete="RESTRICT",
        deferrable=deferrable,
        initially=initially,
        use_alter=use_alter,
    )


def activity_head_elements() -> list[SchemaItem]:
    return [
        sa.Column("project_id", sa.UUID(), primary_key=True),
        sa.Column(
            "stream_id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "historical_through_sequence", sa.BigInteger(), nullable=False, server_default="0"
        ),
        _fk(["project_id"], ["projects.id"], "fk_project_activity_heads_project_id_projects"),
        sa.UniqueConstraint("stream_id", name="uq_project_activity_heads_stream_id"),
        sa.CheckConstraint(
            "historical_through_sequence >= 0 AND last_sequence >= historical_through_sequence",
            name="sequence_bounds",
        ),
    ]


def activity_matrix(*, reports: bool) -> str:
    fields = ["work_event_id", "work_item_id", "lease_generation_id"]
    variants = {
        "work_event": {"work_event_id", "work_item_id"},
        "project_created": set(),
        "project_updated": set(),
        "lease_renewed": {"work_item_id", "lease_generation_id"},
    }
    if reports:
        fields += [
            "job_completion_report_id",
            "human_dismissal_id",
            "follow_up_id",
            "settings_revision",
        ]
        variants.update(
            {
                "job_completion_report_created": {"work_item_id", "job_completion_report_id"},
                "job_completion_report_dismissed": {
                    "work_item_id",
                    "job_completion_report_id",
                    "human_dismissal_id",
                },
                "job_completion_report_follow_up_created": {
                    "work_item_id",
                    "job_completion_report_id",
                    "follow_up_id",
                },
                "project_settings_updated": {"settings_revision"},
            }
        )
    return " OR ".join(
        "(kind = '"
        + kind
        + "' AND "
        + " AND ".join(
            field + (" IS NOT NULL" if field in present else " IS NULL") for field in fields
        )
        + ")"
        for kind, present in variants.items()
    )


def activity_elements(*, reports: bool = True, movable_work: bool = False) -> list[SchemaItem]:
    elements: list[SchemaItem] = [
        sa.Column("project_id", sa.UUID(), primary_key=True),
        sa.Column("sequence", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("work_event_id", sa.BigInteger()),
        sa.Column("work_item_id", sa.UUID()),
        sa.Column("lease_generation_id", sa.UUID()),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("origin", sa.String(16), nullable=False, server_default="live"),
        _fk(["project_id"], ["project_activity_heads.project_id"], "fk_project_activity_head"),
        _fk(
            ["work_item_id"] if movable_work else ["project_id", "work_item_id"],
            (
                ["work_items.id"]
                if movable_work
                else ["work_items.project_id", "work_items.id"]
            ),
            "fk_project_activity_work",
        ),
        _fk(
            ["project_id", "work_item_id", "work_event_id"],
            ["work_events.project_id", "work_events.work_item_id", "work_events.id"],
            "fk_project_activity_event",
        ),
        sa.UniqueConstraint("work_event_id", name="uq_project_activity_work_event_id"),
        sa.CheckConstraint("sequence > 0", name="sequence_positive"),
        sa.CheckConstraint("origin IN ('live', 'history_import')", name="origin_valid"),
        sa.CheckConstraint(
            "origin <> 'history_import' OR kind = 'work_event'", name="history_kind"
        ),
        sa.CheckConstraint(activity_matrix(reports=reports), name="variant_valid"),
    ]
    if reports:
        elements.extend(activity_report_elements())
    return elements


def activity_report_elements() -> list[SchemaItem]:
    return [
        sa.Column("job_completion_report_id", sa.UUID()),
        sa.Column("human_dismissal_id", sa.UUID()),
        sa.Column("follow_up_id", sa.UUID()),
        sa.Column("settings_revision", sa.BigInteger()),
        sa.CheckConstraint(
            "settings_revision IS NULL OR settings_revision > 0", name="settings_revision"
        ),
        _fk(
            ["project_id", "job_completion_report_id"],
            ["job_completion_reports.project_id", "job_completion_reports.id"],
            "fk_project_activity_report",
        ),
        _fk(
            ["project_id", "job_completion_report_id", "human_dismissal_id"],
            [
                "job_completion_report_reviews.project_id",
                "job_completion_report_reviews.report_id",
                "job_completion_report_reviews.dismissal_id",
            ],
            "fk_project_activity_dismissal",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        _fk(
            ["project_id", "job_completion_report_id", "follow_up_id"],
            [
                "job_completion_report_follow_ups.project_id",
                "job_completion_report_follow_ups.report_id",
                "job_completion_report_follow_ups.id",
            ],
            "fk_project_activity_follow_up",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.Index(
            "uq_project_activity_report_created",
            "job_completion_report_id",
            unique=True,
            postgresql_where=sa.text("kind = 'job_completion_report_created'"),
        ),
        sa.UniqueConstraint("human_dismissal_id", name="uq_project_activity_human_dismissal_id"),
        sa.UniqueConstraint("follow_up_id", name="uq_project_activity_follow_up_id"),
    ]


def _actor_columns(prefix: str = "actor_") -> list[SchemaItem]:
    return [
        sa.Column(prefix + "client", sa.String(80), nullable=False),
        sa.Column(prefix + "session_id", sa.String(200), nullable=False),
        sa.Column(prefix + "model", sa.String(120)),
        sa.CheckConstraint(
            f"mnemonic_has_non_whitespace({prefix}client) AND "
            f"mnemonic_has_non_whitespace({prefix}session_id) AND "
            f"({prefix}model IS NULL OR mnemonic_has_non_whitespace({prefix}model))",
            name="actor_valid",
        ),
    ]


def report_elements(*, movable_work: bool = False) -> list[SchemaItem]:
    return [
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("closeout_event_id", sa.BigInteger(), nullable=False),
        sa.Column("closeout_work_version", sa.Integer(), nullable=False),
        sa.Column("closeout_status", sa.String(20), nullable=False),
        sa.Column("completion_checkpoint_id", sa.UUID()),
        sa.Column("work_title_at_closeout", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("fyi_items", ARRAY(sa.Text()), nullable=False),
        sa.Column("prompt_revision", sa.BigInteger(), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        *_actor_columns(),
        sa.UniqueConstraint(
            "closeout_event_id", name="uq_job_completion_reports_closeout_event_id"
        ),
        sa.UniqueConstraint(
            "project_id",
            "work_item_id",
            "closeout_work_version",
            name="uq_job_completion_reports_closeout_slot",
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_job_reports_project_id_id"),
        sa.UniqueConstraint("project_id", "work_item_id", "id", name="uq_job_reports_owner"),
        sa.UniqueConstraint(
            "project_id",
            "work_item_id",
            "id",
            "closeout_event_id",
            name="uq_job_reports_event_owner",
        ),
        _fk(
            ["work_item_id"] if movable_work else ["project_id", "work_item_id"],
            (
                ["work_items.id"]
                if movable_work
                else ["work_items.project_id", "work_items.id"]
            ),
            "fk_job_reports_work",
        ),
        _fk(
            ["project_id", "work_item_id", "closeout_event_id", "id"],
            [
                "work_events.project_id",
                "work_events.work_item_id",
                "work_events.id",
                "work_events.job_completion_report_id",
            ],
            "fk_job_reports_event",
            deferrable=True,
            initially="DEFERRED",
        ),
        _fk(
            ["work_item_id", "completion_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            "fk_job_reports_checkpoint",
        ),
        sa.CheckConstraint(
            "closeout_work_version > 1 AND prompt_revision > 0", name="versions_valid"
        ),
        sa.CheckConstraint(
            "(closeout_status = 'done' AND completion_checkpoint_id IS NOT NULL) OR "
            "(closeout_status IN ('wont-do', 'promoted') AND completion_checkpoint_id IS NULL)",
            name="closeout_status_valid",
        ),
        sa.CheckConstraint(
            "mnemonic_job_report_text_valid_v1(summary, 2000, 8000, false)", name="summary_valid"
        ),
        sa.CheckConstraint(
            "mnemonic_job_report_fyis_valid_v1(summary, fyi_items)", name="fyis_valid"
        ),
        sa.CheckConstraint(
            "mnemonic_job_report_text_valid_v1(prompt_text, 8000, 16384, true)", name="prompt_valid"
        ),
        sa.CheckConstraint(
            "prompt_sha256 = encode(sha256(convert_to(prompt_text, 'UTF8')), 'hex')",
            name="prompt_hash_valid",
        ),
    ]


def review_elements() -> list[SchemaItem]:
    elements: list[SchemaItem] = [
        sa.Column("report_id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("created_sequence", sa.BigInteger(), nullable=False),
        sa.Column("dismissal_id", sa.UUID()),
        sa.Column("dismissed_at", sa.DateTime(timezone=True)),
        sa.Column("dismissal_actor_client", sa.String(80)),
        sa.Column("dismissal_actor_session_id", sa.String(200)),
        sa.Column("dismissal_actor_model", sa.String(120)),
        sa.Column("follow_up_count", sa.BigInteger(), nullable=False, server_default="0"),
        _fk(
            ["project_id", "work_item_id", "report_id"],
            [
                "job_completion_reports.project_id",
                "job_completion_reports.work_item_id",
                "job_completion_reports.id",
            ],
            "fk_job_report_reviews_report",
        ),
        _fk(
            ["project_id", "created_sequence"],
            ["project_activity.project_id", "project_activity.sequence"],
            "fk_job_report_reviews_activity",
        ),
        sa.UniqueConstraint(
            "project_id", "report_id", "dismissal_id", name="uq_job_report_reviews_dismissal_owner"
        ),
        sa.Index(
            "uq_job_report_reviews_dismissal_id",
            "dismissal_id",
            unique=True,
            postgresql_where=sa.text("dismissal_id IS NOT NULL"),
        ),
        sa.CheckConstraint("created_sequence > 0 AND follow_up_count >= 0", name="counts_valid"),
        sa.CheckConstraint(
            "(dismissal_id IS NULL AND dismissed_at IS NULL AND dismissal_actor_client IS NULL "
            "AND dismissal_actor_session_id IS NULL AND dismissal_actor_model IS NULL) OR "
            "(dismissal_id IS NOT NULL AND dismissed_at IS NOT NULL "
            "AND dismissal_actor_client IS NOT NULL AND dismissal_actor_session_id IS NOT NULL "
            "AND mnemonic_has_non_whitespace(dismissal_actor_client) "
            "AND mnemonic_has_non_whitespace(dismissal_actor_session_id) "
            "AND (dismissal_actor_model IS NULL "
            "OR mnemonic_has_non_whitespace(dismissal_actor_model)))",
            name="dismissal_valid",
        ),
    ]
    for work_filter in (False, True):
        for state in ("all", "undismissed", "dismissed"):
            columns = ["project_id"] + (["work_item_id"] if work_filter else [])
            condition = (
                None
                if state == "all"
                else sa.text(
                    "dismissal_id IS " + ("NULL" if state == "undismissed" else "NOT NULL")
                )
            )
            elements.append(
                sa.Index(
                    f"ix_job_report_reviews_{'work_' if work_filter else ''}{state}",
                    *columns,
                    sa.text("created_sequence DESC"),
                    postgresql_where=condition,
                )
            )
    return elements


def report_count_elements() -> list[SchemaItem]:
    return [
        sa.Column("project_id", sa.UUID(), primary_key=True),
        sa.Column("undismissed_count", sa.BigInteger(), nullable=False, server_default="0"),
        _fk(["project_id"], ["projects.id"], "fk_project_job_report_counts_project"),
        sa.CheckConstraint("undismissed_count >= 0", name="nonnegative"),
    ]


def work_report_provenance_head_elements() -> list[SchemaItem]:
    return [
        sa.Column("work_item_id", sa.UUID(), primary_key=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        _fk(
            ["work_item_id"],
            ["work_items.id"],
            "fk_work_report_provenance_heads_work_item",
        ),
        sa.CheckConstraint(
            "last_sequence >= 0",
            name="sequence_nonnegative",
        ),
    ]


def follow_up_elements(
    *,
    movable_work: bool = False,
    sequenced_provenance: bool = False,
) -> list[SchemaItem]:
    provenance_columns: list[SchemaItem] = []
    provenance_constraints: list[SchemaItem] = []
    if sequenced_provenance:
        provenance_columns = [
            sa.Column("source_work_sequence", sa.BigInteger(), nullable=False),
            sa.Column("follow_up_work_sequence", sa.BigInteger(), nullable=False),
        ]
        provenance_constraints = [
            sa.UniqueConstraint(
                "source_work_item_id",
                "source_work_sequence",
                name="uq_job_report_follow_ups_source_work_sequence",
            ),
            sa.UniqueConstraint(
                "follow_up_work_item_id",
                "follow_up_work_sequence",
                name="uq_job_report_follow_ups_follow_up_work_sequence",
            ),
            sa.CheckConstraint(
                "source_work_sequence > 0",
                name=sa.schema.conv("ck_job_completion_report_follow_ups_source_work_sequenc_4b58"),
            ),
            sa.CheckConstraint(
                "follow_up_work_sequence > 0",
                name=sa.schema.conv("ck_job_completion_report_follow_ups_follow_up_work_sequ_b375"),
            ),
        ]
    return [
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("source_work_item_id", sa.UUID(), nullable=False),
        sa.Column("follow_up_work_item_id", sa.UUID(), nullable=False),
        sa.Column("created_sequence", sa.BigInteger(), nullable=False),
        *provenance_columns,
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        *_actor_columns(),
        sa.UniqueConstraint("follow_up_work_item_id", name="uq_job_report_follow_ups_work"),
        sa.UniqueConstraint("project_id", "report_id", "id", name="uq_job_report_follow_ups_owner"),
        *provenance_constraints,
        _fk(
            ["project_id", "source_work_item_id", "report_id"],
            [
                "job_completion_reports.project_id",
                "job_completion_reports.work_item_id",
                "job_completion_reports.id",
            ],
            "fk_job_report_follow_ups_report",
        ),
        _fk(
            ["follow_up_work_item_id"]
            if movable_work
            else ["project_id", "follow_up_work_item_id"],
            ["work_items.id"]
            if movable_work
            else ["work_items.project_id", "work_items.id"],
            "fk_job_report_follow_ups_work",
        ),
        _fk(
            ["project_id", "created_sequence"],
            ["project_activity.project_id", "project_activity.sequence"],
            "fk_job_report_follow_ups_activity",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("source_work_item_id <> follow_up_work_item_id", name="distinct_work"),
        sa.CheckConstraint("created_sequence > 0", name="sequence_positive"),
        sa.Index(
            "ix_job_report_follow_ups_report_sequence",
            "project_id",
            "report_id",
            "created_sequence",
        ),
        sa.Index(
            "ix_job_report_follow_ups_source_sequence",
            "project_id",
            "source_work_item_id",
            "created_sequence",
        ),
    ]
