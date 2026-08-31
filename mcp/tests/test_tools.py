import json

import httpx
import pytest
from conftest import API_KEY, CHECKPOINT_ID, HANDOFF_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import build_server


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
        "list_projects", "create_project",
        "create_work", "search_work", "get_work", "add_checkpoint", "list_checkpoints",
        "recall_work", "update_work", "complete_work", "delete_work",
        "save_handoff", "search_handoffs", "recall_handoff", "list_handoff_comments",
        "add_handoff_comment", "complete_handoff", "update_handoff", "delete_handoff",
    }
    assert all(tool.outputSchema for tool in tools.values())
    for name in (
        "list_projects", "search_work", "get_work", "list_checkpoints", "recall_work",
        "search_handoffs", "recall_handoff", "list_handoff_comments",
    ):
        assert tools[name].annotations.readOnlyHint is True
    for name in (
        "create_project", "create_work", "add_checkpoint", "save_handoff",
        "add_handoff_comment",
    ):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is False
    for name in (
        "update_work", "complete_work", "delete_work", "update_handoff",
        "complete_handoff", "delete_handoff",
    ):
        assert tools[name].annotations.destructiveHint is True
    for name in tools.keys() - {"list_projects", "create_project"}:
        assert "project_id" in tools[name].inputSchema["required"]
    create_required = tools["create_work"].inputSchema["required"]
    assert {"project_id", "title", "summary", "initial_checkpoint"} <= set(create_required)
    checkpoint_schema = tools["create_work"].inputSchema["$defs"]["CheckpointInput"]
    assert {"prompt", "source_client", "source_session_id"} <= set(
        checkpoint_schema["required"]
    )
    assert checkpoint_schema["additionalProperties"] is False
    save_required = tools["save_handoff"].inputSchema["required"]
    assert {"source_client", "source_session_id", "prompt", "summary"} <= set(save_required)
    search_work_schema = json.dumps(tools["search_work"].outputSchema)
    assert '"prompt"' not in search_work_schema
    assert '"source_metadata"' not in search_work_schema
    assert '"source_session_url"' not in search_work_schema
    assert tools["search_work"].inputSchema["properties"]["semantic"]["default"] is False
    search_schema = json.dumps(tools["search_handoffs"].outputSchema)
    assert '"prompt"' not in search_schema
    assert '"source_metadata"' not in search_schema
    assert tools["search_handoffs"].inputSchema["properties"]["semantic"]["default"] is False
    work_changes_schema = tools["update_work"].inputSchema["$defs"]["WorkChanges"]
    assert work_changes_schema["additionalProperties"] is False
    assert "prompt" not in work_changes_schema["properties"]
    assert "lease_token" not in json.dumps(tools["update_work"].inputSchema)
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


async def test_create_work_preserves_nested_checkpoint_and_returns_both_records(
    settings, work_item, checkpoint
):
    prompt = "  Historical context.\r\n\nCode: `x = 1`\nUnicode: β / 🔍\n  "
    metadata = {"evidence": [{"path": "src/search.py", "verified": False}], "count": 2}
    checkpoint_input = {
        "prompt": prompt,
        "source_client": "claude-code",
        "source_session_id": checkpoint["source_session_id"],
        "source_model": "test-model",
        "source_session_url": "https://example.invalid/session/opaque",
        "repository_branch": "main",
        "verified_against": "abcdef1",
        "tags": ["search"],
        "source_metadata": metadata,
    }

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items"
        assert json.loads(request.content) == {
            "title": work_item["title"],
            "summary": work_item["summary"],
            "priority": 7,
            "status": "open",
            "initial_checkpoint": checkpoint_input,
        }
        return httpx.Response(
            201,
            json={
                "work_item": work_item,
                "initial_checkpoint": {**checkpoint, **checkpoint_input},
                "initial_relationships": [],
            },
        )

    result = structured(
        await adapter(settings, handler).call_tool(
            "create_work",
            {
                "project_id": PROJECT_ID,
                "title": work_item["title"],
                "summary": work_item["summary"],
                "priority": 7,
                "initial_checkpoint": checkpoint_input,
            },
        )
    )
    assert result["work_item"]["id"] == WORK_ID
    assert result["initial_checkpoint"]["prompt"] == prompt
    assert result["initial_checkpoint"]["source_metadata"] == metadata
    assert result["initial_relationships"] == []


