from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    REAL,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="name_nonblank"),
        CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="slug_format"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(String(4000), default="", server_default="")
    repository_url: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkItem(Base):
    """The durable, intentionally small identity and lifecycle for work."""

    __tablename__ = "work_items"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="title_nonblank"),
        CheckConstraint("length(btrim(summary)) > 0", name="summary_nonblank"),
        CheckConstraint("status IN ('open', 'done', 'wont-do', 'promoted')", name="status_valid"),
        CheckConstraint("priority BETWEEN 0 AND 100", name="priority_range"),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint("project_id", "id", name="uq_work_items_project_id_id"),
        ForeignKeyConstraint(
            ["id", "initial_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_items_initial_checkpoint",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "ix_work_items_project_status_updated",
            "project_id",
            "status",
            text("updated_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_work_items_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20), default="open", server_default="open")
    priority: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    initial_checkpoint_id: Mapped[UUID] = mapped_column()
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english'::regconfig, coalesce(summary, '')), 'B')",
            persisted=True,
        ),
    )


class Checkpoint(Base):
    """Immutable, session-attributed context appended to durable work."""

    __tablename__ = "checkpoints"
    __table_args__ = (
        CheckConstraint("kind IN ('context', 'progress', 'completion')", name="kind_valid"),
        CheckConstraint("length(btrim(prompt)) BETWEEN 1 AND 100000", name="prompt_length"),
        CheckConstraint("length(prompt) <= 100000", name="prompt_max_length"),
        CheckConstraint("length(btrim(source_client)) > 0", name="source_client_nonblank"),
        CheckConstraint("length(btrim(source_session_id)) > 0", name="session_id_nonblank"),
        CheckConstraint("cardinality(tags) <= 20", name="tags_count"),
        CheckConstraint("jsonb_typeof(source_metadata) = 'object'", name="metadata_object"),
        CheckConstraint(
            "verified_against IS NULL OR verified_against ~ '^[0-9a-f]{7,64}$'",
            name="commit_format",
        ),
        CheckConstraint(
            "(migration_origin IS NULL AND legacy_record_id IS NULL) OR "
            "(migration_origin IN ('legacy-handoff-snapshot', 'legacy-comment') "
            "AND legacy_record_id IS NOT NULL)",
            name="migration_fields_valid",
        ),
        UniqueConstraint("work_item_id", "id", name="uq_checkpoints_work_item_id_id"),
        UniqueConstraint(
            "migration_origin", "legacy_record_id", name="uq_checkpoints_migration_origin_legacy"
        ),
        Index("ix_checkpoints_work_item_created", "work_item_id", "created_at", "id"),
        Index("ix_checkpoints_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_checkpoints_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    work_item_id: Mapped[UUID] = mapped_column(ForeignKey("work_items.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(20), default="context", server_default="context")
    prompt: Mapped[str] = mapped_column(Text)
    source_client: Mapped[str] = mapped_column(String(80))
    source_session_id: Mapped[str] = mapped_column(String(200))
    source_model: Mapped[str | None] = mapped_column(String(120))
    source_session_url: Mapped[str | None] = mapped_column(String(2000))
    repository_branch: Mapped[str | None] = mapped_column(String(200))
    verified_against: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), default=list, server_default=text("'{}'::varchar[]")
    )
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    migration_origin: Mapped[str | None] = mapped_column(String(40))
    legacy_record_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english'::regconfig, coalesce(prompt, '')), 'C')",
            persisted=True,
        ),
    )


class WorkItemEmbedding(Base):
    """Disposable local-model output; work/checkpoint rows remain canonical."""

    __tablename__ = "work_item_embeddings"
    __table_args__ = (CheckConstraint("cardinality(vector) > 0", name="vector_nonempty"),)

    work_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("work_items.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(300))
    digest: Mapped[str] = mapped_column(String(64))
    vector: Mapped[list[float]] = mapped_column(ARRAY(REAL))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkLease(Base):
    """A retained active-or-expired capability lease for one work item."""

    __tablename__ = "work_leases"
    __table_args__ = (
        CheckConstraint("length(btrim(holder_client)) > 0", name="holder_client_nonblank"),
        CheckConstraint(
            "length(btrim(holder_session_id)) > 0", name="holder_session_id_nonblank"
        ),
        CheckConstraint(
            "length(btrim(claim_request_id)) > 0", name="claim_request_id_nonblank"
        ),
        CheckConstraint("length(btrim(lease_token)) > 0", name="lease_token_nonblank"),
        CheckConstraint(
            "acquired_at <= renewed_at AND renewed_at < expires_at",
            name="timestamp_order",
        ),
        Index("ix_work_leases_expires_at", "expires_at"),
    )

    work_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT"), primary_key=True
    )
    holder_client: Mapped[str] = mapped_column(String(80))
    holder_session_id: Mapped[str] = mapped_column(String(200))
    claim_request_id: Mapped[str] = mapped_column(String(200))
    lease_token: Mapped[str] = mapped_column(String(200))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return (
            "WorkLease("
            f"work_item_id={self.work_item_id!r}, "
            f"holder_client={self.holder_client!r}, "
            f"holder_session_id={self.holder_session_id!r}, "
            f"claim_request_id={self.claim_request_id!r}, "
            f"expires_at={self.expires_at!r})"
        )
