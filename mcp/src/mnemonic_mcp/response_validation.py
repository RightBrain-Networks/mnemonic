"""Shared response checks; request scope and cursor policy stay with each tool."""

from collections.abc import Callable, Hashable, Sequence
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel


class LimitedPage(Protocol):
    @property
    def limit(self) -> int: ...


class OffsetPage(LimitedPage, Protocol):
    @property
    def offset(self) -> int: ...


def response_matches[ModelT: BaseModel](
    model: type[ModelT], predicate: Callable[[ModelT], bool]
) -> Callable[[BaseModel], bool]:
    """Adapt a typed predicate to the HTTP boundary's response-validator hook."""
    return lambda response: isinstance(response, model) and predicate(response)


def matches_requested_ids(*pairs: tuple[UUID, UUID]) -> bool:
    """Compare only identities actually present in a response, after UUID parsing."""
    return all(actual == requested for actual, requested in pairs)


def matches_requested_limit(page: LimitedPage, *, limit: int) -> bool:
    return page.limit == limit


def matches_requested_offset_page(page: OffsetPage, *, limit: int, offset: int) -> bool:
    return matches_requested_limit(page, limit=limit) and page.offset == offset


def validate_page_bounds(*, count: int, total: int, limit: int, offset: int = 0) -> None:
    """Validate bounds without assuming full pages or stable totals across requests.

    Empty offset pages remain valid even beyond the total. Cursor pages omit the
    offset; their snapshot, continuation, and count-only rules remain domain policy.
    """
    if min(count, total, limit, offset) < 0 or count > limit:
        raise ValueError("Page items must fit within nonnegative page bounds.")
    if count and offset + count > total:
        raise ValueError("Page items must fit within the declared total.")


def validate_unique_rows[RowT, KeyT: Hashable](
    items: Sequence[RowT], *, key: Callable[[RowT], KeyT]
) -> None:
    identities = [key(item) for item in items]
    if len(identities) != len(set(identities)):
        raise ValueError("Page rows cannot repeat their identity.")


def validate_page_items[RowT, KeyT: Hashable](
    items: Sequence[RowT],
    *,
    total: int,
    limit: int,
    key: Callable[[RowT], KeyT],
    offset: int = 0,
) -> None:
    validate_page_bounds(count=len(items), total=total, limit=limit, offset=offset)
    validate_unique_rows(items, key=key)
