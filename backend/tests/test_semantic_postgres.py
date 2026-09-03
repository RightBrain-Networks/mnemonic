from time import monotonic
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.models import WorkItemEmbedding
from mnemonic_api.semantic import BGE_QUERY_PREFIX

pytestmark = pytest.mark.postgres


class DeterministicEmbedder:
    def __init__(self) -> None:
        self.document_batches: list[list[str]] = []

    def embed_documents(self, texts):
        batch = list(texts)
        self.document_batches.append(batch)
        return [[1.0, 0.0] if "[dense-target]" in text else [0.0, 1.0] for text in batch]

    def embed_query(self, text):
        assert text.startswith(BGE_QUERY_PREFIX)
        return [1.0, 0.0]


class FailingEmbedder:
    def embed_documents(self, texts):
        raise RuntimeError("model unavailable")

    def embed_query(self, text):
        raise RuntimeError("model unavailable")


def save(api, project, payload, **changes):
    initial_changes = {
        field: changes.pop(field)
        for field in ["prompt", "source_session_id"]
        if field in changes
    }
    body = {
        **payload,
        **changes,
        "initial_checkpoint": {**payload["initial_checkpoint"], **initial_changes},
    }
    response = api.post(f"/api/v1/projects/{project['id']}/work-items", json=body)
    assert response.status_code == 201, response.text
    return response.json()["work_item"]


def path(project):
    return f"/api/v1/projects/{project['id']}/work-items"


def test_semantic_search_finds_dense_only_matches_and_reuses_digest_cache(
    api, project, work_payload
):
    target = save(
        api,
        project,
        work_payload,
        title="Relational durability",
        summary="Keep records available across process lifetimes.",
        prompt="[dense-target] Commit records transactionally.",
    )
    save(
        api,
        project,
        work_payload,
        title="Rendering cleanup",
        summary="Tidy the dashboard spacing.",
        prompt="Adjust card layout and colors.",
    )
    query = "protect facts after reboot"
    assert api.get(path(project), params={"q": query}).json()["total"] == 0

    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    result = api.get(path(project), params={"q": query, "semantic": "true"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["total"] == 2
    assert body["items"][0]["summary"]["work_item"]["id"] == target["id"]
    assert "prompt" not in body["items"][0]["summary"]["current_context"]
    assert [len(batch) for batch in embedder.document_batches] == [2]

    api.get(path(project), params={"q": query, "semantic": "true"})
    assert [len(batch) for batch in embedder.document_batches] == [2]

    updated = api.post(
        f"{path(project)}/{target['id']}/checkpoints",
        json={
            "kind": "context",
            "prompt": "[dense-target] Changed canonical text.",
            "source_client": "claude-code",
            "source_session_id": "semantic-context-session",
        },
    )
    assert updated.status_code == 201
    api.get(path(project), params={"q": query, "semantic": "true"})
    assert [len(batch) for batch in embedder.document_batches] == [2, 1]
    checkpoint = api.post(
        f"{path(project)}/{target['id']}/checkpoints",
        json={
            "kind": "progress",
            "prompt": "[dense-target] Verified the durable progress path.",
            "source_client": "claude-code",
            "source_session_id": "semantic-comment-session",
        },
    )
    assert checkpoint.status_code == 201
    api.get(path(project), params={"q": query, "semantic": "true"})
    assert [len(batch) for batch in embedder.document_batches] == [2, 1, 1]
    assert (
        "[dense-target] Verified the durable progress path."
        in embedder.document_batches[-1][0]
    )


def test_semantic_checkpoint_tail_keeps_exact_bounded_recent_text(
    api, project, work_payload
):
    target = save(
        api,
        project,
        work_payload,
        title="Bounded semantic composition",
        summary="Capture only the exact recent checkpoint tail.",
        prompt="Initial bounded semantic context.",
    )
    later_prompts = [character * 1_000 for character in ("A", "B", "C")]
    for index, prompt in enumerate(later_prompts):
        response = api.post(
            f"{path(project)}/{target['id']}/checkpoints",
            json={
                "kind": "progress",
                "prompt": prompt,
                "source_client": "semantic-tail-test",
                "source_session_id": f"semantic-tail-{index}",
            },
        )
        assert response.status_code == 201, response.text

    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    result = api.get(
        path(project),
        params={"q": "bounded composition", "semantic": "true"},
    )

    assert result.status_code == 200, result.text
    expected_tail = "\n".join(later_prompts)[-1_500:]
    assert embedder.document_batches[0][0].endswith("\n" + expected_tail)
    assert "A" not in expected_tail


def test_semantic_cache_lock_wait_is_bounded_and_does_not_discard_ranking(
    api, project, work_payload, postgres_engine
):
    target = save(
        api,
        project,
        work_payload,
        title="Semantic cache contention",
        summary="Keep a ranked result when derived cache persistence is busy.",
        prompt="[dense-target] Original semantic cache input.",
    )
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    query = "semantic cache contention"
    initial = api.get(path(project), params={"q": query, "semantic": "true"})
    assert initial.status_code == 200, initial.text

    changed = api.post(
        f"{path(project)}/{target['id']}/checkpoints",
        json={
            "kind": "progress",
            "prompt": "[dense-target] Changed while the cache row is retained.",
            "source_client": "semantic-cache-test",
            "source_session_id": "semantic-cache-change",
        },
    )
    assert changed.status_code == 201, changed.text

    with Session(postgres_engine) as locker:
        cache_row = locker.scalar(
            select(WorkItemEmbedding)
            .where(WorkItemEmbedding.work_item_id == UUID(target["id"]))
            .with_for_update()
        )
        assert cache_row is not None
        started_at = monotonic()
        contended = api.get(
            path(project), params={"q": query, "semantic": "true"}
        )
        elapsed = monotonic() - started_at

    assert contended.status_code == 200, contended.text
    assert contended.json()["items"][0]["summary"]["work_item"]["id"] == target["id"]
    assert elapsed < 1.0
    assert [len(batch) for batch in embedder.document_batches] == [1, 1]


def test_semantic_search_preserves_strong_lexical_results(api, project, work_payload):
    save(
        api,
        project,
        work_payload,
        title="Dense neighbor",
        summary="Conceptually close but no exact term.",
        prompt="[dense-target] Similar meaning.",
    )
    lexical = save(
        api,
        project,
        work_payload,
        title="Needle appears here",
        summary="Exact vocabulary remains important.",
        prompt="Ordinary lexical record.",
    )
    api.app.state.semantic_embedder = DeterministicEmbedder()
    result = api.get(path(project), params={"q": "needle", "semantic": "true"})
    assert result.status_code == 200
    assert result.json()["items"][0]["summary"]["work_item"]["id"] == lexical["id"]


def test_semantic_failure_is_explicit_and_lexical_search_still_works(
    api, project, work_payload
):
    saved = save(api, project, work_payload, title="Lexical fallback")
    api.app.state.semantic_embedder = FailingEmbedder()
    failed = api.get(path(project), params={"q": "fallback", "semantic": "true"})
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "semantic_unavailable"
    assert "Turn it off" in failed.json()["detail"]["message"]
    ordinary = api.get(path(project), params={"q": "fallback"})
    assert ordinary.status_code == 200
    assert ordinary.json()["items"][0]["summary"]["work_item"]["id"] == saved["id"]
