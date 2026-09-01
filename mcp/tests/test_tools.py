import json

import httpx
import pytest
from conftest import (
    API_KEY,
    CHECKPOINT_ID,
    CLAIM_REQUEST_ID,
    EXPIRES_AT,
    LEASE_TOKEN,
    LOCAL_VALIDATION_CASES,
    OTHER_CHECKPOINT_ID,
    OTHER_WORK_ID,
    PROJECT_ID,
    RELATIONSHIP_ID,
    WORK_ID,
    expected_validation_message,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.models import ClaimAndRecall, ClaimReceipt
from mnemonic_mcp.server import build_server


def adapter(settings, handler):
    return build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))


def structured(result):
    # FastMCP 1.x returns content + structured output when called directly.
    if isinstance(result, tuple):
        return result[1]
    return result


def test_claim_receipt_repr_redacts_token_but_serialization_retains_it(
    claim_receipt, active_work_context
):
    receipt = ClaimReceipt.model_validate(claim_receipt)
    combined = ClaimAndRecall.model_validate(
        {"lease": claim_receipt, "context": active_work_context}
    )
    assert LEASE_TOKEN not in repr(receipt)
    assert LEASE_TOKEN not in str(receipt)
    assert LEASE_TOKEN not in repr(combined)
    assert receipt.model_dump(mode="json")["lease_token"] == LEASE_TOKEN
    assert combined.model_dump(mode="json")["lease"]["lease_token"] == LEASE_TOKEN


