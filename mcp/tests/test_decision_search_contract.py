"""Replay real backend HTTP responses without MCP-generated disclosure fixtures."""

import copy
import json
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from test_compact_search import native_call

# Captured from the backend TestClient and isolated PostgreSQL on an empty project.
# Only its random project UUID was normalized. The envelope, matching modes,
# filters, coverage and diagnostic counts come from the actual REST response.
# Provenance and backend source hashes are retained alongside the captured cases.
CAPTURE = json.loads((Path(__file__).parent / "fixtures" / "decision_search_contract.json").read_text())
CASES = CAPTURE["cases"]
BOUNDARY = json.loads((Path(__file__).parent / "fixtures" / "decision_search_boundary.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
async def test_empty_backend_decision_disclosure_survives_mcp(settings, case):
    actual, requests = await native_call(settings, "search", case["request"], case["response"])
    assert actual == case["response"]
    assert actual["items"] == [] and actual["total"] == 0
    assert len(requests) == 1


@pytest.mark.parametrize("case", [
    case for case in CASES if case["request"]["fulltext"]
    and case["request"]["q"] in {"durable queues", '"durable queues"'}
    and case["request"]["query_mode"] != "literal"
], ids=lambda case: case["name"])
async def test_decisions_reject_artifact_matching_modes(settings, case):
    response = copy.deepcopy(case["response"])
    interpretation = response["query_interpretation"]["decisions"]
    interpretation["match_mode"] = (
        "phrase" if interpretation["match_mode"] == "postgresql_phrase_terms" else "all_terms"
    )
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "search", case["request"], response)


async def test_backend_metadata_content_boundary_evidence_survives_mcp(settings):
    actual, _ = await native_call(settings, "search", BOUNDARY["request"], BOUNDARY["response"])
    assert actual == BOUNDARY["response"]
    assert actual["items"][0]["decision"]["matched_fields"] == ["metadata", "content"]


async def test_backend_boundary_hit_cannot_omit_matching_evidence(settings):
    response = copy.deepcopy(BOUNDARY["response"])
    response["items"][0]["decision"]["matched_fields"] = []
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "search", BOUNDARY["request"], response)
