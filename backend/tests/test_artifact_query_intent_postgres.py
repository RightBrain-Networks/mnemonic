"""Exact artifact intent preserves boundaries, attribution and content access."""

import pytest

from .test_artifact_search_postgres import artifact_storage as artifact_storage
from .test_artifact_search_postgres import extract, search
from .test_artifacts_postgres import collection, headers, upload

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("mode,query", [("terms", '"admission cookie"'),
                                       ("phrase", "admission cookie")])
def test_phrase_order_and_metadata_boundaries(api, project, artifact_storage, mode, query):
    correct = upload(api, project, filename="correct.txt", body=b"An admission cookie expires")
    extract(api, artifact_storage)
    upload(api, project, filename="reverse.txt", body=b"A cookie admission expires")
    extract(api, artifact_storage)
    upload(api, project, filename="admission", metadata={"description": "cookie"})
    page = search(api, project, query, query_mode=mode, fulltext=True)
    assert page["total"] == 1
    assert page["items"][0]["artifact"]["id"] == correct["id"]
    assert "admission cookie" in page["items"][0]["snippet"]
    assert page["query_interpretation"]["query_mode"] == mode
    assert page["warnings"] == []
    assert search(api, project, "admission cookie", fulltext=True)["total"] == 3


def test_phrase_attribution_requires_a_complete_phrase_in_that_field(
    api, project, artifact_storage,
):
    upload(api, project, filename="admission", body=b"admission cookie")
    extract(api, artifact_storage)
    hit = search(api, project, '"admission cookie"', fulltext=True)["items"][0]
    assert hit["matched_fields"] == ["content"]
    metadata = upload(api, project, filename="admission cookie", body=b"cookie admission")
    extract(api, artifact_storage)
    hit = search(api, project, '"admission cookie"', fulltext=True,
                 artifact_id=metadata["id"])["items"][0]
    assert hit["matched_fields"] == ["metadata"]
    assert hit["snippet"] is None


@pytest.mark.parametrize("needle,decoy", [
    ("lease_token_mismatch", "lease token mismatch"),
    ("Lease_token_mismatch", "lease_token_mismatch"),
    ("rate=50%_done", "rate=5000done"),
    ("[]{}", "something else"),
])
def test_literal_preserves_case_punctuation_and_sql_wildcards(
    api, project, artifact_storage, needle, decoy,
):
    correct = upload(api, project, filename="exact.txt", body=needle.encode())
    extract(api, artifact_storage)
    upload(api, project, filename="decoy.txt", body=decoy.encode())
    extract(api, artifact_storage)
    page = search(api, project, needle, query_mode="literal", fulltext=True)
    assert page["total"] == 1
    assert page["items"][0]["artifact"]["id"] == correct["id"]
    assert needle in page["items"][0]["snippet"]
    assert page["match_mode"] == "literal"
    assert search(api, project, needle, query_mode="literal")["total"] == 0


@pytest.mark.parametrize("mode", ["phrase", "literal"])
def test_exact_search_withholds_sensitive_bodies(api, project, artifact_storage, mode):
    upload(api, project, filename="private.txt", body=b"private session cookie",
           metadata={"sensitive": True})
    extract(api, artifact_storage)
    page = search(api, project, "private session cookie", query_mode=mode, fulltext=True)
    assert page["total"] == 0
    assert page["sensitive_content_withheld"] == 1
    assert page["indexing"]["ready"] == 1


def test_literal_replacement_removes_stale_content(api, project, artifact_storage):
    artifact = upload(api, project, filename="record.txt", body=b"old_exact_identifier")
    extract(api, artifact_storage)
    assert search(api, project, "old_exact_identifier", query_mode="literal",
                  fulltext=True)["total"] == 1
    path = collection(project) + "/" + artifact["id"]
    response = api.put(path + "/content", content=b"new_exact_identifier",
                       headers=headers({"filename": "record.txt"}, revision=1))
    assert response.status_code == 200, response.text
    assert search(api, project, "old_exact_identifier", query_mode="literal",
                  fulltext=True)["total"] == 0
    extract(api, artifact_storage)
    assert search(api, project, "new_exact_identifier", query_mode="literal",
                  fulltext=True)["total"] == 1


def test_unified_artifact_search_applies_exact_intent(api, project, artifact_storage):
    upload(api, project, filename="note.txt", body=b"admission cookie")
    extract(api, artifact_storage)
    path = f"/api/v1/projects/{project['id']}/search"
    for mode in ("phrase", "literal"):
        page = api.post(path, json={"q": "cookie admission", "query_mode": mode,
                                    "facets": ["artifacts"], "fulltext": True})
        assert page.status_code == 200, page.text
        assert page.json()["total"] == 0


@pytest.mark.parametrize("mode,query", [("terms", '"cafe cookie"'),
                                       ("phrase", "cafe cookie")])
@pytest.mark.parametrize("endpoint", ["content", "unified"])
@pytest.mark.parametrize("decoy", ["cookie cafe", "cafe " + "x" * 201 + " cookie"])
def test_phrase_snippet_rejects_reversed_decoy_before_accented_punctuation_match(
    api, project, artifact_storage, mode, query, endpoint, decoy,
):
    body = decoy + " decoy " + "irrelevant context " * 30 + "CAFÉ-cookie actual evidence"
    upload(api, project, filename="record.txt", body=body.encode())
    extract(api, artifact_storage)
    if endpoint == "content":
        result = search(api, project, query, query_mode=mode, fulltext=True)
        hit = result["items"][0]
    else:
        response = api.post(f"/api/v1/projects/{project['id']}/search", json={
            "q": query, "query_mode": mode, "fulltext": True, "facets": ["artifacts"],
        })
        assert response.status_code == 200, response.text
        result = response.json()
        hit = result["items"][0]["artifact"]
    assert result["total"] == 1 and hit["matched_fields"] == ["content"]
    assert "CAFÉ-cookie actual evidence" in hit["snippet"]
    assert "decoy" not in hit["snippet"]