async def test_tool_catalog_schemas_and_annotations(settings):
    server = build_server(settings)
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == {
        "list_projects",
        "create_project",
        "create_work",
        "search_work",
        "get_work",
        "add_checkpoint",
        "list_checkpoints",
        "recall_work",
        "claim_work",
        "claim_and_recall",
        "renew_claim",
        "release_claim",
        "add_relationship",
        "get_relationship",
        "list_relationships",
        "remove_relationship",
        "update_work",
        "complete_work",
        "delete_work",
    }
    assert all(tool.outputSchema for tool in tools.values())
    assert all(
        tool.inputSchema.get("additionalProperties") is False for tool in tools.values()
    )
    for name in (
        "list_projects",
        "search_work",
        "get_work",
        "list_checkpoints",
        "recall_work",
        "get_relationship",
        "list_relationships",
    ):
        assert tools[name].annotations.readOnlyHint is True
    for name in (
        "create_project",
        "create_work",
        "add_checkpoint",
        "claim_work",
        "claim_and_recall",
        "renew_claim",
        "release_claim",
        "add_relationship",
    ):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is False
    for name in (
        "update_work",
        "complete_work",
        "delete_work",
        "remove_relationship",
    ):
        assert tools[name].annotations.destructiveHint is True
    for name in ("claim_work", "claim_and_recall", "renew_claim"):
        assert tools[name].annotations.idempotentHint is False
    for name in ("release_claim", "add_relationship", "remove_relationship"):
        assert tools[name].annotations.idempotentHint is True
    assert tools["create_project"].outputSchema["additionalProperties"] is False
    project_page_schema = tools["list_projects"].outputSchema
    assert project_page_schema["additionalProperties"] is False
    assert project_page_schema["$defs"]["Project"]["additionalProperties"] is False

    for name in tools.keys() - {"list_projects", "create_project"}:
        assert "project_id" in tools[name].inputSchema["required"]

    claim_fields = {
        "project_id",
        "work_item_id",
        "holder_client",
        "holder_session_id",
        "claim_request_id",
    }
    for name in ("claim_work", "claim_and_recall"):
        assert set(tools[name].inputSchema["required"]) == claim_fields
        properties = tools[name].inputSchema["properties"]
        assert properties["holder_client"]["maxLength"] == 80
        assert properties["holder_session_id"]["maxLength"] == 200
        assert properties["claim_request_id"]["maxLength"] == 200
        assert "lease_token" not in properties

    receipt_fields = {
        "work_item_id",
        "holder_client",
        "holder_session_id",
        "claim_request_id",
        "acquired_at",
        "renewed_at",
        "expires_at",
        "lease_token",
    }
    for name in ("claim_work", "renew_claim"):
        assert set(tools[name].outputSchema["properties"]) == receipt_fields
        assert tools[name].outputSchema["additionalProperties"] is False
    assert set(tools["claim_and_recall"].outputSchema["properties"]) == {
        "lease",
        "context",
    }
    assert tools["claim_and_recall"].outputSchema["additionalProperties"] is False
    assert set(tools["release_claim"].outputSchema["properties"]) == {
        "work_item_id",
        "released",
    }
    assert tools["release_claim"].outputSchema["additionalProperties"] is False

    for name in ("renew_claim", "release_claim"):
        assert set(tools[name].inputSchema["required"]) == {
            "project_id",
            "work_item_id",
            "lease_token",
        }
        token_schema = tools[name].inputSchema["properties"]["lease_token"]
        assert token_schema["minLength"] == 1
        assert token_schema["maxLength"] == 200
        assert token_schema["format"] == "password"
        assert token_schema["writeOnly"] is True

    lease_capable_mutations = {
        "add_checkpoint",
        "update_work",
        "complete_work",
        "delete_work",
    }
    for name in lease_capable_mutations:
        assert "lease_token" in tools[name].inputSchema["properties"]
        assert "lease_token" not in tools[name].inputSchema["required"]

    create_required = tools["create_work"].inputSchema["required"]
    assert {"project_id", "title", "summary", "initial_checkpoint"} <= set(create_required)
    checkpoint_schema = tools["create_work"].inputSchema["$defs"]["CheckpointInput"]
    assert {"prompt", "source_client", "source_session_id"} <= set(
        checkpoint_schema["required"]
    )
    assert checkpoint_schema["additionalProperties"] is False

    relationship_types = {
        "blocks",
        "parent-child",
        "discovered-from",
        "duplicate-of",
        "related",
    }
    initial_relationships_schema = tools["create_work"].inputSchema["properties"][
        "initial_relationships"
    ]
    initial_relationships_array = next(
        option
        for option in initial_relationships_schema["anyOf"]
        if option.get("type") == "array"
    )
    assert initial_relationships_array["maxItems"] == 10
    initial_relationship_schema = tools["create_work"].inputSchema["$defs"][
        "InitialRelationshipInput"
    ]
    assert initial_relationship_schema["additionalProperties"] is False
    assert set(initial_relationship_schema["required"]) == {
        "type",
        "direction",
        "other_work_item_id",
    }
    assert set(initial_relationship_schema["properties"]) == {
        "type",
        "direction",
        "other_work_item_id",
        "context_checkpoint_id",
    }
    assert set(initial_relationship_schema["properties"]["type"]["enum"]) == (
        relationship_types
    )
    assert initial_relationship_schema["properties"]["direction"]["enum"] == [
        "incoming",
        "outgoing",
    ]

    add_relationship_schema = tools["add_relationship"].inputSchema
    assert set(add_relationship_schema["required"]) == {
        "project_id",
        "source_work_item_id",
        "target_work_item_id",
        "relationship_type",
        "created_by_client",
        "created_by_session_id",
    }
    assert set(add_relationship_schema["properties"]["relationship_type"]["enum"]) == (
        relationship_types
    )
    assert "context_checkpoint_work_item_id" not in add_relationship_schema["properties"]
    list_relationship_schema = tools["list_relationships"].inputSchema["properties"]
    assert list_relationship_schema["direction"]["default"] == "both"
    assert list_relationship_schema["direction"]["enum"] == [
        "incoming",
        "outgoing",
        "undirected",
        "both",
    ]
    assert list_relationship_schema["limit"]["default"] == 50
    assert list_relationship_schema["limit"]["maximum"] == 100
    counterpart_schema = json.dumps(tools["list_relationships"].outputSchema)
    assert '"prompt"' not in counterpart_schema
    assert '"source_metadata"' not in counterpart_schema
    assert '"lease_token"' not in counterpart_schema
    assert set(tools["remove_relationship"].outputSchema["properties"]) == {
        "project_id",
        "relationship_id",
        "removed",
    }
    search_work_schema = json.dumps(tools["search_work"].outputSchema)
    assert '"prompt"' not in search_work_schema
    assert '"source_metadata"' not in search_work_schema
    assert '"source_session_url"' not in search_work_schema
    assert tools["search_work"].inputSchema["properties"]["semantic"]["default"] is False
    work_changes_schema = tools["update_work"].inputSchema["$defs"]["WorkChanges"]
    assert work_changes_schema["additionalProperties"] is False
    assert "prompt" not in work_changes_schema["properties"]
    assert "source_session_id" not in work_changes_schema["properties"]
    for name, tool in tools.items():
        if name not in {"claim_work", "claim_and_recall", "renew_claim"}:
            assert '"lease_token"' not in json.dumps(tool.outputSchema)


