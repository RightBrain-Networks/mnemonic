"""Bounded, inert duplicate suggestions grouped by authoritative canonical root."""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from mnemonic_api.config import Settings
from mnemonic_api.database import begin_coherent_read
from mnemonic_api.errors import duplicate_graph_invalid
from mnemonic_api.external_references import ExternalReference
from mnemonic_api.models import WorkItem, WorkItemEmbedding, WorkStatus
from mnemonic_api.schemas import (
    DuplicateCandidateSummary,
    DuplicateSuggestion,
    DuplicateSuggestionMode,
    DuplicateSuggestionPage,
    DuplicateSuggestionRequest,
    DuplicateSuggestionSemanticScope,
    DuplicateSuggestionSignal,
    WorkIdentityPointer,
)
from mnemonic_api.semantic import (
    BGE_QUERY_PREFIX,
    EMBED_BATCH_SIZE,
    EMBED_BODY_CHARS,
    EMBED_COMMENT_CHARS,
    EMBED_MODEL,
    RRF_K,
    RRF_LEXICAL_WEIGHT,
    Embedder,
    cosine_similarity,
)
from mnemonic_api.services.duplicates import canonical_group_snapshot
from mnemonic_api.services.work_items import missing_work_item, require_project

logger = logging.getLogger(__name__)

COMPOSITION_VERSION = "duplicate-suggestion-v1"
TS_RANK_NORMALIZATION = 32
SUGGESTION_TAG_LIMIT = 30
SUGGESTION_CACHE_LOCK_TIMEOUT_MS = 50

_BOUNDED_TEXT_SQL = """
SELECT work.id,
       pg_catalog.left(initial_checkpoint.prompt, :body_chars) AS initial_prompt,
       COALESCE((
           SELECT pg_catalog.string_agg(
               recent_tag.value,
               ' ' ORDER BY recent_tag.value
           )
           FROM (
               SELECT distinct_tag.value
               FROM (
                   SELECT normalized_tag.value,
                          tag_checkpoint.created_at,
                          tag_checkpoint.id AS checkpoint_id,
                          pg_catalog.row_number() OVER (
                              PARTITION BY normalized_tag.value
                              ORDER BY tag_checkpoint.created_at DESC,
                                       tag_checkpoint.id DESC
                          ) AS occurrence_rank
                   FROM checkpoints AS tag_checkpoint
                   CROSS JOIN LATERAL pg_catalog.unnest(
                       mnemonic_normalized_tags(tag_checkpoint.tags)
                   ) AS normalized_tag(value)
                   WHERE tag_checkpoint.work_item_id = work.id
               ) AS distinct_tag
               WHERE distinct_tag.occurrence_rank = 1
               ORDER BY distinct_tag.created_at DESC,
                        distinct_tag.checkpoint_id DESC,
                        distinct_tag.value ASC
               LIMIT CAST(:tag_limit AS integer)
           ) AS recent_tag
       ), '') AS normalized_tags,
       COALESCE((
           SELECT pg_catalog.right(
               pg_catalog.string_agg(
                   tail_checkpoint.prompt,
                   E'\\n' ORDER BY tail_checkpoint.created_at, tail_checkpoint.id
               ),
               :tail_chars
           )
           FROM (
               SELECT pg_catalog.right(
                          later.prompt,
                          CAST(:tail_chars AS integer)
                      ) AS prompt,
                      later.created_at,
                      later.id,
                      pg_catalog.sum(
                          LEAST(
                              pg_catalog.length(later.prompt),
                              CAST(:tail_chars AS integer)
                          ) + 1
                      ) OVER (
                          ORDER BY later.created_at DESC, later.id DESC
                      ) AS reverse_characters
               FROM checkpoints AS later
               WHERE later.work_item_id = work.id
                 AND later.id <> work.initial_checkpoint_id
           ) AS tail_checkpoint
           WHERE tail_checkpoint.reverse_characters
                 - (LEAST(
                     pg_catalog.length(tail_checkpoint.prompt),
                     CAST(:tail_chars AS integer)
                 ) + 1) <= CAST(:tail_chars AS integer)
       ), '') AS checkpoint_tail
FROM work_items AS work
JOIN checkpoints AS initial_checkpoint
  ON initial_checkpoint.id = work.initial_checkpoint_id
 AND initial_checkpoint.work_item_id = work.id
WHERE work.id = ANY(CAST(:work_item_ids AS uuid[]))
  AND work.deleted_at IS NULL
"""

_ROOT_MAP_CTES = """
visible_ids AS MATERIALIZED (
    SELECT id
    FROM work_items
    WHERE project_id = CAST(:project_id AS uuid)
      AND deleted_at IS NULL
), canonical_walk(member_id, current_id, depth) AS (
    SELECT id, id, 0
    FROM visible_ids
    UNION ALL
    SELECT walk.member_id, duplicate.destination_work_item_id, walk.depth + 1
    FROM canonical_walk AS walk
    JOIN work_duplicate_merges AS duplicate
      ON duplicate.project_id = CAST(:project_id AS uuid)
     AND duplicate.source_work_item_id = walk.current_id
    JOIN visible_ids AS destination
      ON destination.id = duplicate.destination_work_item_id
    WHERE walk.depth < 50
), root_map AS MATERIALIZED (
    SELECT walk.member_id, walk.current_id AS root_id
    FROM canonical_walk AS walk
    WHERE NOT EXISTS (
        SELECT 1
        FROM work_duplicate_merges AS outgoing
        WHERE outgoing.project_id = CAST(:project_id AS uuid)
          AND outgoing.source_work_item_id = walk.current_id
    )
), group_counts AS MATERIALIZED (
    SELECT root_id, CAST(pg_catalog.count(*) - 1 AS integer) AS duplicate_member_count
    FROM root_map
    GROUP BY root_id
)
"""

