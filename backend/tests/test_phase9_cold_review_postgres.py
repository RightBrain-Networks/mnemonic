"""PostgreSQL regressions for Phase 9 cold-review concurrency and secret handling."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from mnemonic_api.semantic import BGE_QUERY_PREFIX

pytestmark = pytest.mark.postgres


class BlockingEmbedder:
    def __init__(self) -> None:
        self.document_batches: list[list[str]] = []
        self.first_batch_started = Event()
        self.release_first_batch = Event()

    def embed_documents(self, texts):
        batch = list(texts)
        self.document_batches.append(batch)
        if len(self.document_batches) == 1:
            self.first_batch_started.set()
            if not self.release_first_batch.wait(timeout=10):
                raise TimeoutError("Timed out waiting for the concurrent work update")
        return [[1.0, 0.0] for _text in batch]

    def embed_query(self, text):
        assert text.startswith(BGE_QUERY_PREFIX)
        return [1.0, 0.0]


def _collection(project: dict) -> str:
    return f"/api/v1/projects/{project['id']}/work-items"


def _create_work(api, project: dict, work_payload: dict, title: str) -> dict:
    response = api.post(
        _collection(project),
        json={
            **work_payload,
            "title": title,
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "source_session_id": f"phase9-cold-review-{uuid4()}",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["work_item"]


def _context(api, project: dict, work_item: dict) -> dict:
    response = api.get(f"{_collection(project)}/{work_item['id']}/context")
    assert response.status_code == 200, response.text
    return response.json()


def _authoritative_counts(postgres_engine) -> tuple[int, ...]:
    tables = (
        "work_items",
        "checkpoints",
        "work_events",
        "work_relationships",
        "work_duplicate_merges",
        "work_leases",
        "client_operations",
    )
    with postgres_engine.connect() as connection:
        return tuple(
            int(connection.scalar(text(f"SELECT count(*) FROM {table}")))
            for table in tables
        )


def test_concurrent_title_change_discards_stale_embedding_and_reembeds_next_request(
    api,
    project,
    work_payload,
    postgres_engine,
) -> None:
    work_item = _create_work(api, project, work_payload, "Title before captured snapshot")
    endpoint = _collection(project)
    embedder = BlockingEmbedder()
    api.app.state.semantic_embedder = embedder

    def semantic_search():
        return api.get(endpoint, params={"q": "semantic race", "semantic": "true"})

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_search = executor.submit(semantic_search)
        try:
            assert embedder.first_batch_started.wait(timeout=5)
            updated = api.patch(
                f"{endpoint}/{work_item['id']}",
                json={
                    "expected_version": work_item["version"],
                    "title": "Title after concurrent commit",
                    "actor": {
                        "actor_client": "pytest",
                        "actor_session_id": "phase9-semantic-cache-race",
                    },
                    "client_operation_id": str(uuid4()),
                },
            )
            assert updated.status_code == 200, updated.text
        finally:
            embedder.release_first_batch.set()
        first_response = first_search.result(timeout=10)

    assert first_response.status_code == 200, first_response.text
    with postgres_engine.connect() as connection:
        cached = connection.scalar(
            text(
                "SELECT count(*) FROM work_item_embeddings "
                "WHERE work_item_id = :work_item_id"
            ),
            {"work_item_id": UUID(work_item["id"])},
        )
    assert cached == 0

    second_response = semantic_search()
    assert second_response.status_code == 200, second_response.text
    assert len(embedder.document_batches) == 2
    assert "Title after concurrent commit" in embedder.document_batches[1][0]

    third_response = semantic_search()
    assert third_response.status_code == 200, third_response.text
    assert len(embedder.document_batches) == 2


def test_merge_rejects_mixed_case_operation_id_in_durable_text_before_writes(
    api,
    project,
    work_payload,
    postgres_engine,
) -> None:
    source = _create_work(api, project, work_payload, "Potential duplicate")
    destination = _create_work(api, project, work_payload, "Canonical destination")
    source_context = _context(api, project, source)
    destination_context = _context(api, project, destination)
    operation_id = uuid4()
    operation_spelling = str(operation_id).upper()
    baseline = _authoritative_counts(postgres_engine)

    response = api.post(
        f"{_collection(project)}/{source['id']}/merge",
        json={
            "destination_work_item_id": destination["id"],
            "reviewed_source_revision": source_context["merge_review_revision"],
            "reviewed_destination_revision": destination_context["merge_review_revision"],
            "rationale": f"Reviewed duplicate evidence containing {operation_spelling} inline.",
            "merged_by_client": "pytest",
            "merged_by_session_id": "phase9-secret-rejection",
            "merged_by_model": "test-model",
            "client_operation_id": str(operation_id),
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "client_operation_secret_echo"
    assert str(operation_id) not in response.text.casefold()
    assert _authoritative_counts(postgres_engine) == baseline