async def test_strict_tool_models_are_isolated_to_mnemonic(settings):
    await build_server(settings).list_tools()

    vanilla = FastMCP("Vanilla")

    @vanilla.tool()
    async def echo(value: str) -> str:
        return value

    [tool] = await vanilla.list_tools()
    assert "additionalProperties" not in tool.inputSchema
    result = structured(
        await vanilla.call_tool("echo", {"value": "ok", "extra": "ignored"})
    )
    assert result == {"result": "ok"}


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


async def test_project_tools_reject_unplanned_upstream_fields(settings, project):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "items": [{**project, "private_upstream_field": "must not be ignored"}],
                "total": 1,
                "limit": 100,
                "offset": 0,
            },
        )

    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, handler).call_tool("list_projects", {})


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
        assert request.extensions["timeout"]["read"] == 20.0
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


async def test_create_work_serializes_atomic_initial_relationships(
    settings, work_item, checkpoint
):
    checkpoint_input = {
        name: checkpoint[name]
        for name in (
            "prompt",
            "source_client",
            "source_session_id",
            "source_model",
            "source_session_url",
            "repository_branch",
            "verified_against",
            "tags",
            "source_metadata",
        )
    }
    initial_relationship = {
        "type": "discovered-from",
        "direction": "outgoing",
        "other_work_item_id": OTHER_WORK_ID,
        "context_checkpoint_id": OTHER_CHECKPOINT_ID,
    }
    relationship = {
        "id": RELATIONSHIP_ID,
        "project_id": PROJECT_ID,
        "relationship_type": "discovered-from",
        "source_work_item_id": WORK_ID,
        "target_work_item_id": OTHER_WORK_ID,
        "context_checkpoint_work_item_id": OTHER_WORK_ID,
        "context_checkpoint_id": OTHER_CHECKPOINT_ID,
        "created_by_client": checkpoint["source_client"],
        "created_by_session_id": checkpoint["source_session_id"],
        "created_by_model": checkpoint["source_model"],
        "created_at": checkpoint["created_at"],
    }

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items"
        assert json.loads(request.content) == {
            "title": work_item["title"],
            "summary": work_item["summary"],
            "priority": 0,
            "status": "open",
            "initial_checkpoint": checkpoint_input,
            "initial_relationships": [initial_relationship],
        }
        return httpx.Response(
            201,
            json={
                "work_item": work_item,
                "initial_checkpoint": checkpoint,
                "initial_relationships": [relationship],
            },
        )

    result = structured(
        await adapter(settings, handler).call_tool(
            "create_work",
            {
                "project_id": PROJECT_ID,
                "title": work_item["title"],
                "summary": work_item["summary"],
                "initial_checkpoint": checkpoint_input,
                "initial_relationships": [initial_relationship],
            },
        )
    )
    assert result["initial_relationships"] == [relationship]


