"""Literal matching keeps bodies in PostgreSQL and respects current content access."""

from collections.abc import Callable, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import SearchHit
from mnemonic_api.models import Artifact, ArtifactExtraction


def literal_artifact_hits(
    database: Session, corpus: Sequence[tuple[Artifact, ArtifactExtraction | None]],
    query: str, fulltext: bool, approved: set[UUID],
    metadata: Callable[[Artifact, ArtifactExtraction | None], Sequence[str]],
) -> list[SearchHit]:
    metadata_ids = {artifact.id for artifact, extraction in corpus
                    if any(query in value for value in metadata(artifact, extraction))}
    allowed = {artifact.id for artifact, extraction in corpus
               if fulltext and artifact.deleted_at is None
               and (not artifact.sensitive or artifact.id in approved)
               and extraction is not None and extraction.status == "ready"}
    content_ids = set(database.scalars(select(Artifact.id).join(ArtifactExtraction,
        (ArtifactExtraction.artifact_id == Artifact.id)
        & (ArtifactExtraction.revision == Artifact.revision)).where(
            Artifact.id.in_(allowed),
            ArtifactExtraction.normalized_text.contains(query, autoescape=True),
        ))) if allowed else set()
    hits = [SearchHit(str(identity), float(2 * (identity in metadata_ids)
                                          + (identity in content_ids)),
                      identity in metadata_ids, identity in content_ids)
            for identity in metadata_ids | content_ids]
    return sorted(hits, key=lambda hit: (-hit.score, hit.identity))