async def test_search_work_is_open_only_and_pointer_only(settings, work_summary):
    upstream_summary = {
        **work_summary,
        "current_context": {
            **work_summary["current_context"],
            "prompt": "must not cross the search boundary",
            "source_metadata": {"private": True},
            "source_session_url": "https://example.invalid/private",
        },
    }

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items"
        assert dict(request.url.params) == {
            "q": "src/search.py",
            "status": "open",
            "limit": "30",
            "offset": "0",
        }
        return httpx.Response(
            200,
            json={"items": [upstream_summary], "total": 1, "limit": 30, "offset": 0},
        )

    result = structured(
        await adapter(settings, handler).call_tool(
            "search_work", {"project_id": PROJECT_ID, "q": "src/search.py"}
        )
    )
    pointer = result["items"][0]["current_context"]
    assert pointer["id"] == CHECKPOINT_ID
    assert "prompt" not in pointer
    assert "source_metadata" not in pointer
    assert "source_session_url" not in pointer


async def test_get_and_update_work_use_identity_endpoint(settings, work_item):
    seen = []

    def handler(request):
        seen.append(request.method)
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}"
        if request.method == "PATCH":
            assert json.loads(request.content) == {
                "expected_version": 3,
                "summary": "Narrowed to UUID punctuation.",
                "priority": 9,
                "status": "promoted",
            }
            return httpx.Response(
                200,
                json={
                    **work_item,
                    "summary": "Narrowed to UUID punctuation.",
                    "priority": 9,
                    "status": "promoted",
                    "version": 4,
                },
            )
        return httpx.Response(200, json=work_item)

    server = adapter(settings, handler)
    recalled = structured(
        await server.call_tool("get_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID})
    )
    assert recalled["version"] == 3
    updated = structured(
        await server.call_tool(
            "update_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "expected_version": 3,
                "changes": {
                    "summary": "Narrowed to UUID punctuation.",
                    "priority": 9,
                    "status": "promoted",
                },
            },
        )
    )
    assert updated["version"] == 4
    assert seen == ["GET", "PATCH"]


async def test_checkpoint_tools_preserve_immutable_context_and_page_history(
    settings, checkpoint
):
    progress = {
        "prompt": "Investigated the race; the focused test now passes.",
        "source_client": "claude-code",
        "source_session_id": "progress-session",
        "source_model": "test-model",
        "source_session_url": None,
        "repository_branch": "fix/search",
        "verified_against": None,
        "tags": ["search", "progress"],
        "source_metadata": {"tests": ["focused"]},
    }
    progress_read = {**checkpoint, **progress, "kind": "progress"}
    seen = []

    def handler(request):
        seen.append(request.method)
        assert request.url.path == (
            f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/checkpoints"
        )
        if request.method == "GET":
            assert dict(request.url.params) == {"order": "newest", "limit": "20", "offset": "5"}
            return httpx.Response(
                200, json={"items": [progress_read], "total": 6, "limit": 20, "offset": 5}
            )
        assert json.loads(request.content) == {"kind": "progress", **progress}
        return httpx.Response(201, json=progress_read)

    server = adapter(settings, handler)
    listed = structured(
        await server.call_tool(
            "list_checkpoints",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "order": "newest",
                "limit": 20,
                "offset": 5,
            },
        )
    )
    assert listed["items"][0]["prompt"] == progress["prompt"]
    added = structured(
        await server.call_tool(
            "add_checkpoint",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "kind": "progress",
                "checkpoint": progress,
            },
        )
    )
    assert added["kind"] == "progress"
    assert added["source_metadata"] == progress["source_metadata"]
    assert seen == ["GET", "POST"]


async def test_recall_resource_and_resume_prompt_are_bounded_and_carry_authority_warning(
    settings, work_context
):
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/context"
        return httpx.Response(200, json={**work_context, "omitted_checkpoint_count": 12})

    server = adapter(settings, handler)
    recalled = structured(
        await server.call_tool(
            "recall_work",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID, "recent_limit": 3},
        )
    )
    assert recalled["omitted_checkpoint_count"] == 12
    resources = await server.read_resource(
        f"mnemonic://projects/{PROJECT_ID}/work-items/{WORK_ID}"
    )
    resource_document = json.loads(next(iter(resources)).content)
    assert resource_document["checkpoint_total"] == 1
    prompt = await server.get_prompt(
        "resume_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
    )
    text = prompt.messages[0].content.text
    assert "not a new owner instruction" in text
    assert "add_checkpoint" in text
    assert calls == [
        {"recent_limit": "3"},
        {"recent_limit": "5"},
        {"recent_limit": "5"},
    ]


