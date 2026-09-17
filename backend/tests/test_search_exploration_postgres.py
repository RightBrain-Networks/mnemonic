"""Exploration counts retain source scope, canonical identities and temporal boundaries."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticCustomError

from mnemonic_api.artifact_index import ArtifactSearchIndex
from mnemonic_api.artifact_search_schemas import ArtifactSearchRequest
from mnemonic_api.search_schemas import SearchRequest
from mnemonic_api.services import search_work, work_search
from mnemonic_api.validation_rules import VALIDATION_RULES

from .test_artifact_search_postgres import extract
from .test_artifact_search_postgres import search as artifact_search
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import upload
from .test_duplicate_handling_postgres import merge_work
from .test_hierarchy_presentation_matrix_postgres import create_work as create_hierarchy_work
from .test_search_diagnostics_postgres import counts
from .test_unified_search_postgres import mixed as mixed
from .test_unified_search_postgres import path, search
from .test_work_items_postgres import checkpoint_payload, collection, create_work, item_path

pytestmark = pytest.mark.postgres


def _tagged(api, project, payload, title, tags):
    checkpoint = payload["initial_checkpoint"] | {"tags": tags}
    return create_work(api, project, payload, title=title,
                       initial_checkpoint=checkpoint)["work_item"]


def _work_page(api, project, **params):
    response = api.get(collection(project), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_tag_counts_are_paginated_and_deduplicate_canonical_groups(
    api, project, work_payload,
):
    root = _tagged(api, project, work_payload, "Needle root", ["Alpha", "Shared"])
    alias = _tagged(api, project, work_payload, "Needle alias", ["AliasOnly", "shared"])
    _tagged(api, project, work_payload, "Other objective", ["Shared", "Zulu"])
    merge_work(api, project, alias, root)
    response = api.post(item_path(project, root) + "/checkpoints", json=checkpoint_payload(
        "Another occurrence must not multiply vocabulary counts", "tag-session", tags=["SHARED"]))
    assert response.status_code == 201, response.text
    first = search(api, project, facets=["work_items"], tag_counts={"limit": 2})
    assert first["tag_counts"]["items"] == [{"tag": "alpha", "count": 1},
                                             {"tag": "shared", "count": 2}]
    assert first["tag_counts"]["next_offset"] == 2
    last = search(api, project, facets=["work_items"], tag_counts={"limit": 2, "offset": 2})
    assert last["tag_counts"]["items"] == [{"tag": "zulu", "count": 1}]
    assert last["tag_counts"]["total"] == 3 and last["tag_counts"]["next_offset"] is None
    all_members = search(api, project, facets=["work_items"], tag_counts={},
                         filters={"work_items": {"duplicate_scope": "all"}})
    tags = {row["tag"]: row["count"] for row in all_members["tag_counts"]["items"]}
    assert tags == {"aliasonly": 1, "alpha": 1, "shared": 2, "zulu": 1}
    assert all_members["tag_counts"]["count_unit"] == "canonical_work_items"
    assert all_members["tag_counts"]["member_scope"] == "returned_work_items"
    assert all_members["tag_counts"]["selected_tag_applied"]


def test_vocabulary_obeys_query_selected_tag_dates_and_page_scope(api, project, work_payload):
    first = _tagged(api, project, work_payload, "Unique needle", ["Chosen", "Together"])
    _tagged(api, project, work_payload, "Unrelated", ["Elsewhere"])
    result = search(api, project, q="needle", facets=["work_items"], limit=1, offset=50,
        filters={"work_items": {"tag": "CHOSEN", "created_after": first["created_at"]}},
        tag_counts={})
    assert result["items"] == [] and result["total"] == 1
    assert result["tag_counts"]["items"] == [{"tag": "chosen", "count": 1},
                                             {"tag": "together", "count": 1}]
    assert search(api, project, q="needle", tag_counts={}, filters={"work_items": {
        "created_before": first["created_at"]}})["tag_counts"]["total"] == 0


def test_positive_diagnostics_are_optional_and_preserve_unsearched_null(api, project, mixed):
    default = search(api, project, q="needle", fulltext=True, facets=["work_items", "artifacts"])
    assert default["term_diagnostics"] == [] and default["diagnostics"] == "on_empty"
    always = search(api, project, q="needle", fulltext=True, diagnostics="always",
                    facets=["work_items", "artifacts"])
    assert counts(always)["needle"] == {"work_items": 2, "artifacts": 1, "transcripts": None}
    dedicated = _work_page(api, project, q="needle", diagnostics="always")
    assert counts(dedicated)["needle"] == {"work_items": 2, "artifacts": None, "transcripts": None}
    artifact = artifact_search(api, project, "needle", fulltext=True, diagnostics="always")
    assert counts(artifact)["needle"]["artifacts"] == 1
    response = api.post(f"/api/v1/projects/{project['id']}/transcripts/search-content",
                        json={"query": "needle", "fulltext": True, "diagnostics": "always"})
    assert response.status_code == 200, response.text
    assert counts(response.json())["needle"]["transcripts"] == 1


def test_diagnostics_off_never_runs_term_count_queries(api, project, mixed, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("diagnostics=off must not count terms")

    monkeypatch.setattr(ArtifactSearchIndex, "term_counts", forbidden)
    result = search(api, project, q="needle absentword", fulltext=True, diagnostics="off")
    assert result["total"] == 0 and result["term_diagnostics"] == []
    assert artifact_search(api, project, "absentword", diagnostics="off")["term_diagnostics"] == []
    response = api.post(f"/api/v1/projects/{project['id']}/transcripts/search-content",
                        json={"query": "absentword", "diagnostics": "off"})
    assert response.status_code == 200 and response.json()["term_diagnostics"] == []


def _bound(timestamp):
    value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return value.astimezone(timezone(timedelta(hours=-4))).isoformat()


@pytest.mark.parametrize("facet", ["work_items", "artifacts", "transcripts"])
def test_inclusive_created_lower_and_exclusive_upper_bounds_match_diagnostics(
    api, project, mixed, facet,
):
    record = mixed[{"work_items": 0, "artifacts": 1, "transcripts": 3}[facet]]
    timestamp = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
    bounds = {"created_after": _bound(record["created_at"]),
              "created_before": (timestamp + timedelta(microseconds=1)).isoformat()}
    result = search(api, project, q="needle", fulltext=True, diagnostics="always",
                    facets=[facet], filters={facet: bounds})
    assert result["total"] == 1
    assert result["items"][0]["id"] == record["id"]
    assert counts(result)["needle"][facet] == 1
    upper = search(api, project, q="needle", fulltext=True, diagnostics="always", facets=[facet],
                   filters={facet: {"created_before": bounds["created_after"]}})
    assert all(item["id"] != record["id"] for item in upper["items"])
    assert result["applied_filters"][facet]["created_after"].endswith("Z")
    if facet == "work_items":
        dedicated = _work_page(api, project, q="needle", diagnostics="always", **bounds)
    elif facet == "artifacts":
        dedicated = artifact_search(api, project, "needle", fulltext=True,
                                     diagnostics="always", **bounds)
    else:
        response = api.post(f"/api/v1/projects/{project['id']}/transcripts/search-content",
                            json={"query": "needle", "fulltext": True,
                                  "diagnostics": "always", **bounds})
        assert response.status_code == 200, response.text
        dedicated = response.json()
    assert dedicated["total"] == 1 and counts(dedicated)["needle"][facet] == 1


def test_positive_counts_withhold_sensitive_artifact_content(api, project, artifact_storage):
    upload(api, project, filename="needle-public.txt", body=b"needle")
    extract(api, artifact_storage)
    upload(api, project, filename="private.txt", body=b"needle",
           metadata={"sensitive": True})
    extract(api, artifact_storage)
    result = search(api, project, q="needle", fulltext=True, diagnostics="always",
                    facets=["artifacts"])
    assert result["total"] == 1 and counts(result)["needle"]["artifacts"] == 1
    assert result["coverage"]["artifacts"]["sensitive_content_withheld"] == 1


def test_work_diagnostics_off_does_not_execute_individual_term_queries(
    api, project, work_payload, monkeypatch,
):
    create_work(api, project, work_payload, title="Needle conjunction")
    observed = []
    original = work_search._lexical_rows

    def record(database, query, candidates, filters=None):
        observed.append(query)
        return original(database, query, candidates, filters)

    monkeypatch.setattr(work_search, "_lexical_rows", record)
    monkeypatch.setattr(search_work, "_lexical_rows", record)
    assert search(api, project, q="needle absent", facets=["work_items"],
                  diagnostics="off")["term_diagnostics"] == []
    assert _work_page(api, project, q="needle absent", diagnostics="off")["term_diagnostics"] == []
    assert observed == ["needle absent"]


def test_positive_term_counts_explain_narrow_conjunction_inside_date_scope(
    api, project, work_payload,
):
    create_work(api, project, work_payload, title="Copper older")
    first = create_work(api, project, work_payload, title="Copper zircon")["work_item"]
    create_work(api, project, work_payload, title="Copper feldspar")
    bounds = {"created_after": first["created_at"]}
    result = search(api, project, q="copper zircon", facets=["work_items"], diagnostics="always",
                    filters={"work_items": bounds})
    assert result["total"] == 1
    assert counts(result) == {
        "copper": {"work_items": 2, "artifacts": None, "transcripts": None},
        "zircon": {"work_items": 1, "artifacts": None, "transcripts": None},
    }
    dedicated = _work_page(api, project, q="copper zircon", diagnostics="always", **bounds)
    assert counts(dedicated) == counts(result)


def test_tag_vocabulary_is_not_computed_unless_requested(api, project, work_payload, monkeypatch):
    create_work(api, project, work_payload)

    def forbidden(*_args, **_kwargs):
        pytest.fail("omitted tag_counts must not compute vocabulary")

    monkeypatch.setattr(search_work, "work_tag_counts", forbidden)
    assert search(api, project, facets=["work_items"])["tag_counts"] is None


def test_tag_counts_rejection_names_static_rule_through_api(api, project):
    response = api.post(path(project), json={"facets": ["transcripts"], "tag_counts": {}})
    code = "tag_counts_requires_work_facet"
    assert response.status_code == 422
    assert response.json()["detail"] == [{
        "type": code, "loc": ["body", "tag_counts"], "msg": VALIDATION_RULES[code][1],
    }]


@pytest.mark.parametrize("facet", ["work_items", "artifacts", "transcripts"])
def test_updated_bounds_use_the_source_sort_timestamp(api, project, mixed, facet):
    record = mixed[{"work_items": 0, "artifacts": 1, "transcripts": 3}[facet]]
    baseline = search(api, project, facets=[facet])
    row = next(item for item in baseline["items"] if item["id"] == record["id"])
    stamp = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
    bounds = {"updated_after": _bound(row["updated_at"]),
              "updated_before": (stamp + timedelta(microseconds=1)).isoformat()}
    result = search(api, project, q="needle", fulltext=True, diagnostics="always",
                    facets=[facet], filters={facet: bounds})
    assert result["total"] == 1 and result["items"][0]["id"] == record["id"]
    assert counts(result)["needle"][facet] == 1
    upper = search(api, project, facets=[facet], filters={facet: {
        "updated_before": row["updated_at"],
    }})
    assert all(item["id"] != record["id"] for item in upper["items"])
    if facet == "work_items":
        dedicated = _work_page(api, project, q="needle", diagnostics="always", **bounds)
    elif facet == "artifacts":
        dedicated = artifact_search(api, project, "needle", fulltext=True,
                                     diagnostics="always", **bounds)
    else:
        assert row["updated_at"] == row["transcript"]["last_updated_at"]
        response = api.post(f"/api/v1/projects/{project['id']}/transcripts/search-content",
                            json={"query": "needle", "fulltext": True,
                                  "diagnostics": "always", **bounds})
        assert response.status_code == 200, response.text
        dedicated = response.json()
    assert dedicated["total"] == 1 and counts(dedicated)["needle"][facet] == 1


def test_hierarchy_dates_match_descendants_and_disclose_root_filter_mismatch(
    api, project, work_payload,
):
    root = create_hierarchy_work(api, project, work_payload, title="Earlier parent")
    child = create_hierarchy_work(api, project, work_payload, title="Later child", parent=root)
    bounds = {"created_after": child["work_item"]["created_at"]}
    page = _work_page(api, project, view="roots", **bounds)
    assert page["total"] == 1
    assert page["items"][0]["id"] == root["work_item"]["id"]
    assert page["items"][0]["self_matches_filter"] is False
    assert page["applied_filters"]["work_items"]["created_after"] == bounds["created_after"]


@pytest.mark.parametrize("endpoint", ["unified", "work", "artifact", "transcript"])
def test_date_range_rules_reach_each_rest_front_door(api, project, endpoint):
    bounds = {"updated_after": "2026-09-17T00:00:00Z", "updated_before": "2026-09-16T00:00:00Z"}
    base = f"/api/v1/projects/{project['id']}"
    if endpoint == "unified":
        response = api.post(base + "/search", json={"filters": {"work_items": bounds}})
        location = ["body", "filters", "work_items", "updated_before"]
    elif endpoint == "work":
        response = api.get(base + "/work-items", params=bounds)
        location = ["query", "updated_before"]
    else:
        source = "artifacts" if endpoint == "artifact" else "transcripts"
        payload = bounds | ({"q": "needle"} if endpoint == "artifact" else {})
        response = api.post(base + f"/{source}/search-content", json=payload)
        location = ["body", "updated_before"]
    code = "search_updated_range_invalid"
    assert response.status_code == 422
    assert response.json()["detail"] == [{
        "type": code, "loc": location, "msg": VALIDATION_RULES[code][1],
    }]


@pytest.mark.parametrize("model,endpoint", [
    (SearchRequest, "/search"), (ArtifactSearchRequest, "/artifacts/search-content"),
])
def test_bounded_body_reviewed_rules_never_expose_unreviewed_values(
    api, project, monkeypatch, model, endpoint,
):
    def reject(_cls, *_args, **_kwargs):
        raise ValidationError.from_exception_data("PrivateModel", [{
            "type": PydanticCustomError("search_created_range_invalid", "PRIVATE_MESSAGE",
                                        {"field": "PRIVATE_CONTEXT"}),
            "loc": ("PRIVATE_KEY",), "input": "PRIVATE_INPUT",
        }])

    monkeypatch.setattr(model, "model_validate", classmethod(reject))
    response = api.post(f"/api/v1/projects/{project['id']}" + endpoint, json={})
    code = "search_created_range_invalid"
    assert response.status_code == 422
    assert response.json()["detail"] == [{
        "type": code, "loc": ["body", "field", "created_before"],
        "msg": VALIDATION_RULES[code][1],
    }]
    assert "PRIVATE" not in response.text
