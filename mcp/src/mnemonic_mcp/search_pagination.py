"""Validate explicit search continuation, including byte-limited pages."""

from typing import Any, Protocol, cast

from pydantic import BaseModel, Field, StrictBool, StrictInt


class SearchPagination(BaseModel):
    next_offset: StrictInt | None = Field(default=None, ge=0)
    page_truncated: StrictBool = False


class _Page(Protocol):
    items: list[Any]
    total: int
    limit: int
    offset: int
    next_offset: int | None
    page_truncated: bool


def pagination_matches(page: BaseModel) -> bool:
    if not {"next_offset", "page_truncated"} <= page.model_fields_set:
        return False
    value = cast(_Page, page)
    count = len(value.items)
    expected = min(value.limit, max(0, value.total - value.offset))
    end = value.offset + count
    return (
        0 <= count <= expected
        and (count == expected if not value.page_truncated else 0 < count < expected)
        and value.next_offset == (end if count and end < value.total else None)
    )
