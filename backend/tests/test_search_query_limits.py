"""All search front doors accept the same bounded query length."""

import pytest
from pydantic import ValidationError

from mnemonic_api.artifact_search_schemas import ArtifactSearchRequest
from mnemonic_api.schemas import WorkItemListQuery
from mnemonic_api.search_schemas import SearchRequest
from mnemonic_api.transcript_schemas import TranscriptSearch


@pytest.mark.parametrize("model,field", [
    (WorkItemListQuery, "q"), (SearchRequest, "q"),
    (ArtifactSearchRequest, "q"), (TranscriptSearch, "query"),
])
def test_search_query_limit_is_consistent(model, field):
    query = "x" * 1000
    assert getattr(model.model_validate({field: query}), field) == query
    with pytest.raises(ValidationError) as caught:
        model.model_validate({field: query + "x"})
    assert caught.value.errors(include_input=False)[0]["type"] == "string_too_long"
