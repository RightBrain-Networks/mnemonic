"""Upstream corruption tests shared by every supported page shape."""

from copy import deepcopy
from uuid import UUID

import httpx
import pytest
from conftest import (
    CHECKPOINT_ID,
    OTHER_CHECKPOINT_ID,
    OTHER_WORK_ID,
    PROJECT_ID,
    RELATIONSHIP_ID,
    WORK_ID,
)
from mcp.server.fastmcp.exceptions import ToolError

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import build_server

OFFSET_TOOLS = (
    "list_projects", "search_work", "list_ready_work", "list_checkpoints",
    "list_relationships", "list_work_events",
)
CURSOR_TOOLS = ("list_human_attention", "list_work_gates")


@pytest.fixture
def pages(project, work_summary, checkpoint, adjacent_relationship, progress_event, human_gate):
    work = work_summary["work_item"]
    attention_summary = deepcopy(work_summary)
    attention_summary["readiness"].update(
        unresolved_gate_count=1, is_gated=True, is_ready=False, display_state="waiting",
    )
    rows = {
        "list_projects": project,
        "search_work": {
            "summary": work_summary,
            "matched_member": {key: work[key] for key in ("id", "title", "status")},
        },
        "list_ready_work": {
            "work_item": {
                key: work[key]
                for key in ("id", "title", "status", "priority", "version", "updated_at")
            },
            "checkpoint_count": 1,
            "display_state": "pending",
        },
        "list_checkpoints": checkpoint,
        "list_relationships": adjacent_relationship,
        "list_work_events": progress_event,
        "list_human_attention": {"gate": human_gate, "summary": attention_summary},
        "list_work_gates": human_gate,
    }
    return {
        tool: {
            "items": [row], "total": 6, "limit": 3,
            **({"offset": 2} if tool in OFFSET_TOOLS else {"next_cursor": None}),
            **({"pre_phase5_history_may_be_incomplete": False}
               if tool == "list_work_events" else {}),
        }
        for tool, row in rows.items()
    }


def arguments(tool):
    args = {"limit": 3}
    if tool in OFFSET_TOOLS:
        args["offset"] = 2
    if tool != "list_projects":
        args["project_id"] = PROJECT_ID
    if tool not in {"list_projects", "search_work", "list_ready_work"}:
        args["work_item_id"] = WORK_ID
    return args


async def call(settings, tool, args, document):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=document)

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    try:
        result = await server.call_tool(tool, args)
        return result[1] if isinstance(result, tuple) else result
    finally:
        # A malformed success is checked once; validation never dispatches a retry.
        assert len(requests) == 1


def distinct_row(tool, row, index):
    """Keep oversized-page rows individually coherent and unique."""
    row = deepcopy(row)
    identity = str(UUID(int=index + 1))
    if tool == "search_work":
        row["summary"]["work_item"]["id"] = identity
        row["summary"]["current_context"]["work_item_id"] = identity
        row["summary"]["readiness"]["canonical_work_item_id"] = identity
        row["matched_member"]["id"] = identity
    elif tool == "list_ready_work":
        row["work_item"]["id"] = identity
    elif tool == "list_relationships":
        row["relationship"]["id"] = identity
    elif tool == "list_human_attention":
        row["gate"]["id"] = identity
    elif tool == "list_work_events":
        row["id"] += index + 1
    else:
        row["id"] = identity
    return row


@pytest.mark.parametrize("tool", (*OFFSET_TOOLS, *CURSOR_TOOLS))
@pytest.mark.parametrize("corruption", ("limit", "duplicate", "total", "oversized", "negative"))
async def test_pages_reject_common_corruption(settings, pages, tool, corruption):
    page = pages[tool]
    if corruption == "limit":
        page["limit"] = 4
    elif corruption == "duplicate":
        page["items"] *= 2
    elif corruption == "total":
        page["total"] = 0
    elif corruption == "oversized":
        page["items"] = [distinct_row(tool, page["items"][0], index) for index in range(4)]
    else:
        page["total"] = -1
    with pytest.raises(ToolError, match="unexpected response") as caught:
        await call(settings, tool, arguments(tool), page)
    assert WORK_ID not in str(caught.value)
    assert PROJECT_ID not in str(caught.value)


@pytest.mark.parametrize("tool", OFFSET_TOOLS)
@pytest.mark.parametrize("corruption", ("offset", "negative_offset", "offset_count"))
async def test_offset_pages_bind_parameters_and_count_bounds(settings, pages, tool, corruption):
    page = pages[tool]
    if corruption == "offset":
        page["offset"] = 1
    elif corruption == "negative_offset":
        page["offset"] = -1
    else:
        page["total"] = 2
    with pytest.raises(ToolError, match="unexpected response"):
        await call(settings, tool, arguments(tool), page)


@pytest.mark.parametrize("tool", OFFSET_TOOLS)
async def test_empty_offset_pages_beyond_total_remain_valid(settings, pages, tool):
    page = pages[tool]
    page.update(items=[], total=0)
    result = await call(settings, tool, arguments(tool), page)
    assert result["items"] == []
    assert result["offset"] == 2


@pytest.mark.parametrize("tool", (*OFFSET_TOOLS, *CURSOR_TOOLS))
async def test_underfilled_pages_do_not_imply_a_fabricated_complete_history(settings, pages, tool):
    result = await call(settings, tool, arguments(tool), pages[tool])
    assert result["total"] == 6
    assert len(result["items"]) == 1