_PROJECT_BOUNDED_TEXT_SQL = _BOUNDED_TEXT_SQL.replace(
    "WHERE work.id = ANY(CAST(:work_item_ids AS uuid[]))\n  AND work.deleted_at IS NULL",
    "WHERE work.project_id = CAST(:project_id AS uuid)\n  AND work.deleted_at IS NULL",
)

_EXACT_GROUPS_SQL = f"""
WITH RECURSIVE {_ROOT_MAP_CTES},
exact_visible AS MATERIALIZED (
    SELECT member.id, member.updated_at
    FROM work_items AS member
    WHERE member.project_id = CAST(:project_id AS uuid)
      AND member.deleted_at IS NULL
      AND mnemonic_duplicate_title_key_v1(member.title)
          = mnemonic_duplicate_title_key_v1(:title)
), ranked_exact AS (
    SELECT roots.root_id,
           exact_visible.id AS matched_member_id,
           pg_catalog.row_number() OVER (
               PARTITION BY roots.root_id
               ORDER BY exact_visible.updated_at DESC, exact_visible.id ASC
           ) AS member_rank
    FROM exact_visible
    JOIN root_map AS roots ON roots.member_id = exact_visible.id
    WHERE CAST(:excluded_root_id AS uuid) IS NULL
       OR roots.root_id <> CAST(:excluded_root_id AS uuid)
), exact_groups AS MATERIALIZED (
    SELECT root_id, matched_member_id
    FROM ranked_exact
    WHERE member_rank = 1
), totals AS (
    SELECT (SELECT pg_catalog.count(*) FROM visible_ids) AS visible_member_count,
           (SELECT pg_catalog.count(*) FROM root_map) AS mapped_member_count,
           (SELECT pg_catalog.count(*) FROM exact_groups) AS exact_group_total
)
SELECT totals.visible_member_count,
       totals.mapped_member_count,
       totals.exact_group_total,
       candidate.root_id,
       candidate.matched_member_id,
       candidate.duplicate_member_count
FROM totals
LEFT JOIN LATERAL (
    SELECT exact_groups.root_id,
           exact_groups.matched_member_id,
           group_counts.duplicate_member_count
    FROM exact_groups
    JOIN group_counts ON group_counts.root_id = exact_groups.root_id
    JOIN work_items AS root ON root.id = exact_groups.root_id
    ORDER BY root.updated_at DESC, root.id ASC
    LIMIT CAST(:public_limit AS integer)
) AS candidate ON true
"""

_LEXICAL_GROUPS_SQL = f"""
WITH RECURSIVE {_ROOT_MAP_CTES},
exact_visible AS MATERIALIZED (
    SELECT member.id
    FROM work_items AS member
    WHERE member.project_id = CAST(:project_id AS uuid)
      AND member.deleted_at IS NULL
      AND mnemonic_duplicate_title_key_v1(member.title)
          = mnemonic_duplicate_title_key_v1(:title)
), exact_roots AS MATERIALIZED (
    SELECT DISTINCT roots.root_id
    FROM exact_visible
    JOIN root_map AS roots ON roots.member_id = exact_visible.id
), bounded AS (
    {_PROJECT_BOUNDED_TEXT_SQL}
), documents AS (
    SELECT work.id,
           work.updated_at,
           pg_catalog.setweight(
               pg_catalog.to_tsvector('english'::regconfig, COALESCE(work.title, '')),
               'A'
           ) ||
           pg_catalog.setweight(
               pg_catalog.to_tsvector('english'::regconfig, COALESCE(work.summary, '')),
               'B'
           ) ||
           pg_catalog.setweight(
               pg_catalog.to_tsvector('english'::regconfig, bounded.normalized_tags),
               'B'
           ) ||
           pg_catalog.setweight(
               pg_catalog.to_tsvector('english'::regconfig, bounded.initial_prompt),
               'C'
           ) ||
           pg_catalog.setweight(
               pg_catalog.to_tsvector('english'::regconfig, bounded.checkpoint_tail),
               'D'
           ) AS document
    FROM work_items AS work
    JOIN bounded ON bounded.id = work.id
), query_lexemes AS (
    SELECT lexeme.value
    FROM pg_catalog.unnest(
        pg_catalog.tsvector_to_array(
            pg_catalog.to_tsvector('english'::regconfig, :query_text)
        )
    ) AS lexeme(value)
), query AS (
    SELECT CAST(
               pg_catalog.string_agg(
                   pg_catalog.quote_literal(query_lexemes.value),
                   ' | ' ORDER BY query_lexemes.value
               )
               AS pg_catalog.tsquery
           ) AS terms
    FROM query_lexemes
), scored AS (
    SELECT roots.root_id,
           documents.id AS matched_member_id,
           documents.updated_at AS matched_updated_at,
           pg_catalog.ts_rank_cd(
               documents.document,
               query.terms,
               :rank_normalization
           ) AS score
    FROM documents
    JOIN root_map AS roots ON roots.member_id = documents.id
    CROSS JOIN query
    WHERE documents.document @@ query.terms
      AND NOT EXISTS (
          SELECT 1 FROM exact_roots WHERE exact_roots.root_id = roots.root_id
      )
      AND (
          CAST(:excluded_root_id AS uuid) IS NULL
          OR roots.root_id <> CAST(:excluded_root_id AS uuid)
      )
), ranked AS (
    SELECT scored.*,
           pg_catalog.row_number() OVER (
               PARTITION BY scored.root_id
               ORDER BY scored.score DESC,
                        scored.matched_updated_at DESC,
                        scored.matched_member_id ASC
           ) AS member_rank
    FROM scored
)
SELECT ranked.root_id,
       ranked.matched_member_id,
       group_counts.duplicate_member_count
FROM ranked
JOIN group_counts ON group_counts.root_id = ranked.root_id
JOIN work_items AS root ON root.id = ranked.root_id
WHERE ranked.member_rank = 1
ORDER BY ranked.score DESC, root.updated_at DESC, root.id ASC
LIMIT CAST(:shortlist_limit AS integer)
"""


