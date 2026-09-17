"""Bounded, resumable passage batches use the existing PostgreSQL/RabbitMQ outbox."""

from dataclasses import dataclass
from math import hypot, isfinite
from uuid import UUID, uuid4

from sqlalchemy import String, cast, delete, exists, func, insert, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api import artifact_passages as passages
from mnemonic_api.artifact_extraction import _pending_operation
from mnemonic_api.artifact_tokenizer import PassageTokenizer, passage_tokenizer
from mnemonic_api.models import Artifact, ArtifactExtraction, BackgroundJob
from mnemonic_api.semantic import Embedder, _valid_vector
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_jobs.ledger import JobContext, RetryJob, enqueue_job


def current_source():
    return (
        Artifact.id == ArtifactExtraction.artifact_id,
        Artifact.revision == ArtifactExtraction.revision,
        Artifact.deleted_at.is_(None), ~_pending_operation(Artifact.id),
        ArtifactExtraction.status == "ready", ArtifactExtraction.text_sha256.is_not(None),
    )


def current_index(chunk_config: str | None = None):
    index = passages.INDEXES.c
    return (*current_source(), index.artifact_id == Artifact.id,
            index.revision == Artifact.revision,
            index.text_sha256 == ArtifactExtraction.text_sha256,
            index.model == passages.PASSAGE_MODEL,
            *([index.chunk_config == chunk_config] if chunk_config is not None else []))


def _initialize(database: Session, tokenizer: PassageTokenizer) -> None:
    index = passages.INDEXES
    candidates = database.execute(select(Artifact.id, Artifact.project_id)
        .join(ArtifactExtraction, Artifact.id == ArtifactExtraction.artifact_id)
        .outerjoin(index, index.c.artifact_id == Artifact.id)
        .where(*current_source(), or_(index.c.id.is_(None),
            index.c.revision != Artifact.revision,
            index.c.text_sha256 != ArtifactExtraction.text_sha256,
            index.c.model != passages.PASSAGE_MODEL, index.c.chunk_config != tokenizer.config))
        .order_by(Artifact.project_id, Artifact.id).limit(20)).all()
    for identity, project_id in candidates:
        with project_mutation(database, project_id):
            source = database.execute(select(Artifact.revision, ArtifactExtraction.text_sha256,
                func.length(ArtifactExtraction.normalized_text).label("total_chars"))
                .join(ArtifactExtraction, Artifact.id == ArtifactExtraction.artifact_id)
                .where(Artifact.id == identity, *current_source())).first()
            if source is None:
                continue
            existing = database.execute(select(index).where(index.c.artifact_id == identity))\
                .mappings().first()
            witness = (source.revision, source.text_sha256, passages.PASSAGE_MODEL,
                       tokenizer.config)
            if existing and tuple(existing[key] for key in (
                "revision", "text_sha256", "model", "chunk_config")) == witness:
                continue
            database.execute(delete(index).where(index.c.artifact_id == identity))
            database.execute(insert(index).values(id=uuid4(), artifact_id=identity,
                revision=source.revision, text_sha256=source.text_sha256,
                model=passages.PASSAGE_MODEL, chunk_config=tokenizer.config,
                token_limit=tokenizer.maximum,
                total_chars=source.total_chars,
                status="ready" if not source.total_chars else "pending"))


def enqueue_artifact_passage_jobs(database: Session, tokenizer: PassageTokenizer) -> int:
    _initialize(database, tokenizer)
    index = passages.INDEXES
    failed = exists(select(BackgroundJob.id).where(
        BackgroundJob.kind == "artifact_embed", BackgroundJob.status == "failed",
        BackgroundJob.payload["passage_index_id"].astext == cast(index.c.id, String),
        BackgroundJob.payload["offset"].as_integer() == index.c.next_offset,
    ))
    exhausted = select(index.c.id).where(
        failed, index.c.status.in_(["pending", "processing"])).limit(100)
    database.execute(update(index).where(index.c.id.in_(exhausted)).values(
        status="failed", error_code="artifact_embedding_failed", updated_at=func.clock_timestamp()))
    rows = database.execute(select(index.c.id, index.c.next_offset)
        .select_from(index).join(Artifact, Artifact.id == index.c.artifact_id)
        .join(ArtifactExtraction, Artifact.id == ArtifactExtraction.artifact_id)
        .where(*current_index(tokenizer.config), index.c.status != "ready", ~failed)
        .order_by(index.c.updated_at, index.c.id).limit(100))
    count = 0
    for identity, offset in rows:
        enqueue_job(database, "artifact_embed", f"artifact_embed:{identity}:{offset}",
                    {"passage_index_id": str(identity), "offset": offset})
        count += 1
    return count


@dataclass(frozen=True)
class EmbeddingBatch:
    index_id: UUID
    artifact_id: UUID
    project_id: UUID
    revision: int
    text_sha256: str
    model: str
    chunk_config: str
    offset: int
    ordinal: int
    total_chars: int
    dimensions: int | None
    text: str


