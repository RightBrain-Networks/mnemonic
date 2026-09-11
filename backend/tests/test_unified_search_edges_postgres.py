"""Unified search preserves alias audit identity and pages complete candidate pools."""

import pytest

from .test_artifact_search_postgres import extract
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import collection, upload
from .test_duplicate_handling_postgres import merge_work
from .test_unified_search_postgres import identities, path, search
from .test_work_items_postgres import create_work

pytestmark = pytest.mark.postgres


def test_alias_text_matches_canonical_work_and_retains_member_identity(
    api, project, work_payload,
):
    root = create_work(api, project, work_payload, title="Canonical objective")["work_item"]
    alias = create_work(api, project, work_payload, title="uniquelysearchablealias")["work_item"]
    merge_work(api, project, alias, root)
    result = search(api, project, q="uniquelysearchablealias", facets=["work_items"])
    assert result["total"] == 1 and identities(result) == [root["id"]]
    assert result["items"][0]["work_item"]["matched_member"]["id"] == alias["id"]
    aliases = search(api, project, q="uniquelysearchablealias", facets=["work_items"], filters={
        "work_items": {"duplicate_scope": "aliases", "canonical_work_item_id": root["id"]},
    })
    assert identities(aliases) == [alias["id"]]
    invalid = api.post(path(project), json={"facets": ["work_items"], "filters": {
        "work_items": {"duplicate_scope": "all", "canonical_work_item_id": alias["id"]},
    }})
    assert invalid.status_code == 409
    assert invalid.json()["detail"]["code"] == "work_duplicate"


def test_global_pagination_reaches_work_candidates_after_the_first_hundred(
    api, project, work_payload,
):
    work_ids = [create_work(api, project, work_payload,
                           title=f"Paginationneedle {index}")["work_item"]["id"]
                for index in range(103)]
    artifact = upload(api, project, filename="paginationneedle.txt", body=b"page data")
    payload = {"q": "paginationneedle", "sort": {"by": "created_at", "direction": "asc"}}
    first = search(api, project, limit=100, **payload)
    second = search(api, project, offset=100, limit=100, **payload)
    assert first["total"] == second["total"] == 104
    assert identities(first) + identities(second) == work_ids + [artifact["id"]]
    grouped = search(api, project, offset=100, limit=3, **payload,
                     facet_order=[{"facet": "artifacts"}])
    assert identities(grouped) == work_ids[99:102]
    assert grouped["facet_totals"] == {"work_items": 103, "artifacts": 1, "transcripts": 0}


def test_human_sensitive_search_is_audited_and_does_not_populate_agent_results(
    api, project, artifact_storage,
):
    artifact = upload(api, project, filename="private.txt", body=b"humanonlyneedle",
                      metadata={"sensitive": True})
    extract(api, artifact_storage)
    payload = {"q": "humanonlyneedle", "fulltext": True, "facets": ["artifacts"]}
    human = api.post(path(project), json=payload,
                     headers={"X-Artifact-Access": "human-dashboard"})
    assert human.status_code == 200, human.text
    assert identities(human.json()) == [artifact["id"]]
    assert human.json()["coverage"]["artifacts"]["sensitive_content_withheld"] == 0
    hit = human.json()["items"][0]["artifact"]
    assert hit["matched_fields"] == ["content"] and "humanonlyneedle" in hit["snippet"]
    agent = search(api, project, **payload)
    assert not agent["items"] and agent["coverage"]["artifacts"]["sensitive_content_withheld"] == 1
    history = api.get(collection(project) + "/" + artifact["id"] + "/history").json()
    searches = [entry for entry in history["audit"]["items"]
                if entry["action"] == "sensitive_searched"]
    assert len(searches) == 1
    assert searches[0]["details"]["access_mode"] == "human_dashboard"


def _walk_references(document, value, seen):
    if isinstance(value, list):
        for child in value:
            _walk_references(document, child, seen)
    elif isinstance(value, dict):
        reference = value.get("$ref")
        if reference is not None and reference not in seen:
            assert reference.startswith("#/")
            seen.add(reference)
            target = document
            for part in reference[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            _walk_references(document, target, seen)
        for child in value.values():
            _walk_references(document, child, seen)


def test_search_openapi_request_and_every_response_reference_resolve(api):
    document = api.get("/openapi.json").json()
    operation = document["paths"]["/api/v1/projects/{project_id}/search"]["post"]
    assert operation["x-mnemonic-effect"] == "safe_read"
    request = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request["properties"]["filters"]["properties"]["work_items"]["properties"][
        "semantic"]["default"] is False
    references = set()
    _walk_references(document, operation, references)
    assert "#/components/schemas/SearchPage" in references


@pytest.mark.parametrize("query", [
    "q=text", "limit=1", "client_operation_id=11111111-1111-4111-8111-111111111111",
])
def test_safe_search_does_not_accept_query_parameters(api, project, query):
    response = api.post(path(project) + "?" + query, json={})
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