@dataclass(frozen=True, slots=True)
class WorkSnapshot:
    id: UUID
    project_id: UUID
    title: str
    summary: str
    status: WorkStatus
    version: int
    updated_at: datetime
    external_references: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class GroupedCandidate:
    root_id: UUID
    matched_member_id: UUID
    duplicate_member_count: int


@dataclass(frozen=True, slots=True)
class CandidatePopulation:
    work_by_id: dict[UUID, WorkSnapshot]
    root_by_member: dict[UUID, UUID]
    member_count_by_root: dict[UUID, int]
    eligible_ids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class VectorCandidate:
    work: WorkSnapshot
    text: str
    digest: str
    cached_vector: tuple[float, ...] | None


@dataclass(frozen=True, slots=True)
class CacheUpdate:
    work_item_id: UUID
    project_id: UUID
    work_version: int
    digest: str
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class CapturedSuggestionSnapshot:
    work_by_id: dict[UUID, WorkSnapshot]
    root_by_member: dict[UUID, UUID]
    member_count_by_root: dict[UUID, int]
    eligible_ids: frozenset[UUID]
    exact_winner_by_root: dict[UUID, UUID]
    lexical_winner_by_root: dict[UUID, UUID]
    lexical_root_order: tuple[UUID, ...]
    vectors_by_id: dict[UUID, VectorCandidate]
    visible_member_count: int
    exact_title_group_total: int


@dataclass(frozen=True, slots=True)
class InternalSuggestionResult:
    page: DuplicateSuggestionPage
    query_vector: tuple[float, ...] | None


def suggest_duplicate_work(
    database: Session,
    project_id: UUID,
    payload: DuplicateSuggestionRequest,
    *,
    settings: Settings,
    embedder: Embedder,
    inference_permitted: bool,
    deadline: float,
) -> DuplicateSuggestionPage:
    return capture_internal_suggestions(
        database, project_id, payload, settings=settings, embedder=embedder,
        inference_permitted=inference_permitted, deadline=deadline,
    ).page


def capture_internal_suggestions(
    database: Session,
    project_id: UUID,
    payload: DuplicateSuggestionRequest,
    *,
    settings: Settings,
    embedder: Embedder,
    inference_permitted: bool,
    deadline: float,
) -> InternalSuggestionResult:
    """Capture once, rank outside the snapshot, and persist only derived cache rows."""
    _remaining_deadline_milliseconds(deadline)
    query_vector = _query_vector(embedder, payload) if inference_permitted else None
    dimensions = len(query_vector) if query_vector is not None else None
    _remaining_deadline_milliseconds(deadline)
    begin_coherent_read(database)
    _set_transaction_deadline(database, deadline)
    snapshot = _capture_snapshot(database, project_id, payload, settings, dimensions)
    database.commit()
    if query_vector is None:
        return InternalSuggestionResult(_lexical_page(snapshot, payload.limit), query_vector)
    try:
        page, updates = _semantic_page(snapshot, payload.limit, query_vector, embedder, settings)
        _persist_cache_updates(
            database,
            updates,
            dimensions=len(query_vector),
            deadline=deadline,
        )
    except Exception as exc:
        logger.warning("Duplicate suggestion semantic fallback (%s)", type(exc).__name__)
        return InternalSuggestionResult(_lexical_page(snapshot, payload.limit), query_vector)
    return InternalSuggestionResult(page, query_vector)


def _query_vector(
    embedder: Embedder,
    payload: DuplicateSuggestionRequest,
) -> tuple[float, ...] | None:
    try:
        vector = tuple(float(value) for value in embedder.embed_query(
            BGE_QUERY_PREFIX + _draft_text(payload)
        ))
    except Exception as exc:
        logger.warning("Duplicate suggestion query fallback (%s)", type(exc).__name__)
        return None
    return vector if _valid_vector(vector) else None


def _draft_text(payload: DuplicateSuggestionRequest) -> str:
    return "\n".join(
        (
            payload.title,
            payload.summary,
            " ".join(payload.tags),
            payload.initial_prompt[:EMBED_BODY_CHARS],
            "",
        )
    )


