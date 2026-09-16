"""Content-kind filters bind both search front doors, including blank discovery."""

import json

import pytest

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import collection, register, run

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("query", ["", "needle"])
@pytest.mark.parametrize("detail", ["compact", "full"])
def test_content_kind_browse_and_match_have_identical_scope(
    api, project, work_payload, tmp_path, postgres_engine, query, detail,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    source.write_text(json.dumps({"role": "user", "content": [
        {"type": "tool_result", "content": "needle in tool output"},
    ]}))
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    for kind, total in [("human_text", 0), ("tool_result", 1)]:
        dedicated = api.post(collection(project) + "/search-content", json={
            "query": query, "fulltext": True, "content_kinds": [kind], "detail": detail,
        })
        unified = api.post(f"/api/v1/projects/{project['id']}/search", json={
            "q": query, "fulltext": True, "detail": detail, "facets": ["transcripts"],
            "filters": {"transcripts": {"content_kinds": [kind]}},
        })
        for response in (dedicated, unified):
            assert response.status_code == 200, response.text
            page = response.json()
            assert page["total"] == total
            assert page["applied_filters"]["transcripts"]["content_kinds"] == [kind]
        if total:
            assert dedicated.json()["items"][0]["id"] == record["id"]
            assert unified.json()["items"][0]["id"] == record["id"]


@pytest.mark.parametrize("unified", [False, True])
def test_kind_filter_validation_names_the_actual_rule(api, project, unified):
    path = (f"/api/v1/projects/{project['id']}/search" if unified
            else collection(project) + "/search-content")
    kinds = {"content_kinds": ["human_text"]}
    payload = ({"q": "needle", "facets": ["transcripts"],
                "filters": {"transcripts": kinds}} if unified else {"query": "needle", **kinds})
    response = api.post(path, json=payload)
    assert response.status_code == 422, response.text
    assert "content_kinds_requires_fulltext" in response.text
    assert "content_kinds requires fulltext=true." in response.text
    kinds["content_kinds"] *= 2
    response = api.post(path, json={**payload, "fulltext": True,
                                   **({} if unified else kinds)})
    assert response.status_code == 422, response.text