async def test_relationship_tools_use_exact_rest_contract_and_pointer_only_counterparts(
    settings, relationship, adjacent_relationship, work_context
):
    upstream_adjacency = {
        **adjacent_relationship,
        "counterpart": {
            **adjacent_relationship["counterpart"],
            "prompt": "must not cross the pointer boundary",
            "summary": "must not cross the pointer boundary",
            "source_metadata": {"private": True},
            "source_session_url": "https://example.invalid/private",
        },
    }
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        relationship_path = f"/api/v1/projects/{PROJECT_ID}/relationships"
        if request.method == "POST":
            assert request.url.path == relationship_path
            assert not request.url.params
            assert json.loads(request.content) == {
                "source_work_item_id": OTHER_WORK_ID,
                "target_work_item_id": WORK_ID,
                "relationship_type": "blocks",
                "created_by_client": relationship["created_by_client"],
                "created_by_session_id": relationship["created_by_session_id"],
                "created_by_model": relationship["created_by_model"],
                "context_checkpoint_id": None,
            }
            return httpx.Response(
                200, json={"relationship": relationship, "created": False}
            )
        if request.url.path.endswith(f"/work-items/{WORK_ID}/relationships"):
            assert request.method == "GET"
            assert dict(request.url.params) == {
                "direction": "incoming",
                "limit": "7",
                "offset": "2",
                "type": "blocks",
            }
            return httpx.Response(
                200,
                json={"items": [upstream_adjacency], "total": 3, "limit": 7, "offset": 2},
            )
        if request.url.path.endswith(f"/work-items/{WORK_ID}/context"):
            assert request.method == "GET"
            assert dict(request.url.params) == {"recent_limit": "5"}
            return httpx.Response(
                200,
                json={
                    **work_context,
                    "incoming_relationships": [upstream_adjacency],
                    "relationship_counts": {
                        "incoming": 1,
                        "outgoing": 0,
                        "undirected": 0,
                        "total": 1,
                    },
                },
            )
        assert request.url.path == f"{relationship_path}/{RELATIONSHIP_ID}"
        if request.method == "GET":
            return httpx.Response(200, json=relationship)
        assert request.method == "DELETE"
        return httpx.Response(
            200,
            json={
                "project_id": PROJECT_ID,
                "relationship_id": RELATIONSHIP_ID,
                "removed": True,
            },
        )

    server = adapter(settings, handler)
    created = structured(
        await server.call_tool(
            "add_relationship",
            {
                "project_id": PROJECT_ID,
                "source_work_item_id": OTHER_WORK_ID,
                "target_work_item_id": WORK_ID,
                "relationship_type": "blocks",
                "created_by_client": relationship["created_by_client"],
                "created_by_session_id": relationship["created_by_session_id"],
                "created_by_model": relationship["created_by_model"],
            },
        )
    )
    assert created == {"relationship": relationship, "created": False}
    fetched = structured(
        await server.call_tool(
            "get_relationship",
            {"project_id": PROJECT_ID, "relationship_id": RELATIONSHIP_ID},
        )
    )
    assert fetched == relationship
    listed = structured(
        await server.call_tool(
            "list_relationships",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "direction": "incoming",
                "relationship_type": "blocks",
                "limit": 7,
                "offset": 2,
            },
        )
    )
    assert listed["total"] == 3
    counterpart = listed["items"][0]["counterpart"]
    assert counterpart["id"] == OTHER_WORK_ID
    assert set(counterpart) == {"id", "title", "status", "readiness"}
    recalled = structured(
        await server.call_tool(
            "recall_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
        )
    )
    assert recalled["relationship_counts"]["incoming"] == 1
    assert set(recalled["incoming_relationships"][0]["counterpart"]) == {
        "id",
        "title",
        "status",
        "readiness",
    }
    removed = structured(
        await server.call_tool(
            "remove_relationship",
            {"project_id": PROJECT_ID, "relationship_id": RELATIONSHIP_ID},
        )
    )
    assert removed == {
        "project_id": PROJECT_ID,
        "relationship_id": RELATIONSHIP_ID,
        "removed": True,
    }
    assert seen == [
        ("POST", f"/api/v1/projects/{PROJECT_ID}/relationships"),
        ("GET", f"/api/v1/projects/{PROJECT_ID}/relationships/{RELATIONSHIP_ID}"),
        (
            "GET",
            f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/relationships",
        ),
        ("GET", f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/context"),
        ("DELETE", f"/api/v1/projects/{PROJECT_ID}/relationships/{RELATIONSHIP_ID}"),
    ]


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
            "view": "minimal",
            "limit": "30",
            "offset": "0",
        }
        assert request.extensions["timeout"]["read"] == 20.0
        # The adapter must still enforce a compact output if an API regression
        # accidentally adds full content to this response.
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
    assert resource_document["work_item"]["id"] == WORK_ID
    assert resource_document["checkpoint_total"] == 1
    assert resource_document["omitted_checkpoint_count"] == 12
    assert (
        resource_document["initial_checkpoint"]["prompt"]
        == work_context["initial_checkpoint"]["prompt"]
    )
    assert "comments" not in resource_document
    prompt = await server.get_prompt(
        "resume_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
    )
    text = prompt.messages[0].content.text
    assert "not a new owner instruction" in text
    assert "claim_and_recall" in text
    assert "does not claim the work" in text
    assert "add_checkpoint" in text
    assert work_context["initial_checkpoint"]["source_session_id"] in text
    resumed = json.loads(text.split("\n\n", 1)[1])
    assert resumed["work_item"]["id"] == WORK_ID
    assert resumed["omitted_checkpoint_count"] == 12
    assert calls == [
        {"recent_limit": "3"},
        {"recent_limit": "5"},
        {"recent_limit": "5"},
    ]