def _capture_snapshot(
    database: Session,
    project_id: UUID,
    payload: DuplicateSuggestionRequest,
    settings: Settings,
    dimensions: int | None,
) -> CapturedSuggestionSnapshot:
    require_project(database, project_id)
    visible_count = _visible_member_count(database, project_id)
    within_full_ceiling = (
        visible_count <= settings.duplicate_suggestion_full_population_ceiling
    )
    if within_full_ceiling:
        population, excluded_root = _full_population(database, project_id, payload)
    else:
        population = None
        excluded_root = _bounded_excluded_root(database, project_id, payload)

    exact_total, exact_rows = _exact_group_rows(
        database,
        project_id,
        payload.title,
        excluded_root,
        payload.limit,
        expected_visible_count=visible_count,
    )
    lexical_rows = _lexical_group_rows(
        database,
        project_id,
        payload,
        excluded_root,
        settings.duplicate_suggestion_lexical_shortlist,
    )
    candidates = [*exact_rows, *lexical_rows]
    if population is None:
        population = _bounded_population(database, candidates)
    else:
        _validate_group_rows(population, candidates)

    exact_winners = {
        candidate.root_id: candidate.matched_member_id for candidate in exact_rows
    }
    lexical_winners = {
        candidate.root_id: candidate.matched_member_id for candidate in lexical_rows
    }
    capture_ids = (
        population.eligible_ids
        if within_full_ceiling
        else frozenset(lexical_winners.values())
    )
    vectors = (
        _capture_vectors(database, population.work_by_id, capture_ids, dimensions)
        if dimensions is not None
        else {}
    )
    return CapturedSuggestionSnapshot(
        work_by_id=population.work_by_id,
        root_by_member=population.root_by_member,
        member_count_by_root=population.member_count_by_root,
        eligible_ids=population.eligible_ids,
        exact_winner_by_root=exact_winners,
        lexical_winner_by_root=lexical_winners,
        lexical_root_order=tuple(lexical_winners),
        vectors_by_id=vectors,
        visible_member_count=visible_count,
        exact_title_group_total=exact_total,
    )


def _work_snapshot(work_item: WorkItem) -> WorkSnapshot:
    return WorkSnapshot(
        id=work_item.id,
        project_id=work_item.project_id,
        title=work_item.title,
        summary=work_item.summary,
        status=work_item.status,
        version=work_item.version,
        updated_at=work_item.updated_at,
        external_references=tuple(deepcopy(work_item.external_references or [])),
    )


def _visible_member_count(database: Session, project_id: UUID) -> int:
    count = database.scalar(
        select(func.count())
        .select_from(WorkItem)
        .where(WorkItem.project_id == project_id, WorkItem.deleted_at.is_(None))
    )
    return int(count or 0)


def _full_population(
    database: Session,
    project_id: UUID,
    payload: DuplicateSuggestionRequest,
) -> tuple[CandidatePopulation, UUID | None]:
    visible = list(
        database.scalars(
            select(WorkItem)
            .where(WorkItem.project_id == project_id, WorkItem.deleted_at.is_(None))
            .order_by(WorkItem.id)
        )
    )
    groups = canonical_group_snapshot(database, project_id, visible)
    excluded_root = _excluded_root_from_map(
        database, project_id, payload.exclude_work_item_id, groups.root_by_member
    )
    eligible_ids = frozenset(
        item.id
        for item in visible
        if groups.root_by_member[item.id] != excluded_root
    )
    return (
        CandidatePopulation(
            work_by_id={item.id: _work_snapshot(item) for item in visible},
            root_by_member=groups.root_by_member,
            member_count_by_root=groups.duplicate_member_count_by_root,
            eligible_ids=eligible_ids,
        ),
        excluded_root,
    )


def _excluded_root_from_map(
    database: Session,
    project_id: UUID,
    excluded_id: UUID | None,
    root_by_member: dict[UUID, UUID],
) -> UUID | None:
    if excluded_id is None:
        return None
    root_id = root_by_member.get(excluded_id)
    if root_id is None:
        raise missing_work_item(database, project_id)
    return root_id


def _bounded_excluded_root(
    database: Session,
    project_id: UUID,
    payload: DuplicateSuggestionRequest,
) -> UUID | None:
    excluded_id = payload.exclude_work_item_id
    if excluded_id is None:
        return None
    visible = database.scalar(
        select(WorkItem.id).where(
            WorkItem.id == excluded_id,
            WorkItem.project_id == project_id,
            WorkItem.deleted_at.is_(None),
        )
    )
    if visible is None:
        raise missing_work_item(database, project_id)
    root_id = database.scalar(
        text(
            """
            WITH RECURSIVE canonical_path(current_id, depth) AS (
                SELECT id, 0
                FROM work_items
                WHERE id = CAST(:work_item_id AS uuid)
                  AND project_id = CAST(:project_id AS uuid)
                  AND deleted_at IS NULL
                UNION ALL
                SELECT duplicate.destination_work_item_id, path.depth + 1
                FROM canonical_path AS path
                JOIN work_duplicate_merges AS duplicate
                  ON duplicate.project_id = CAST(:project_id AS uuid)
                 AND duplicate.source_work_item_id = path.current_id
                JOIN work_items AS destination
                  ON destination.id = duplicate.destination_work_item_id
                 AND destination.project_id = CAST(:project_id AS uuid)
                 AND destination.deleted_at IS NULL
                WHERE path.depth < 50
            )
            SELECT path.current_id
            FROM canonical_path AS path
            WHERE NOT EXISTS (
                SELECT 1
                FROM work_duplicate_merges AS outgoing
                WHERE outgoing.project_id = CAST(:project_id AS uuid)
                  AND outgoing.source_work_item_id = path.current_id
            )
            ORDER BY path.depth DESC
            LIMIT 1
            """
        ),
        {"project_id": project_id, "work_item_id": excluded_id},
    )
    if not isinstance(root_id, UUID):
        raise duplicate_graph_invalid()
    return root_id


