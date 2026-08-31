from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    REAL,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
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


class Handoff(Base):
    __tablename__ = "handoffs"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="title_nonblank"),
        CheckConstraint("length(btrim(summary)) > 0", name="summary_nonblank"),
        CheckConstraint("length(btrim(prompt)) BETWEEN 1 AND 100000", name="prompt_length"),
        CheckConstraint("length(prompt) <= 100000", name="prompt_max_length"),
        CheckConstraint("length(btrim(source_client)) > 0", name="source_client_nonblank"),
        CheckConstraint("length(btrim(source_session_id)) > 0", name="session_id_nonblank"),
        CheckConstraint("status IN ('open', 'done', 'wont-do', 'promoted')", name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("cardinality(tags) <= 20", name="tags_count"),
        CheckConstraint("jsonb_typeof(source_metadata) = 'object'", name="metadata_object"),
        CheckConstraint(
            "verified_against IS NULL OR verified_against ~ '^[0-9a-f]{7,64}$'",
            name="commit_format",
        ),
        Index(
            "ix_handoffs_project_status_updated",
            "project_id",
            "status",
            "updated_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_handoffs_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_handoffs_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(1000))
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
    status: Mapped[str] = mapped_column(String(20), default="open", server_default="open")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english'::regconfig, coalesce(summary, '')), 'B') || "
            "setweight(to_tsvector('english'::regconfig, coalesce(prompt, '')), 'C')",
            persisted=True,
        ),
    )


class HandoffComment(Base):
    """Append-only progress recorded against a hand-off."""

    __tablename__ = "handoff_comments"
    __table_args__ = (
        CheckConstraint("length(btrim(body)) BETWEEN 1 AND 50000", name="body_length"),
        CheckConstraint("length(body) <= 50000", name="body_max_length"),
        CheckConstraint("kind IN ('comment', 'work-summary')", name="kind_valid"),
        CheckConstraint("length(btrim(source_client)) > 0", name="source_client_nonblank"),
        CheckConstraint("length(btrim(source_session_id)) > 0", name="session_id_nonblank"),
        Index("ix_handoff_comments_handoff_created", "handoff_id", "created_at", "id"),
        Index("ix_handoff_comments_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    handoff_id: Mapped[UUID] = mapped_column(ForeignKey("handoffs.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="comment", server_default="comment")
    source_client: Mapped[str] = mapped_column(String(80))
    source_session_id: Mapped[str] = mapped_column(String(200))
    source_model: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english'::regconfig, coalesce(body, '')), 'B')",
            persisted=True,
        ),
    )


class HandoffEmbedding(Base):
    """Disposable local-model output; hand-off text remains the canonical source."""

    __tablename__ = "handoff_embeddings"
    __table_args__ = (CheckConstraint("cardinality(vector) > 0", name="vector_nonempty"),)

    handoff_id: Mapped[UUID] = mapped_column(
        ForeignKey("handoffs.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(300))
    digest: Mapped[str] = mapped_column(String(64))
    vector: Mapped[list[float]] = mapped_column(ARRAY(REAL))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
