"""Bound search pages without dropping evidence, coverage, or continuation."""

import json
from typing import Any, Protocol, cast

from pydantic import BaseModel, Field

from mnemonic_api.errors import ApplicationError

# Count indented, ASCII-escaped JSON, which also bounds MCP's textual rendering.
# HTTP JSON is smaller; JSON-RPC framing is outside this result-payload budget.
SEARCH_PAGE_MAX_BYTES = 32 * 1024


class SearchPagination(BaseModel):
    next_offset: int | None = Field(default=None, ge=0)
    page_truncated: bool = False


class _Page(Protocol):
    items: list[Any]
    total: int
    limit: int
    offset: int
    next_offset: int | None
    page_truncated: bool


def bound_search_page[Page: BaseModel](page: Page) -> Page:
    """Keep the longest fitting prefix; never return an empty continuation page."""
    result = cast(_Page, page)
    original = result.items
    payload = page.model_dump(mode="json")
    wire_items = payload["items"]

    def fits(count: int) -> bool:
        end = result.offset + count
        payload.update(items=wire_items[:count],
                       next_offset=end if end < result.total and count else None,
                       page_truncated=count < len(original))
        return len(json.dumps(payload, ensure_ascii=True, indent=2).encode()) <= (
            SEARCH_PAGE_MAX_BYTES)

    if not fits(min(1, len(original))):
        raise ApplicationError(
            413, "search_result_too_large",
            "One search result or its coverage envelope exceeds the 32768-byte page budget. "
            "Use detail=compact, diagnostics=off and a smaller tag_counts.limit; "
            "retrieve selected records with their detail tools.",
        )
    low, high = min(1, len(original)), len(original)
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    result.items = original[:low]
    result.page_truncated = low < len(original)
    end = result.offset + low
    result.next_offset = end if end < result.total and low else None
    return page
