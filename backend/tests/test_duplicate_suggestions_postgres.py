"""Adversarial Advisory selection tests against real PostgreSQL."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from mnemonic_api.application.routes import duplicates as duplicate_routes
from mnemonic_api.database import begin_coherent_read
from mnemonic_api.models import (
    ClientOperation,
    WorkEvent,
    WorkItem,
    WorkItemEmbedding,
    WorkRelationship,
)
from mnemonic_api.schemas import DuplicateSuggestionRequest
from mnemonic_api.semantic import BGE_QUERY_PREFIX
from mnemonic_api.services import duplicate_suggestions as suggestion_service

from .report_fixtures import reported

pytestmark = pytest.mark.postgres


class FailingEmbedder:
    def embed_query(self, _text):
        raise RuntimeError("model unavailable")

    def embed_documents(self, _texts):
        raise RuntimeError("model unavailable")


class NeverEmbedder:
    def embed_query(self, _text):
        raise AssertionError("inference capacity was not acquired")

    def embed_documents(self, _texts):
        raise AssertionError("inference capacity was not acquired")


class DeterministicEmbedder:
    def __init__(self):
        self.document_batches = []

    def embed_query(self, text):
        assert text.startswith(BGE_QUERY_PREFIX)
        return [1.0, 0.0]

    def embed_documents(self, texts):
        batch = list(texts)
        self.document_batches.append(batch)
        return [
            [1.0, 0.0] if "[dense-target]" in candidate else [0.0, 1.0]
            for candidate in batch
        ]


class FixedQueryEmbedder:
    def __init__(self, vector):
        self.vector = vector

    def embed_query(self, _text):
        return self.vector

    def embed_documents(self, _texts):
        raise AssertionError("invalid query vectors must fall back before document inference")


class BrokenDocumentEmbedder:
    def __init__(self, mode):
        self.mode = mode

    def embed_query(self, _text):
        return [1.0, 0.0]

    def embed_documents(self, texts):
        if self.mode == "wrong-count":
            return []
        if self.mode == "wrong-dimension":
            return [[1.0] for _text in texts]
        if self.mode == "nonfinite":
            return [[float("nan"), 0.0] for _text in texts]
        return [[0.0, 0.0] for _text in texts]


def save(api, project, work_payload, *, title, summary=None, prompt=None, tags=None):
    body = {
        **work_payload,
        "title": title,
        "summary": summary or f"Summary for {title}",
        "initial_checkpoint": {
            **work_payload["initial_checkpoint"],
            "prompt": prompt or f"Initial context for {title}",
            "tags": tags or [],
        },
    }
    response = api.post(f"/api/v1/projects/{project['id']}/work-items", json=body)
    assert response.status_code == 201, response.text
    return response.json()["work_item"]


def suggest(api, project, **changes):
    payload = {
        "title": "cache repair",
        "summary": "Repair cache invalidation behavior.",
        "initial_prompt": "Reproduce cache repair failures.",
        "tags": ["cache"],
        **changes,
    }
    return api.post(
        f"/api/v1/projects/{project['id']}/duplicate-suggestions",
        json=payload,
    )


def merge(api, project, source, destination):
    prefix = f"/api/v1/projects/{project['id']}/work-items"
    source_context = api.get(f"{prefix}/{source['id']}/context").json()
    destination_context = api.get(f"{prefix}/{destination['id']}/context").json()
    response = api.post(
        f"{prefix}/{source['id']}/merge",
        json={
            "destination_work_item_id": destination["id"],
            "reviewed_source_revision": source_context["merge_review_revision"],
            "reviewed_destination_revision": destination_context["merge_review_revision"],
            "rationale": "The destination is the explicitly reviewed canonical continuation.",
            "merged_by_client": "duplicate-suggestion-tests",
            "merged_by_session_id": "group-exclusion",
            "client_operation_id": str(uuid4()),
        },
    )
    assert response.status_code == 201, response.text


def bulk_save(
    postgres_engine,
    project_id,
    *,
    api=None,
    count,
    title_prefix,
    same_title=False,
    status_cycle=False,
    deleted_ordinal=None,
    target_ordinal=None,
):
    target_id = None
    done_ids = []
    other_states = []
    ordered_ids = []
    with postgres_engine.begin() as connection:
        connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        connection.execute(
            text(
                """
                CREATE TEMPORARY TABLE advisory_bulk_ids (
                    ordinal integer PRIMARY KEY,
                    work_item_id uuid NOT NULL,
                    checkpoint_id uuid NOT NULL
                ) ON COMMIT DROP
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO advisory_bulk_ids (ordinal, work_item_id, checkpoint_id)
                SELECT ordinal, pg_catalog.gen_random_uuid(), pg_catalog.gen_random_uuid()
                FROM pg_catalog.generate_series(1, CAST(:count AS integer)) AS ordinal
                """
            ),
            {"count": count},
        )
        connection.execute(
            text(
                """
                INSERT INTO work_items (
                    id, project_id, title, summary, status, priority,
                    initial_checkpoint_id, version, created_at, updated_at, deleted_at
                )
                SELECT bulk.work_item_id,
                       CAST(:project_id AS uuid),
                       CASE
                           WHEN CAST(:same_title AS boolean) THEN CAST(:title_prefix AS text)
                           ELSE CAST(:title_prefix AS text) || ' '
                                || pg_catalog.lpad(bulk.ordinal::text, 6, '0')
                       END,
                       'Bulk suggestion candidate ' || bulk.ordinal::text,
                       'pending',
                       0,
                       bulk.checkpoint_id,
                       1,
                       TIMESTAMPTZ '2026-01-01 00:00:00+00'
                           + bulk.ordinal * INTERVAL '1 microsecond',
                       TIMESTAMPTZ '2026-01-01 00:00:00+00'
                           + bulk.ordinal * INTERVAL '1 microsecond',
                       CASE
                           WHEN bulk.ordinal = CAST(:deleted_ordinal AS integer)
                           THEN TIMESTAMPTZ '2026-02-01 00:00:00+00'
                           ELSE NULL
                       END
                FROM advisory_bulk_ids AS bulk
                """
            ),
            {
                "project_id": project_id,
                "same_title": same_title,
                "title_prefix": title_prefix,
                "status_cycle": status_cycle,
                "deleted_ordinal": deleted_ordinal,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO checkpoints (
                    id, work_item_id, kind, prompt, source_client, source_session_id,
                    tags, source_metadata, created_at
                )
                SELECT bulk.checkpoint_id,
                       bulk.work_item_id,
                       'context',
                       'Opaque bulk initial context ' || bulk.ordinal::text,
                       'duplicate-suggestion-tests',
                       'bulk-fixture',
                       ARRAY[]::varchar[],
                       '{}'::jsonb,
                       TIMESTAMPTZ '2026-01-01 00:00:00+00'
                           + bulk.ordinal * INTERVAL '1 microsecond'
                FROM advisory_bulk_ids AS bulk
                """
            )
        )
        if target_ordinal is not None:
            target_id = connection.scalar(
                text(
                    """
                    SELECT work_item_id
                    FROM advisory_bulk_ids
                    WHERE ordinal = CAST(:target_ordinal AS integer)
                    """
                ),
                {"target_ordinal": target_ordinal},
            )
        if status_cycle:
            ordered_ids = list(connection.scalars(text(
                "SELECT work_item_id FROM advisory_bulk_ids ORDER BY ordinal"
            )))
            other_states = list(connection.execute(text(
                "SELECT work_item_id, (ARRAY['pending','deferred','done','wont-do','promoted'])"
                "[((ordinal - 1) % 5) + 1] AS status FROM advisory_bulk_ids "
                "WHERE ((ordinal - 1) % 5) + 1 IN (2,4,5) ORDER BY ordinal"
            )))
            done_ids = list(
                connection.scalars(
                    text(
                        """
                        SELECT work_item_id
                        FROM advisory_bulk_ids
                        WHERE ((ordinal - 1) % 5) + 1 = 3
                        ORDER BY ordinal
                        """
                    )
                )
            )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    for work_item_id, target_status in other_states:
        assert api is not None
        endpoint = f"/api/v1/projects/{project_id}/work-items/{work_item_id}"
        if target_status == "deferred":
            response = api.post(endpoint + "/defer", json={"expected_version": 1})
        else:
            response = api.patch(endpoint, json=reported(
                {"expected_version": 1, "status": target_status}, retirement=True,
            ))
        assert response.status_code == 200, response.text
    if done_ids:
        assert api is not None
        for work_item_id in done_ids:
            completed = api.post(
                f"/api/v1/projects/{project_id}/work-items/{work_item_id}/complete",
                json=reported({
                    "expected_version": 1,
                    "checkpoint": {
                        "prompt": "Bulk fixture completion episode.",
                        "source_client": "duplicate-suggestion-tests",
                        "source_session_id": "bulk-fixture",
                    },
                }),
            )
            assert completed.status_code == 200, completed.text
    if ordered_ids:
        # Actual closeouts advance activity time. Restore the fixture's intended
        # ordinal ranking after all transitions, so the bounded exact lane spans
        # all five states instead of ranking every untouched Pending row last.
        with postgres_engine.begin() as connection:
            connection.execute(text(
                "UPDATE work_items w SET updated_at=statement_timestamp() "
                "+ ranked.ordinal * INTERVAL '1 microsecond' "
                "FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS ranked(id,ordinal) "
                "WHERE w.id=ranked.id"
            ), {"ids": ordered_ids})
    assert target_id is None or isinstance(target_id, UUID)
    return target_id