def _exact_group_rows(
    database: Session,
    project_id: UUID,
    title: str,
    excluded_root: UUID | None,
    public_limit: int,
    *,
    expected_visible_count: int,
) -> tuple[int, list[GroupedCandidate]]:
    rows = list(
        database.execute(
            text(_EXACT_GROUPS_SQL),
            {
                "project_id": project_id,
                "title": title,
                "excluded_root_id": excluded_root,
                "public_limit": public_limit,
            },
        ).mappings()
    )
    if not rows:
        raise duplicate_graph_invalid()
    metadata = rows[0]
    visible_count = _integer_value(metadata["visible_member_count"])
    mapped_count = _integer_value(metadata["mapped_member_count"])
    exact_total = _integer_value(metadata["exact_group_total"])
    if visible_count != expected_visible_count or mapped_count != visible_count:
        raise duplicate_graph_invalid()
    candidates = [
        _grouped_candidate(row) for row in rows if row["root_id"] is not None
    ]
    if len(candidates) > public_limit:
        raise duplicate_graph_invalid()
    return exact_total, candidates


def _lexical_group_rows(
    database: Session,
    project_id: UUID,
    payload: DuplicateSuggestionRequest,
    excluded_root: UUID | None,
    shortlist_limit: int,
) -> list[GroupedCandidate]:
    rows = database.execute(
        text(_LEXICAL_GROUPS_SQL),
        {
            "project_id": project_id,
            "title": payload.title,
            "excluded_root_id": excluded_root,
            "shortlist_limit": shortlist_limit,
            "body_chars": EMBED_BODY_CHARS,
            "tail_chars": EMBED_COMMENT_CHARS,
            "tag_limit": SUGGESTION_TAG_LIMIT,
            "query_text": _draft_text(payload),
            "rank_normalization": TS_RANK_NORMALIZATION,
        },
    ).mappings()
    candidates = [_grouped_candidate(row) for row in rows]
    if len(candidates) > shortlist_limit:
        raise duplicate_graph_invalid()
    return candidates


def _grouped_candidate(row: RowMapping) -> GroupedCandidate:
    root_id = row["root_id"]
    matched_member_id = row["matched_member_id"]
    if not isinstance(root_id, UUID) or not isinstance(matched_member_id, UUID):
        raise duplicate_graph_invalid()
    return GroupedCandidate(
        root_id=root_id,
        matched_member_id=matched_member_id,
        duplicate_member_count=_integer_value(row["duplicate_member_count"]),
    )


