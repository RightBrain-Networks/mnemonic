"""Progressive command discovery without unrelated schema/context expansion."""

import copy
import json

import httpx
import pytest
from jsonschema import Draft202012Validator

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.command_help import render_help
from mnemonic_mcp.help_guides import GUIDES
from mnemonic_mcp.help_schema import children, scoped_schema
from mnemonic_mcp.server import build_server


def reject_http(request):
    pytest.fail("Help must not reach the API")


@pytest.fixture
async def catalog(settings):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(reject_http)))
    return {tool.name: tool for tool in await server.list_tools()}


async def test_help_is_local_read_only_and_returns_one_plain_text_page(settings):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(reject_http)))
    tools = {tool.name: tool for tool in await server.list_tools()}
    tool = tools["help"]
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.openWorldHint is False
    assert tool.outputSchema is None  # Avoid a duplicated structured/text payload.
    assert set(tool.inputSchema["properties"]) == {"topic"}
    assert tool.inputSchema.get("required", []) == []
    content = await server.call_tool("help", {})
    assert len(content) == 1
    assert content[0].type == "text"
    assert content[0].text == render_help("", tools)


def test_index_lists_every_command_and_gives_exact_navigation(catalog):
    page = render_help("", catalog)
    assert len(catalog) == 57
    assert catalog.keys() == GUIDES.keys()
    for name in catalog:
        assert name in page
    assert 'help({"topic":"<command>"})' in page
    assert "usage" in page and "schema" in page
    assert len(page) < 2200


def test_every_command_has_compact_overview_and_workflow(catalog):
    for name, tool in catalog.items():
        overview = render_help(name, catalog)
        usage = render_help(name + " usage", catalog)
        for field in tool.inputSchema.get("properties", {}):
            assert field in overview, (name, field)
        assert f'help({{"topic":"{name} schema"}})' in overview
        assert f'help({{"topic":"{name} usage"}})' in overview
        assert len(overview) < 1800, name
        assert len(usage) < 1100, name
        assert '"$defs"' not in overview + usage
        if "client_operation_id" in tool.inputSchema.get("required", []):
            assert "Freeze every argument" in usage


def test_complete_work_pages_cover_shapes_and_semantic_rules(catalog):
    overview = render_help("complete_work", catalog)
    assert "Fresh calls also require job_completion_report and explicit subagent_transcripts" in overview
    checkpoint = render_help("complete_work checkpoint", catalog)
    assert "prompt: string; source_client: string; source_session_id: string" in checkpoint
    assert "affected_paths requires verified_against" in checkpoint
    assert 'help({"topic":"complete_work"})' in checkpoint
    evidence = render_help("complete_work completion_evidence", catalog)
    assert "omit rather than null" in evidence
    assert "verification_results" in evidence and "artifact_references" in evidence
    assert "source_metadata" not in evidence
    artifacts = render_help("complete_work completion_evidence artifact_references", catalog)
    assert "Fields below describe each array item" in artifacts
    assert "artifact_type" in artifacts and "label" in artifacts and "reference" in artifacts
    assert "kind is not accepted" in artifacts
    verification = render_help("complete_work completion_evidence verification_results", catalog)
    assert "Choose verification_type: command, observation" in verification
    command = render_help("complete_work completion_evidence verification_results command", catalog)
    assert 'verification_type: "command"' in command
    assert "passed requires 0; failed requires nonzero; inconclusive must omit exit_code" in command
    observation = render_help("complete_work completion_evidence verification_results observation", catalog)
    assert 'verification_type: "observation"' in observation
    assert "exit_code" not in observation
    assert len(artifacts + verification + command) < 2200


def test_field_help_includes_exact_constraints_and_accepts_dotted_paths(catalog):
    page = render_help("complete_work checkpoint prompt", catalog)
    assert "Default: 20" in render_help("search limit", catalog)
    assert "Default: false" in render_help("search fulltext", catalog)
    assert "maxLength=100000" in page
    assert page == render_help("complete_work checkpoint.prompt", catalog)
    page = render_help("complete_work completion_evidence.artifact_references[].artifact_type", catalog)
    assert "allowed=" in page
    assert "build_artifact" in page  # All enums remain discoverable beyond the overview's preview.
    assert page == render_help("complete_work completion_evidence artifact_references artifact_type", catalog)


def test_every_field_and_discriminator_is_navigable(catalog):
    for name, tool in catalog.items():
        pending = [((), tool.inputSchema)]
        visited = set()
        while pending:
            path, node = pending.pop()
            topic = " ".join((name, *path))
            page = render_help(topic, catalog)
            assert "Unknown child topic" not in page, topic
            assert len(page) < 2000, topic
            if repr(node) in visited:
                continue
            visited.add(repr(node))
            for field, child in children(node, tool.inputSchema).items():
                pending.append(((*path, field), child))


def test_explicit_full_schema_is_the_exact_registered_contract(catalog):
    for name, tool in catalog.items():
        schema = json.loads(render_help(name + " schema", catalog))
        assert schema == tool.inputSchema
        Draft202012Validator.check_schema(schema)


def test_field_schema_contains_only_its_transitive_definitions(catalog):
    schema = json.loads(render_help("complete_work completion_evidence schema", catalog))
    assert "CheckpointInput" not in schema["$defs"]
    validator = Draft202012Validator(schema)
    validator.validate({"verification_results": [{
        "verification_type": "command", "name": "Tests", "command": "pytest",
        "outcome": "passed", "summary": "Passed", "exit_code": 0,
    }], "artifact_references": [{"artifact_type": "commit", "label": "Change", "reference": "a" * 40}]})
    assert not validator.is_valid({"verification_results": [{
        "verification_type": "command", "name": "Tests", "command": "pytest",
        "outcome": "passed", "summary": "Passed",
    }]})
    validator.validate({"verification_results": [{
        "verification_type": "observation", "name": "Review", "outcome": "passed", "summary": "Read",
    }]})
    assert not validator.is_valid(None)
    leaf = json.loads(render_help("complete_work checkpoint prompt schema", catalog))
    assert leaf["maxLength"] == 100000
    assert "$defs" not in leaf


@pytest.mark.parametrize("topic", [
    "PRIVATE_UNKNOWN_COMMAND", "complete_work PRIVATE_UNKNOWN_CHILD",
    "complete_work checkpoint PRIVATE_UNKNOWN_CHILD schema", "complete_work " + "private " * 13,
])
def test_unknown_topic_is_bounded_value_free_and_points_back(catalog, topic):
    page = render_help(topic, catalog)
    assert "PRIVATE" not in page
    assert "private" not in page
    assert "help(" in page
    assert len(page) < 1800


def test_scoped_schema_preserves_recursive_refs_literals_and_long_patterns():
    root = {"$defs": {
        "Node": {"anyOf": [{"type": "null"}, {"type": "array", "items": {"$ref": "#/$defs/Node"}}]},
        "Unrelated": {"type": "boolean"},
    }}
    node = {"type": "object", "properties": {
        "title": {"type": "string", "pattern": "a" * 121},
        "description": {"const": {"title": "literal", "default": 1}},
        "children": {"$ref": "#/$defs/Node"},
    }, "dependentSchemas": {"title": {"required": ["description"]}}}
    original = copy.deepcopy(node)
    schema = scoped_schema(node, root)
    assert node == original
    assert schema["properties"] == node["properties"]
    assert set(schema["$defs"]) == {"Node"}
    validator = Draft202012Validator(schema)
    validator.validate({"title": "a" * 121, "description": {"title": "literal", "default": 1},
                        "children": [None, [None]]})
    assert not validator.is_valid({"title": "a" * 121})
