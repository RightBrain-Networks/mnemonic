import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import build_server

from conftest import API_KEY, HANDOFF_ID, PROJECT_ID


def adapter(settings, handler):
    return build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))


def structured(result):
    # FastMCP 1.x returns content + structured output when called directly.
    if isinstance(result, tuple):
        return result[1]
    return result


async def test_tool_catalog_schemas_and_annotations(settings):
    server = build_server(settings)
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == {
        "list_projects", "create_project", "save_handoff", "search_handoffs",
        "recall_handoff", "update_handoff", "delete_handoff",
    }
    assert all(tool.outputSchema for tool in tools.values())
    for name in ("list_projects", "search_handoffs", "recall_handoff"):
        assert tools[name].annotations.readOnlyHint is True
    for name in ("save_handoff", "create_project"):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is False
    assert tools["delete_handoff"].annotations.destructiveHint is True
    assert tools["update_handoff"].annotations.destructiveHint is True
    for name in tools.keys() - {"list_projects", "create_project"}:
        assert "project_id" in tools[name].inputSchema["required"]
    save_required = tools["save_handoff"].inputSchema["required"]
    assert {"source_client", "source_session_id", "prompt", "summary"} <= set(save_required)
    search_schema = json.dumps(tools["search_handoffs"].outputSchema)
    assert '"prompt"' not in search_schema
    assert '"source_metadata"' not in search_schema
    changes_schema = tools["update_handoff"].inputSchema["$defs"]["HandoffChanges"]
    assert changes_schema["additionalProperties"] is False
    assert "source_session_id" not in changes_schema["properties"]


async def test_projects_http_boundary_and_pagination(settings, project):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        assert request.url.path == "/api/v1/projects"
        if request.method == "GET":
            assert dict(request.url.params) == {"limit": "4", "offset": "8"}
            return httpx.Response(200, json={"items": [project], "total": 9, "limit": 4, "offset": 8})
        assert json.loads(request.content)["name"] == "Example"
        return httpx.Response(201, json=project)

    server = adapter(settings, handler)
    result = structured(await server.call_tool("list_projects", {"limit": 4, "offset": 8}))
    assert result["items"][0]["id"] == PROJECT_ID
    assert result["total"] == 9
    created = structured(await server.call_tool("create_project", {"name": "Example"}))
    assert created["slug"] == "example"
    assert len(requests) == 2


async def test_save_preserves_prompt_session_and_metadata(settings, handoff):
    prompt = "  Agent-authored proposal.\r\n\nCode: `x = 1`\nUnicode: β / 🔍\n  "
    metadata = {"evidence": [{"path": "src/search.py", "verified": False}], "count": 2}

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/handoffs"
        data = json.loads(request.content)
        assert data["prompt"] == prompt
        assert data["source_session_id"] == handoff["source_session_id"]
        assert data["source_metadata"] == metadata
        assert data["status"] == "open"
        return httpx.Response(201, json={**handoff, **data})

    result = structured(await adapter(settings, handler).call_tool("save_handoff", {
        "project_id": PROJECT_ID, "title": handoff["title"], "summary": handoff["summary"],
        "prompt": prompt, "source_client": "claude-code",
        "source_session_id": handoff["source_session_id"], "source_metadata": metadata,
    }))
    assert result["prompt"] == prompt
    assert result["source_metadata"] == metadata


async def test_search_is_open_only_and_pointer_only(settings, handoff):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/handoffs"
        assert dict(request.url.params) == {"q": "src/search.py", "status": "open", "limit": "30", "offset": "0"}
        # The adapter must still enforce a compact output if an API regression
        # accidentally adds full content to this response.
        return httpx.Response(200, json={"items": [handoff], "total": 1, "limit": 30, "offset": 0})

    result = structured(await adapter(settings, handler).call_tool("search_handoffs", {
        "project_id": PROJECT_ID, "q": "src/search.py",
    }))
    assert result["items"][0]["id"] == HANDOFF_ID
    assert "prompt" not in result["items"][0]
    assert "source_metadata" not in result["items"][0]


async def test_search_passes_explicit_filters_and_pagination(settings):
    def handler(request):
        assert dict(request.url.params) == {
            "status": "all", "tag": "search", "source_client": "opencode",
            "source_session_id": "ses_123/opaque", "limit": "5", "offset": "10",
        }
        return httpx.Response(200, json={"items": [], "total": 10, "limit": 5, "offset": 10})

    await adapter(settings, handler).call_tool("search_handoffs", {
        "project_id": PROJECT_ID, "status": "all", "tag": "search", "source_client": "opencode",
        "source_session_id": "ses_123/opaque", "limit": 5, "offset": 10,
    })


