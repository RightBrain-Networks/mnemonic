"""Finding metadata stays exact in batched writes and bounded read projections."""

import json

import httpx
import pytest
from conftest import PROJECT_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_assessment_batch_tools import arguments, response
from test_decision_candidate_tools import CHANGE
from test_decision_candidate_tools import response as grouped_response
from test_impact_projections import COMPACT, compact_assessment
from test_knowledge_impact_tools import ASSESSMENT_ID
from test_tools import adapter, structured


def labelled_batch():
    args, result = arguments(), response()
    for requested, returned, target in zip(
        args["items"], result["items"], ("code", "decision-record"), strict=True,
    ):
        values = {"finding_id": "shared-cause", "remediation_target": target,
                  "disposition": "change-required"}
        requested.update(values)
        returned.update(values)
    return args, result


async def test_batch_finding_metadata_roundtrips_exactly_on_explicit_retry(settings):
    args, expected = labelled_batch()
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(201, json=expected)

    server = adapter(settings, handler)
    for _ in range(2):
        result = structured(await server.call_tool("decision_assessment_append_batch", args))
        assert result == expected
    assert sent[0] == sent[1]
    assert sent[0]["items"] == args["items"]
    assert sent[0]["client_operation_id"] == args["client_operation_id"]


@pytest.mark.parametrize("field,replacement", [
    ("finding_id", "other-cause"), ("finding_id", None),
    ("remediation_target", "both"), ("remediation_target", None),
])
async def test_batch_success_cannot_change_or_drop_requested_finding_metadata(
    settings, field, replacement,
):
    args, result = labelled_batch()
    result["items"][1][field] = replacement
    with pytest.raises(ToolError, match="operation may already have committed"):
        await adapter(settings, lambda request: httpx.Response(201, json=result)).call_tool(
            "decision_assessment_append_batch", args,
        )


@pytest.mark.parametrize("field,value", [
    ("finding_id", "invented"), ("remediation_target", "both"),
])
async def test_batch_legacy_request_rejects_invented_metadata(settings, field, value):
    args, result = arguments(), response()
    result["items"][0][field] = value
    with pytest.raises(ToolError, match="operation may already have committed"):
        await adapter(settings, lambda request: httpx.Response(201, json=result)).call_tool(
            "decision_assessment_append_batch", args,
        )


def projection(surface):
    if surface == "compact":
        result = compact_assessment()
        row = result["dispositions"][0]
        args = {"project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID, **COMPACT}
        tool = "decision_assessment_get"
        result["finding_summary"] = {"named_finding_count": 1,
            "unlabelled_change_required_count": 0, "change_required_row_count": 1}
    else:
        result = grouped_response()
        row = result["items"][0]["occurrences"][1]["latest_disposition"]
        args = {"project_id": PROJECT_ID, "change_id": CHANGE}
        tool = "decision_change_candidates"
    row.update(finding_id="shared-cause", remediation_target="decision-record")
    return tool, args, result, row


@pytest.mark.parametrize("surface", ["compact", "grouped"])
async def test_compact_and_grouped_reads_preserve_finding_metadata_without_prose(settings, surface):
    tool, args, expected, row = projection(surface)
    assert "reason" not in row and "actor" not in row
    actual = structured(await adapter(settings, lambda request: stream_json(expected)).call_tool(
        tool, args,
    ))
    assert actual == expected


@pytest.mark.parametrize("surface", ["compact", "grouped"])
@pytest.mark.parametrize("field,value", [
    ("finding_id", "invalid label"), ("remediation_target", "documentation"),
    ("remediation_target", ["code"]),
])
async def test_read_projections_reject_malformed_finding_metadata(settings, surface, field, value):
    tool, args, result, row = projection(surface)
    row[field] = value
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(result)).call_tool(tool, args)


@pytest.mark.parametrize("surface", ["compact", "grouped"])
async def test_read_projections_reject_target_after_resolving_finding(settings, surface):
    tool, args, result, row = projection(surface)
    row["disposition"] = "resolved"
    result["change_required_count"] = 0
    if surface == "compact":
        result["status"] = "resolved"
        result["finding_summary"] = {"named_finding_count": 0,
            "unlabelled_change_required_count": 0, "change_required_row_count": 0}
    else:
        result["items"][0]["change_required_count"] = 0
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(result)).call_tool(tool, args)
    row.pop("remediation_target")
    actual = structured(await adapter(settings, lambda request: stream_json(result)).call_tool(
        tool, args,
    ))
    assert actual == result


async def test_batch_finding_capability_echo_rejection_is_definite_and_redacted(settings):
    args = arguments() | {"lease_token": "private-finding-capability"}
    args["items"][1]["finding_id"] = "finding-" + args["lease_token"]
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(422, json={"detail": {"code": "client_operation_secret_echo",
            "message": args["lease_token"], "context": {"private": args["lease_token"]}}})

    with pytest.raises(ToolError, match="operation or capability material") as caught:
        await adapter(settings, handler).call_tool("decision_assessment_append_batch", args)
    assert len(sent) == 1 and sent[0]["items"] == args["items"]
    assert args["lease_token"] not in str(caught.value)
    assert "changing an argument makes this a new intent" in str(caught.value)
