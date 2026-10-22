"""Immutable revision-pinned dependency and reassessment storage definitions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from mnemonic_api.knowledge_db import _identity, _timestamp


def edge_elements(*, supersession: bool = False) -> list:
    table = "decision_supersession_details" if supersession else "decision_dependencies"
    elements = [
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("revision_id", UUID, primary_key=True),
        sa.Column("target_revision_id", UUID, primary_key=True),
        sa.ForeignKeyConstraint(
            ["project_id", "revision_id"],
            ["decision_revisions.project_id", "decision_revisions.id"],
            ondelete="RESTRICT",
            name=f"fk_{table}_source_revision",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "target_revision_id"],
            ["decision_revisions.project_id", "decision_revisions.id"],
            ondelete="RESTRICT",
            name=f"fk_{table}_target_revision",
        ),
        sa.CheckConstraint("revision_id <> target_revision_id", name="no_self_reference"),
    ]
    if supersession:
        elements.extend(
            [
                sa.Column("extent", sa.String(7), nullable=False),
                sa.Column("reason", sa.String(4000), nullable=False),
                sa.CheckConstraint("extent IN ('full','partial')", name="extent"),
                sa.CheckConstraint("length(btrim(reason)) > 0", name="reason_nonblank"),
                sa.Index(
                    "ix_decision_supersession_details_target", "project_id", "target_revision_id"
                ),
            ]
        )
    else:
        elements.extend(
            [
                sa.Column("rationale", sa.String(4000), nullable=False),
                sa.CheckConstraint("length(btrim(rationale)) > 0", name="rationale_nonblank"),
                sa.Index("ix_decision_dependencies_target", "project_id", "target_revision_id"),
            ]
        )
    return elements


def assessment_elements() -> list:
    return [
        *_identity(),
        sa.Column("origin_revision_id", UUID, nullable=False),
        sa.Column("replacement_revision_id", UUID, nullable=False),
        sa.Column("trigger", sa.String(12), nullable=False),
        sa.Column("extent", sa.String(7)),
        sa.Column("reason", sa.String(4000)),
        sa.Column("diff", JSONB, nullable=False),
        sa.Column("candidates", JSONB, nullable=False),
        sa.Column("coverage", JSONB, nullable=False),
        _timestamp(),
        sa.UniqueConstraint("project_id", "id", name="uq_decision_impact_assessments_project"),
        sa.UniqueConstraint(
            "origin_revision_id",
            "replacement_revision_id",
            "trigger",
            name="uq_decision_impact_assessments_episode",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "origin_revision_id"],
            ["decision_revisions.project_id", "decision_revisions.id"],
            ondelete="RESTRICT",
            name="fk_decision_impact_assessments_origin_revision",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "replacement_revision_id"],
            ["decision_revisions.project_id", "decision_revisions.id"],
            ondelete="RESTRICT",
            name="fk_decision_impact_assessments_replacement_revision",
        ),
        sa.CheckConstraint("trigger IN ('revision','supersession')", name="trigger"),
        sa.CheckConstraint("extent IS NULL OR extent IN ('full','partial')", name="extent"),
        sa.CheckConstraint(
            "(extent IS NULL AND reason IS NULL) OR "
            "(extent IS NOT NULL AND reason IS NOT NULL AND length(btrim(reason))>0)",
            name="extent_reason",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(candidates)='array' AND jsonb_array_length(candidates)<=500",
            name="bounded_candidates",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(diff)='object' AND jsonb_typeof(coverage)='object'", name="snapshot_shape"
        ),
        sa.CheckConstraint(
            "octet_length(candidates::text)<=16777216 AND octet_length(diff::text)<=131072",
            name="snapshot_bytes",
        ),
        sa.Index("ix_decision_impact_assessments_project", "project_id", "created_at", "id"),
    ]


def disposition_elements() -> list:
    return [
        *_identity(),
        sa.Column("assessment_id", UUID, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("candidate_reference", sa.String(36), nullable=False),
        sa.Column("disposition", sa.String(15), nullable=False),
        sa.Column("reason", sa.String(4000), nullable=False),
        sa.Column("remediation_work_item_id", UUID),
        sa.Column("actor", JSONB, nullable=False),
        _timestamp(),
        sa.UniqueConstraint(
            "assessment_id", "version", name="uq_decision_impact_dispositions_version"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "assessment_id"],
            ["decision_impact_assessments.project_id", "decision_impact_assessments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "remediation_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.CheckConstraint(
            "disposition IN ('still-valid','change-required','not-affected','resolved')",
            name="disposition",
        ),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="reason_nonblank"),
        sa.CheckConstraint(
            "jsonb_typeof(actor)='object' "
            "AND actor ?& ARRAY['actor_client','actor_session_id'] "
            "AND jsonb_typeof(actor->'actor_client')='string' "
            "AND jsonb_typeof(actor->'actor_session_id')='string' "
            "AND length(btrim(actor->>'actor_client'))>0 "
            "AND length(btrim(actor->>'actor_session_id'))>0",
            name="actor_identity",
        ),
        sa.Index(
            "ix_decision_impact_dispositions_project", "project_id", "assessment_id", "version"
        ),
    ]
