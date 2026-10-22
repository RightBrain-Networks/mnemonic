"""Shared SQL definitions for the knowledge graph's immutable revision ledger."""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


def _identity() -> list:
    return [
        sa.Column("id", UUID, primary_key=True, default=uuid4),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
        ),
    ]


def _timestamp(name: str = "created_at") -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()
    )


def domain_elements() -> list:
    return [
        *_identity(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("prefix", sa.String(5), nullable=False),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("next_ordinal", sa.BigInteger, nullable=False, server_default="1"),
        _timestamp(),
        _timestamp("updated_at"),
        sa.UniqueConstraint("project_id", "id", name="uq_knowledge_domains_project_id"),
        sa.UniqueConstraint("project_id", "prefix", name="uq_knowledge_domains_prefix"),
        sa.Index("uq_knowledge_domains_name", "project_id", sa.text("lower(name)"), unique=True),
        sa.CheckConstraint("length(btrim(name)) > 0", name="name_nonblank"),
        sa.CheckConstraint("prefix ~ '^[A-Z]{1,5}$'", name="prefix_format"),
        sa.CheckConstraint("next_ordinal > 0 AND version > 0", name="positive_counters"),
    ]


def document_elements() -> list:
    return [
        *_identity(),
        sa.Column("domain_id", UUID, nullable=False),
        sa.Column("ordinal", sa.BigInteger, nullable=False),
        sa.Column("current_revision", sa.Integer, nullable=False),
        _timestamp(),
        _timestamp("updated_at"),
        sa.UniqueConstraint("project_id", "id", name="uq_decision_documents_project_id"),
        sa.UniqueConstraint("domain_id", "ordinal", name="uq_decision_documents_ordinal"),
        sa.ForeignKeyConstraint(
            ["project_id", "domain_id"],
            ["knowledge_domains.project_id", "knowledge_domains.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "id", "current_revision"],
            [
                "decision_revisions.project_id",
                "decision_revisions.document_id",
                "decision_revisions.revision",
            ],
            name="fk_decision_documents_current_revision",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        sa.CheckConstraint("ordinal > 0 AND current_revision >= 0", name="valid_numbers"),
        sa.Index("ix_decision_documents_project_updated", "project_id", "updated_at", "id"),
    ]


def revision_elements(*, product_requests: bool = False) -> list:
    return [
        *_identity(),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("media_type", sa.String(20), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("author_kind", sa.String(5), nullable=False),
        sa.Column("agent_session_id", sa.String(200)),
        sa.Column("model_id", sa.String(120)),
        sa.Column(
            "creation_transaction_id",
            sa.BigInteger,
            nullable=False,
            server_default=sa.text("pg_current_xact_id()::text::bigint"),
        ),
        _timestamp(),
        sa.UniqueConstraint("project_id", "id", name="uq_decision_revisions_project_id"),
        sa.UniqueConstraint(
            "project_id", "document_id", "revision", name="uq_decision_revisions_number"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "document_id"],
            ["decision_documents.project_id", "decision_documents.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("revision >= 0", name="revision_nonnegative"),
        sa.CheckConstraint("length(btrim(title)) > 0", name="title_nonblank"),
        sa.CheckConstraint("media_type IN ('text/plain','text/markdown')", name="media_type"),
        sa.CheckConstraint(
            "state IN ('proposed','rejected','accepted','superseded','partially-superseded')",
            name="state",
        ),
        sa.CheckConstraint(
            "size_bytes = octet_length(content) AND size_bytes <= 1048576", name="content_size"
        ),
        sa.CheckConstraint(
            "sha256 = encode(sha256(convert_to(content,'UTF8')),'hex')", name="content_hash"
        ),
        sa.CheckConstraint(
            "(author_kind='human' AND agent_session_id IS NULL AND model_id IS NULL) OR "
            "(author_kind='agent' AND agent_session_id IS NOT NULL "
            "AND length(btrim(agent_session_id)) > 0 AND "
            + ("(model_id IS NULL OR length(btrim(model_id)) > 0))" if product_requests else
               "model_id IS NOT NULL AND length(btrim(model_id)) > 0)"),
            name="author_identity",
        ),
        sa.Index("ix_decision_revisions_project_hash", "project_id", "sha256"),
    ]


def relationship_elements() -> list:
    return [
        sa.Column("revision_id", UUID, primary_key=True),
        sa.Column("target_document_id", UUID, primary_key=True),
        sa.Column("kind", sa.String(10), primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "revision_id"],
            ["decision_revisions.project_id", "decision_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "target_document_id"],
            ["decision_documents.project_id", "decision_documents.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("kind IN ('related','supersedes')", name="kind"),
        sa.Index("ix_decision_relationships_target", "project_id", "target_document_id"),
    ]


def resource_link_elements(kind: str) -> list:
    table = "work_items" if kind == "work_item" else "artifacts"
    field = f"{kind}_id"
    return [
        sa.Column("revision_id", UUID, primary_key=True),
        sa.Column(field, UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "revision_id"],
            ["decision_revisions.project_id", "decision_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", field], [f"{table}.project_id", f"{table}.id"], ondelete="RESTRICT"
        ),
        sa.Index(f"ix_decision_{kind}_links_target", "project_id", field),
    ]


def scope_elements(*, enriched: bool = False) -> list:
    return [
        *_identity(),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("commit_hash", sa.String(64), nullable=False),
        *([sa.Column("repository", sa.String(500)),
           sa.Column("completion_report_id", UUID),
           sa.ForeignKeyConstraint(["project_id", "completion_report_id"],
               ["job_completion_reports.project_id", "job_completion_reports.id"],
               name="fk_decision_scopes_completion_report", ondelete="RESTRICT")]
          if enriched else []),
        sa.Column("work_item_id", UUID),
        sa.Column("agent_session_id", sa.String(200)),
        _timestamp(),
        sa.ForeignKeyConstraint(
            ["project_id", "document_id", "revision"],
            [
                "decision_revisions.project_id",
                "decision_revisions.document_id",
                "decision_revisions.revision",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "work_item_id"],
            ["work_items.project_id", "work_items.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "document_id",
            "revision",
            "commit_hash",
            "work_item_id",
            "agent_session_id",
            *(["repository", "completion_report_id"] if enriched else []),
            name="uq_decision_scopes_provenance",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint("commit_hash ~ '^([a-f0-9]{40}|[a-f0-9]{64})$'", name="commit_hash"),
        sa.Index("ix_decision_scopes_project_commit", "project_id", "commit_hash"),
    ]
