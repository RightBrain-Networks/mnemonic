"""Knowledge tools bind immutable text and implementation provenance to exact requests."""

import hashlib
import json
from uuid import UUID

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, NOW, PROJECT_ID, WORK_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_tools import adapter, structured

from mnemonic_mcp.api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME, MnemonicAPI
from mnemonic_mcp.command_help import render_help
from mnemonic_mcp.server import build_server

DOMAIN_ID = "4be923cc-b558-4447-a8c6-259474973c6b"
DECISION_UUID = "81650308-9840-40c1-9384-5c5c84e1cd36"
SCOPE_UUID = "de9b9890-bb69-4752-bfb7-673eea40885f"
CONTENT = "# Decision\nUse café queues.\n"
WRITES = {
    "create_knowledge_domain", "rename_knowledge_domain", "decision_put", "decision_scope_append",
        "decision_assessment_append", "decision_scope_reconcile",
}


def decision(**changes):
    return {
        "id": DECISION_UUID, "project_id": PROJECT_ID, "domain_id": DOMAIN_ID,
        "domain_name": "Architecture", "prefix": "ADR", "ordinal": 1,
        "decision_id": "ADR-00001", "reference": "ADR-00001-0", "revision": 0,
        "title": "Queue decisions", "state": "accepted", "media_type": "text/markdown",
        "size_bytes": len(CONTENT.encode()), "sha256": hashlib.sha256(CONTENT.encode()).hexdigest(),
        "created_at": NOW, "updated_at": NOW, "author_kind": "agent",
        "agent_session_id": "native-thread", "model_id": "test-model",
        "related_decision_ids": [], "referenced_by": [],
        "related_work_item_ids": [], "related_artifact_ids": [],
        "supersedes": [], "superseded_by": [], "scope_count": 0, **changes,
    }


def arguments():
    return {
        "project_id": PROJECT_ID, "domain_id": DOMAIN_ID, "title": "Queue decisions",
        "content": CONTENT, "media_type": "text/markdown", "state": "accepted",
        "agent_session_id": "native-thread", "model_id": "test-model",
        "client_operation_id": CLIENT_OPERATION_ID,
    }


def domain(**changes):
    return {
        "id": DOMAIN_ID, "project_id": PROJECT_ID, "name": "Architecture", "prefix": "ADR",
        "version": 1, "created_at": NOW, "updated_at": NOW, "decision_count": 0, **changes,
    }


async def test_put_forwards_utf8_and_exact_receipt_intent(settings):
    seen = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        assert request.method == "PUT"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/knowledge/decisions"
        assert body["content"] == CONTENT
        assert body["state"] == "accepted" and body["author_kind"] == "agent"
        assert body["client_operation_id"] == CLIENT_OPERATION_ID
        return httpx.Response(200, json={"decision": decision(), "created": True})

    result = structured(await adapter(settings, handler).call_tool("decision_put", arguments()))
    assert result["decision"]["reference"] == "ADR-00001-0"
    assert len(seen) == 1


async def test_hash_only_upload_dedup_returns_retained_original_metadata(settings):
    original = decision(title="Original title", state="proposed", agent_session_id="first-author")
    server = adapter(settings, lambda request: httpx.Response(
        200, json={"decision": original, "created": False},
    ))
    result = structured(await server.call_tool("decision_put", arguments()))
    assert result["decision"]["title"] == "Original title" and not result["created"]


async def test_unchanged_update_preserves_revision_and_original_author(settings):
    original = decision(revision=2, reference="ADR-00001-2", agent_session_id="first-author")
    server = adapter(settings, lambda request: httpx.Response(
        200, json={"decision": original, "created": False},
    ))
    result = structured(await server.call_tool("decision_put", arguments() | {
        "decision_id": "ADR-00001", "expected_revision": 2,
    }))
    assert result["decision"]["revision"] == 2 and not result["created"]


@pytest.mark.parametrize("changes", [
    {"state": None}, {"media_type": "application/pdf"}, {"agent_session_id": None},
    {"model_id": None}, {"content": "secret\x00"}, {"content": "secret\ud800"},
    {"content": "é" * 600_000}, {"decision_id": "ADR-00001"},
    {"expected_revision": 0}, {"author_kind": "human"},
])
async def test_put_rejects_invalid_text_state_revision_or_authorship_locally(settings, changes):
    def reject(request):
        pytest.fail("Invalid decision must be rejected before HTTP")

    with pytest.raises(ToolError) as caught:
        await adapter(settings, reject).call_tool("decision_put", arguments() | changes)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("changes", [
    {"project_id": WORK_ID}, {"sha256": "0" * 64}, {"size_bytes": 1},
    {"reference": "ADR-00001-7"}, {"domain_id": WORK_ID}, {"state": "rejected"},
    {"agent_session_id": "different-author"},
])
async def test_put_incoherent_success_is_an_unknown_outcome(settings, changes):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"decision": decision(**changes), "created": True})

    with pytest.raises(ToolError, match="operation may already have committed"):
        await adapter(settings, handler).call_tool("decision_put", arguments())
    assert len(calls) == 1


