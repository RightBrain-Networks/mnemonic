"""Search rejections teach the retry without exposing caller values."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mnemonic_api.application.validation import public_validation_errors
from mnemonic_api.schemas import WorkItemListQuery
from mnemonic_api.validation_rules import VALIDATION_RULES


@pytest.mark.parametrize("arguments,code,field", [
    ({"q": "PRIVATE_QUERY", "view": "roots"}, "view_requires_blank_query", "view"),
    ({"semantic": True}, "semantic_requires_query", "q"),
    ({"external_url": "https://example.com", "view": "roots"},
     "external_url_requires_full_view", "view"),
    ({"view": "roots", "duplicate_scope": "aliases"},
     "roots_require_canonical_scope", "duplicate_scope"),
    ({"canonical_work_item_id": str(uuid4())},
     "canonical_filter_requires_alias_scope", "canonical_work_item_id"),
    ({"external_url": "github.com/PRIVATE_PATH"},
     "absolute_http_url_required", "external_url"),
])
def test_search_rules_have_actionable_public_locations(arguments, code, field):
    with pytest.raises(ValidationError) as caught:
        WorkItemListQuery(**arguments)
    errors = [{**error, "loc": ("query", *error["loc"])} for error in caught.value.errors()]
    public = public_validation_errors(errors)
    assert public == [{"type": code, "loc": ["query", field],
                       "msg": VALIDATION_RULES[code][1]}]
    assert "PRIVATE" not in json.dumps(public)


def test_reviewed_rule_does_not_echo_arbitrary_context_or_location():
    result = public_validation_errors([{
        "type": "view_requires_blank_query", "loc": ["query", "PRIVATE_KEY"],
        "msg": "PRIVATE_VALUE", "input": "PRIVATE_VALUE", "ctx": {"field": "PRIVATE_KEY"},
    }])
    assert result[0]["loc"] == ["query", "field", "view"]
    assert "PRIVATE" not in json.dumps(result)


def test_public_rules_match_reviewed_catalog():
    root = Path(__file__).resolve().parents[2]
    catalog = json.loads((root / "docs/validation-vocabulary.json").read_text())
    assert {code: {"field": field, "message": message}
            for code, (field, message) in VALIDATION_RULES.items()} == catalog["rules"]


@pytest.mark.postgres
@pytest.mark.parametrize("arguments,code,field", [
    ({"q": "PRIVATE_QUERY", "semantic": "true", "view": "roots"},
     "view_requires_blank_query", "view"),
    ({"external_url": "github.com/PRIVATE_PATH"},
     "absolute_http_url_required", "external_url"),
])
def test_reported_search_failures_return_static_rule_through_api(
    api, project, arguments, code, field,
):
    response = api.get(f"/api/v1/projects/{project['id']}/work-items", params=arguments)
    assert response.status_code == 422
    assert response.json()["detail"] == [{
        "type": code, "loc": ["query", field], "msg": VALIDATION_RULES[code][1],
    }]
    assert "PRIVATE" not in response.text
