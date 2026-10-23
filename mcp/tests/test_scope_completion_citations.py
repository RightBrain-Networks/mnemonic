"""Scope reads retain separate immutable closeout authors when code was linked earlier."""

import pytest
from conftest import NOW, PROJECT_ID, WORK_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_knowledge_impact_tools import candidate, impact
from test_knowledge_tools import DECISION_UUID, SCOPE_UUID
from test_tools import adapter, structured

REPORT_ID = "299e18ce-3bf9-41cb-859b-5505f53a989a"


def completion(**changes):
    return {
        "report_id": REPORT_ID, "work_item_id": WORK_ID, "actor_client": "codex",
        "actor_session_id": "closeout-session", "actor_model": "model", "created_at": NOW,
        **changes,
    }


def scope(**changes):
    return {
        "id": SCOPE_UUID, "project_id": PROJECT_ID, "document_id": DECISION_UUID,
        "decision_id": "ADR-00001", "revision": 0, "commit_hash": "a" * 40,
        "repository": "example/repository", "work_item_id": None,
        "agent_session_id": "merge-session", "created_at": NOW,
        "completion_citations": [completion()],
        **changes,
    }


@pytest.mark.parametrize("tool", ["decision_scope", "decision_commit_lookup", "decision_impact"])
async def test_scope_read_keeps_original_author_and_additional_report_evidence(settings, tool):
    item = scope()
    if tool == "decision_impact":
        for key in ("project_id", "document_id", "decision_id", "created_at"):
            item.pop(key)
        item.update(completion_report_id=None, merged_commit_hashes=[])
        response = impact(items=[candidate(scopes=[item])])
        response["coverage"]["warnings"] = []
    else:
        response = {"items": [item], "total": 1, "offset": 0, "limit": 50}
    arguments = {"project_id": PROJECT_ID}
    if tool == "decision_commit_lookup":
        arguments.update(repository="example/repository", commit_hash="a" * 40)
    else:
        arguments["decision_id"] = "ADR-00001"
    result = structured(await adapter(settings, lambda request: stream_json(response)).call_tool(
        tool, arguments,
    ))
    retained = result["items"][0]
    if tool == "decision_impact":
        retained = retained["scopes"][0]
    assert retained["agent_session_id"] == "merge-session"
    assert retained["completion_citations"] == [completion()]


@pytest.mark.parametrize("change", [
    {"report_id": "not-a-uuid"}, {"work_item_id": None},
    {"actor_session_id": []}, {"created_at": "not-a-timestamp"},
])
async def test_scope_read_rejects_malformed_completion_evidence(settings, change):
    response = {"items": [scope(completion_citations=[completion(**change)])],
                "total": 1, "offset": 0, "limit": 50}
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(response)).call_tool(
            "decision_scope", {"project_id": PROJECT_ID, "decision_id": "ADR-00001"},
        )