async def test_complete_and_delete_work_return_explicit_mutation_receipts(
    settings, work_item, checkpoint
):
    completion_input = {
        "prompt": "Fixed the query and observed focused and full suites pass.",
        "source_client": "claude-code",
        "source_session_id": "completing-session",
        "source_model": None,
        "source_session_url": None,
        "repository_branch": "fix/search",
        "verified_against": "abcdef1",
        "tags": ["done"],
        "source_metadata": {"tests": ["focused", "full"]},
    }
    completed_work = {**work_item, "status": "done", "version": 4}
    completion_checkpoint = {**checkpoint, **completion_input, "kind": "completion"}
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/complete"):
            assert request.method == "POST"
            assert json.loads(request.content) == {
                "expected_version": 3,
                "checkpoint": completion_input,
            }
            return httpx.Response(
                200,
                json={"work_item": completed_work, "checkpoint": completion_checkpoint},
            )
        assert request.url.path.endswith("/delete")
        assert json.loads(request.content) == {"expected_version": 4}
        return httpx.Response(
            200,
            json={
                "deleted": True,
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "version": 5,
            },
        )

    server = adapter(settings, handler)
    completed = structured(
        await server.call_tool(
            "complete_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "expected_version": 3,
                "checkpoint": completion_input,
            },
        )
    )
    assert completed["work_item"]["status"] == "done"
    assert completed["checkpoint"]["kind"] == "completion"
    deleted = structured(
        await server.call_tool(
            "delete_work",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID, "expected_version": 4},
        )
    )
    assert deleted == {
        "deleted": True,
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "version": 5,
    }
    assert seen == [
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/complete",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/delete",
    ]


async def test_save_preserves_prompt_session_and_metadata(settings, handoff):
    prompt = "  Agent-authored proposal.\r\n\nCode: `x = 1`\nUnicode: β / 🔍\n  "
    metadata = {"evidence": [{"path": "src/search.py", "verified": False}], "count": 2}

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/handoffs"
        data = json.loads(request.content)
        assert request.extensions["timeout"]["read"] == 20.0
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
        assert request.extensions["timeout"]["read"] == 20.0
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
            "semantic": "true",
        }
        assert request.extensions["timeout"]["read"] == 60.0
        assert request.extensions["timeout"]["connect"] == 5.0
        return httpx.Response(200, json={"items": [], "total": 10, "limit": 5, "offset": 10})

    await adapter(settings, handler).call_tool("search_handoffs", {
        "project_id": PROJECT_ID, "status": "all", "tag": "search", "source_client": "opencode",
        "source_session_id": "ses_123/opaque", "semantic": True, "limit": 5, "offset": 10,
    })


async def test_semantic_search_unavailable_suggests_lexical_fallback(settings):
    def handler(request):
        assert request.url.params["semantic"] == "true"
        return httpx.Response(
            503,
            json={
                "detail": {
                    "code": "semantic_unavailable",
                    "message": f"private detail {API_KEY}",
                    "context": {"secret": API_KEY},
                }
            },
        )

    with pytest.raises(ToolError, match="semantic search is unavailable") as caught:
        await adapter(settings, handler).call_tool("search_handoffs", {
            "project_id": PROJECT_ID, "q": "conceptual query", "semantic": True,
        })
    assert API_KEY not in str(caught.value)


async def test_legacy_recall_and_mutable_updates_remain_project_scoped(settings, handoff):
    seen = []

    def handler(request):
        seen.append(request.method)
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/handoffs/{HANDOFF_ID}"
        if request.method == "PATCH":
            assert json.loads(request.content) == {
                "expected_version": 3,
                "summary": "Updated durable identity.",
                "status": "promoted",
            }
            return httpx.Response(
                200,
                json={
                    **handoff,
                    "summary": "Updated durable identity.",
                    "status": "promoted",
                    "version": 4,
                },
            )
        return httpx.Response(200, json=handoff)

    server = adapter(settings, handler)
    recalled = structured(await server.call_tool("recall_handoff", {"project_id": PROJECT_ID, "handoff_id": HANDOFF_ID}))
    assert recalled["prompt"] == handoff["prompt"]
    updated = structured(await server.call_tool("update_handoff", {
        "project_id": PROJECT_ID, "handoff_id": HANDOFF_ID, "expected_version": 3,
        "changes": {"summary": "Updated durable identity.", "status": "promoted"},
    }))
    assert updated["version"] == 4
    assert seen == ["GET", "PATCH"]



