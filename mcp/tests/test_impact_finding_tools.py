"""Disposition receipts bind optional finding identity and remediation intent exactly."""

import httpx
import pytest
from conftest import stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_knowledge_impact_tools import (
    assessment,
    disposition,
    disposition_arguments,
)
from test_tools import adapter, structured


@pytest.mark.parametrize("target", ["code", "decision-record", "both"])
async def test_single_append_preserves_finding_metadata(settings, target):
    values = {"finding_id": "wording-vs-code", "remediation_target": target}
    result = structured(await adapter(settings, lambda request: httpx.Response(
        201, json=disposition(**values),
    )).call_tool("decision_assessment_append", disposition_arguments() | values))
    assert result["finding_id"] == "wording-vs-code"
    assert result["remediation_target"] == target


@pytest.mark.parametrize("changed", [
    {"finding_id": "another-finding"}, {"remediation_target": "both"},
    {"finding_id": None}, {"remediation_target": None},
])
async def test_single_append_refuses_mismatched_finding_receipt(settings, changed):
    values = {"finding_id": "wording-vs-code", "remediation_target": "decision-record"}
    with pytest.raises(ToolError, match="operation may already have committed"):
        await adapter(settings, lambda request: httpx.Response(
            201, json=disposition(**(values | changed)),
        )).call_tool("decision_assessment_append", disposition_arguments() | values)


@pytest.mark.parametrize("values", [{"finding_id": "invented"}, {"remediation_target": "code"}])
async def test_old_request_rejects_unrequested_finding_metadata(settings, values):
    with pytest.raises(ToolError, match="operation may already have committed"):
        await adapter(settings, lambda request: httpx.Response(
            201, json=disposition(**values),
        )).call_tool("decision_assessment_append", disposition_arguments())


@pytest.mark.parametrize("summary", [
    {"named_finding_count": 0, "unlabelled_change_required_count": 0,
     "change_required_row_count": 1},
    {"named_finding_count": 1, "unlabelled_change_required_count": 0,
     "change_required_row_count": 2},
    {"named_finding_count": 1, "unlabelled_change_required_count": 0,
     "change_required_row_count": 1, "extra": True},
])
async def test_assessment_rejects_incoherent_finding_summary(settings, summary):
    value = assessment(unassessed_count=0, change_required_count=1,
                       status="change-required", finding_summary=summary)
    page = {"items": [value], "total": 1, "limit": 50, "offset": 0}
    with pytest.raises(ToolError):
        await adapter(settings, lambda request: stream_json(page)).call_tool(
            "decision_assessments", {"project_id": value["project_id"]},
        )
