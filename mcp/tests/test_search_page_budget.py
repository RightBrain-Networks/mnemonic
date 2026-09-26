"""All discovery tools accept bounded pages only with coherent continuation."""

import copy

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from test_compact_search import TOOLS, native_call, response_page


@pytest.mark.parametrize("tool", TOOLS)
async def test_short_pages_preserve_items_totals_and_coverage(settings, work_summary, tool):
    query = "" if tool == "list_transcripts" else "needle"
    page = response_page(tool, work_summary, q=query)
    if tool == "search":
        page["items"] = page["items"][:1]
    else:
        page["total"] = 2
    page.update(next_offset=1, page_truncated=True)
    arguments = {} if tool == "list_transcripts" else {"q": query}
    result, _ = await native_call(settings, tool, arguments, page)
    assert result == page


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("mutation", ["missing", "skip", "repeat", "empty", "unmarked"])
async def test_incoherent_budget_continuation_is_rejected(
    settings, work_summary, tool, mutation,
):
    query = "" if tool == "list_transcripts" else "needle"
    page = response_page(tool, work_summary, q=query)
    if tool == "search":
        page["items"] = page["items"][:1]
    else:
        page["total"] = 2
    page.update(next_offset=1, page_truncated=True)
    changed = copy.deepcopy(page)
    if mutation == "missing":
        changed.pop("next_offset")
    elif mutation == "skip":
        changed["next_offset"] = 2
    elif mutation == "repeat":
        changed["next_offset"] = 0
    elif mutation == "empty":
        changed["items"] = []
    else:
        changed["page_truncated"] = False
    arguments = {} if tool == "list_transcripts" else {"q": query}
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, tool, arguments, changed)
