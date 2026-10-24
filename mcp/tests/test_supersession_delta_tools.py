"""Supersession comparisons retain exact replacement/target identities and explicit bounds."""

import pytest
from conftest import PROJECT_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_knowledge_impact_tools import ASSESSMENT_ID, assessment, author, candidate
from test_tools import adapter, structured


def section(found=True):
    return {"found": found, "section_count": int(found), "text": "## Supersedes\nPrior rule" if found
            else "", "truncated": False, "source_characters_scanned": 100 if found else 0}


def delta():
    return {
        "source": "read-projection", "target_reference": "ADR-00001-0",
        "before_reference": "ADR-00002-0", "after_reference": "ADR-00002-1",
        "kind": "continued-supersession", "details_before": None, "details_after": None,
        "metadata_changed": False, "section_before": section(), "section_after": section(),
        "unified_diff": "", "truncated": False, "section_scan_limit": 65536,
        "section_byte_limit": 8192, "diff_byte_limit": 16384,
        "interpretation": "Supersedes headings are evidence aids; read exact revisions to "
                          "establish rule scope.",
    }


def response():
    return assessment(trigger="supersession", after_reference="ADR-00002-1",
        after_author=author("ADR-00002-1"), supersession_delta=delta(), kind="code-impact",
        total=1, candidates=[candidate()], dispositions=[], disposition_history_total=0,
        limit=50, offset=0, next_offset=None)


@pytest.mark.parametrize("compact", [False, True])
async def test_assessment_reads_accept_pinned_supersession_delta_and_unknown_extent_warning(
    settings, compact,
):
    body = response()
    if compact:
        body["projection"] = {"detail": "full", "include_diff": False, "include_scopes": "full"}
        body["diff"].pop("unified_diff")
        body["supersession_delta"].pop("unified_diff")
        for key in ("section_before", "section_after"):
            body["supersession_delta"][key].pop("text")
    body["coverage"]["warnings"] = [{"code": "supersession_extent_reason_unknown",
        "reference": "ADR-00001-0", "message": "Inspect the exact superseding revision."}]
    result = structured(await adapter(settings, lambda request: stream_json(body))
        .call_tool("decision_assessment_get", {"project_id": PROJECT_ID,
                                              "assessment_id": ASSESSMENT_ID,
                                              "include_diff": not compact}))
    assert result["supersession_delta"] == body["supersession_delta"]
    assert result["coverage"]["warnings"] == body["coverage"]["warnings"]


@pytest.mark.parametrize("include_diff", [False, True])
@pytest.mark.parametrize("field", ["unified_diff", "section_before", "section_after"])
async def test_supersession_prose_cannot_disagree_with_requested_projection(
    settings, include_diff, field,
):
    body = response()
    value = body["supersession_delta"]
    if not include_diff:
        body["projection"] = {"detail": "full", "include_diff": False, "include_scopes": "full"}
        body["diff"].pop("unified_diff")
        value.pop("unified_diff")
        value["section_before"].pop("text")
        value["section_after"].pop("text")
    target, key = (value, field) if field == "unified_diff" else (value[field], "text")
    if include_diff:
        target.pop(key)
    else:
        target[key] = None
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(body)).call_tool(
            "decision_assessment_get", {"project_id": PROJECT_ID,
                                        "assessment_id": ASSESSMENT_ID,
                                        "include_diff": include_diff},
        )


@pytest.mark.parametrize("change", ["target", "after", "before", "metadata", "detail", "truncated",
                                    "found", "section-bound", "diff-bound", "source", "trigger"])
async def test_unrelated_or_unbounded_delta_cannot_be_confirmed_as_assessment_read(settings, change):
    body = response()
    value = body["supersession_delta"]
    if change == "detail":
        value["details_after"] = {"decision_id": "ADR-00003", "revision": 0,
                                  "extent": "partial", "reason": "Unrelated"}
        value["metadata_changed"] = True
    elif change == "truncated":
        value["section_after"]["truncated"] = True
    elif change == "found":
        value["section_after"]["found"] = False
    elif change == "section-bound":
        value["section_after"]["text"] = "🦊" * 3000
    elif change == "diff-bound":
        value["unified_diff"] = "x" * 16385
    elif change == "trigger":
        body["trigger"] = "revision"
    else:
        key, replacement = {"target": ("target_reference", "ADR-00003-0"),
            "after": ("after_reference", "ADR-00003-1"),
            "before": ("before_reference", "ADR-00002-5"), "metadata": ("metadata_changed", True),
            "source": ("source", "captured")} [change]
        value[key] = replacement
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(body)).call_tool(
            "decision_assessment_get", {"project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID},
        )