async def test_comment_tools_preserve_progress_and_provenance(settings, handoff):
    comment = {
        "id": "20ec4ac9-4ac2-48cd-b0dc-3117b86e22c2",
        "handoff_id": HANDOFF_ID,
        "body": "Investigated the race; the focused test now passes.",
        "kind": "comment",
        "source_client": "claude-code",
        "source_session_id": "progress-session",
        "source_model": "test-model",
        "created_at": handoff["created_at"],
    }
    seen = []

    def handler(request):
        seen.append(request.method)
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/handoffs/{HANDOFF_ID}/comments"
        if request.method == "GET":
            assert dict(request.url.params) == {"limit": "20", "offset": "5"}
            return httpx.Response(
                200, json={"items": [comment], "total": 6, "limit": 20, "offset": 5}
            )
        assert json.loads(request.content) == {
            "body": comment["body"],
            "source_client": "claude-code",
            "source_session_id": "progress-session",
            "source_model": "test-model",
        }
        return httpx.Response(201, json=comment)

    server = adapter(settings, handler)
    listed = structured(await server.call_tool("list_handoff_comments", {
        "project_id": PROJECT_ID, "handoff_id": HANDOFF_ID, "limit": 20, "offset": 5,
    }))
    assert listed["items"][0]["body"] == comment["body"]
    added = structured(await server.call_tool("add_handoff_comment", {
        "project_id": PROJECT_ID,
        "handoff_id": HANDOFF_ID,
        "body": comment["body"],
        "source_client": "claude-code",
        "source_session_id": "progress-session",
        "source_model": "test-model",
    }))
    assert added["kind"] == "comment"
    assert seen == ["GET", "POST"]


async def test_complete_handoff_sends_summary_and_returns_both_records(settings, handoff):
    summary = "Implemented the fix and observed the focused and full suites pass."
    comment = {
        "id": "20ec4ac9-4ac2-48cd-b0dc-3117b86e22c2",
        "handoff_id": HANDOFF_ID,
        "body": summary,
        "kind": "work-summary",
        "source_client": "claude-code",
        "source_session_id": "completing-session",
        "source_model": None,
        "created_at": handoff["created_at"],
    }

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/handoffs/{HANDOFF_ID}/complete"
        assert json.loads(request.content) == {
            "expected_version": 3,
            "summary": summary,
            "source_client": "claude-code",
            "source_session_id": "completing-session",
            "source_model": None,
        }
        return httpx.Response(
            200, json={"handoff": {**handoff, "status": "done", "version": 4}, "comment": comment}
        )

    completed = structured(await adapter(settings, handler).call_tool("complete_handoff", {
        "project_id": PROJECT_ID,
        "handoff_id": HANDOFF_ID,
        "expected_version": 3,
        "summary": summary,
        "source_client": "claude-code",
        "source_session_id": "completing-session",
    }))
    assert completed["handoff"]["status"] == "done"
    assert completed["comment"]["body"] == summary


@pytest.mark.parametrize("changes", [{}, {"source_session_id": "forged-session"}, {"prompt": None}, {"status": "done"}])
async def test_invalid_update_never_reaches_api(settings, changes):
    def handler(request):
        pytest.fail("Invalid or immutable changes must not cross the HTTP boundary")

    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("update_handoff", {
            "project_id": PROJECT_ID, "handoff_id": HANDOFF_ID,
            "expected_version": 3, "changes": changes,
        })


@pytest.mark.parametrize(
    "arguments",
    [
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "changes": {},
        },
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "changes": {"prompt": "rewrite history"},
        },
    ],
)
async def test_phase_one_update_rejects_empty_and_immutable_fields(settings, arguments):
    def handler(request):
        pytest.fail("Invalid or immutable fields must not cross the HTTP boundary")

    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("update_work", arguments)