@pytest.mark.parametrize("field", ("id", "project_id"))
async def test_exact_relationship_binds_all_available_requested_ids(settings, relationship, field):
    relationship[field] = OTHER_CHECKPOINT_ID
    with pytest.raises(ToolError, match="unexpected response") as caught:
        await call(
            settings, "get_relationship",
            {"project_id": PROJECT_ID, "relationship_id": RELATIONSHIP_ID}, relationship,
        )
    assert OTHER_CHECKPOINT_ID not in str(caught.value)


async def test_checkpoint_history_binds_work_identity(settings, pages):
    page = pages["list_checkpoints"]
    page["items"][0]["work_item_id"] = OTHER_WORK_ID
    with pytest.raises(ToolError, match="unexpected response"):
        await call(settings, "list_checkpoints", arguments("list_checkpoints"), page)


@pytest.mark.parametrize(
    "corruption",
    ("counterpart_project", "relative_work", "direction", "counterpart", "endpoints"),
)
async def test_relationship_adjacency_binds_scope_and_projection(settings, pages, corruption):
    row = pages["list_relationships"]["items"][0]
    if corruption == "counterpart_project":
        row["counterpart"].pop("project_id")
    elif corruption == "relative_work":
        row.update(relative_to_work_item_id=OTHER_WORK_ID, direction="outgoing")
        row["counterpart"]["id"] = WORK_ID
    elif corruption == "direction":
        row["direction"] = "outgoing"
    elif corruption == "counterpart":
        row["counterpart"]["id"] = OTHER_CHECKPOINT_ID
    else:
        row["relationship"]["target_work_item_id"] = OTHER_CHECKPOINT_ID
    with pytest.raises(ToolError, match="unexpected response"):
        await call(settings, "list_relationships", arguments("list_relationships"),
                   pages["list_relationships"])


@pytest.mark.parametrize(
    ("tool", "filters"),
    (
        ("list_relationships", {"direction": "outgoing"}),
        ("list_relationships", {"relationship_type": "parent-child"}),
        ("list_work_events", {"event_type": "work_created"}),
        ("list_work_gates", {"status": "resolved"}),
        ("list_ready_work", {"min_priority": 8}),
    ),
)
async def test_represented_read_filters_are_bound(settings, pages, tool, filters):
    with pytest.raises(ToolError, match="unexpected response"):
        await call(settings, tool, {**arguments(tool), **filters}, pages[tool])


async def test_distinct_relationships_can_share_a_compact_counterpart(settings, pages):
    page = pages["list_relationships"]
    second = deepcopy(page["items"][0])
    second["relationship"].update(id=OTHER_CHECKPOINT_ID, relationship_type="related")
    second["direction"] = "undirected"
    second["counterpart"]["prompt"] = "accidental private upstream content"
    page["items"].append(second)
    result = await call(settings, "list_relationships", arguments("list_relationships"), page)
    assert len(result["items"]) == 2
    assert "prompt" not in result["items"][1]["counterpart"]


async def test_undirected_relationship_filter_remains_valid(settings, pages):
    page = pages["list_relationships"]
    row = page["items"][0]
    row["relationship"]["relationship_type"] = "related"
    row["direction"] = "undirected"
    result = await call(settings, "list_relationships",
                        {**arguments("list_relationships"), "direction": "undirected"}, page)
    assert result["items"][0]["direction"] == "undirected"


async def test_attention_can_repeat_work_for_distinct_gates(settings, pages):
    page = pages["list_human_attention"]
    second = deepcopy(page["items"][0])
    second["gate"]["id"] = OTHER_CHECKPOINT_ID
    page["items"].append(second)
    result = await call(settings, "list_human_attention", arguments("list_human_attention"), page)
    assert len(result["items"]) == 2


async def test_attention_count_only_page_preserves_nonzero_total(settings, pages):
    page = pages["list_human_attention"]
    page.update(items=[], limit=0)
    result = await call(settings, "list_human_attention",
                        {**arguments("list_human_attention"), "limit": 0}, page)
    assert result["total"] == 6


@pytest.mark.parametrize("tool", CURSOR_TOOLS)
async def test_gate_cursor_continuations_do_not_use_offset_arithmetic(settings, pages, tool):
    page = pages[tool]
    page["next_cursor"] = "opaque-next-cursor"
    result = await call(settings, tool, {**arguments(tool), "cursor": "opaque-request-cursor"}, page)
    assert result["next_cursor"] == "opaque-next-cursor"


async def test_uuid_case_is_identity_preserving(settings, pages):
    page = pages["list_checkpoints"]
    page["items"][0]["work_item_id"] = WORK_ID.upper()
    result = await call(settings, "list_checkpoints", arguments("list_checkpoints"), page)
    assert result["items"][0]["id"] == CHECKPOINT_ID


@pytest.mark.parametrize("slice_name", ("recent_checkpoints", "recent_events"))
async def test_recall_rejects_slices_exceeding_requested_bounds(
    settings, work_context, checkpoint, progress_event, slice_name,
):
    if slice_name == "recent_checkpoints":
        work_context.update(
            recent_checkpoints=[{**checkpoint, "id": OTHER_CHECKPOINT_ID, "kind": "progress"}],
            checkpoint_total=2,
        )
    else:
        work_context.update(recent_events=[progress_event], omitted_event_count=0)
    with pytest.raises(ToolError, match="unexpected response"):
        await call(settings, "recall_work", {
            "project_id": PROJECT_ID, "work_item_id": WORK_ID,
            "recent_limit": 0, "recent_event_limit": 0,
        }, work_context)
