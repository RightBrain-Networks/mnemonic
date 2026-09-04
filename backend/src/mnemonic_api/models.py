from datetime import datetime
from typing import Any, Literal
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

# The work lifecycle vocabulary. WorkItem's status_valid check constraint is the
# database guard for the same five values.
WorkStatus = Literal["pending", "deferred", "done", "wont-do", "promoted"]


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
        Index(
            "ix_client_operations_completion_checkpoint_receipt",
            "project_id",
            text("(response_body #>> '{checkpoint,id}'::text[])"),
            postgresql_where=text("operation_kind = 'complete_work' AND state = 'completed'"),
        ),
        Index(
            "ix_client_operations_completion_receipt_correspondence",
            text("(response_body #>> '{checkpoint,id}'::text[])"),
            text("(response_body #>> '{work_item,id}'::text[])"),
            unique=True,
            postgresql_where=text("operation_kind = 'complete_work' AND state = 'completed'"),
        ),
        CheckConstraint(
            "operation_kind IN ('create_work', 'add_checkpoint', 'append_event', "
            "'add_relationship', 'update_work', 'defer_work', 'complete_work', "
            "'delete_work', 'remove_relationship', 'release_claim', "
            "'request_human_input', 'resolve_human_input', 'merge_work')",
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
        CheckConstraint(
            "completion_generation >= -9223372036854775806",
            name="completion_generation_range",
        ),
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
        Index(
            "ix_work_items_duplicate_title_key_v1",
            "project_id",
            text("mnemonic_duplicate_title_key_v1(title)"),
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_work_items_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(1000))
    status: Mapped[WorkStatus] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    priority: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    initial_checkpoint_id: Mapped[UUID] = mapped_column()
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    completion_generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
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
            "mnemonic_affected_paths_valid_v1(affected_paths)",
            name="affected_paths_valid_v1",
        ),
        CheckConstraint(
            "pg_catalog.cardinality(affected_paths) "
            "OPERATOR(pg_catalog.=) 0 OR verified_against IS NOT NULL",
            name="affected_paths_require_commit",
        ),
        CheckConstraint(
            "(migration_origin IS NULL AND legacy_record_id IS NULL) OR "
            "(migration_origin IN ('legacy-handoff-snapshot', 'legacy-comment') "
            "AND legacy_record_id IS NOT NULL)",
            name="migration_fields_valid",
        ),
        CheckConstraint(
            "(kind = 'completion' AND completion_generation IS NOT NULL) OR "
            "(kind <> 'completion' AND completion_generation IS NULL)",
            name="completion_generation_kind",
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
        Index(
            "uq_checkpoints_completion_generation",
            "work_item_id",
            "completion_generation",
            unique=True,
            postgresql_where=text("kind = 'completion'"),
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
    affected_paths: Mapped[list[str]] = mapped_column(
        ARRAY(String(512)), default=list, server_default=text("'{}'::varchar[]")
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), default=list, server_default=text("'{}'::varchar[]")
    )
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    migration_origin: Mapped[str | None] = mapped_column(String(40))
    legacy_record_id: Mapped[UUID | None] = mapped_column()
    completion_generation: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english'::regconfig, coalesce(prompt, '')), 'C')",
            persisted=True,
        ),
    )


