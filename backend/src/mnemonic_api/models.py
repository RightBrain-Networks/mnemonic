from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
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


class ProjectSettings(Base):
    """Optional project-local dashboard behavior overrides."""

    __tablename__ = "project_settings"
    __table_args__ = (
        CheckConstraint(
            "mnemonic_has_non_whitespace(recall_pointer_template)",
            name="recall_pointer_template_nonblank",
        ),
        CheckConstraint(
            "length(recall_pointer_template) <= 100000",
            name="recall_pointer_template_max_length",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    recall_pointer_template: Mapped[str] = mapped_column(Text)


class ClientOperation(Base):
    """Private durable receipt for one project-scoped client mutation."""

    __tablename__ = "client_operations"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "client_operation_id",
            name="uq_client_operations_scope",
        ),
        CheckConstraint(
            "operation_kind IN ('create_work', 'add_checkpoint', 'append_event', "
            "'add_relationship', 'update_work', 'defer_work', 'complete_work', "
            "'delete_work', 'remove_relationship', 'release_claim')",
            name="operation_kind_valid",
        ),
        CheckConstraint(
            "request_fingerprint_version = 1",
            name="request_fingerprint_version_valid",
        ),
        CheckConstraint(
            "octet_length(request_fingerprint_salt) = 32",
            name="request_fingerprint_salt_length",
        ),
        CheckConstraint(
            "octet_length(request_fingerprint) = 32",
            name="request_fingerprint_length",
        ),
        CheckConstraint(
            "response_contract_version = 1",
            name="response_contract_version_valid",
        ),
        CheckConstraint("state IN ('pending', 'completed')", name="state_valid"),
        CheckConstraint(
            "(state = 'pending' AND response_status IS NULL AND response_body IS NULL "
            "AND mutation_applied IS NULL AND completed_at IS NULL) OR "
            "(state = 'completed' AND response_status BETWEEN 200 AND 299 "
            "AND response_body IS NOT NULL AND jsonb_typeof(response_body) = 'object' "
            "AND octet_length(response_body::text) <= 1048576 "
            "AND mutation_applied IS NOT NULL AND completed_at IS NOT NULL)",
            name="state_fields_valid",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="timestamp_order",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column()
    client_operation_id: Mapped[UUID] = mapped_column()
    operation_kind: Mapped[str] = mapped_column(String(40))
    request_fingerprint_version: Mapped[int] = mapped_column(
        SmallInteger, default=1, server_default="1"
    )
    request_fingerprint_salt: Mapped[bytes] = mapped_column(LargeBinary)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary)
    response_contract_version: Mapped[int] = mapped_column(
        SmallInteger, default=1, server_default="1"
    )
    state: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    response_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    mutation_applied: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkItem(Base):
    """The durable, intentionally small identity and lifecycle for work."""

    __tablename__ = "work_items"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="title_nonblank"),
        CheckConstraint("length(btrim(summary)) > 0", name="summary_nonblank"),
        CheckConstraint(
            "status IN ('pending', 'deferred', 'done', 'wont-do', 'promoted')",
            name="status_valid",
        ),
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
        Index(
            "ix_work_items_ready_order",
            "project_id",
            text("priority DESC"),
            text("created_at ASC"),
            text("id ASC"),
            postgresql_where=text("deleted_at IS NULL AND status = 'pending'"),
        ),
        Index("ix_work_items_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
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
        Index(
            "ix_checkpoints_normalized_tags_gin",
            text("mnemonic_normalized_tags(tags)"),
            postgresql_using="gin",
        ),
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
        CheckConstraint("length(btrim(holder_session_id)) > 0", name="holder_session_id_nonblank"),
        CheckConstraint("length(btrim(claim_request_id)) > 0", name="claim_request_id_nonblank"),
        CheckConstraint("length(btrim(lease_token)) > 0", name="lease_token_nonblank"),
        CheckConstraint(
            "acquired_at <= renewed_at AND renewed_at < expires_at",
            name="timestamp_order",
        ),
        UniqueConstraint("lease_generation_id", name="uq_work_leases_lease_generation_id"),
        Index(
            "uq_work_leases_pending_release_id",
            "pending_release_id",
            unique=True,
            postgresql_where=text("pending_release_id IS NOT NULL"),
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
    lease_generation_id: Mapped[UUID] = mapped_column(
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    pending_release_id: Mapped[UUID | None] = mapped_column()
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


class WorkRelationship(Base):
    """An immutable, project-local structural fact between two work items."""

    __tablename__ = "work_relationships"
    __table_args__ = (
        CheckConstraint(
            "relationship_type IN "
            "('blocks', 'parent-child', 'discovered-from', 'duplicate-of', 'related')",
            name="type_valid",
        ),
        CheckConstraint("source_work_item_id <> target_work_item_id", name="endpoints_differ"),
        CheckConstraint(
            "(context_checkpoint_work_item_id IS NULL AND context_checkpoint_id IS NULL) OR "
            "(context_checkpoint_work_item_id IS NOT NULL AND context_checkpoint_id IS NOT NULL)",
            name="context_pair",
        ),
        CheckConstraint(
            "context_checkpoint_work_item_id IS NULL OR "
            "context_checkpoint_work_item_id IN (source_work_item_id, target_work_item_id)",
            name="context_endpoint",
        ),
        CheckConstraint(
            "relationship_type <> 'discovered-from' OR "
            "(context_checkpoint_id IS NOT NULL AND "
            "context_checkpoint_work_item_id = target_work_item_id)",
            name="discovery_context",
        ),
        CheckConstraint(
            "relationship_type <> 'related' OR source_work_item_id < target_work_item_id",
            name="related_normalized",
        ),
        CheckConstraint("length(btrim(created_by_client)) > 0", name="created_by_client_nonblank"),
        CheckConstraint(
            "length(btrim(created_by_session_id)) > 0",
            name="created_by_session_id_nonblank",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_relationships_source_work_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "target_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_relationships_target_work_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["context_checkpoint_work_item_id", "context_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_relationships_context_checkpoint",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id",
            "relationship_type",
            "source_work_item_id",
            "target_work_item_id",
            name="uq_work_relationships_identity",
        ),
        Index(
            "uq_work_relationships_one_parent",
            "target_work_item_id",
            unique=True,
            postgresql_where=text("relationship_type = 'parent-child'"),
        ),
        Index(
            "ix_work_relationships_source",
            "project_id",
            "source_work_item_id",
            "relationship_type",
        ),
        Index(
            "ix_work_relationships_target",
            "project_id",
            "target_work_item_id",
            "relationship_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column()
    relationship_type: Mapped[str] = mapped_column(String(32))
    source_work_item_id: Mapped[UUID] = mapped_column()
    target_work_item_id: Mapped[UUID] = mapped_column()
    context_checkpoint_work_item_id: Mapped[UUID | None] = mapped_column()
    context_checkpoint_id: Mapped[UUID | None] = mapped_column()
    created_by_client: Mapped[str] = mapped_column(String(80))
    created_by_session_id: Mapped[str] = mapped_column(String(200))
    created_by_model: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkEvent(Base):
    """An immutable, actor-attributed fact in one work item's history."""

    __tablename__ = "work_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('work_created', 'work_updated', 'work_status_changed', "
            "'work_reopened', 'work_claimed', 'work_released', 'checkpoint_added', "
            "'progress', 'dependency_added', 'dependency_removed', "
            "'relationship_added', 'relationship_removed', 'work_completed', "
            "'work_deleted')",
            name="event_type_valid",
        ),
        CheckConstraint(
            "actor_kind IN ('client', 'unattributed')",
            name="actor_kind_valid",
        ),
        CheckConstraint(
            "(actor_kind = 'client' AND actor_client IS NOT NULL "
            "AND mnemonic_has_non_whitespace(actor_client) "
            "AND actor_session_id IS NOT NULL "
            "AND mnemonic_has_non_whitespace(actor_session_id) "
            "AND (actor_model IS NULL OR mnemonic_has_non_whitespace(actor_model))) OR "
            "(actor_kind = 'unattributed' AND actor_client IS NULL "
            "AND actor_session_id IS NULL AND actor_model IS NULL)",
            name="actor_fields_valid",
        ),
        CheckConstraint(
            "(origin = 'live' AND ("
            "event_type NOT IN ('work_created', 'checkpoint_added', 'work_completed', "
            "'work_claimed', 'dependency_added', 'relationship_added', 'progress') "
            "OR actor_kind = 'client')) OR "
            "(origin = 'backfill' AND (event_type <> 'work_deleted' "
            "OR actor_kind = 'unattributed'))",
            name="actor_matrix_valid",
        ),
        CheckConstraint(
            "(event_type = 'progress' AND body IS NOT NULL "
            "AND length(body) <= 4000 AND mnemonic_has_non_whitespace(body)) OR "
            "(event_type <> 'progress' AND body IS NULL)",
            name="body_valid",
        ),
        CheckConstraint(
            "(event_type IN ('work_created', 'checkpoint_added', 'work_completed') "
            "AND checkpoint_id IS NOT NULL) OR "
            "(event_type NOT IN ('work_created', 'checkpoint_added', 'work_completed') "
            "AND checkpoint_id IS NULL)",
            name="checkpoint_reference_valid",
        ),
        CheckConstraint(
            "(event_type IN ('work_claimed', 'work_released') "
            "AND lease_generation_id IS NOT NULL) OR "
            "(event_type NOT IN ('work_claimed', 'work_released') "
            "AND lease_generation_id IS NULL)",
            name="lease_generation_reference_valid",
        ),
        CheckConstraint(
            "(event_type = 'work_released' AND lease_release_id IS NOT NULL) OR "
            "(event_type <> 'work_released' AND lease_release_id IS NULL)",
            name="lease_release_reference_valid",
        ),
        CheckConstraint(
            "(event_type IN ('dependency_added', 'dependency_removed', "
            "'relationship_added', 'relationship_removed') "
            "AND relationship_id IS NOT NULL "
            "AND relationship_source_work_item_id IS NOT NULL "
            "AND relationship_target_work_item_id IS NOT NULL "
            "AND ((relationship_context_checkpoint_work_item_id IS NULL "
            "AND relationship_context_checkpoint_id IS NULL) OR "
            "(relationship_context_checkpoint_work_item_id IS NOT NULL "
            "AND relationship_context_checkpoint_id IS NOT NULL)) "
            "AND (relationship_context_checkpoint_work_item_id IS NULL OR "
            "relationship_context_checkpoint_work_item_id IN "
            "(relationship_source_work_item_id, relationship_target_work_item_id)) "
            "AND work_item_id IN "
            "(relationship_source_work_item_id, relationship_target_work_item_id)) OR "
            "(event_type NOT IN ('dependency_added', 'dependency_removed', "
            "'relationship_added', 'relationship_removed') "
            "AND relationship_id IS NULL "
            "AND relationship_source_work_item_id IS NULL "
            "AND relationship_target_work_item_id IS NULL "
            "AND relationship_context_checkpoint_work_item_id IS NULL "
            "AND relationship_context_checkpoint_id IS NULL)",
            name="relationship_references_valid",
        ),
        CheckConstraint("metadata_version = 1", name="metadata_version_valid"),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object' AND octet_length(metadata::text) <= 16384",
            name="metadata_envelope_valid",
        ),
        CheckConstraint(
            "mnemonic_work_event_metadata_v1_is_valid("
            "event_type, origin, work_item_id, checkpoint_id, lease_generation_id, "
            "lease_release_id, relationship_id, relationship_source_work_item_id, "
            "relationship_target_work_item_id, "
            "relationship_context_checkpoint_work_item_id, "
            "relationship_context_checkpoint_id, metadata_version, metadata)",
            name="metadata_v1_valid",
        ),
        CheckConstraint(
            "event_type <> 'progress' OR "
            "mnemonic_phase6_progress_metadata_is_valid(metadata)",
            name="client_operation_id_reserved",
        ),
        CheckConstraint(
            "origin IN ('live', 'backfill')",
            name="origin_valid",
        ),
        CheckConstraint(
            "origin = 'live' OR event_type IN ('work_created', 'checkpoint_added', "
            "'work_completed', 'work_claimed', 'dependency_added', "
            "'relationship_added', 'work_deleted')",
            name="backfill_event_type_valid",
        ),
        ForeignKeyConstraint(
            ["project_id", "work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_events_work_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["work_item_id", "checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_events_checkpoint",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "relationship_source_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_events_relationship_source_work_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "relationship_target_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_events_relationship_target_work_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "relationship_context_checkpoint_work_item_id",
                "relationship_context_checkpoint_id",
            ],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_events_relationship_context_checkpoint",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_work_events_checkpoint_fact",
            "work_item_id",
            "checkpoint_id",
            unique=True,
            postgresql_where=text("checkpoint_id IS NOT NULL"),
        ),
        Index(
            "uq_work_events_work_created",
            "work_item_id",
            unique=True,
            postgresql_where=text("event_type = 'work_created'"),
        ),
        Index(
            "uq_work_events_work_deleted",
            "work_item_id",
            unique=True,
            postgresql_where=text("event_type = 'work_deleted'"),
        ),
        Index(
            "uq_work_events_work_claimed_fact",
            "work_item_id",
            "lease_generation_id",
            unique=True,
            postgresql_where=text("event_type = 'work_claimed'"),
        ),
        Index(
            "uq_work_events_work_released_fact",
            "work_item_id",
            "lease_generation_id",
            unique=True,
            postgresql_where=text("event_type = 'work_released'"),
        ),
        Index(
            "uq_work_events_lease_release_id",
            "lease_release_id",
            unique=True,
            postgresql_where=text("event_type = 'work_released'"),
        ),
        Index(
            "uq_work_events_relationship_added_fact",
            "work_item_id",
            "relationship_id",
            unique=True,
            postgresql_where=text("event_type IN ('dependency_added', 'relationship_added')"),
        ),
        Index(
            "uq_work_events_relationship_removed_fact",
            "work_item_id",
            "relationship_id",
            unique=True,
            postgresql_where=text("event_type IN ('dependency_removed', 'relationship_removed')"),
        ),
        Index(
            "ix_work_events_timeline",
            "project_id",
            "work_item_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_work_events_timeline_type",
            "project_id",
            "work_item_id",
            "event_type",
            text("created_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column()
    work_item_id: Mapped[UUID] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(32))
    actor_kind: Mapped[str] = mapped_column(String(20))
    actor_client: Mapped[str | None] = mapped_column(String(80))
    actor_session_id: Mapped[str | None] = mapped_column(String(200))
    actor_model: Mapped[str | None] = mapped_column(String(120))
    body: Mapped[str | None] = mapped_column(Text)
    checkpoint_id: Mapped[UUID | None] = mapped_column()
    lease_generation_id: Mapped[UUID | None] = mapped_column()
    lease_release_id: Mapped[UUID | None] = mapped_column()
    relationship_id: Mapped[UUID | None] = mapped_column()
    relationship_source_work_item_id: Mapped[UUID | None] = mapped_column()
    relationship_target_work_item_id: Mapped[UUID | None] = mapped_column()
    relationship_context_checkpoint_work_item_id: Mapped[UUID | None] = mapped_column()
    relationship_context_checkpoint_id: Mapped[UUID | None] = mapped_column()
    metadata_version: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    origin: Mapped[str] = mapped_column(String(16), default="live", server_default="live")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
