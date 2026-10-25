"""Bounded literal evidence from an exact decision revision, never a relevance verdict."""

import re
from bisect import bisect_right
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from mnemonic_api.impact_schemas import CitationExcerpts, ImpactCandidateRead
from mnemonic_api.models import DecisionDocument, DecisionRevision, KnowledgeDomain

MAX_EXCERPTS = 3
MAX_EXCERPT_CHARACTERS = 400
_SENTENCE_END = re.compile(r"[.!?](?:\s+|$)|\n+")


def citation_excerpts(content: str, path: list[str]) -> tuple[list[str], bool]:
    """Find path citations with neighboring sentences; exclude the candidate's own ID.

    Offsets are internal Python character positions. Returned strings are literal
    source slices; the flag covers clipped context and additional omitted matches.
    Markdown lines are sentence boundaries so metadata citations remain useful.
    """
    identifiers = {value.rsplit("-", 1)[0] for value in path[:-1]}
    if path:
        # Revision-pinned paths can revisit a document (A-0 -> B-0 -> A-1).
        # Its own stable ID must not make the candidate's heading look like a citation.
        identifiers.discard(path[-1].rsplit("-", 1)[0])
    if not identifiers:
        return [], False
    pattern = re.compile(
        r"(?<![A-Za-z0-9_-])(?:" + "|".join(map(re.escape, sorted(identifiers)))
        + r")(?:-[0-9]{1,10})?(?![A-Za-z0-9_-])"
    )
    boundaries = [0, *(match.end() for match in _SENTENCE_END.finditer(content))]
    if boundaries[-1] != len(content):
        boundaries.append(len(content))
    excerpts: list[str] = []
    selected: list[tuple[int, int]] = []
    truncated = False
    for match in pattern.finditer(content):
        if any(start <= match.start() and match.end() <= end for start, end in selected):
            continue
        if len(excerpts) == MAX_EXCERPTS:
            truncated = True
            break
        sentence = bisect_right(boundaries, match.start()) - 1
        start, end = boundaries[max(0, sentence - 1)], boundaries[min(
            len(boundaries) - 1, sentence + 2,
        )]
        if end - start > MAX_EXCERPT_CHARACTERS:
            truncated = True
            start = max(start, min(
                match.start() - (MAX_EXCERPT_CHARACTERS - len(match.group())) // 2,
                end - MAX_EXCERPT_CHARACTERS,
            ))
            end = start + MAX_EXCERPT_CHARACTERS
        selected.append((start, end))
        excerpts.append(content[start:end].strip())
    return excerpts, truncated


def hydrate_citation_excerpts(
    database: Session, project_id: UUID, candidates: list[ImpactCandidateRead],
) -> list[ImpactCandidateRead]:
    """Read pinned text in one query; never alter the persisted candidate snapshot."""
    if not candidates:
        return candidates
    identities = [
        (item.decision_id.split("-")[0], int(item.decision_id.split("-")[1]), item.revision)
        for item in candidates
    ]
    rows = database.execute(
        select(KnowledgeDomain.prefix, DecisionDocument.ordinal, DecisionRevision)
        .select_from(KnowledgeDomain)
        .join(DecisionDocument, DecisionDocument.domain_id == KnowledgeDomain.id)
        .join(DecisionRevision, DecisionRevision.document_id == DecisionDocument.id)
        .where(DecisionDocument.project_id == project_id,
               DecisionDocument.deleted_at.is_(None), KnowledgeDomain.deleted_at.is_(None), tuple_(
            KnowledgeDomain.prefix, DecisionDocument.ordinal, DecisionRevision.revision,
        ).in_(identities))
    ).all()
    revisions = {f"{prefix}-{ordinal:05d}-{revision.revision}": revision
                 for prefix, ordinal, revision in rows}
    result = []
    for item in candidates:
        source = revisions.get(item.reference)
        if source is None:
            # Immutable assessment snapshots may cite a subsequently deleted decision.
            result.append(item)
            continue
        excerpts, truncated = citation_excerpts(source.content, item.path)
        result.append(item.model_copy(update={"citation_excerpts": CitationExcerpts(
            source_sha256=source.sha256, excerpts=excerpts, truncated=truncated,
        )}))
    return result
