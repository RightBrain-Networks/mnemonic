"""Previously incomplete imports and enrolled sessions refresh from retained bytes."""

import json
from dataclasses import replace

import pytest

from mnemonic_api import transcript_normalization as normalization

from .test_leases_postgres import expire_lease
from .test_transcript_imports_postgres import import_folder
from .test_transcript_indexing_postgres import collection, read, register, run
from .test_transcript_upgrades_postgres import refresh

pytestmark = pytest.mark.postgres


def register_runtime_session(api, project, work_payload, tmp_path, postgres_engine, kind):
    if kind == "primary":
        work, _, record, source = register(api, project, work_payload, tmp_path)
        expire_lease(postgres_engine, work["id"])
    else:
        source = tmp_path / "native.jsonl"
        source.write_text(json.dumps({"role": "user", "content": "existing conversation"}) + "\n")
        api.app.state.settings.transcript_allowed_roots = [tmp_path]
        response = import_folder(api, project, tmp_path)
        assert response.status_code == 200, response.text
        record, = api.get(collection(project)).json()["items"]
    rows = [
        {"type": "custom-title", "customTitle": "Example title", "sessionId": "session-one"},
        {"type": "agent-name", "agentName": "Example agent", "sessionId": "session-one"},
        {"type": "attachment", "attachment": {
            "type": "task_status", "description": "restoredtaskneedle", "status": "completed"}},
        {"type": "attachment", "attachment": {
            "type": "thinking_drop", "newlyDropped": {"reason": "restoredthinkingneedle"}}},
    ]
    with source.open("a") as output:
        output.writelines(json.dumps(row) + "\n" for row in rows)
    return record, source


@pytest.mark.parametrize("kind", ["primary", "imported"])
def test_normalizer_three_repairs_runtime_warnings_without_recopy_or_manual_rebuild(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch, kind,
):
    record, source = register_runtime_session(
        api, project, work_payload, tmp_path, postgres_engine, kind)
    normalize = normalization.normalize_transcript
    with monkeypatch.context() as previous:
        previous.setattr(normalization, "NORMALIZER_VERSION", 2)
        previous.setattr(normalization, "BOOKKEEPING_RECORDS",
                         normalization.BOOKKEEPING_RECORDS - {"custom-title", "agent-name"})
        previous.setattr(normalization, "CONTEXT_ATTACHMENTS",
                         normalization.CONTEXT_ATTACHMENTS - {"task_status", "thinking_drop"})
        previous.setattr("mnemonic_api.transcript_indexing.normalize_transcript",
                         lambda *args: replace(normalize(*args), normalizer_version=2))
        assert run(api)
    original = read(api, project, record)
    assert original["normalization_incomplete"] and original["normalizer_version"] == 2
    assert original["metadata"]["transcript:normalization_warnings"] == [
        "unsupported_attachment=2", "unsupported_role=2"]
    query = {"query": "restoredtaskneedle", "fulltext": True}
    assert api.get(collection(project), params=query).json()["total"] == 0
    source.unlink()
    assert refresh(api) == 1 and refresh(api) == 0
    queued = read(api, project, record)
    assert queued["text_sha256"] == original["text_sha256"] and queued["status"] == "ready"
    assert run(api)
    current = read(api, project, record)
    assert current["normalizer_version"] == normalization.NORMALIZER_VERSION
    assert not current["normalization_incomplete"] and current["index_status"] == "ready"
    assert "transcript:normalization_warnings" not in current["metadata"]
    assert current["normalized_revision"] != original["normalized_revision"]
    assert current["sha256"] == original["sha256"] and current["copied_at"] == original["copied_at"]
    assert current["last_updated_at"] == original["last_updated_at"]
    for term in ("restoredtaskneedle", "restoredthinkingneedle"):
        hit, = api.get(collection(project), params={**query, "query": term}).json()["items"]
        assert hit["id"] == record["id"] and hit["content_kind"] == "system_text"
    assert refresh(api) == 0
