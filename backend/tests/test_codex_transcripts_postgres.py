"""Codex primary and child rollouts follow the existing lifecycle and search contracts."""

import hashlib
import json

import pytest

from .report_fixtures import reported
from .test_leases_postgres import create_work, item_path
from .test_transcript_indexing_postgres import collection, run
from .test_transcript_lifecycle_postgres import claim

pytestmark = pytest.mark.postgres


def rollout(path, session, text, *, parent=None):
    source = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1}}} \
        if parent else "cli"
    rows = [
        {"type": "session_meta", "payload": {"id": session, "source": source}},
        {"type": "turn_context", "payload": {"model": "synthetic-codex-model"}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": text}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": text}]}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return {"client": "codex", "path": str(path)}


def test_codex_primary_and_subagent_closeout_search_and_rebuild(
    api, project, work_payload, tmp_path,
):
    primary = rollout(tmp_path / "rollout-primary.jsonl", "codex-parent", "primaryquartz")
    child = rollout(tmp_path / "rollout-child.jsonl", "codex-child", "childzircon",
                    parent="codex-parent")
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    lease, _ = claim(api, path, source=primary)
    assert not run(api)
    payload = reported({
        "expected_version": work["version"], "lease_token": lease["lease_token"],
        "checkpoint": {"prompt": "Indexed Codex primary and child rollouts.",
                       "source_client": "claude-code", "source_session_id": "transcript-test"},
        "subagent_transcripts": [child],
    })
    completed = api.post(path + "/complete", json=payload)
    assert completed.status_code == 200, completed.text
    assert api.post(path + "/complete", json=payload).json() == completed.json()
    assert run(api) and run(api)
    assert not run(api)
    entries = api.get(collection(project)).json()["items"]
    assert {entry["kind"] for entry in entries} == {"primary", "subagent"}
    for entry in entries:
        assert entry["client"] == "codex"
        assert entry["status"] == "ready"
        assert entry["format"] == "codex-jsonl"
        needle = "primaryquartz" if entry["kind"] == "primary" else "childzircon"
        content = api.get(collection(project) + "/" + entry["id"] + "/content",
                          params={"expected_sha256": entry["text_sha256"]})
        assert content.status_code == 200
        assert content.text.count(needle) == 1
        assert hashlib.sha256(content.content).hexdigest() == entry["text_sha256"]
        search = f"/api/v1/projects/{project['id']}/search"
        request = {"q": needle, "facets": ["transcripts"], "fulltext": True, "filters": {
            "transcripts": {"client": "codex"}}}
        result = api.post(search, json=request)
        assert result.status_code == 200, result.text
        assert result.json()["total"] == 1
        request["fulltext"] = False
        assert api.post(search, json=request).json()["total"] == 0
    rebuilt = api.post(collection(project) + "/rebuild", json={
        "client_operation_id": payload["client_operation_id"]})
    assert rebuilt.status_code == 200, rebuilt.text
    assert run(api) and run(api)
    after = api.get(collection(project)).json()["items"]
    assert {row["id"]: row["text_sha256"] for row in after} == {
        row["id"]: row["text_sha256"] for row in entries}