def cache_project_vectors(postgres_engine, project_id, target_id):
    with Session(postgres_engine) as database:
        work_item_ids = list(
            database.scalars(
                select(WorkItem.id).where(
                    WorkItem.project_id == project_id,
                    WorkItem.deleted_at.is_(None),
                )
            )
        )
        compositions = suggestion_service._bounded_compositions(database, work_item_ids)
    rows = [
        {
            "work_item_id": work_item_id,
            "model": suggestion_service._cache_version(2),
            "digest": suggestion_service._digest(compositions[work_item_id]),
            "vector": [1.0, 0.0] if work_item_id == target_id else [0.0, 1.0],
        }
        for work_item_id in work_item_ids
    ]
    with postgres_engine.begin() as connection:
        for start in range(0, len(rows), 1_000):
            connection.execute(
                WorkItemEmbedding.__table__.insert(), rows[start : start + 1_000]
            )


def test_exact_title_lane_is_global_normalized_grouped_and_private(
    api, project, work_payload
):
    candidate = save(
        api,
        project,
        work_payload,
        title="Cache   Repair",
        summary="A completed-looking retained candidate.",
    )
    api.app.state.semantic_embedder = FailingEmbedder()
    response = suggest(api, project, title="  ＣＡＣＨＥ  ＲＥＰＡＩＲ  ", limit=1)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["mode"] == "lexical"
    assert body["semantic_available"] is False
    assert body["semantic_scope"] == "unavailable"
    assert body["composition_version"] == "duplicate-suggestion-v1"
    assert body["exact_title_group_total"] == 1
    assert body["omitted_exact_title_group_count"] == 0
    item = body["items"][0]
    assert item["canonical_work"]["work_item_id"] == candidate["id"]
    assert item["matched_member"]["id"] == candidate["id"]
    assert item["signals"][0] == "exact_title"
    assert set(item["canonical_work"]) == {
        "work_item_id",
        "title",
        "summary",
        "status",
        "updated_at",
        "duplicate_member_count",
    }
    serialized = response.text
    for forbidden in (
        "readiness",
        "lease",
        "checkpoint",
        "actor",
        "score",
        "vector",
        "provenance",
    ):
        assert forbidden not in serialized