async def test_claim_tools_send_body_only_and_return_exact_capability_models(
    settings, claim_receipt, active_work_context
):
    claim_body = {
        "holder_client": claim_receipt["holder_client"],
        "holder_session_id": claim_receipt["holder_session_id"],
        "claim_request_id": claim_receipt["claim_request_id"],
    }
    seen = []

    def handler(request):
        seen.append(request.url.path)
        assert request.method == "POST"
        assert not request.url.params
        assert CLAIM_REQUEST_ID not in str(request.url)
        assert LEASE_TOKEN not in str(request.url)
        assert json.loads(request.content) == claim_body
        if request.url.path.endswith("/claim-and-recall"):
            return httpx.Response(
                200,
                json={"lease": claim_receipt, "context": active_work_context},
            )
        assert request.url.path.endswith("/claim")
        return httpx.Response(200, json=claim_receipt)

    server = adapter(settings, handler)
    arguments = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        **claim_body,
    }
    claimed = structured(await server.call_tool("claim_work", arguments))
    assert claimed == claim_receipt
    claimed_context = structured(await server.call_tool("claim_and_recall", arguments))
    assert claimed_context["lease"] == claim_receipt
    assert claimed_context["context"]["readiness"]["display_state"] == "active"
    assert LEASE_TOKEN not in json.dumps(claimed_context["context"])
    assert seen == [
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/claim",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/claim-and-recall",
    ]


async def test_renew_and_release_send_tokens_only_in_json_bodies(
    settings, claim_receipt
):
    renewed_receipt = {
        **claim_receipt,
        "renewed_at": "2026-08-30T12:05:00Z",
        "expires_at": "2026-08-30T12:20:00Z",
    }
    seen = []

    def handler(request):
        seen.append(request.url.path)
        assert request.method == "POST"
        assert not request.url.params
        assert LEASE_TOKEN not in str(request.url)
        assert json.loads(request.content) == {"lease_token": LEASE_TOKEN}
        if request.url.path.endswith("/renew-claim"):
            return httpx.Response(200, json=renewed_receipt)
        assert request.url.path.endswith("/release-claim")
        return httpx.Response(200, json={"work_item_id": WORK_ID, "released": False})

    server = adapter(settings, handler)
    arguments = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "lease_token": LEASE_TOKEN,
    }
    renewed = structured(await server.call_tool("renew_claim", arguments))
    assert renewed == renewed_receipt
    released = structured(await server.call_tool("release_claim", arguments))
    assert released == {"work_item_id": WORK_ID, "released": False}
    assert "lease_token" not in released
    assert seen == [
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/renew-claim",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/release-claim",
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


async def test_canonical_mutations_send_optional_lease_token_only_in_body(
    settings, work_item, checkpoint
):
    checkpoint_input = {
        name: checkpoint[name]
        for name in (
            "prompt",
            "source_client",
            "source_session_id",
            "source_model",
            "source_session_url",
            "repository_branch",
            "verified_against",
            "tags",
            "source_metadata",
        )
    }
    seen = []

    def handler(request):
        seen.append(request.url.path)
        assert LEASE_TOKEN not in str(request.url)
        assert json.loads(request.content)["lease_token"] == LEASE_TOKEN
        if request.url.path.endswith("/checkpoints"):
            return httpx.Response(201, json=checkpoint)
        if request.method == "PATCH":
            return httpx.Response(
                200,
                json={**work_item, "status": "promoted", "version": 4},
            )
        if request.url.path.endswith("/complete"):
            return httpx.Response(
                200,
                json={
                    "work_item": {**work_item, "status": "done", "version": 4},
                    "checkpoint": {**checkpoint, "kind": "completion"},
                },
            )
        assert request.url.path.endswith("/delete")
        return httpx.Response(
            200,
            json={
                "deleted": True,
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "version": 4,
            },
        )

    server = adapter(settings, handler)
    common = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "lease_token": LEASE_TOKEN,
    }
    await server.call_tool(
        "add_checkpoint",
        {**common, "checkpoint": checkpoint_input},
    )
    await server.call_tool(
        "update_work",
        {**common, "expected_version": 3, "changes": {"status": "promoted"}},
    )
    await server.call_tool(
        "complete_work",
        {**common, "expected_version": 3, "checkpoint": checkpoint_input},
    )
    await server.call_tool("delete_work", {**common, "expected_version": 3})
    assert seen == [
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/checkpoints",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/complete",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/delete",
    ]


