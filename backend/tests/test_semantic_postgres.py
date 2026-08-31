import pytest

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
    response = api.post(
        f"/api/v1/projects/{project['id']}/handoffs", json={**payload, **changes}
    )
    assert response.status_code == 201, response.text
    return response.json()


def path(project):
    return f"/api/v1/projects/{project['id']}/handoffs"


def test_semantic_search_finds_dense_only_matches_and_reuses_digest_cache(
    api, project, handoff_payload
):
    target = save(
        api,
        project,
        handoff_payload,
        title="Relational durability",
        summary="Keep records available across process lifetimes.",
        prompt="[dense-target] Commit records transactionally.",
    )
    save(
        api,
        project,
        handoff_payload,
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
    assert body["items"][0]["id"] == target["id"]
    assert "prompt" not in body["items"][0]
    assert [len(batch) for batch in embedder.document_batches] == [2]

    api.get(path(project), params={"q": query, "semantic": "true"})
    assert [len(batch) for batch in embedder.document_batches] == [2]

    updated = api.patch(
        f"{path(project)}/{target['id']}",
        json={
            "expected_version": target["version"],
            "prompt": "[dense-target] Changed canonical text.",
        },
    )
    assert updated.status_code == 200
    api.get(path(project), params={"q": query, "semantic": "true"})
    assert [len(batch) for batch in embedder.document_batches] == [2, 1]
    comment = api.post(
        f"{path(project)}/{target['id']}/comments",
        json={
            "body": "[dense-target] Verified the durable progress path.",
            "source_client": "claude-code",
            "source_session_id": "semantic-comment-session",
        },
    )
    assert comment.status_code == 201
    api.get(path(project), params={"q": query, "semantic": "true"})
    assert [len(batch) for batch in embedder.document_batches] == [2, 1, 1]
    assert (
        "[dense-target] Verified the durable progress path."
        in embedder.document_batches[-1][0]
    )


def test_semantic_search_preserves_strong_lexical_results(api, project, handoff_payload):
    save(
        api,
        project,
        handoff_payload,
        title="Dense neighbor",
        summary="Conceptually close but no exact term.",
        prompt="[dense-target] Similar meaning.",
    )
    lexical = save(
        api,
        project,
        handoff_payload,
        title="Needle appears here",
        summary="Exact vocabulary remains important.",
        prompt="Ordinary lexical record.",
    )
    api.app.state.semantic_embedder = DeterministicEmbedder()
    result = api.get(path(project), params={"q": "needle", "semantic": "true"})
    assert result.status_code == 200
    assert result.json()["items"][0]["id"] == lexical["id"]


def test_semantic_failure_is_explicit_and_lexical_search_still_works(
    api, project, handoff_payload
):
    saved = save(api, project, handoff_payload, title="Lexical fallback")
    api.app.state.semantic_embedder = FailingEmbedder()
    failed = api.get(path(project), params={"q": "fallback", "semantic": "true"})
    assert failed.status_code == 503
    assert "Turn it off" in failed.json()["detail"]
    ordinary = api.get(path(project), params={"q": "fallback"})
    assert ordinary.status_code == 200
    assert ordinary.json()["items"][0]["id"] == saved["id"]
