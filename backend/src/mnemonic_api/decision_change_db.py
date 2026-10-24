"""Explicit decision-change identity, classification, and immutable review scope."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from mnemonic_api.knowledge_db import _identity, _timestamp


def change_elements() -> list:
    return [
        *_identity(),
        sa.Column("origin_revision_id", UUID,
                  sa.ForeignKey("decision_revisions.id", ondelete="RESTRICT")),
        sa.Column("review_work_item_id", UUID,
                  sa.ForeignKey("work_items.id", ondelete="RESTRICT"), unique=True),
        sa.Column("parent_change_id", UUID,
                  sa.ForeignKey("decision_changes.id", ondelete="RESTRICT")),
        sa.Column("scope_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("acknowledged_scope_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("admission_actor_client", sa.String(64)),
        sa.Column("admission_actor_session_id", sa.String(200)),
        sa.Column("admission_actor_model", sa.String(200)),
        _timestamp(),
        _timestamp("updated_at"),
        sa.CheckConstraint("scope_version >= 0", name="scope_version"),
        sa.CheckConstraint(
            "acknowledged_scope_version BETWEEN 0 AND scope_version", name="acknowledged_scope"
        ),
        sa.CheckConstraint("parent_change_id IS NULL OR parent_change_id <> id", name="no_self"),
        sa.CheckConstraint(
            "(parent_change_id IS NULL AND admission_actor_client IS NULL "
            "AND admission_actor_session_id IS NULL AND admission_actor_model IS NULL) OR "
            "(parent_change_id IS NOT NULL AND admission_actor_client = 'dashboard' "
            "AND admission_actor_session_id IS NOT NULL "
            "AND length(btrim(admission_actor_session_id)) > 0 "
            "AND (admission_actor_model IS NULL OR length(btrim(admission_actor_model)) > 0))",
            name="human_admission",
        ),
        sa.Index("ix_decision_changes_project", "project_id", "created_at", "id"),
        sa.Index("ix_decision_changes_parent", "parent_change_id"),
    ]


def revision_elements() -> list:
    return [
        sa.Column("revision_id", UUID,
                  sa.ForeignKey("decision_revisions.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("change_id", UUID,
                  sa.ForeignKey("decision_changes.id", ondelete="RESTRICT")),
        sa.Column("kind", sa.String(15), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("governing_revision_id", UUID,
                  sa.ForeignKey("decision_revisions.id", ondelete="RESTRICT")),
        sa.Column("source_work_item_id", UUID,
                  sa.ForeignKey("work_items.id", ondelete="RESTRICT")),
        sa.Column("requires_admission", sa.Boolean, nullable=False, server_default="false"),
        _timestamp(),
        sa.CheckConstraint(
            "kind IN ('substantive','record-existing','maintenance')", name="kind"
        ),
        sa.CheckConstraint(
            "kind <> 'substantive' OR change_id IS NOT NULL", name="substantive_change"
        ),
        sa.CheckConstraint(
            "kind = 'substantive' OR (reason IS NOT NULL AND length(btrim(reason)) > 0)",
            name="classification_reason",
        ),
        sa.CheckConstraint(
            "kind <> 'record-existing' OR "
            "(governing_revision_id IS NOT NULL AND change_id IS NOT NULL)",
            name="existing_governance",
        ),
        sa.CheckConstraint(
            "governing_revision_id IS NULL OR governing_revision_id <> revision_id",
            name="no_self_governance",
        ),
        sa.CheckConstraint(
            "NOT requires_admission OR "
            "(kind='substantive' AND change_id IS NOT NULL AND source_work_item_id IS NOT NULL)",
            name="held_admission",
        ),
        sa.Index("ix_decision_revision_changes_change", "change_id"),
        sa.Index("ix_decision_revision_changes_source_work", "source_work_item_id"),
    ]


def assessment_elements() -> list:
    return [
        sa.Column("change_id", UUID,
                  sa.ForeignKey("decision_changes.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("assessment_id", UUID,
                  sa.ForeignKey("decision_impact_assessments.id", ondelete="RESTRICT"),
                  primary_key=True),
        _timestamp(),
        sa.Index("ix_decision_change_assessments_assessment", "assessment_id"),
    ]


def work_elements() -> list:
    return [
        sa.Column("work_item_id", UUID,
                  sa.ForeignKey("work_items.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("change_id", UUID,
                  sa.ForeignKey("decision_changes.id", ondelete="RESTRICT"), primary_key=True),
        _timestamp(),
        sa.Index("ix_decision_change_work_change", "change_id"),
    ]


def supersession_reuse_elements() -> list:
    table = "decision_supersession_reuses"
    elements = [
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("revision_id", UUID, primary_key=True),
        sa.Column("target_revision_id", UUID, primary_key=True),
        sa.Column("governing_revision_id", UUID, nullable=False),
        sa.Column("reason", sa.String(8000), nullable=False),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="reason_nonblank"),
        sa.CheckConstraint("revision_id <> governing_revision_id", name="no_self_governance"),
        sa.CheckConstraint("revision_id <> target_revision_id", name="no_self_target"),
    ]
    for name in ("revision_id", "target_revision_id", "governing_revision_id"):
        elements.append(sa.ForeignKeyConstraint(
            ["project_id", name], ["decision_revisions.project_id", "decision_revisions.id"],
            ondelete="RESTRICT", name=f"fk_{table}_{name}",
        ))
    return elements