async def test_search_passes_explicit_filters_and_pagination(settings):
    def handler(request):
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items"
        assert dict(request.url.params) == {
            "status": "all", "tag": "search", "source_client": "opencode",
            "source_session_id": "ses_123/opaque", "view": "full", "limit": "5",
            "offset": "10", "semantic": "true",
        }
        assert request.extensions["timeout"]["read"] == 60.0
        assert request.extensions["timeout"]["connect"] == 5.0
        return httpx.Response(200, json={"items": [], "total": 10, "limit": 5, "offset": 10})

    await adapter(settings, handler).call_tool("search_work", {
        "project_id": PROJECT_ID, "status": "all", "tag": "search", "source_client": "opencode",
        "source_session_id": "ses_123/opaque", "view": "full", "semantic": True,
        "limit": 5, "offset": 10,
    })


async def test_search_defaults_to_the_minimal_view_and_can_opt_up(settings, work_summary):
    """The agent path is cheap by default; view="full" is an explicit opt-in."""
    minimal_item = {
        "work_item": {
            name: work_summary["work_item"][name]
            for name in ("id", "title", "status", "priority", "version", "updated_at")
        },
        "checkpoint_count": 1,
        "display_state": "ready",
    }
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.params["view"])
        payload = minimal_item if request.url.params["view"] == "minimal" else work_summary
        return httpx.Response(
            200, json={"items": [payload], "total": 1, "limit": 30, "offset": 0}
        )

    server = adapter(settings, handler)
    default = structured(await server.call_tool("search_work", {"project_id": PROJECT_ID}))
    item = default["items"][0]
    assert set(item) == {"work_item", "checkpoint_count", "display_state"}
    assert set(item["work_item"]) == {
        "id", "title", "status", "priority", "version", "updated_at"
    }
    assert item["display_state"] == "ready"
    # No summary, no current-context pointer, no readiness object, no ancestor path.
    assert "summary" not in item["work_item"]
    assert "current_context" not in item
    assert "readiness" not in item
    assert "ancestor_path" not in item

    full = structured(
        await server.call_tool("search_work", {"project_id": PROJECT_ID, "view": "full"})
    )
    assert full["items"][0]["current_context"]["id"] == work_summary["current_context"]["id"]
    assert full["items"][0]["readiness"]["display_state"] == "ready"
    assert seen == ["minimal", "full"]


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
        await adapter(settings, handler).call_tool("search_work", {
            "project_id": PROJECT_ID, "q": "conceptual query", "semantic": True,
        })
    assert API_KEY not in str(caught.value)


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
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "changes": {"source_session_id": "forged-session"},
        },
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "changes": {"summary": None},
        },
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "changes": {"status": "done"},
        },
    ],
)
async def test_update_rejects_empty_and_immutable_fields(settings, arguments):
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
            f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/delete"
        )
        assert json.loads(request.content) == {"expected_version": 3}
        return httpx.Response(409, json={"detail": "internal database version details"})

    with pytest.raises(ToolError, match="Version conflict") as caught:
        await adapter(settings, handler).call_tool("delete_work", {
            "project_id": PROJECT_ID, "work_item_id": WORK_ID, "expected_version": 3,
        })
    assert "internal database" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "code, expected",
    [
        ("version_conflict", "Version conflict"),
        ("work_not_open", "not open"),
        ("work_blocked", "unresolved blocker"),
        ("invalid_status_transition", "lifecycle transition is not allowed"),
        ("lease_expired", "claim has expired"),
        ("lease_token_mismatch", "does not match"),
        ("claim_request_expired", "new claim_request_id"),
        ("relationship_cycle", "create a cycle"),
        ("relationship_context_invalid", "originating target work item"),
        ("relationship_exists", "already exists"),
        ("parent_already_set", "already has a parent"),
        ("active_relationships", "relationships before deleting"),
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


async def test_lease_held_reports_only_allowlisted_holder_and_expiry(settings):
    private_url = "https://internal.invalid/session/private"

    def handler(request):
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "lease_held",
                    "message": f"private {private_url} {LEASE_TOKEN}",
                    "context": {
                        "holder_client": "other-client",
                        "expires_at": EXPIRES_AT,
                        "holder_session_id": "must-not-be-rendered",
                        "lease_token": LEASE_TOKEN,
                        "private_url": private_url,
                    },
                }
            },
        )

    with pytest.raises(ToolError, match="other-client") as caught:
        await adapter(settings, handler).call_tool(
            "claim_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "holder_client": "claude-code",
                "holder_session_id": "claiming-session",
                "claim_request_id": CLAIM_REQUEST_ID,
            },
        )
    message = str(caught.value)
    assert EXPIRES_AT in message
    assert "must-not-be-rendered" not in message
    assert LEASE_TOKEN not in message
    assert private_url not in message


