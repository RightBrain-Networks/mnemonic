import json

import httpx
import pytest
from conftest import NOW, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_artifacts import ARTIFACT_ID, artifact, call, search_page


async def test_search_defaults_to_metadata_and_does_not_require_a_receipt(settings):
    def handler(request):
        assert json.loads(request.content) == {
            "q": "report", "fulltext": False, "include_deleted": False, "limit": 50, "offset": 0,
        }
        return httpx.Response(200, json=search_page())

    result = await call(settings, "search_artifact_contents", {
        "project_id": PROJECT_ID, "query": "report",
    }, handler)
    assert result["items"] == [] and result["fulltext"] is False


@pytest.mark.parametrize("failure", [
    "project", "artifact", "deleted", "content-opt-in", "snippet-opt-in", "fulltext", "duplicate",
    "page", "fields", "score", "snippet", "extraction", "count", "encoded", "oversize", "status",
])
async def test_search_rejects_malformed_or_out_of_scope_results(settings, failure):
    row = {"artifact": artifact(), "score": 1.0, "snippet": None, "matched_fields": ["metadata"]}
    page = search_page(items=[row], total=1)
    changes = {
        "project": (row["artifact"], "project_id", WORK_ID),
        "artifact": (row["artifact"], "id", WORK_ID),
        "deleted": (row["artifact"], "deleted_at", row["artifact"]["created_at"]),
        "content-opt-in": (row, "matched_fields", ["content"]),
        "snippet-opt-in": (row, "snippet", "private content outside scope"),
        "fulltext": (page, "fulltext", True),
        "page": (page, "offset", 1),
        "fields": (row, "matched_fields", ["metadata", "metadata"]),
        "score": (row, "score", -1), "snippet": (row, "snippet", "x" * 1001),
        "extraction": (row["artifact"]["extraction"], "metadata", {"title": ["x" * 513]}),
        "count": (page["indexing"], "pending", True),
    }
    if failure in changes:
        target, key, value = changes[failure]
        target[key] = value
    if failure == "duplicate":
        page["items"] = [row, row]
        page["total"] = 2

    def handler(request):
        if failure == "encoded":
            return httpx.Response(200, json=page, headers={"content-encoding": "identity, gzip"})
        if failure == "oversize":
            return httpx.Response(200, json=page, headers={"content-length": "4194305"})
        return httpx.Response(201 if failure == "status" else 200, json=page)

    with pytest.raises(ToolError):
        await call(settings, "search_artifact_contents", {
            "project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID, "query": "report",
        }, handler)


async def test_failed_search_remains_a_safe_read_and_never_echoes_upstream_data(settings):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("private extraction diagnostics", request=request)

    with pytest.raises(ToolError, match="safe read") as raised:
        await call(settings, "search_artifact_contents", {
            "project_id": PROJECT_ID, "query": "report", "fulltext": True,
        }, handler)
    assert len(requests) == 1
    assert "private extraction diagnostics" not in str(raised.value)
    assert "unknown outcome" not in str(raised.value)


async def test_history_accepts_two_full_pages_with_bounded_extracted_metadata(settings):
    description = "🗎" * 4000
    extraction = {"status": "ready", "metadata": {"title": ["a" * 500] * 8,
                                                   "author": ["b" * 500] * 8},
                  "truncated": False, "error_code": None, "extracted_at": NOW}
    common = {"artifact_id": ARTIFACT_ID, "filename": "a" * 255, "description": description,
              "agent_session_id": "🗎" * 200, "actor_client": "🗎" * 80, "created_at": NOW}
    revisions = [{**common, "revision": revision, "size_bytes": 2, "sha256": "a" * 64,
                  "mime_type": "text/plain", "related_work_item_ids": [],
                  "related_artifact_ids": [], "sensitive": False,
                  "extraction": extraction} for revision in range(1, 101)]
    audit = [{**common, "id": revision, "revision": revision, "action": "replaced", "details": {}}
             for revision in range(1, 101)]
    payload = {"revisions": {"items": revisions, "total": 100, "limit": 100, "offset": 0},
               "audit": {"items": audit, "total": 100, "limit": 100, "offset": 0}}
    response = httpx.Response(200, json=payload)
    assert 4 * 1024 * 1024 < len(response.content) < 6 * 1024 * 1024
    result = await call(settings, "list_artifact_history", {
        "project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID, "limit": 100,
    }, lambda request: response)
    assert result["revisions"]["items"][0]["extraction"]["metadata"] == extraction["metadata"]
    assert len(result["audit"]["items"]) == 100


async def test_search_omits_verbose_properties_but_preserves_identity_and_coverage(settings):
    properties = {f"pdf:property-{index}": ["private document property " * 5] for index in range(45)}
    extraction = {"status": "ready", "metadata": properties, "truncated": True,
                  "error_code": None, "extracted_at": NOW}
    source = artifact(description="private long description " * 100, extraction=extraction)
    hit = {"artifact": source, "score": 2.5, "snippet": "Find this content " * 17,
           "matched_fields": ["metadata", "content"]}
    payload = search_page(items=[hit], total=1, fulltext=True)
    payload["indexing"] = {"ready": 1, "pending": 0, "failed": 0, "truncated": 1}
    result = await call(settings, "search_artifact_contents", {
        "project_id": PROJECT_ID, "query": "content", "fulltext": True,
    }, lambda request: httpx.Response(200, json=payload))
    compact = result["items"][0]
    assert len(json.dumps(compact)) < 1200
    assert compact["artifact"]["id"] == ARTIFACT_ID
    assert compact["artifact"]["project_id"] == PROJECT_ID
    assert compact["artifact"]["revision"] == source["revision"]
    assert compact["artifact"]["sha256"] == source["sha256"]
    assert compact["artifact"]["extraction"]["truncated"] is True
    assert result["indexing"]["truncated"] == 1
    assert compact["snippet"] == hit["snippet"]
    assert compact["matched_fields"] == hit["matched_fields"]
    assert "metadata" not in compact["artifact"]["extraction"]
    assert "description" not in compact["artifact"]
    detail = await call(settings, "get_artifact", {
        "project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID,
    }, lambda request: httpx.Response(200, json=source))
    assert detail["extraction"]["metadata"] == properties
    assert detail["description"] == source["description"]