async def test_delete_passes_version_and_conflict_is_not_retried(settings):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == (
            f"/api/v1/projects/{PROJECT_ID}/work-items/{HANDOFF_ID}/delete"
        )
        assert json.loads(request.content) == {"expected_version": 3}
        return httpx.Response(409, json={"detail": "internal database version details"})

    with pytest.raises(ToolError, match="Version conflict") as caught:
        await adapter(settings, handler).call_tool("delete_handoff", {
            "project_id": PROJECT_ID, "handoff_id": HANDOFF_ID, "expected_version": 3,
        })
    assert "internal database" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "code, expected",
    [
        ("version_conflict", "Version conflict"),
        ("work_not_open", "not open"),
        ("work_blocked", "unresolved blocker"),
    ],
)
async def test_typed_application_errors_are_actionable_and_sanitized(
    settings, code, expected
):
    def handler(request):
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": code,
                    "message": f"private {API_KEY}",
                    "context": {"secret": API_KEY},
                }
            },
        )

    with pytest.raises(ToolError, match=expected) as caught:
        await adapter(settings, handler).call_tool(
            "delete_work",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID, "expected_version": 3},
        )
    assert API_KEY not in str(caught.value)


async def test_unknown_typed_error_does_not_fall_through_to_legacy_conflict_guess(settings):
    def handler(request):
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "future_private_error",
                    "message": f"private {API_KEY}",
                    "context": {"secret": API_KEY},
                }
            },
        )

    with pytest.raises(ToolError, match="could not complete this operation") as caught:
        await adapter(settings, handler).call_tool(
            "delete_work",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID, "expected_version": 3},
        )
    assert "Version conflict" not in str(caught.value)
    assert API_KEY not in str(caught.value)


async def test_delete_returns_structured_receipt(settings):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "deleted": True,
                "project_id": PROJECT_ID,
                "work_item_id": HANDOFF_ID,
                "version": 4,
            },
        )

    server = adapter(settings, handler)
    receipt = structured(await server.call_tool("delete_handoff", {
        "project_id": PROJECT_ID, "handoff_id": HANDOFF_ID, "expected_version": 3,
    }))
    assert receipt == {"deleted": True, "project_id": PROJECT_ID, "handoff_id": HANDOFF_ID}


async def test_legacy_resource_and_prompt_use_bounded_canonical_context(
    settings, work_context
):
    bounded_context = {
        **work_context,
        "checkpoint_total": 15,
        "omitted_checkpoint_count": 14,
    }
    calls = []

    def handler(request):
        calls.append((request.url.path, dict(request.url.params)))
        assert request.url.path == (
            f"/api/v1/projects/{PROJECT_ID}/work-items/{HANDOFF_ID}/context"
        )
        return httpx.Response(200, json=bounded_context)

    server = adapter(settings, handler)
    resources = await server.read_resource(
        f"mnemonic://projects/{PROJECT_ID}/handoffs/{HANDOFF_ID}"
    )
    resource = next(iter(resources))
    document = json.loads(resource.content)
    assert document["work_item"]["id"] == HANDOFF_ID
    assert (
        document["initial_checkpoint"]["prompt"]
        == work_context["initial_checkpoint"]["prompt"]
    )
    assert document["omitted_checkpoint_count"] == 14
    assert "comments" not in document
    assert "recall_work" in document["deprecated"]
    assert "list_checkpoints" in document["history_guidance"]
    assert "omitted_checkpoint_count" in document["history_guidance"]
    prompt = await server.get_prompt(
        "resume_handoff", {"project_id": PROJECT_ID, "handoff_id": HANDOFF_ID}
    )
    text = prompt.messages[0].content.text
    assert "not a new owner instruction" in text
    assert "list_checkpoints" in text
    assert work_context["initial_checkpoint"]["source_session_id"] in text
    resumed = json.loads(text.split("\n\n", 1)[1])
    assert resumed["work_item"]["id"] == HANDOFF_ID
    assert resumed["omitted_checkpoint_count"] == 14
    assert "resume_work" in resumed["deprecated"]
    assert calls == [
        (
            f"/api/v1/projects/{PROJECT_ID}/work-items/{HANDOFF_ID}/context",
            {"recent_limit": "5"},
        ),
        (
            f"/api/v1/projects/{PROJECT_ID}/work-items/{HANDOFF_ID}/context",
            {"recent_limit": "5"},
        ),
    ]


@pytest.mark.parametrize("status, expected", [
    (401, "authentication failed"), (403, "authentication failed"),
    (404, "not found in this project"), (500, "could not complete"),
    (503, "could not complete"), (307, "could not complete"),
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