class VerificationResult(Base):
    """One immutable caller-reported observation in a completion episode."""

    __tablename__ = "verification_results"
    __table_args__ = (
        CheckConstraint(
            "verification_type::text = ANY "
            "(ARRAY['command'::text, 'observation'::text])",
            name="verification_type_valid",
        ),
        CheckConstraint(
            "outcome::text = ANY "
            "(ARRAY['passed'::text, 'failed'::text, 'inconclusive'::text, "
            "'skipped'::text])",
            name="outcome_valid",
        ),
        CheckConstraint(
            "(verification_type = 'command' AND outcome = 'passed' "
            "AND command IS NOT NULL AND exit_code = 0) OR "
            "(verification_type = 'command' AND outcome = 'failed' "
            "AND command IS NOT NULL AND exit_code IS NOT NULL AND exit_code <> 0) OR "
            "(verification_type = 'command' AND outcome = 'inconclusive' "
            "AND command IS NOT NULL AND exit_code IS NULL) OR "
            "(verification_type = 'observation' AND command IS NULL AND exit_code IS NULL)",
            name="result_matrix_valid",
        ),
        CheckConstraint(
            "mnemonic_has_non_whitespace(name) AND length(name) <= 200 "
            "AND octet_length(name) <= 800",
            name="name_valid",
        ),
        CheckConstraint(
            "mnemonic_has_non_whitespace(summary) AND length(summary) <= 4000 "
            "AND octet_length(summary) <= 16000",
            name="summary_valid",
        ),
        CheckConstraint(
            "command IS NULL OR (mnemonic_has_non_whitespace(command) "
            "AND length(command) <= 4096 AND octet_length(command) <= 16384)",
            name="command_valid",
        ),
        CheckConstraint(
            "observed_at_commit IS NULL OR observed_at_commit ~ '^[0-9a-f]{7,64}$'",
            name="observed_at_commit_valid",
        ),
        CheckConstraint(
            "observed_at IS NULL OR (isfinite(observed_at) AND observed_at >= "
            "TIMESTAMPTZ '0001-01-01 00:00:00+00' AND observed_at < "
            "TIMESTAMPTZ '10000-01-01 00:00:00+00')",
            name="observed_at_range",
        ),
        CheckConstraint("position BETWEEN 0 AND 19", name="position_range"),
        ForeignKeyConstraint(
            ["project_id", "work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_verification_results_work_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["work_item_id", "completion_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_verification_results_completion_checkpoint",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "work_item_id",
            "completion_checkpoint_id",
            "position",
            name="uq_verification_results_episode_position",
        ),
        UniqueConstraint("work_item_id", "id", name="uq_verification_results_work_item_id_id"),
        Index(
            "ix_verification_results_completion_checkpoint_id_id",
            "completion_checkpoint_id",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[UUID] = mapped_column()
    work_item_id: Mapped[UUID] = mapped_column()
    completion_checkpoint_id: Mapped[UUID] = mapped_column()
    position: Mapped[int] = mapped_column(SmallInteger)
    verification_type: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(String(20))
    summary: Mapped[str] = mapped_column(Text)
    command: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at_commit: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactReference(Base):
    """One immutable caller-reported locator in a completion episode."""

    __tablename__ = "artifact_references"
    __table_args__ = (
        CheckConstraint(
            "artifact_type::text = ANY "
            "(ARRAY['commit'::text, 'pull_request'::text, 'branch'::text, "
            "'test_run'::text, 'repository_path'::text, 'external_issue'::text, "
            "'build_artifact'::text])",
            name="artifact_type_valid",
        ),
        CheckConstraint(
            "mnemonic_has_non_whitespace(label) AND length(label) <= 200 "
            "AND octet_length(label) <= 800",
            name="label_valid",
        ),
        CheckConstraint(
            "mnemonic_completion_artifact_reference_v1_is_valid(artifact_type, reference)",
            name="reference_valid",
        ),
        CheckConstraint("position BETWEEN 0 AND 19", name="position_range"),
        ForeignKeyConstraint(
            ["project_id", "work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_artifact_references_work_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["work_item_id", "completion_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_artifact_references_completion_checkpoint",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "work_item_id",
            "completion_checkpoint_id",
            "position",
            name="uq_artifact_references_episode_position",
        ),
        UniqueConstraint("work_item_id", "id", name="uq_artifact_references_work_item_id_id"),
        UniqueConstraint(
            "work_item_id",
            "completion_checkpoint_id",
            "artifact_type",
            "reference",
            name="uq_artifact_references_episode_reference",
        ),
        Index(
            "ix_artifact_references_completion_checkpoint_id_id",
            "completion_checkpoint_id",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[UUID] = mapped_column()
    work_item_id: Mapped[UUID] = mapped_column()
    completion_checkpoint_id: Mapped[UUID] = mapped_column()
    position: Mapped[int] = mapped_column(SmallInteger)
    artifact_type: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(200))
    reference: Mapped[str] = mapped_column(Text(collation="C"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
        UniqueConstraint(
            "project_id",
            "id",
            "relationship_type",
            "source_work_item_id",
            "target_work_item_id",
            name="uq_work_relationships_merge_identity",
        ),
        UniqueConstraint(
            "project_id",
            "created_for_duplicate_merge_id",
            name="uq_work_relationships_merge_witness",
        ),
        ForeignKeyConstraint(
            ["project_id", "created_for_duplicate_merge_id"],
            ["work_duplicate_merges.project_id", "work_duplicate_merges.id"],
            name="fk_work_relationships_duplicate_merge",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
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
    created_for_duplicate_merge_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class WorkDuplicateMerge(Base):
    """An immutable selection of one duplicate alias and its direct destination."""

    __tablename__ = "work_duplicate_merges"
    __table_args__ = (
        CheckConstraint(
            "duplicate_relationship_type = 'duplicate-of'",
            name="relationship_type_valid",
        ),
        CheckConstraint(
            "source_work_item_id <> destination_work_item_id",
            name="endpoints_differ",
        ),
        CheckConstraint(
            "reviewed_source_work_version > 0 "
            "AND reviewed_destination_work_version > 0 "
            "AND reviewed_source_work_event_count > 0 "
            "AND reviewed_destination_work_event_count > 0",
            name="review_revision_positive",
        ),
        CheckConstraint(
            "resulting_source_work_version = reviewed_source_work_version + 1 "
            "AND resulting_destination_work_version = "
            "reviewed_destination_work_version + 1",
            name="result_versions_valid",
        ),
        CheckConstraint(
            "mnemonic_has_non_whitespace(rationale)",
            name="rationale_nonblank",
        ),
        CheckConstraint(
            "mnemonic_has_non_whitespace(merged_by_client)",
            name="merged_by_client_nonblank",
        ),
        CheckConstraint(
            "mnemonic_has_non_whitespace(merged_by_session_id)",
            name="merged_by_session_id_nonblank",
        ),
        CheckConstraint(
            "merged_by_model IS NULL OR mnemonic_has_non_whitespace(merged_by_model)",
            name="merged_by_model_nonblank",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_duplicate_merges_source_work_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "destination_work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_duplicate_merges_destination_work_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_work_item_id", "reviewed_source_context_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_duplicate_merges_source_context_checkpoint",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["destination_work_item_id", "reviewed_destination_context_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_duplicate_merges_destination_context_checkpoint",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "project_id",
                "duplicate_relationship_id",
                "duplicate_relationship_type",
                "source_work_item_id",
                "destination_work_item_id",
            ],
            [
                "work_relationships.project_id",
                "work_relationships.id",
                "work_relationships.relationship_type",
                "work_relationships.source_work_item_id",
                "work_relationships.target_work_item_id",
            ],
            name="fk_work_duplicate_merges_relationship",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("merge_sequence", name="uq_work_duplicate_merges_merge_sequence"),
        UniqueConstraint(
            "project_id",
            "source_work_item_id",
            name="uq_work_duplicate_merges_source",
        ),
        UniqueConstraint(
            "duplicate_relationship_id",
            name="uq_work_duplicate_merges_relationship",
        ),
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_work_duplicate_merges_project_id_id",
        ),
        Index(
            "ix_work_duplicate_merges_destination",
            "project_id",
            "destination_work_item_id",
            "merge_sequence",
            "id",
        ),
        Index(
            "ix_work_duplicate_merges_audit",
            "project_id",
            "merge_sequence",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    merge_sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    source_work_item_id: Mapped[UUID] = mapped_column()
    destination_work_item_id: Mapped[UUID] = mapped_column()
    duplicate_relationship_id: Mapped[UUID] = mapped_column()
    duplicate_relationship_type: Mapped[str] = mapped_column(String(32))
    reviewed_source_work_version: Mapped[int] = mapped_column(Integer)
    reviewed_source_context_checkpoint_id: Mapped[UUID] = mapped_column()
    reviewed_source_work_event_count: Mapped[int] = mapped_column(BigInteger)
    reviewed_destination_work_version: Mapped[int] = mapped_column(Integer)
    reviewed_destination_context_checkpoint_id: Mapped[UUID] = mapped_column()
    reviewed_destination_work_event_count: Mapped[int] = mapped_column(BigInteger)
    resulting_source_work_version: Mapped[int] = mapped_column(Integer)
    resulting_destination_work_version: Mapped[int] = mapped_column(Integer)
    rationale: Mapped[str] = mapped_column(String(4000))
    merged_by_client: Mapped[str] = mapped_column(String(80))
    merged_by_session_id: Mapped[str] = mapped_column(String(200))
    merged_by_model: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkGate(Base):
    """An immutable human question with one optional immutable resolution."""

    __tablename__ = "work_gates"
    __table_args__ = (
        CheckConstraint("attention_sequence > 0", name="attention_sequence_positive"),
        CheckConstraint("gate_type = 'human'", name="gate_type_valid"),
        CheckConstraint(
            "mnemonic_has_non_whitespace(question) AND length(question) <= 4000",
            name="question_valid",
        ),
        CheckConstraint(
            "mnemonic_has_non_whitespace(requested_by_client) "
            "AND mnemonic_has_non_whitespace(requested_by_session_id) "
            "AND (requested_by_model IS NULL "
            "OR mnemonic_has_non_whitespace(requested_by_model))",
            name="requester_valid",
        ),
        CheckConstraint(
            "requested_work_version > 0 AND requested_relationship_event_count >= 0",
            name="requested_revision_valid",
        ),
        CheckConstraint(
            "(resolved_at IS NULL AND resolution IS NULL "
            "AND resolved_by_client IS NULL AND resolved_by_session_id IS NULL "
            "AND resolved_by_model IS NULL AND resolved_work_version IS NULL "
            "AND resolved_context_checkpoint_id IS NULL "
            "AND resolved_relationship_event_count IS NULL) OR "
            "(resolved_at IS NOT NULL AND resolution IS NOT NULL "
            "AND resolved_by_client IS NOT NULL "
            "AND resolved_by_session_id IS NOT NULL "
            "AND resolved_work_version IS NOT NULL "
            "AND resolved_context_checkpoint_id IS NOT NULL "
            "AND resolved_relationship_event_count IS NOT NULL)",
            name="resolution_state_valid",
        ),
        CheckConstraint(
            "resolution IS NULL OR "
            "(mnemonic_has_non_whitespace(resolution) AND length(resolution) <= 4000)",
            name="resolution_valid",
        ),
        CheckConstraint(
            "resolved_by_client IS NULL OR mnemonic_has_non_whitespace(resolved_by_client)",
            name="resolver_client_valid",
        ),
        CheckConstraint(
            "resolved_by_session_id IS NULL OR mnemonic_has_non_whitespace(resolved_by_session_id)",
            name="resolver_session_valid",
        ),
        CheckConstraint(
            "resolved_by_model IS NULL OR mnemonic_has_non_whitespace(resolved_by_model)",
            name="resolver_model_valid",
        ),
        CheckConstraint(
            "resolved_work_version IS NULL OR resolved_work_version > 0",
            name="resolved_work_version_positive",
        ),
        CheckConstraint(
            "resolved_relationship_event_count IS NULL OR resolved_relationship_event_count >= 0",
            name="resolved_relationship_event_count_nonnegative",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= created_at",
            name="timestamp_order",
        ),
        ForeignKeyConstraint(
            ["project_id", "work_item_id"],
            ["work_items.project_id", "work_items.id"],
            name="fk_work_gates_work_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["work_item_id", "requested_context_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_gates_requested_context_checkpoint",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["work_item_id", "resolved_context_checkpoint_id"],
            ["checkpoints.work_item_id", "checkpoints.id"],
            name="fk_work_gates_resolved_context_checkpoint",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("attention_sequence", name="uq_work_gates_attention_sequence"),
        UniqueConstraint(
            "work_item_id",
            "id",
            name="uq_work_gates_work_item_id_id",
        ),
        Index(
            "ix_work_gates_project_unresolved",
            "project_id",
            "attention_sequence",
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index(
            "ix_work_gates_work_unresolved",
            "work_item_id",
            "attention_sequence",
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index(
            "ix_work_gates_work_timeline",
            "work_item_id",
            text("attention_sequence DESC"),
        ),
        Index(
            "ix_work_gates_work_resolved_recent",
            "work_item_id",
            text("resolved_at DESC"),
            text("id DESC"),
            postgresql_where=text("resolved_at IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    attention_sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    project_id: Mapped[UUID] = mapped_column()
    work_item_id: Mapped[UUID] = mapped_column()
    gate_type: Mapped[str] = mapped_column(
        String(16),
        default="human",
        server_default="human",
    )
    question: Mapped[str] = mapped_column(Text)
    requested_by_client: Mapped[str] = mapped_column(String(80))
    requested_by_session_id: Mapped[str] = mapped_column(String(200))
    requested_by_model: Mapped[str | None] = mapped_column(String(120))
    requested_work_version: Mapped[int] = mapped_column(Integer)
    requested_context_checkpoint_id: Mapped[UUID] = mapped_column()
    requested_relationship_event_count: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.clock_timestamp(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by_client: Mapped[str | None] = mapped_column(String(80))
    resolved_by_session_id: Mapped[str | None] = mapped_column(String(200))
    resolved_by_model: Mapped[str | None] = mapped_column(String(120))
    resolved_work_version: Mapped[int | None] = mapped_column(Integer)
    resolved_context_checkpoint_id: Mapped[UUID | None] = mapped_column()
    resolved_relationship_event_count: Mapped[int | None] = mapped_column(BigInteger)


class WorkEvent(Base):
    """An immutable, actor-attributed fact in one work item's history."""

    __tablename__ = "work_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('work_created', 'work_updated', 'work_status_changed', "
            "'work_reopened', 'work_claimed', 'work_released', 'checkpoint_added', "
            "'progress', 'dependency_added', 'dependency_removed', "
            "'relationship_added', 'relationship_removed', 'work_completed', "
            "'work_deleted', 'work_merged', 'human_attention_requested', "
            "'human_attention_resolved')",
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
            "'work_claimed', 'dependency_added', 'relationship_added', 'progress', "
            "'work_merged', 'human_attention_requested', 'human_attention_resolved') "
            "OR actor_kind = 'client')) OR "
            "(origin = 'backfill' AND (event_type <> 'work_deleted' "
            "OR actor_kind = 'unattributed'))",
            name="actor_matrix_valid",
        ),
        CheckConstraint(
            "(event_type IN ('progress', 'work_merged', 'human_attention_requested', "
            "'human_attention_resolved') AND body IS NOT NULL "
            "AND length(body) <= 4000 AND mnemonic_has_non_whitespace(body)) OR "
            "(event_type NOT IN ('progress', 'work_merged', 'human_attention_requested', "
            "'human_attention_resolved') AND body IS NULL)",
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
        CheckConstraint(
            "(event_type IN ('human_attention_requested', 'human_attention_resolved') "
            "AND gate_id IS NOT NULL) OR "
            "(event_type NOT IN ('human_attention_requested', "
            "'human_attention_resolved') AND gate_id IS NULL)",
            name="gate_reference_valid",
        ),
        CheckConstraint(
            "event_type IN ('human_attention_requested', 'human_attention_resolved') "
            "OR NOT (metadata ? 'gate_id' OR metadata ? 'gate_type')",
            name="gate_metadata_reserved",
        ),
        CheckConstraint(
            "(created_for_duplicate_merge_id IS NULL "
            "OR event_type = 'relationship_added') AND "
            "((event_type = 'work_merged' AND work_duplicate_merge_id IS NOT NULL) OR "
            "(event_type <> 'work_merged' AND work_duplicate_merge_id IS NULL))",
            name="duplicate_merge_references_valid",
        ),
        CheckConstraint("metadata_version = 1", name="metadata_version_valid"),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object' AND octet_length(metadata::text) <= 16384",
            name="metadata_envelope_valid",
        ),
        CheckConstraint(
            "(event_type IN ('human_attention_requested', 'human_attention_resolved') "
            "AND metadata_version = 1 "
            "AND metadata = jsonb_build_object('gate_id', gate_id::text, "
            "'gate_type', 'human')) OR "
            "(event_type = 'work_merged' AND "
            "mnemonic_work_merged_metadata_v1_is_valid(work_item_id, "
            "work_duplicate_merge_id, metadata_version, metadata)) OR "
            "(event_type NOT IN ('human_attention_requested', 'human_attention_resolved', "
            "'work_merged') AND mnemonic_work_event_metadata_v2_is_valid("
            "event_type, origin, work_item_id, checkpoint_id, lease_generation_id, "
            "lease_release_id, relationship_id, relationship_source_work_item_id, "
            "relationship_target_work_item_id, "
            "relationship_context_checkpoint_work_item_id, "
            "relationship_context_checkpoint_id, metadata_version, metadata))",
            name="metadata_v1_valid",
        ),
        CheckConstraint(
            "event_type <> 'progress' OR mnemonic_phase6_progress_metadata_is_valid(metadata)",
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
        CheckConstraint(
            "(event_type = 'work_reopened' AND reopen_generation IS NOT NULL "
            "AND reopen_generation <> 0) OR "
            "(event_type <> 'work_reopened' AND reopen_generation IS NULL)",
            name="reopen_generation_kind",
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
        ForeignKeyConstraint(
            ["work_item_id", "gate_id"],
            ["work_gates.work_item_id", "work_gates.id"],
            name="fk_work_events_gate",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "created_for_duplicate_merge_id"],
            ["work_duplicate_merges.project_id", "work_duplicate_merges.id"],
            name="fk_work_events_created_for_duplicate_merge",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["project_id", "work_duplicate_merge_id"],
            ["work_duplicate_merges.project_id", "work_duplicate_merges.id"],
            name="fk_work_events_duplicate_merge",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
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
            "uq_work_events_gate_fact",
            "work_item_id",
            "gate_id",
            "event_type",
            unique=True,
            postgresql_where=text("gate_id IS NOT NULL"),
        ),
        Index(
            "uq_work_events_relationship_merge_witness",
            "project_id",
            "created_for_duplicate_merge_id",
            "work_item_id",
            unique=True,
            postgresql_where=text("created_for_duplicate_merge_id IS NOT NULL"),
        ),
        Index(
            "uq_work_events_merge_endpoint",
            "project_id",
            "work_duplicate_merge_id",
            "work_item_id",
            unique=True,
            postgresql_where=text("work_duplicate_merge_id IS NOT NULL"),
        ),
        Index(
            "uq_work_events_merge_role",
            "project_id",
            "work_duplicate_merge_id",
            text("(metadata ->> 'role')"),
            unique=True,
            postgresql_where=text("work_duplicate_merge_id IS NOT NULL"),
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
        Index(
            "uq_work_events_reopen_generation",
            "work_item_id",
            "reopen_generation",
            unique=True,
            postgresql_where=text("event_type = 'work_reopened'"),
        ),
        Index(
            "ix_work_events_completion_evidence_history",
            "project_id",
            "work_item_id",
            text("id DESC"),
            postgresql_where=text("event_type = 'work_completed'"),
        ),
        Index(
            "ix_work_events_live_completion_version_order",
            "work_item_id",
            text("id DESC"),
            postgresql_where=text("event_type = 'work_completed' AND origin = 'live'"),
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
    gate_id: Mapped[UUID | None] = mapped_column()
    created_for_duplicate_merge_id: Mapped[UUID | None] = mapped_column()
    work_duplicate_merge_id: Mapped[UUID | None] = mapped_column()
    reopen_generation: Mapped[int | None] = mapped_column(BigInteger)
    metadata_version: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    origin: Mapped[str] = mapped_column(String(16), default="live", server_default="live")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
