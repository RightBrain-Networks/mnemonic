"""Every discovery route returns contiguous budgeted pages with complete metadata."""

import json

import pytest

from .test_artifact_search_postgres import artifact_storage as artifact_storage
from .test_artifact_search_postgres import extract
from .test_artifacts_postgres import upload
from .test_transcript_search_postgres import seed_transcripts
from .test_work_items_postgres import collection, create_work

pytestmark = pytest.mark.postgres


def _read(api, project, source, detail, offset=0):
    prefix = f"/api/v1/projects/{project['id']}"
    payload = {"detail": detail, "limit": 4, "offset": offset}
    if source in {"work", "roots"}:
        return api.get(collection(project), params={**payload,
                       "view": "roots" if source == "roots" else "full"})
    if source == "unified":
        return api.post(prefix + "/search", json={**payload, "facets": ["work_items"]})
    if source == "artifacts":
        return api.post(prefix + "/artifacts/search-content", json={**payload, "q": "needle"})
    if source == "list":
        return api.get(prefix + "/transcripts", params=payload)
    return api.post(prefix + "/transcripts/search-content", json=payload)


@pytest.mark.parametrize("source", ["work", "roots", "unified", "artifacts", "transcripts", "list"])
@pytest.mark.parametrize("detail", ["compact", "full"])
def test_discovery_budget_preserves_order_scope_coverage_and_next_offset(
    api, project, work_payload, artifact_storage, monkeypatch, source, detail,
):
    monkeypatch.setattr("mnemonic_api.search_pagination.SEARCH_PAGE_MAX_BYTES", 1024 * 1024)
    if source in {"work", "roots", "unified"}:
        for index in range(4):
            create_work(api, project, work_payload, title=f"Budget needle {index}")
    elif source == "artifacts":
        for index in range(4):
            upload(api, project, filename=f"needle-{index}.txt", body=b"needle evidence")
            extract(api, artifact_storage)
    else:
        seed_transcripts(api, project, "needle evidence", count=4)
    baseline = _read(api, project, source, detail)
    assert baseline.status_code == 200, baseline.text
    original = baseline.json()
    assert len(original["items"]) == 4
    candidate = {**original, "items": original["items"][:2], "page_truncated": True,
                 "next_offset": 2}
    cap = len(json.dumps(candidate, indent=2, ensure_ascii=True).encode())
    monkeypatch.setattr("mnemonic_api.search_pagination.SEARCH_PAGE_MAX_BYTES", cap)
    collected = []
    offset = 0
    while True:
        response = _read(api, project, source, detail, offset)
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(json.dumps(page, indent=2, ensure_ascii=True).encode()) <= cap
        assert page["items"]
        assert page["items"] == original["items"][offset:offset + len(page["items"])]
        assert {key: value for key, value in page.items() if key not in {
            "items", "offset", "next_offset", "page_truncated",
        }} == {key: value for key, value in original.items() if key not in {
            "items", "offset", "next_offset", "page_truncated",
        }}
        collected.extend(page["items"])
        if page["next_offset"] is None:
            break
        assert page["next_offset"] == offset + len(page["items"])
        assert page["page_truncated"]
        offset = page["next_offset"]
    assert collected == original["items"]
