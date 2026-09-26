"""Page budgets keep complete result evidence and cannot strand a continuation."""

import json

import pytest

from mnemonic_api.errors import ApplicationError
from mnemonic_api.search_pagination import SearchPagination, bound_search_page


class Page(SearchPagination):
    items: list[dict[str, str]]
    total: int
    limit: int
    offset: int
    coverage: dict[str, int]


def test_budget_counts_escaped_unicode_and_preserves_envelope(monkeypatch):
    monkeypatch.setattr("mnemonic_api.search_pagination.SEARCH_PAGE_MAX_BYTES", 1600)
    items = [{"id": str(index), "evidence": "🦊" * 50} for index in range(5)]
    page = Page(items=items, total=30, limit=5, offset=10, coverage={"withheld": 4})
    result = bound_search_page(page)
    assert result.items == items[:2]
    assert result.next_offset == 12 and result.page_truncated
    assert result.limit == 5 and result.total == 30 and result.coverage == {"withheld": 4}
    assert len(json.dumps(result.model_dump(), indent=2, ensure_ascii=True).encode()) <= 1600


@pytest.mark.parametrize("items,total,offset,next_offset", [
    ([{"id": "one"}], 10, 2, 3), ([{"id": "one"}], 3, 2, None), ([], 3, 8, None),
])
def test_complete_and_beyond_total_pages_have_coherent_continuation(
    items, total, offset, next_offset,
):
    result = bound_search_page(Page(
        items=items, total=total, limit=1, offset=offset, coverage={},
    ))
    assert result.next_offset == next_offset and not result.page_truncated


@pytest.mark.parametrize("oversized_hit", [True, False])
def test_singleton_or_envelope_overflow_is_actionable_not_an_empty_page(
    monkeypatch, oversized_hit,
):
    monkeypatch.setattr("mnemonic_api.search_pagination.SEARCH_PAGE_MAX_BYTES", 50)
    page = Page(items=[{"id": "one"}] if oversized_hit else [], total=1 if oversized_hit else 0,
                limit=20, offset=0, coverage={"withheld": 4})
    with pytest.raises(ApplicationError) as error:
        bound_search_page(page)
    assert error.value.status_code == 413
    assert error.value.detail["code"] == "search_result_too_large"
    assert "detail=compact" in error.value.detail["message"]
