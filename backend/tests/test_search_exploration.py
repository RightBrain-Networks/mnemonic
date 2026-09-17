"""Date controls have explicit UTC semantics and reviewed, value-free errors."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mnemonic_api.application.validation import public_validation_errors
from mnemonic_api.artifact_search_schemas import ArtifactSearchRequest
from mnemonic_api.schemas import WorkItemListQuery
from mnemonic_api.search_schemas import SearchRequest, WorkSearchFilters
from mnemonic_api.transcript_schemas import TranscriptSearch
from mnemonic_api.validation_rules import VALIDATION_RULES


@pytest.mark.parametrize("model,required", [
    (WorkItemListQuery, {}), (WorkSearchFilters, {}),
    (ArtifactSearchRequest, {"q": "needle"}), (TranscriptSearch, {}),
])
def test_source_date_bounds_normalize_aware_values_and_omit_unused_bounds(model, required):
    value = model(**required, created_after="2026-09-16T04:00:00+04:00")
    assert value.created_after == datetime(2026, 9, 16, tzinfo=UTC)
    assert value.model_dump(mode="json")["created_after"] == "2026-09-16T00:00:00Z"
    assert "updated_before" not in value.model_dump()


@pytest.mark.parametrize("arguments,code,field", [
    ({"created_after": "2026-09-16T00:00:00"},
     "search_datetime_timezone_required", "created_after"),
    ({"created_after": "2026-09-16T00:00:00Z", "created_before": "2026-09-16T00:00:00Z"},
     "search_created_range_invalid", "created_before"),
    ({"updated_after": "2026-09-17T00:00:00Z", "updated_before": "2026-09-16T00:00:00Z"},
     "search_updated_range_invalid", "updated_before"),
])
def test_invalid_dates_name_the_rule_and_field(arguments, code, field):
    with pytest.raises(ValidationError) as caught:
        WorkItemListQuery(**arguments)
    public = public_validation_errors(caught.value.errors())
    assert public == [{"type": code, "loc": [field], "msg": VALIDATION_RULES[code][1]}]


def test_tag_counts_cannot_silently_search_an_omitted_source():
    with pytest.raises(ValidationError) as caught:
        SearchRequest(facets=["artifacts"], tag_counts={})
    public = public_validation_errors(caught.value.errors())
    assert public[0]["type"] == "tag_counts_requires_work_facet"
    assert public[0]["loc"] == ["tag_counts"]