async def test_lease_error_never_echoes_token_or_upstream_url(settings):
    private_url = "http://api:8000/private/lease"

    def handler(request):
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "lease_token_mismatch",
                    "message": f"wrong {LEASE_TOKEN} at {private_url}",
                    "context": {
                        "lease_token": LEASE_TOKEN,
                        "url": private_url,
                    },
                }
            },
        )

    with pytest.raises(ToolError, match="does not match") as caught:
        await adapter(settings, handler).call_tool(
            "renew_claim",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "lease_token": LEASE_TOKEN,
            },
        )
    assert LEASE_TOKEN not in str(caught.value)
    assert private_url not in str(caught.value)


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


@pytest.mark.parametrize("status, expected", [
    (401, "authentication failed"), (403, "authentication failed"),
    (404, "not found in this project"), (500, "could not complete"),
    (503, "could not complete"), (307, "could not complete"),
])
async def test_upstream_errors_are_actionable_and_do_not_leak_details(settings, status, expected):
    def handler(request):
        return httpx.Response(status, json={"detail": f"private URL http://api:8000 and {API_KEY}"})

    with pytest.raises(ToolError, match=expected) as caught:
        await adapter(settings, handler).call_tool(
            "recall_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
        )
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


async def test_relationship_validation_errors_name_only_allowlisted_fields(settings):
    fields = {
        "relationship_id",
        "relationship_type",
        "source_work_item_id",
        "target_work_item_id",
        "other_work_item_id",
        "context_checkpoint_id",
        "initial_relationships",
        "type",
        "direction",
        "created_by_client",
        "created_by_session_id",
        "created_by_model",
    }

    def handler(request):
        return httpx.Response(
            422,
            json={
                "detail": [
                    {"loc": ["body", field], "msg": API_KEY, "input": API_KEY}
                    for field in fields
                ]
            },
        )

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool("list_projects", {})
    message = str(caught.value)
    for field in fields:
        assert field in message
    assert API_KEY not in message


@pytest.mark.parametrize(
    "tool_name,arguments,fields,secrets",
    LOCAL_VALIDATION_CASES,
    ids=[case[0] for case in LOCAL_VALIDATION_CASES],
)
async def test_local_validation_is_strict_and_never_echoes_values(
    settings, tool_name, arguments, fields, secrets
):
    def handler(request):
        pytest.fail("Locally invalid tool input must not cross the HTTP boundary")

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(tool_name, arguments)

    message = str(caught.value)
    assert message == expected_validation_message(fields)
    for secret in secrets:
        assert secret not in message
    assert "input_value" not in message
    assert "input_type" not in message
    assert "errors.pydantic.dev" not in message