def test_candidate_response_preserves_retained_boundary_whitespace(
    api, project, work_payload, postgres_engine
):
    candidate = save(api, project, work_payload, title="Boundary candidate")
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE work_items SET title = :title, summary = :summary "
                "WHERE id = CAST(:work_item_id AS uuid)"
            ),
            {
                "title": "  Boundary candidate  ",
                "summary": "\tRetained summary\t",
                "work_item_id": candidate["id"],
            },
        )
    api.app.state.semantic_embedder = FailingEmbedder()

    response = suggest(
        api,
        project,
        title="boundary candidate",
        summary="Unrelated draft summary.",
        initial_prompt="Unrelated draft context.",
        tags=[],
    )

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["canonical_work"]["title"] == "  Boundary candidate  "
    assert item["canonical_work"]["summary"] == "\tRetained summary\t"
    assert item["matched_member"]["title"] == "  Boundary candidate  "


def test_exact_lane_counts_globally_filters_scope_and_returns_bounded_sql_rows(
    api, project, postgres_engine
):
    bulk_save(
        postgres_engine,
        project["id"],
        api=api,
        count=26,
        title_prefix="Cache Repair",
        same_title=True,
        status_cycle=True,
        deleted_ordinal=26,
    )
    bulk_save(
        postgres_engine,
        project["id"],
        count=250,
        title_prefix="Cache repair candidate",
    )
    other = api.post("/api/v1/projects", json={"name": "Other suggestion scope"})
    assert other.status_code == 201, other.text
    bulk_save(
        postgres_engine,
        other.json()["id"],
        count=5,
        title_prefix="Cache Repair",
        same_title=True,
    )
    payload = DuplicateSuggestionRequest.model_validate(
        {
            "title": "cache repair",
            "summary": "cache repair",
            "initial_prompt": "cache repair",
            "tags": [],
            "limit": 10,
        }
    )
    with Session(postgres_engine) as database:
        exact_total, exact_rows = suggestion_service._exact_group_rows(
            database,
            UUID(project["id"]),
            payload.title,
            None,
            payload.limit,
            expected_visible_count=275,
        )
        lexical_rows = suggestion_service._lexical_group_rows(
            database,
            UUID(project["id"]),
            payload,
            None,
            200,
        )
    assert exact_total == 25
    assert len(exact_rows) == 10
    assert len(lexical_rows) == 200
    assert len({row.root_id for row in exact_rows}) == 10
    assert len({row.root_id for row in lexical_rows}) == 200

    api.app.state.semantic_embedder = FailingEmbedder()
    response = suggest(
        api,
        project,
        title="cache repair",
        summary="cache repair",
        initial_prompt="cache repair",
        tags=[],
        limit=10,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exact_title_group_total"] == 25
    assert body["omitted_exact_title_group_count"] == 15
    assert len(body["items"]) == 10
    assert {item["canonical_work"]["status"] for item in body["items"]} == {
        "pending",
        "deferred",
        "done",
        "wont-do",
        "promoted",
    }
    assert all(item["signals"][0] == "exact_title" for item in body["items"])


def test_weighted_lexical_lane_uses_tags_and_bounded_later_checkpoint_tail(
    api, project, work_payload, postgres_engine
):
    tagged = save(
        api,
        project,
        work_payload,
        title="Unrelated title",
        tags=[f"a-tag-{index:02d}" for index in range(20)],
    )
    tag_path = f"/api/v1/projects/{project['id']}/work-items/{tagged['id']}/checkpoints"
    for tags in (
        [f"b-tag-{index:02d}" for index in range(20)],
        ["rare-tag-token"],
    ):
        checkpoint = api.post(
            tag_path,
            json={
                "kind": "context",
                "prompt": "Bounded tag selection context.",
                "source_client": "duplicate-suggestion-tests",
                "source_session_id": "bounded-tags",
                "tags": tags,
            },
        )
        assert checkpoint.status_code == 201, checkpoint.text
    tagged_id = UUID(tagged["id"])
    with Session(postgres_engine) as database:
        tagged_text = suggestion_service._bounded_compositions(database, [tagged_id])[
            tagged_id
        ]
    assert "rare-tag-token" in tagged_text
    assert "a-tag-19" not in tagged_text
    later = save(api, project, work_payload, title="Another unrelated title")
    checkpoint = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{later['id']}/checkpoints",
        json={
            "kind": "context",
            "prompt": "x" * 3_000 + " recent-tail-token",
            "source_client": "duplicate-suggestion-tests",
            "source_session_id": "bounded-tail",
        },
    )
    assert checkpoint.status_code == 201, checkpoint.text
    api.app.state.semantic_embedder = FailingEmbedder()
    tagged_result = suggest(
        api,
        project,
        title="rare tag token",
        summary="rare tag token",
        initial_prompt="rare tag token",
        tags=["rare-tag-token"],
    )
    assert tagged_result.status_code == 200, tagged_result.text
    assert tagged["id"] in {
        item["matched_member"]["id"] for item in tagged_result.json()["items"]
    }
    tail_result = suggest(
        api,
        project,
        title="recent tail token",
        summary="recent tail token",
        initial_prompt="recent tail token",
        tags=[],
    )
    assert tail_result.status_code == 200, tail_result.text
    assert later["id"] in {
        item["matched_member"]["id"] for item in tail_result.json()["items"]
    }


