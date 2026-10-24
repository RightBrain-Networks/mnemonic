"""Opt-in evidence keeps existing request defaults and rejects malformed projections."""

import pytest
from conftest import PROJECT_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_knowledge_impact_tools import ASSESSMENT_ID, assessment, candidate, impact
from test_tools import adapter, structured


def response(tool, evidence):
    row = candidate(**({"citation_excerpts": evidence} if evidence is not None else {}))
    return impact(items=[row]) if tool == "decision_impact" else assessment(
        kind="code-impact", total=1, candidates=[row], dispositions=[],
        disposition_history_total=0, limit=50, offset=0, next_offset=None,
    )


def arguments(tool, requested):
    return {"project_id": PROJECT_ID, "include_citation_excerpts": requested,
            **({"decision_id": "ADR-00001-0"} if tool == "decision_impact"
               else {"assessment_id": ASSESSMENT_ID})}


@pytest.mark.parametrize("tool", ["decision_impact", "decision_assessment_get"])
@pytest.mark.parametrize("requested", [False, True])
async def test_excerpts_are_opt_in_with_strict_response_binding(settings, tool, requested):
    evidence = {"source_sha256": "a" * 64, "excerpts": [], "truncated": False}

    def handler(request):
        assert (request.url.params.get("include_citation_excerpts") == "true") == requested
        return stream_json(response(tool, evidence if requested else None))

    result = structured(await adapter(settings, handler).call_tool(tool, arguments(tool, requested)))
    row = result["items" if tool == "decision_impact" else "candidates"][0]
    assert ("citation_excerpts" in row) == requested
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(response(
            tool, evidence if not requested else None,
        ))).call_tool(tool, arguments(tool, requested))


@pytest.mark.parametrize("evidence", [
    {"source_sha256": "a" * 63, "excerpts": [], "truncated": False},
    {"source_sha256": "a" * 64, "excerpts": ["x"] * 4, "truncated": False},
    {"source_sha256": "a" * 64, "excerpts": ["🙂" * 401], "truncated": True},
    {"source_sha256": "a" * 64, "excerpts": [], "truncated": "false"},
])
async def test_excerpt_shape_and_unicode_bounds_are_enforced(settings, evidence):
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(response(
            "decision_impact", evidence,
        ))).call_tool("decision_impact", arguments("decision_impact", True))