async def test_historical_get_and_inclusive_line_range_bind_exact_revision(settings):
    def handler(request):
        assert request.url.path.endswith("/knowledge/decisions/ADR-00001-2")
        assert dict(request.url.params) == {"line_start": "2", "line_end": "2"}
        return stream_json(decision(
            revision=2, reference="ADR-00001-2", content="Use café queues.\n",
            total_lines=2, line_start=2, line_end=2,
        ))

    result = structured(await adapter(settings, handler).call_tool("decision_get", {
        "project_id": PROJECT_ID, "decision_id": "ADR-00001-2", "line_start": 2, "line_end": 2,
    }))
    assert result["revision"] == 2 and result["content"] == "Use café queues.\n"


@pytest.mark.parametrize("changes", [{"content": "corrupted"}, {"sha256": "f" * 64}])
async def test_full_text_read_verifies_hash_and_byte_count(settings, changes):
    response = decision(content=CONTENT, total_lines=2, line_start=1, line_end=2) | changes
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(response)).call_tool(
            "decision_get", {"project_id": PROJECT_ID, "decision_id": "ADR-00001"},
        )


async def test_scope_pins_historical_revision_and_encouraged_provenance(settings):
    def handler(request):
        assert request.url.path.endswith("/knowledge/decisions/ADR-00001/scope")
        body = json.loads(request.content)
        assert body == {
            "revision": 0, "commit_hash": "a" * 40, "work_item_id": WORK_ID,
            "agent_session_id": "native-thread", "client_operation_id": CLIENT_OPERATION_ID,
        }
        return httpx.Response(200, json={"scope": {
            "id": SCOPE_UUID, "project_id": PROJECT_ID, "decision_id": "ADR-00001",
            "document_id": DECISION_UUID,
            "revision": 0, "commit_hash": "a" * 40, "work_item_id": WORK_ID,
            "agent_session_id": "native-thread", "created_at": NOW,
        }, "created": True})

    result = structured(await adapter(settings, handler).call_tool("decision_scope_append", {
        "project_id": PROJECT_ID, "decision_id": "ADR-00001", "revision": 0,
        "commit_hash": "A" * 40, "work_item_id": WORK_ID, "agent_session_id": "native-thread",
        "client_operation_id": CLIENT_OPERATION_ID,
    }))
    assert result["scope"]["revision"] == 0


@pytest.mark.parametrize("changes", [{"revision": None}, {"commit_hash": "a" * 7}])
async def test_scope_requires_exact_revision_and_full_commit_hash(settings, changes):
    with pytest.raises(ToolError):
        await adapter(settings, lambda request: pytest.fail("Invalid scope reached HTTP")).call_tool(
            "decision_scope_append", {
                "project_id": PROJECT_ID, "decision_id": "ADR-00001", "revision": 0,
                "commit_hash": "a" * 40, "client_operation_id": CLIENT_OPERATION_ID, **changes,
            },
        )


async def test_domains_uppercase_prefix_and_rename_preserves_identity(settings):
    def handler(request):
        body = json.loads(request.content)
        assert body["client_operation_id"] == CLIENT_OPERATION_ID
        if request.method == "POST":
            assert body["prefix"] == "ADR"
            return httpx.Response(201, json=domain())
        assert body["expected_version"] == 1 and "prefix" not in body
        return httpx.Response(200, json=domain(name="Technical decisions", version=2))

    server = adapter(settings, handler)
    await server.call_tool("create_knowledge_domain", {
        "project_id": PROJECT_ID, "name": "Architecture", "prefix": "adr",
        "client_operation_id": CLIENT_OPERATION_ID,
    })
    result = structured(await server.call_tool("rename_knowledge_domain", {
        "project_id": PROJECT_ID, "domain_id": DOMAIN_ID, "name": "Technical decisions",
        "expected_version": 1, "client_operation_id": CLIENT_OPERATION_ID,
    }))
    assert result["prefix"] == "ADR" and result["version"] == 2


async def test_automatic_uuid_is_retained_on_uncertain_put_without_automatic_retry(settings):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        raise httpx.ReadTimeout("private upstream detail")

    server = adapter(settings, handler)
    args = arguments()
    args.pop("client_operation_id")
    result = await server.call_tool("decision_put", args)
    assert result.isError
    generated = result.meta["mnemonic_generated_ids"]["client_operation_id"]
    assert UUID(generated).version == 4
    assert seen[0]["client_operation_id"] == generated and len(seen) == 1
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in result.content[0].text


async def test_knowledge_help_and_catalog_explain_revision_scope_obligation(settings):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(
        lambda request: pytest.fail("Help must be local"),
    )))
    catalog = {tool.name: tool for tool in await server.list_tools()}
    assert len(catalog) == 74
    for name in WRITES:
        tool = catalog[name]
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.idempotentHint is False
        assert "client_operation_id" not in tool.inputSchema["required"]
        assert "Omit the retry ID" in render_help(name + " usage", catalog)
    assert "job_completion_report.decision_provenance" in render_help("decision_get usage", catalog)
    assert "Reading alone does not imply use" in render_help("decision_get usage", catalog)
    assert "exact revision" in render_help("decision_scope_append usage", catalog)
    assert "domain_ids" in render_help("search filters decisions", catalog)