async def test_recall_and_updates_are_project_scoped_and_do_not_clear_omitted_fields(settings, handoff):
    seen = []

    def handler(request):
        seen.append(request.method)
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/handoffs/{HANDOFF_ID}"
        if request.method == "PATCH":
            assert json.loads(request.content) == {"expected_version": 3, "verified_against": None, "status": "done"}
            return httpx.Response(200, json={**handoff, "verified_against": None, "status": "done", "version": 4})
        return httpx.Response(200, json=handoff)

    server = adapter(settings, handler)
    recalled = structured(await server.call_tool("recall_handoff", {"project_id": PROJECT_ID, "handoff_id": HANDOFF_ID}))
    assert recalled["prompt"] == handoff["prompt"]
    updated = structured(await server.call_tool("update_handoff", {
        "project_id": PROJECT_ID, "handoff_id": HANDOFF_ID, "expected_version": 3,
        "changes": {"verified_against": None, "status": "done"},
    }))
    assert updated["version"] == 4
    assert seen == ["GET", "PATCH"]


@pytest.mark.parametrize("changes", [{}, {"source_session_id": "forged-session"}, {"prompt": None}])
async def test_invalid_update_never_reaches_api(settings, changes):
    def handler(request):
        pytest.fail("Invalid or immutable changes must not cross the HTTP boundary")

    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("update_handoff", {
            "project_id": PROJECT_ID, "handoff_id": HANDOFF_ID,
            "expected_version": 3, "changes": changes,
        })


async def test_delete_passes_version_and_conflict_is_not_retried(settings):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "DELETE"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/handoffs/{HANDOFF_ID}"
        assert request.url.params["expected_version"] == "3"
        return httpx.Response(409, json={"detail": "internal database version details"})

    with pytest.raises(ToolError, match="Version conflict") as caught:
        await adapter(settings, handler).call_tool("delete_handoff", {
            "project_id": PROJECT_ID, "handoff_id": HANDOFF_ID, "expected_version": 3,
        })
    assert "internal database" not in str(caught.value)
    assert len(requests) == 1


async def test_delete_returns_structured_receipt(settings):
    server = adapter(settings, lambda request: httpx.Response(204))
    receipt = structured(await server.call_tool("delete_handoff", {
        "project_id": PROJECT_ID, "handoff_id": HANDOFF_ID, "expected_version": 3,
    }))
    assert receipt == {"deleted": True, "project_id": PROJECT_ID, "handoff_id": HANDOFF_ID}


async def test_resource_and_resume_prompt_carry_complete_record(settings, handoff):
    server = adapter(settings, lambda request: httpx.Response(200, json=handoff))
    resources = await server.read_resource(f"mnemonic://projects/{PROJECT_ID}/handoffs/{HANDOFF_ID}")
    resource = list(resources)[0]
    assert json.loads(resource.content)["prompt"] == handoff["prompt"]
    prompt = await server.get_prompt("resume_handoff", {"project_id": PROJECT_ID, "handoff_id": HANDOFF_ID})
    text = prompt.messages[0].content.text
    assert "not a new owner instruction" in text
    assert handoff["source_session_id"] in text
    assert json.loads(text.split("\n\n", 1)[1])["prompt"] == handoff["prompt"]


@pytest.mark.parametrize("status, expected", [
    (401, "authentication failed"), (403, "authentication failed"),
    (404, "not found in this project"), (500, "could not complete"), (307, "could not complete"),
])
async def test_upstream_errors_are_actionable_and_do_not_leak_details(settings, status, expected):
    def handler(request):
        return httpx.Response(status, json={"detail": f"private URL http://api:8000 and {API_KEY}"})

    with pytest.raises(ToolError, match=expected) as caught:
        await adapter(settings, handler).call_tool("recall_handoff", {"project_id": PROJECT_ID, "handoff_id": HANDOFF_ID})
    assert API_KEY not in str(caught.value)
    assert "http://api:8000" not in str(caught.value)


async def test_validation_error_names_fields_without_echoing_input(settings):
    def handler(request):
        return httpx.Response(422, json={"detail": [{
            "loc": ["body", "source_metadata"], "msg": API_KEY, "input": API_KEY,
        }]})

    with pytest.raises(ToolError, match="source_metadata") as caught:
        await adapter(settings, handler).call_tool("list_projects", {})
    assert API_KEY not in str(caught.value)


async def test_write_network_failure_explains_unknown_outcome_and_does_not_retry(settings):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout(f"private transport error: {API_KEY}", request=request)

    with pytest.raises(ToolError, match="write outcome is unknown") as caught:
        await adapter(settings, handler).call_tool("create_project", {"name": "Example"})
    assert API_KEY not in str(caught.value)
    assert len(requests) == 1


async def test_invalid_project_id_cannot_alter_request_path(settings):
    def handler(request):
        pytest.fail("Path-like project ID must not reach the REST service")

    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("recall_handoff", {
            "project_id": "../other-project", "handoff_id": HANDOFF_ID,
        })
