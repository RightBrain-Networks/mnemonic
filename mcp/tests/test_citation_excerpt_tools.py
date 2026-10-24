"""Opt-in evidence keeps existing request defaults and rejects malformed projections."""

import pytest
from conftest import PROJECT_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_impact_projections import COMPACT
from test_knowledge_impact_tools import ASSESSMENT_ID, assessment, candidate, impact
from test_tools import adapter, structured


def response(tool, evidence, compact=False):
    row = candidate(**({"citation_excerpts": evidence} if evidence is not None else {}))
    result = impact(items=[row]) if tool == "decision_impact" else assessment(
        kind="code-impact", total=1, candidates=[row], dispositions=[],
        disposition_history_total=0, limit=50, offset=0, next_offset=None,
    )
    if compact:
        result["projection"] = COMPACT
        for key in ("title", "rationale", "related_work_item_ids", "related_artifact_ids", "scopes"):
            row.pop(key)
        row["scope_summary"] = {
            "captured_scope_count": 0, "commit_hashes": [], "evidence_truncated": False,
        }
        authors = [result["origin_author"]] if tool == "decision_impact" else [
            result["before_author"], result["after_author"],
        ]
        for author in authors:
            author.pop("active_work_items")
            author["active_work_item_ids"] = []
        if tool != "decision_impact":
            result["diff"].pop("unified_diff")
    return result


def arguments(tool, requested):
    return {"project_id": PROJECT_ID, "include_citation_excerpts": requested,
            **({"decision_id": "ADR-00001-0"} if tool == "decision_impact"
               else {"assessment_id": ASSESSMENT_ID})}


@pytest.mark.parametrize("tool", ["decision_impact", "decision_assessment_get"])
@pytest.mark.parametrize("requested", [False, True])
@pytest.mark.parametrize("compact", [False, True])
async def test_excerpts_are_opt_in_with_strict_response_binding(settings, tool, requested, compact):
    evidence = {"source_sha256": "a" * 64, "excerpts": [], "truncated": False}

    def handler(request):
        assert (request.url.params.get("include_citation_excerpts") == "true") == requested
        return stream_json(response(tool, evidence if requested else None, compact))

    args = arguments(tool, requested) | (COMPACT if compact else {})
    result = structured(await adapter(settings, handler).call_tool(tool, args))
    row = result["items" if tool == "decision_impact" else "candidates"][0]
    assert ("citation_excerpts" in row) == requested
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(response(
            tool, evidence if not requested else None, compact,
        ))).call_tool(tool, args)


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