def _capture(factory: sessionmaker[Session], context: JobContext) -> EmbeddingBatch | None:
    index = passages.INDEXES
    with factory.begin() as database:
        context.assert_owned(database)
        row = database.execute(select(index, Artifact.project_id,
            func.substr(ArtifactExtraction.normalized_text, index.c.next_offset + 1,
                        passages.BATCH_CHARS).label("text"))
            .select_from(index).join(Artifact, Artifact.id == index.c.artifact_id)
            .join(ArtifactExtraction, Artifact.id == ArtifactExtraction.artifact_id)
            .where(*current_index(), index.c.id == UUID(context.payload["passage_index_id"]),
                   index.c.next_offset == context.payload["offset"], index.c.status != "ready")
            .with_for_update(of=index)).mappings().first()
        if row is None:
            return None
        database.execute(update(index).where(index.c.id == row["id"]).values(
            status="processing", error_code=None, updated_at=func.clock_timestamp()))
        return EmbeddingBatch(row["id"], row["artifact_id"], row["project_id"], row["revision"],
            row["text_sha256"], row["model"], row["chunk_config"], row["next_offset"],
            row["next_ordinal"], row["total_chars"], row["dimensions"], row["text"])


def _publish(factory: sessionmaker[Session], context: JobContext, batch: EmbeddingBatch,
             chunks: list[passages.Passage], vectors: list[list[float]] | None,
             chunk_config: str) -> str:
    index = passages.INDEXES
    with factory() as database:
        with project_mutation(database, batch.project_id):
            context.assert_owned(database)
            current = database.scalar(select(index.c.id).select_from(index)
                .join(Artifact, Artifact.id == index.c.artifact_id)
                .join(ArtifactExtraction, Artifact.id == ArtifactExtraction.artifact_id)
                .where(*current_index(chunk_config), index.c.id == batch.index_id,
                       index.c.next_offset == batch.offset).with_for_update(of=index))
            if current is None:
                return "obsolete"
            if vectors is None:
                state = "failed"
                values = {"status": state, "error_code": "artifact_embedding_unavailable"}
            else:
                database.execute(insert(passages.PASSAGES), [{
                    "index_id": batch.index_id, "ordinal": chunk.ordinal,
                    "start_offset": chunk.start, "end_offset": chunk.end,
                    "token_count": chunk.token_count,
                    "passage_id": passages.passage_identity(batch.artifact_id, batch.revision,
                        batch.text_sha256, batch.model, batch.chunk_config, chunk),
                    "vector": vector,
                } for chunk, vector in zip(chunks, vectors, strict=True)])
                last = chunks[-1]
                state = "ready" if last.end == batch.total_chars else "pending"
                values = {"status": state, "error_code": None, "dimensions": len(vectors[0]),
                          "next_offset": batch.total_chars if state == "ready" else
                          passages.next_passage_offset(last), "next_ordinal": last.ordinal + 1}
            database.execute(update(index).where(index.c.id == batch.index_id).values(
                **values, updated_at=func.clock_timestamp()))
            database.commit()
            return state


def _unit_vectors(vectors: list[list[float]]) -> list[list[float]]:
    result = []
    for vector in vectors:
        norm = hypot(*vector)
        if not isfinite(norm) or norm <= 0:
            raise ValueError("Embedding vector norm is outside the supported range")
        result.append([value / norm for value in vector])
    return result


def handle_artifact_embedding(factory: sessionmaker[Session], embedder: Embedder,
                              context: JobContext) -> dict:
    batch = _capture(factory, context)
    if batch is None:
        return {"disposition": "obsolete"}
    chunks = []
    vectors = None
    chunk_config = batch.chunk_config
    try:
        tokenizer = passage_tokenizer(embedder)
        chunk_config = tokenizer.config
        if chunk_config != batch.chunk_config:
            return {"disposition": "obsolete", "passages": 0}
        chunks = passages.passage_batch(
            batch.text, batch.offset, batch.ordinal, batch.total_chars, tokenizer)
        embedded = embedder.embed_documents([chunk.text for chunk in chunks])
        dimensions = batch.dimensions or (len(embedded[0]) if embedded else 0)
        if (len(embedded) != len(chunks) or not 1 <= dimensions <= 4096
                or any(not _valid_vector(vector, dimensions) or not any(vector)
                       for vector in embedded)):
            raise ValueError("Embedding model returned invalid passage vectors")
        # Unit vectors fit PostgreSQL REAL even when a provider returns very large
        # finite values. Normalization does not change cosine ordering.
        vectors = _unit_vectors(embedded)
    except Exception:
        # Native text and arbitrary provider messages never enter errors or logs.
        pass
    state = _publish(factory, context, batch, chunks, vectors,
                     tokenizer.config if vectors is not None else chunk_config)
    if state == "failed":
        raise RetryJob("artifact_embedding_unavailable",
                       min(3600, 30 * 2 ** min(context.attempts, 7)))
    return {"disposition": state, "passages": len(chunks) if state in {"pending", "ready"} else 0}