def test_partial_title_overlap_survives_disjoint_draft_fields_and_seeds_semantic(
    api, project, work_payload
):
    candidate = save(
        api,
        project,
        work_payload,
        title="Repair cache invalidation",
        summary="Retained implementation context.",
        prompt="[dense-target] Opaque retained details.",
        tags=[],
    )
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    response = suggest(
        api,
        project,
        title="repair cache",
        summary="Nebula constellation analysis.",
        initial_prompt="Orchid geology field notes.",
        tags=["volcanic"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "hybrid_shortlist"
    item = next(
        item for item in body["items"] if item["matched_member"]["id"] == candidate["id"]
    )
    assert "lexical" in item["signals"]
    assert "semantic" in item["signals"]
    assert any(
        "[dense-target]" in text for batch in embedder.document_batches for text in batch
    )


def test_checkpoint_tail_preserves_delimiter_at_exact_character_boundary(
    api, project, work_payload, postgres_engine
):
    candidate = save(
        api,
        project,
        work_payload,
        title="Opaque boundary candidate",
        summary="Opaque boundary summary",
        prompt="Opaque initial context",
        tags=[],
    )
    path = f"/api/v1/projects/{project['id']}/work-items/{candidate['id']}/checkpoints"
    older_prompts = [f"old-{index:02d}-" + "x" * 2_992 for index in range(20)]
    for prompt in (*older_prompts, "z", "n" * 1_499):
        checkpoint = api.post(
            path,
            json={
                "kind": "context",
                "prompt": prompt,
                "source_client": "duplicate-suggestion-tests",
                "source_session_id": "tail-boundary",
            },
        )
        assert checkpoint.status_code == 201, checkpoint.text
    work_id = UUID(candidate["id"])
    with Session(postgres_engine) as database:
        composition = suggestion_service._bounded_compositions(database, [work_id])[work_id]
    assert composition.endswith("\n" + "n" * 1_499)
    assert not composition.endswith("z\n" + "n" * 1_499)
    assert "old-" not in composition


def test_shortlist_cache_fill_then_full_project_hybrid_is_deterministic_and_inert(
    api, project, work_payload, postgres_engine
):
    target = save(
        api,
        project,
        work_payload,
        title="Cache durability target",
        summary="Repair cache consistency.",
        prompt="[dense-target] cache repair context",
    )
    save(
        api,
        project,
        work_payload,
        title="Cache rendering neighbor",
        summary="Repair cache rendering.",
        prompt="ordinary cache repair context",
    )
    with postgres_engine.connect() as connection:
        before = connection.execute(
            select(
                func.count(WorkItem.id),
                func.sum(WorkItem.version),
                select(func.count()).select_from(WorkEvent).scalar_subquery(),
                select(func.count()).select_from(WorkRelationship).scalar_subquery(),
                select(func.count()).select_from(ClientOperation).scalar_subquery(),
            )
        ).one()
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    first = suggest(api, project, title="cache", summary="cache", initial_prompt="cache")
    assert first.status_code == 200, first.text
    assert first.json()["mode"] == "hybrid_shortlist"
    assert first.json()["semantic_scope"] == "lexical_shortlist"
    assert any("semantic" in item["signals"] for item in first.json()["items"])
    assert any("[dense-target]" in text for batch in embedder.document_batches for text in batch)
    assert target["id"] in {item["matched_member"]["id"] for item in first.json()["items"]}

    second = suggest(api, project, title="cache", summary="cache", initial_prompt="cache")
    assert second.status_code == 200, second.text
    assert second.json()["mode"] == "hybrid_full"
    assert second.json()["semantic_scope"] == "full_project"
    assert len(embedder.document_batches) == 1
    with postgres_engine.connect() as connection:
        after = connection.execute(
            select(
                func.count(WorkItem.id),
                func.sum(WorkItem.version),
                select(func.count()).select_from(WorkEvent).scalar_subquery(),
                select(func.count()).select_from(WorkRelationship).scalar_subquery(),
                select(func.count()).select_from(ClientOperation).scalar_subquery(),
            )
        ).one()
        assert connection.scalar(select(func.count()).select_from(WorkItemEmbedding)) == 2
    assert after == before


@pytest.mark.parametrize(
    "query_vector",
    ([], [float("nan"), 0.0], [0.0, 0.0]),
    ids=("empty", "nonfinite", "zero-norm"),
)
def test_invalid_query_vectors_return_lexical_success(
    api, project, work_payload, query_vector
):
    save(
        api,
        project,
        work_payload,
        title="Cache candidate",
        summary="cache",
        prompt="cache",
        tags=[],
    )
    api.app.state.semantic_embedder = FixedQueryEmbedder(query_vector)
    response = suggest(
        api,
        project,
        title="cache",
        summary="cache",
        initial_prompt="cache",
        tags=[],
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "lexical"


@pytest.mark.parametrize(
    "document_mode",
    ("wrong-count", "wrong-dimension", "nonfinite", "zero-norm"),
)
def test_invalid_document_vectors_return_lexical_success(
    api, project, work_payload, document_mode
):
    save(
        api,
        project,
        work_payload,
        title="Cache candidate",
        summary="cache",
        prompt="cache",
        tags=[],
    )
    api.app.state.semantic_embedder = BrokenDocumentEmbedder(document_mode)
    response = suggest(
        api,
        project,
        title="cache",
        summary="cache",
        initial_prompt="cache",
        tags=[],
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "lexical"


def test_stale_wrong_dimension_and_nonfinite_caches_are_recomputed(
    api, project, work_payload, postgres_engine
):
    candidates = [
        save(
            api,
            project,
            work_payload,
            title=f"Cache candidate {index}",
            summary="cache",
            prompt="cache",
            tags=[],
        )
        for index in range(3)
    ]
    candidate_ids = [UUID(candidate["id"]) for candidate in candidates]
    with Session(postgres_engine) as database:
        compositions = suggestion_service._bounded_compositions(database, candidate_ids)
        database.add_all(
            [
                WorkItemEmbedding(
                    work_item_id=candidate_ids[0],
                    model=suggestion_service._cache_version(2),
                    digest="0" * 64,
                    vector=[1.0, 0.0],
                ),
                WorkItemEmbedding(
                    work_item_id=candidate_ids[1],
                    model=suggestion_service._cache_version(2),
                    digest=suggestion_service._digest(compositions[candidate_ids[1]]),
                    vector=[1.0],
                ),
                WorkItemEmbedding(
                    work_item_id=candidate_ids[2],
                    model=suggestion_service._cache_version(2),
                    digest=suggestion_service._digest(compositions[candidate_ids[2]]),
                    vector=[float("nan"), 0.0],
                ),
            ]
        )
        database.commit()

    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    response = suggest(
        api,
        project,
        title="cache",
        summary="cache",
        initial_prompt="cache",
        tags=[],
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "hybrid_shortlist"
    assert sum(len(batch) for batch in embedder.document_batches) == 3
    with Session(postgres_engine) as database:
        cache_rows = list(
            database.scalars(
                select(WorkItemEmbedding).where(
                    WorkItemEmbedding.work_item_id.in_(candidate_ids)
                )
            )
        )
    assert len(cache_rows) == 3
    assert all(suggestion_service._valid_vector(row.vector, 2) for row in cache_rows)
    assert {
        row.work_item_id: row.digest for row in cache_rows
    } == {
        work_item_id: suggestion_service._digest(compositions[work_item_id])
        for work_item_id in candidate_ids
    }


def test_ten_thousand_cached_members_find_dense_only_candidate_beyond_lexical_cap(
    api, project, postgres_engine, monkeypatch
):
    bulk_save(
        postgres_engine,
        project["id"],
        count=201,
        title_prefix="Lexical needle candidate",
    )
    target_id = bulk_save(
        postgres_engine,
        project["id"],
        count=9_799,
        title_prefix="Opaque population member",
        target_ordinal=9_799,
    )
    assert target_id is not None
    cache_project_vectors(postgres_engine, project["id"], target_id)
    observed = {}
    hybrid_page = suggestion_service._hybrid_page

    def observe_full_dense_lane(snapshot, limit, query_vector, vectors, *, mode):
        _winner_by_root, dense_order = suggestion_service._dense_groups(
            snapshot, query_vector, vectors
        )
        observed.update(
            {
                "lexical_order": snapshot.lexical_root_order,
                "dense_order": dense_order,
                "vector_count": len(vectors),
                "mode": mode,
            }
        )
        return hybrid_page(snapshot, limit, query_vector, vectors, mode=mode)

    monkeypatch.setattr(suggestion_service, "_hybrid_page", observe_full_dense_lane)
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder

    response = suggest(
        api,
        project,
        title="lexical needle",
        summary="lexical needle",
        initial_prompt="lexical needle",
        tags=[],
        limit=10,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "hybrid_full"
    assert body["semantic_scope"] == "full_project"
    assert embedder.document_batches == []
    assert observed["mode"] == "hybrid_full"
    assert len(observed["lexical_order"]) == 200
    assert target_id not in observed["lexical_order"]
    assert observed["dense_order"][0] == target_id
    assert observed["vector_count"] == 10_000
    with postgres_engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(WorkItemEmbedding)
        ) == 10_000


def test_project_above_full_ceiling_uses_sql_bounded_shortlist(
    api, project, postgres_engine, monkeypatch
):
    bulk_save(
        postgres_engine,
        project["id"],
        count=10_001,
        title_prefix="Oversize lexical candidate",
    )
    captured_population_sizes = []
    bounded_population = suggestion_service._bounded_population

    def observe_population(database, candidates):
        population = bounded_population(database, candidates)
        captured_population_sizes.append(len(population.work_by_id))
        return population

    monkeypatch.setattr(suggestion_service, "_bounded_population", observe_population)
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    response = suggest(
        api,
        project,
        title="oversize lexical",
        summary="oversize lexical",
        initial_prompt="oversize lexical",
        tags=[],
        limit=10,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "hybrid_shortlist"
    assert body["semantic_scope"] == "lexical_shortlist"
    assert captured_population_sizes == [200]
    assert sum(len(batch) for batch in embedder.document_batches) == 128
    with postgres_engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(WorkItemEmbedding)
        ) == 128


def test_exclude_alias_removes_its_complete_canonical_group(api, project, work_payload):
    source = save(api, project, work_payload, title="Cache repair")
    root = save(api, project, work_payload, title="Cache repair")
    merge(api, project, source, root)
    api.app.state.semantic_embedder = FailingEmbedder()
    response = suggest(api, project, exclude_work_item_id=source["id"])
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["exact_title_group_total"] == 0


def test_suggestion_snapshot_does_not_mix_groups_across_concurrent_merge(
    api, project, work_payload, monkeypatch
):
    source = save(api, project, work_payload, title="Coherent exact candidate")
    root = save(api, project, work_payload, title="Coherent exact candidate")
    api.app.state.semantic_embedder = FailingEmbedder()
    snapshot_pinned = Event()
    continue_read = Event()
    visible_member_count = suggestion_service._visible_member_count

    def pause_after_snapshot(database, project_id):
        count = visible_member_count(database, project_id)
        if not snapshot_pinned.is_set():
            snapshot_pinned.set()
            if not continue_read.wait(timeout=10):
                raise TimeoutError("Timed out waiting for the concurrent merge")
        return count

    monkeypatch.setattr(
        suggestion_service, "_visible_member_count", pause_after_snapshot
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        response_future = executor.submit(
            suggest,
            api,
            project,
            title="coherent exact candidate",
            summary="disjoint summary",
            initial_prompt="disjoint prompt",
            tags=[],
        )
        try:
            assert snapshot_pinned.wait(timeout=5)
            merge(api, project, source, root)
        finally:
            continue_read.set()
        response = response_future.result(timeout=10)

    assert response.status_code == 200, response.text
    assert response.json()["exact_title_group_total"] == 2
    assert {
        item["canonical_work"]["work_item_id"] for item in response.json()["items"]
    } == {source["id"], root["id"]}
    current = suggest(
        api,
        project,
        title="coherent exact candidate",
        summary="disjoint summary",
        initial_prompt="disjoint prompt",
        tags=[],
    )
    assert current.status_code == 200, current.text
    assert current.json()["exact_title_group_total"] == 1
    assert current.json()["items"][0]["canonical_work"]["work_item_id"] == root["id"]


def test_alias_heavy_exact_members_group_before_public_limit(
    api, project, work_payload, postgres_engine
):
    root = save(api, project, work_payload, title="Shared exact candidate")
    aliases = [
        save(api, project, work_payload, title="Shared exact candidate")
        for _index in range(12)
    ]
    standalone = [
        save(api, project, work_payload, title="Shared exact candidate")
        for _index in range(10)
    ]
    for alias in aliases:
        merge(api, project, alias, root)

    payload = DuplicateSuggestionRequest.model_validate(
        {
            "title": "shared exact candidate",
            "summary": "unrelated summary",
            "initial_prompt": "unrelated prompt",
            "tags": [],
            "limit": 10,
        }
    )
    lexical_payload = DuplicateSuggestionRequest.model_validate(
        {
            "title": "shared exact",
            "summary": "shared exact",
            "initial_prompt": "shared exact",
            "tags": [],
            "limit": 10,
        }
    )
    with Session(postgres_engine) as database:
        total, rows = suggestion_service._exact_group_rows(
            database,
            UUID(project["id"]),
            payload.title,
            None,
            payload.limit,
            expected_visible_count=23,
        )
        lexical_rows = suggestion_service._lexical_group_rows(
            database,
            UUID(project["id"]),
            lexical_payload,
            None,
            10,
        )
    assert total == 11
    assert len(rows) == 10
    assert len({row.root_id for row in rows}) == 10
    root_row = next(row for row in rows if row.root_id == UUID(root["id"]))
    assert root_row.duplicate_member_count == 12
    assert len(lexical_rows) == 10
    assert len({row.root_id for row in lexical_rows}) == 10
    lexical_root = next(row for row in lexical_rows if row.root_id == UUID(root["id"]))
    assert lexical_root.duplicate_member_count == 12

    api.app.state.semantic_embedder = FailingEmbedder()
    response = suggest(
        api,
        project,
        title=payload.title,
        summary=payload.summary,
        initial_prompt=payload.initial_prompt,
        tags=[],
        limit=10,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exact_title_group_total"] == 11
    assert body["omitted_exact_title_group_count"] == 1
    returned_roots = {
        item["canonical_work"]["work_item_id"] for item in body["items"]
    }
    assert len(returned_roots) == 10
    assert len(returned_roots & {item["id"] for item in standalone}) == 9
    root_item = next(
        item
        for item in body["items"]
        if item["canonical_work"]["work_item_id"] == root["id"]
    )
    assert root_item["canonical_work"]["duplicate_member_count"] == 12


def test_resource_saturation_falls_back_or_returns_bounded_retry(
    api, project, work_payload
):
    save(api, project, work_payload, title="Cache repair candidate")
    resources = api.app.state.duplicate_suggestion_resources
    resources.inference_slots = asyncio.Semaphore(0)
    resources.inference_wait_seconds = 0.001
    api.app.state.semantic_embedder = NeverEmbedder()
    fallback = suggest(api, project)
    assert fallback.status_code == 200, fallback.text
    assert fallback.json()["mode"] == "lexical"

    invalid_search = api.get(
        f"/api/v1/projects/{project['id']}/work-items",
        params={"q": "cache", "semantic": "t", "limit": "invalid"},
    )
    assert invalid_search.status_code == 422
    unavailable_search = api.get(
        f"/api/v1/projects/{project['id']}/work-items",
        params={"q": "cache", "semantic": "y"},
    )
    assert unavailable_search.status_code == 503
    assert unavailable_search.json()["detail"]["code"] == "semantic_unavailable"

    resources.request_slots = asyncio.Semaphore(0)
    resources.request_wait_seconds = 0.001
    busy = suggest(api, project)
    assert busy.status_code == 429
    assert busy.headers["retry-after"] == "1"
    assert busy.headers["cache-control"] == "no-store"
    assert busy.json()["detail"]["code"] == "duplicate_suggestion_busy"


def test_locked_cache_candidate_is_skipped_without_delaying_semantic_success(
    api, project, work_payload, postgres_engine
):
    candidate = save(
        api,
        project,
        work_payload,
        title="Cache repair target",
        prompt="[dense-target] retained candidate context",
    )
    api.app.state.semantic_embedder = DeterministicEmbedder()

    with Session(postgres_engine) as locker:
        locked = locker.scalar(
            select(WorkItem)
            .where(WorkItem.id == UUID(candidate["id"]))
            .with_for_update()
        )
        assert locked is not None
        started_at = monotonic()
        response = suggest(
            api,
            project,
            title="cache repair",
            summary="Find the cache repair target.",
            initial_prompt="Use semantic matching.",
            tags=[],
        )
        elapsed = monotonic() - started_at

    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "hybrid_shortlist"
    assert elapsed < 1.0
    with Session(postgres_engine) as database:
        assert database.get(WorkItemEmbedding, UUID(candidate["id"])) is None


def test_locked_embedding_cache_row_falls_back_before_transport_deadline(
    api, project, work_payload, postgres_engine
):
    candidate = save(
        api,
        project,
        work_payload,
        title="Cache lock fallback target",
        prompt="[dense-target] original cache-lock context",
    )
    api.app.state.semantic_embedder = DeterministicEmbedder()
    initial = suggest(
        api,
        project,
        title="cache lock fallback",
        summary="Prime the derived candidate cache.",
        initial_prompt="Use semantic matching.",
        tags=[],
    )
    assert initial.status_code == 200, initial.text
    assert initial.json()["mode"] == "hybrid_shortlist"

    changed = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{candidate['id']}/checkpoints",
        json={
            "kind": "progress",
            "prompt": "[dense-target] stale the retained cache digest.",
            "source_client": "suggestion-cache-lock-test",
            "source_session_id": "suggestion-cache-lock-change",
        },
    )
    assert changed.status_code == 201, changed.text

    with Session(postgres_engine) as locker:
        cache_row = locker.scalar(
            select(WorkItemEmbedding)
            .where(WorkItemEmbedding.work_item_id == UUID(candidate["id"]))
            .with_for_update()
        )
        assert cache_row is not None
        started_at = monotonic()
        contended = suggest(
            api,
            project,
            title="cache lock fallback",
            summary="Retry while the derived cache row is locked.",
            initial_prompt="Use semantic matching.",
            tags=[],
        )
        elapsed = monotonic() - started_at

    assert contended.status_code == 200, contended.text
    assert contended.json()["mode"] == "lexical"
    assert elapsed < 1.0


def test_transaction_deadline_bounds_postgresql_statements(postgres_engine):
    with Session(postgres_engine) as unused_database:
        with pytest.raises(TimeoutError):
            suggestion_service._set_transaction_deadline(
                unused_database, monotonic() - 1
            )
        assert unused_database.in_transaction() is False

    with Session(postgres_engine) as database:
        begin_coherent_read(database)
        suggestion_service._set_transaction_deadline(database, monotonic() + 0.05)
        started_at = monotonic()
        with pytest.raises(DBAPIError):
            database.execute(text("SELECT pg_catalog.pg_sleep(1)"))
        assert monotonic() - started_at < 1.0


def test_scope_errors_are_sanitized_and_system_failures_use_advisory_error(
    api, project, monkeypatch
):
    api.app.state.semantic_embedder = FailingEmbedder()
    absent = suggest(api, project, exclude_work_item_id=str(uuid4()))
    assert absent.status_code == 404
    assert absent.json()["detail"]["code"] == "work_item_not_found"

    def fail(*_args, **_kwargs):
        raise RuntimeError("private system detail")

    monkeypatch.setattr(duplicate_routes, "capture_internal_suggestions", fail)
    unavailable = suggest(api, project)
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "duplicate_suggestion_unavailable"
    assert "private system detail" not in unavailable.text


def test_title_key_function_and_partial_expression_index_are_frozen(postgres_engine):
    with postgres_engine.connect() as connection:
        normalized = connection.execute(
            text(
                "SELECT mnemonic_duplicate_title_key_v1(:value), "
                "mnemonic_duplicate_title_key_v1(:equivalent)"
            ),
            {"value": "  ＣＡＣＨＥ\t Repair ", "equivalent": "cache repair"},
        ).one()
        assert normalized[0] == normalized[1] == "cache repair"
        narrow_case = connection.execute(
            text(
                "SELECT mnemonic_duplicate_title_key_v1(:dotted_i), "
                "mnemonic_duplicate_title_key_v1(:sharp_s), "
                "mnemonic_duplicate_title_key_v1(:fullwidth_a)"
            ),
            {"dotted_i": "İ", "sharp_s": "ß", "fullwidth_a": "Ａ"},
        ).one()
        assert narrow_case == ("İ", "ß", "a")
        attributes = connection.execute(
            text(
                """
                SELECT procedure.provolatile, procedure.proparallel, procedure.proisstrict
                FROM pg_proc AS procedure
                JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = current_schema()
                  AND procedure.proname = 'mnemonic_duplicate_title_key_v1'
                """
            )
        ).one()
        assert attributes == ("i", "s", True)
        index_definition = connection.scalar(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND indexname = 'ix_work_items_duplicate_title_key_v1'
                """
            )
        )
        assert "mnemonic_duplicate_title_key_v1" in index_definition
        assert "WHERE (deleted_at IS NULL)" in index_definition
        connection.execute(text("SET enable_seqscan = off"))
        exact_plan = "\n".join(
            row[0]
            for row in connection.execute(
                text(
                    """
                    EXPLAIN (COSTS OFF)
                    SELECT id
                    FROM work_items
                    WHERE project_id = CAST(:project_id AS uuid)
                      AND deleted_at IS NULL
                      AND mnemonic_duplicate_title_key_v1(title)
                          = mnemonic_duplicate_title_key_v1(:title)
                    """
                ),
                {"project_id": uuid4(), "title": "cache repair"},
            )
        )
        assert "ix_work_items_duplicate_title_key_v1" in exact_plan
        head, capacity = connection.execute(
            text(
                """
                SELECT version_num,
                       information_schema.columns.character_maximum_length
                FROM alembic_version
                CROSS JOIN information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'alembic_version'
                  AND column_name = 'version_num'
                """
            )
        ).one()
        assert head == "0024_code_reviews"
        assert capacity == 64