@pytest.mark.parametrize("invalid_token", [LEASE_TOKEN + "private-suffix" * 20, 123456789])
async def test_invalid_lease_token_is_redacted_before_the_rest_boundary(
    settings, invalid_token
):
    def handler(request):
        pytest.fail("An invalid capability must not cross the HTTP boundary")

    with pytest.raises(ToolError, match="lease_token") as caught:
        await adapter(settings, handler).call_tool(
            "renew_claim",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "lease_token": invalid_token,
            },
        )
    message = str(caught.value)
    assert str(invalid_token) not in message
    assert "errors.pydantic.dev" not in message


async def test_write_network_failure_explains_unknown_outcome_and_does_not_retry(settings):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout(f"private transport error: {API_KEY}", request=request)

    with pytest.raises(ToolError, match="write outcome is unknown") as caught:
        await adapter(settings, handler).call_tool("create_project", {"name": "Example"})
    assert API_KEY not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize("tool_name", ["claim_work", "claim_and_recall"])
async def test_claim_network_failure_requires_exact_same_request_id(settings, tool_name):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout(
            f"private transport error: {API_KEY} {LEASE_TOKEN}", request=request
        )

    with pytest.raises(ToolError, match="exact same claim_request_id") as caught:
        await adapter(settings, handler).call_tool(
            tool_name,
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "holder_client": "claude-code",
                "holder_session_id": "claiming-session",
                "claim_request_id": CLAIM_REQUEST_ID,
            },
        )
    message = str(caught.value)
    assert "claim outcome is unknown" in message
    assert "search or recall cannot recover the lease token" in message
    assert CLAIM_REQUEST_ID not in message
    assert LEASE_TOKEN not in message
    assert API_KEY not in message
    assert len(requests) == 1


@pytest.mark.parametrize(
    "status, response_body",
    [
        (500, {"detail": f"private {LEASE_TOKEN} at http://api:8000"}),
        (200, {"work_item_id": WORK_ID, "lease_token": LEASE_TOKEN}),
    ],
)
async def test_ambiguous_claim_response_requires_same_request_id(
    settings, status, response_body
):
    def handler(request):
        return httpx.Response(status, json=response_body)

    with pytest.raises(ToolError, match="exact same claim_request_id") as caught:
        await adapter(settings, handler).call_tool(
            "claim_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "holder_client": "claude-code",
                "holder_session_id": "claiming-session",
                "claim_request_id": CLAIM_REQUEST_ID,
            },
        )
    message = str(caught.value)
    assert "claim outcome is unknown" in message
    assert LEASE_TOKEN not in message
    assert "http://api:8000" not in message


@pytest.mark.parametrize("tool_name", ["claim_work", "claim_and_recall"])
@pytest.mark.parametrize("code", ["database_unavailable", "claim_request_expired"])
async def test_structured_503_claim_response_requires_same_request_id(
    settings, tool_name, code
):
    private_url = "https://internal.invalid/private/database"

    def handler(request):
        return httpx.Response(
            503,
            json={
                "detail": {
                    "code": code,
                    "message": f"private {LEASE_TOKEN} at {private_url}",
                    "context": {
                        "lease_token": LEASE_TOKEN,
                        "private_url": private_url,
                    },
                }
            },
        )

    with pytest.raises(ToolError, match="exact same claim_request_id") as caught:
        await adapter(settings, handler).call_tool(
            tool_name,
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "holder_client": "claude-code",
                "holder_session_id": "claiming-session",
                "claim_request_id": CLAIM_REQUEST_ID,
            },
        )
    message = str(caught.value)
    assert "claim outcome is unknown" in message
    assert LEASE_TOKEN not in message
    assert private_url not in message


async def test_invalid_project_id_cannot_alter_request_path(settings):
    def handler(request):
        pytest.fail("Path-like project ID must not reach the REST service")

    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("recall_work", {
            "project_id": "../other-project", "work_item_id": WORK_ID,
        })