def _integer_value(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise duplicate_graph_invalid()
    return value


def _bounded_population(
    database: Session,
    candidates: Sequence[GroupedCandidate],
) -> CandidatePopulation:
    candidate_ids = frozenset(
        work_item_id
        for candidate in candidates
        for work_item_id in (candidate.root_id, candidate.matched_member_id)
    )
    items = list(
        database.scalars(
            select(WorkItem)
            .where(WorkItem.id.in_(candidate_ids), WorkItem.deleted_at.is_(None))
            .order_by(WorkItem.id)
        )
    ) if candidate_ids else []
    work_by_id = {item.id: _work_snapshot(item) for item in items}
    if set(work_by_id) != set(candidate_ids):
        raise duplicate_graph_invalid()
    root_by_member: dict[UUID, UUID] = {}
    member_count_by_root: dict[UUID, int] = {}
    for candidate in candidates:
        root_by_member[candidate.root_id] = candidate.root_id
        root_by_member[candidate.matched_member_id] = candidate.root_id
        previous = member_count_by_root.setdefault(
            candidate.root_id, candidate.duplicate_member_count
        )
        if previous != candidate.duplicate_member_count:
            raise duplicate_graph_invalid()
    return CandidatePopulation(
        work_by_id=work_by_id,
        root_by_member=root_by_member,
        member_count_by_root=member_count_by_root,
        eligible_ids=frozenset(work_by_id),
    )


def _validate_group_rows(
    population: CandidatePopulation,
    candidates: Sequence[GroupedCandidate],
) -> None:
    for candidate in candidates:
        if (
            population.root_by_member.get(candidate.matched_member_id)
            != candidate.root_id
            or population.member_count_by_root.get(candidate.root_id)
            != candidate.duplicate_member_count
        ):
            raise duplicate_graph_invalid()


def _capture_vectors(
    database: Session,
    work_by_id: dict[UUID, WorkSnapshot],
    work_item_ids: Iterable[UUID],
    dimensions: int,
) -> dict[UUID, VectorCandidate]:
    ids = frozenset(work_item_ids)
    if not ids:
        return {}
    composition = _bounded_compositions(database, ids)
    cache_version = _cache_version(dimensions)
    cache_by_id = {
        row.work_item_id: row
        for row in database.scalars(
            select(WorkItemEmbedding).where(
                WorkItemEmbedding.work_item_id.in_(ids),
                WorkItemEmbedding.model == cache_version,
                func.cardinality(WorkItemEmbedding.vector) == dimensions,
            )
        )
    }
    captured: dict[UUID, VectorCandidate] = {}
    for work_item_id in ids:
        work = work_by_id[work_item_id]
        value = composition[work_item_id]
        digest = _digest(value)
        cache = cache_by_id.get(work_item_id)
        vector = (
            tuple(float(component) for component in cache.vector)
            if cache is not None
            and cache.model == cache_version
            and cache.digest == digest
            and _valid_vector(cache.vector, dimensions)
            else None
        )
        captured[work_item_id] = VectorCandidate(work, value, digest, vector)
    return captured


def _bounded_compositions(database: Session, work_item_ids: Iterable[UUID]) -> dict[UUID, str]:
    ids = list(work_item_ids)
    if not ids:
        return {}
    rows = database.execute(
        text(_BOUNDED_TEXT_SQL),
        {
            "work_item_ids": ids,
            "body_chars": EMBED_BODY_CHARS,
            "tail_chars": EMBED_COMMENT_CHARS,
            "tag_limit": SUGGESTION_TAG_LIMIT,
        },
    ).mappings()
    values = {
        row["id"]: "\n".join(
            (
                "",  # title and summary are injected from the immutable work snapshot below
                "",
                row["normalized_tags"],
                row["initial_prompt"],
                row["checkpoint_tail"],
            )
        )
        for row in rows
    }
    work_rows = {
        row.id: row
        for row in database.execute(
            select(WorkItem.id, WorkItem.title, WorkItem.summary).where(WorkItem.id.in_(ids))
        )
    }
    if set(values) != set(ids) or set(work_rows) != set(ids):
        raise ValueError("A duplicate-suggestion candidate is missing canonical text")
    return {
        work_item_id: "\n".join(
            (
                work_rows[work_item_id].title,
                work_rows[work_item_id].summary,
                values[work_item_id].split("\n", 2)[2],
            )
        )
        for work_item_id in ids
    }


def _semantic_page(
    snapshot: CapturedSuggestionSnapshot,
    limit: int,
    query_vector: Sequence[float],
    embedder: Embedder,
    settings: Settings,
) -> tuple[DuplicateSuggestionPage, list[CacheUpdate]]:
    all_cached = (
        snapshot.visible_member_count
        <= settings.duplicate_suggestion_full_population_ceiling
        and set(snapshot.vectors_by_id) == set(snapshot.eligible_ids)
        and all(
            candidate.cached_vector is not None
            for candidate in snapshot.vectors_by_id.values()
        )
    )
    if all_cached:
        vectors = {
            work_item_id: candidate.cached_vector
            for work_item_id, candidate in snapshot.vectors_by_id.items()
            if candidate.cached_vector is not None
        }
        return _hybrid_page(snapshot, limit, query_vector, vectors, mode="hybrid_full"), []
    vectors, updates = _shortlist_vectors(snapshot, embedder, settings, len(query_vector))
    return (
        _hybrid_page(snapshot, limit, query_vector, vectors, mode="hybrid_shortlist"),
        updates,
    )


def _shortlist_vectors(
    snapshot: CapturedSuggestionSnapshot,
    embedder: Embedder,
    settings: Settings,
    dimensions: int,
) -> tuple[dict[UUID, tuple[float, ...]], list[CacheUpdate]]:
    candidate_ids = [
        snapshot.lexical_winner_by_root[root_id]
        for root_id in snapshot.lexical_root_order
        if root_id not in snapshot.exact_winner_by_root
    ]
    candidates = [snapshot.vectors_by_id[work_item_id] for work_item_id in candidate_ids]
    vectors = {
        candidate.work.id: candidate.cached_vector
        for candidate in candidates
        if candidate.cached_vector is not None
    }
    missing = [candidate for candidate in candidates if candidate.cached_vector is None]
    missing = missing[: settings.duplicate_suggestion_missing_vector_limit]
    updates: list[CacheUpdate] = []
    for start in range(0, len(missing), EMBED_BATCH_SIZE):
        batch = missing[start : start + EMBED_BATCH_SIZE]
        embedded = embedder.embed_documents([candidate.text for candidate in batch])
        if len(embedded) != len(batch):
            raise ValueError("Embedding model returned the wrong number of vectors")
        for candidate, raw_vector in zip(batch, embedded, strict=True):
            vector = tuple(float(value) for value in raw_vector)
            if not _valid_vector(vector, dimensions):
                raise ValueError("Embedding model returned an invalid vector")
            vectors[candidate.work.id] = vector
            updates.append(
                CacheUpdate(
                    work_item_id=candidate.work.id,
                    project_id=candidate.work.project_id,
                    work_version=candidate.work.version,
                    digest=candidate.digest,
                    vector=vector,
                )
            )
    return vectors, updates


def _hybrid_page(
    snapshot: CapturedSuggestionSnapshot,
    limit: int,
    query_vector: Sequence[float],
    vectors: Mapping[UUID, Sequence[float]],
    *,
    mode: DuplicateSuggestionMode,
) -> DuplicateSuggestionPage:
    dense_winner, dense_order = _dense_groups(snapshot, query_vector, vectors)
    nonexact_dense = [
        root_id
        for root_id in dense_order
        if root_id not in snapshot.exact_winner_by_root
    ]
    dense_rank = {
        root_id: rank for rank, root_id in enumerate(nonexact_dense, start=1)
    }
    nonexact_lexical = [
        root_id
        for root_id in snapshot.lexical_root_order
        if root_id not in snapshot.exact_winner_by_root
    ]
    lexical_rank = {
        root_id: rank for rank, root_id in enumerate(nonexact_lexical, start=1)
    }
    exact_roots = _ordered_roots(snapshot.exact_winner_by_root, snapshot.work_by_id)
    nonexact_roots = set(lexical_rank) | set(dense_rank)
    nonexact_roots.difference_update(snapshot.exact_winner_by_root)
    ranked_nonexact = _rank_fused_roots(
        nonexact_roots,
        dense_rank,
        lexical_rank,
        snapshot.work_by_id,
    )
    root_order = [*exact_roots, *ranked_nonexact][:limit]
    items = _suggestions(
        snapshot,
        root_order,
        dense_winner=dense_winner,
        semantic_roots=frozenset(dense_winner),
    )
    return _page(
        items,
        limit,
        snapshot,
        mode=mode,
        semantic_scope="full_project" if mode == "hybrid_full" else "lexical_shortlist",
    )


def _dense_groups(
    snapshot: CapturedSuggestionSnapshot,
    query_vector: Sequence[float],
    vectors: Mapping[UUID, Sequence[float]],
) -> tuple[dict[UUID, UUID], tuple[UUID, ...]]:
    similarities = {
        work_item_id: cosine_similarity(query_vector, vector)
        for work_item_id, vector in vectors.items()
    }
    members = list(vectors)
    members.sort(key=lambda item: item.int)
    members.sort(key=lambda item: snapshot.work_by_id[item].updated_at, reverse=True)
    members.sort(key=lambda item: similarities[item], reverse=True)
    winner_by_root: dict[UUID, UUID] = {}
    score_by_root: dict[UUID, float] = {}
    for member_id in members:
        root_id = snapshot.root_by_member[member_id]
        if root_id not in winner_by_root:
            winner_by_root[root_id] = member_id
            score_by_root[root_id] = similarities[member_id]
    roots = list(winner_by_root)
    roots.sort(key=lambda item: item.int)
    roots.sort(key=lambda item: snapshot.work_by_id[item].updated_at, reverse=True)
    roots.sort(key=lambda item: score_by_root[item], reverse=True)
    return winner_by_root, tuple(roots)


def _rank_fused_roots(
    roots: Iterable[UUID],
    dense_rank: dict[UUID, int],
    lexical_rank: dict[UUID, int],
    work_by_id: dict[UUID, WorkSnapshot],
) -> list[UUID]:
    def score(root_id: UUID) -> float:
        dense = 1.0 / (RRF_K + dense_rank[root_id]) if root_id in dense_rank else 0.0
        lexical = (
            RRF_LEXICAL_WEIGHT / (RRF_K + lexical_rank[root_id])
            if root_id in lexical_rank
            else 0.0
        )
        return dense + lexical

    ordered = sorted(roots, key=lambda item: item.int)
    ordered.sort(key=lambda item: work_by_id[item].updated_at, reverse=True)
    ordered.sort(key=score, reverse=True)
    return ordered


def _lexical_page(
    snapshot: CapturedSuggestionSnapshot,
    limit: int,
) -> DuplicateSuggestionPage:
    exact_roots = _ordered_roots(snapshot.exact_winner_by_root, snapshot.work_by_id)
    nonexact = [
        root_id
        for root_id in snapshot.lexical_root_order
        if root_id not in snapshot.exact_winner_by_root
    ]
    items = _suggestions(snapshot, [*exact_roots, *nonexact][:limit])
    return _page(
        items,
        limit,
        snapshot,
        mode="lexical",
        semantic_scope="unavailable",
    )


def _ordered_roots(
    roots: Iterable[UUID],
    work_by_id: dict[UUID, WorkSnapshot],
) -> list[UUID]:
    ordered = sorted(roots, key=lambda item: item.int)
    ordered.sort(key=lambda item: work_by_id[item].updated_at, reverse=True)
    return ordered


def _suggestions(
    snapshot: CapturedSuggestionSnapshot,
    root_order: Sequence[UUID],
    *,
    dense_winner: dict[UUID, UUID] | None = None,
    semantic_roots: frozenset[UUID] = frozenset(),
) -> list[DuplicateSuggestion]:
    dense_winner = dense_winner or {}
    suggestions: list[DuplicateSuggestion] = []
    for rank, root_id in enumerate(root_order, start=1):
        matched_id = (
            snapshot.exact_winner_by_root.get(root_id)
            or snapshot.lexical_winner_by_root.get(root_id)
            or dense_winner[root_id]
        )
        signals: list[DuplicateSuggestionSignal] = []
        if root_id in snapshot.exact_winner_by_root:
            signals.append("exact_title")
        if root_id in snapshot.lexical_winner_by_root:
            signals.append("lexical")
        if root_id in semantic_roots:
            signals.append("semantic")
        root = snapshot.work_by_id[root_id]
        matched = snapshot.work_by_id[matched_id]
        suggestions.append(
            DuplicateSuggestion(
                canonical_work=DuplicateCandidateSummary(
                    work_item_id=root.id,
                    title=root.title,
                    summary=root.summary,
                    status=root.status,
                    updated_at=root.updated_at,
                    external_references=[ExternalReference.model_validate(item)
                                         for item in root.external_references],
                    duplicate_member_count=snapshot.member_count_by_root[root.id],
                ),
                matched_member=WorkIdentityPointer(
                    id=matched.id,
                    title=matched.title,
                    status=matched.status,
                ),
                rank=rank,
                signals=signals,
            )
        )
    return suggestions


def _page(
    items: list[DuplicateSuggestion],
    limit: int,
    snapshot: CapturedSuggestionSnapshot,
    *,
    mode: DuplicateSuggestionMode,
    semantic_scope: DuplicateSuggestionSemanticScope,
) -> DuplicateSuggestionPage:
    exact_total = snapshot.exact_title_group_total
    visible_exact = min(exact_total, limit)
    return DuplicateSuggestionPage(
        items=items,
        limit=limit,
        mode=mode,
        semantic_available=mode != "lexical",
        semantic_scope=semantic_scope,
        composition_version=COMPOSITION_VERSION,
        exact_title_group_total=exact_total,
        omitted_exact_title_group_count=exact_total - visible_exact,
    )


def _persist_cache_updates(
    database: Session,
    updates: Sequence[CacheUpdate],
    *,
    dimensions: int,
    deadline: float,
) -> None:
    if not updates or monotonic() >= deadline:
        return
    by_id = {update.work_item_id: update for update in updates}
    with Session(bind=database.get_bind()) as cache_database:
        cache_database.connection()
        _set_transaction_deadline(
            cache_database,
            deadline,
            lock_timeout_milliseconds=SUGGESTION_CACHE_LOCK_TIMEOUT_MS,
        )
        current = list(
            cache_database.scalars(
                select(WorkItem)
                .where(WorkItem.id.in_(by_id), WorkItem.deleted_at.is_(None))
                .order_by(WorkItem.id)
                .with_for_update(skip_locked=True)
            )
        )
        compositions = _bounded_compositions(cache_database, [item.id for item in current])
        rows = _current_cache_rows(current, compositions, by_id, dimensions)
        if rows:
            statement = insert(WorkItemEmbedding).values(rows)
            cache_database.execute(
                statement.on_conflict_do_update(
                    index_elements=[WorkItemEmbedding.work_item_id],
                    set_={
                        "model": statement.excluded.model,
                        "digest": statement.excluded.digest,
                        "vector": statement.excluded.vector,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )
        cache_database.commit()


def _set_transaction_deadline(
    database: Session,
    deadline: float,
    *,
    lock_timeout_milliseconds: int | None = None,
) -> None:
    remaining_milliseconds = _remaining_deadline_milliseconds(deadline)
    statement_timeout = f"{remaining_milliseconds}ms"
    lock_milliseconds = (
        remaining_milliseconds
        if lock_timeout_milliseconds is None
        else max(1, min(lock_timeout_milliseconds, remaining_milliseconds))
    )
    lock_timeout = f"{lock_milliseconds}ms"
    database.execute(
        text(
            "SELECT set_config('statement_timeout', :statement_timeout, true), "
            "set_config('lock_timeout', :lock_timeout, true), "
            "set_config('transaction_timeout', :statement_timeout, true)"
        ),
        {
            "statement_timeout": statement_timeout,
            "lock_timeout": lock_timeout,
        },
    )


def _remaining_deadline_milliseconds(deadline: float) -> int:
    remaining_seconds = deadline - monotonic()
    if remaining_seconds <= 0:
        raise TimeoutError
    return max(1, math.ceil(remaining_seconds * 1_000))


def _current_cache_rows(
    current: Sequence[WorkItem],
    compositions: dict[UUID, str],
    updates: dict[UUID, CacheUpdate],
    dimensions: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for work_item in current:
        update = updates[work_item.id]
        if (
            update.project_id != work_item.project_id
            or update.work_version != work_item.version
            or update.digest != _digest(compositions[work_item.id])
        ):
            continue
        rows.append(
            {
                "work_item_id": work_item.id,
                "model": _cache_version(dimensions),
                "digest": update.digest,
                "vector": list(update.vector),
            }
        )
    return rows


def _cache_version(dimensions: int) -> str:
    return (
        f"{COMPOSITION_VERSION}|title=mnemonic_duplicate_title_key_v1|model={EMBED_MODEL}|"
        f"dimensions={dimensions}|body={EMBED_BODY_CHARS}|tail={EMBED_COMMENT_CHARS}|"
        f"tags=recent-{SUGGESTION_TAG_LIMIT}|batch={EMBED_BATCH_SIZE}|rrf={RRF_K}|"
        f"lexical={RRF_LEXICAL_WEIGHT}|"
        "weights=A1.0,B0.4,B0.4,C0.2,D0.1"
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_vector(vector: Sequence[float], dimensions: int | None = None) -> bool:
    if not vector or (dimensions is not None and len(vector) != dimensions):
        return False
    try:
        values = tuple(float(value) for value in vector)
    except (OverflowError, TypeError, ValueError):
        return False
    if not all(math.isfinite(value) for value in values):
        return False
    squared_norm = sum(value * value for value in values)
    return squared_norm > 0 and math.isfinite(squared_norm)
