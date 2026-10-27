"""Artifact labels share the catalog while preserving mutation and search boundaries."""

import copy
import json
import runpy
from pathlib import Path

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_artifact_metadata_receipts import run_response
from test_artifacts import ARTIFACT_ID, artifact, call, search_page, upload_arguments
from test_unified_search import artifact_hit, page
from test_upload_grants import intent, send

from mnemonic_mcp.upload_grants import canonical, issue_token, verify_token


@pytest.mark.parametrize("tool", ["upload_artifact", "replace_artifact", "update_artifact"])
@pytest.mark.parametrize("tags", [["research", "reference"], None, "omitted"])
async def test_assignment_changes_preserve_explicit_null_and_omission(settings, tool, tags):
    args = upload_arguments()
    metadata_field = "X-Artifact-Metadata"
    revision = 1
    if tool != "upload_artifact":
        args.update(artifact_id=ARTIFACT_ID, expected_revision=1)
        revision = 2
    if tool == "update_artifact":
        for field in ("filename", "content_base64", "work_item_id"):
            args.pop(field)
    if tags != "omitted":
        args["tags"] = tags
    expected = ["existing"] if tags == "omitted" and revision == 2 else tags
    if expected == "omitted":
        expected = None
    seen = []

    def handler(request):
        body = (json.loads(request.content) if tool == "update_artifact"
                else json.loads(request.headers[metadata_field]))
        seen.append(body)
        assert ("tags" in body) == (tags != "omitted")
        if tags != "omitted":
            assert body["tags"] == tags
        return httpx.Response(201 if revision == 1 else 200,
                              json=artifact(tags=expected, revision=revision))

    result = await call(settings, tool, args, handler)
    assert result["tags"] == expected
    assert len(seen) == 1


@pytest.mark.parametrize("tool", ["upload_artifact", "replace_artifact", "update_artifact"])
@pytest.mark.parametrize("tags", [[], ["Not Normalized"], ["same", "same"], ["a"] * 21])
async def test_invalid_assignments_never_dispatch(settings, tool, tags):
    args = upload_arguments(tags=tags)
    if tool != "upload_artifact":
        args.update(artifact_id=ARTIFACT_ID, expected_revision=1)
    if tool == "update_artifact":
        for field in ("filename", "content_base64", "work_item_id"):
            args.pop(field)
    with pytest.raises(ToolError):
        await call(settings, tool, args, lambda request: pytest.fail("Invalid tags dispatched"))


@pytest.mark.parametrize("tool", ["upload_artifact", "replace_artifact", "update_artifact"])
async def test_receipt_must_echo_the_requested_assignment(settings, tool):
    args = upload_arguments(tags=["research"])
    revision = 1
    if tool != "upload_artifact":
        args.update(artifact_id=ARTIFACT_ID, expected_revision=1)
        revision = 2
    if tool == "update_artifact":
        for field in ("filename", "content_base64", "work_item_id"):
            args.pop(field)
    with pytest.raises(ToolError, match="unknown outcome"):
        await call(settings, tool, args, lambda request: httpx.Response(
            201 if revision == 1 else 200, json=artifact(tags=None, revision=revision),
        ))


@pytest.mark.parametrize("replay", ["true", "false"])
@pytest.mark.parametrize("tool", ["upload_artifact", "update_artifact"])
async def test_only_historical_receipts_can_omit_tags(settings, replay, tool):
    payload = artifact(revision=1 if tool == "upload_artifact" else 2)
    payload.pop("tags")
    args = upload_arguments()
    if tool == "update_artifact":
        args.update(artifact_id=ARTIFACT_ID, expected_revision=1)
        for field in ("filename", "content_base64", "work_item_id"):
            args.pop(field)
    response = httpx.Response(201 if tool == "upload_artifact" else 200, json=payload, headers={
        "X-Client-Operation-ID": CLIENT_OPERATION_ID, "X-Artifact-Operation-Replayed": replay,
    })
    if replay == "true":
        assert (await run_response(settings, tool, args, response))["tags"] is None
    else:
        with pytest.raises(ToolError, match="unknown outcome"):
            await run_response(settings, tool, args, response)


@pytest.mark.parametrize("tool", ["list_artifacts", "search_artifact_contents", "search"])
@pytest.mark.parametrize("mode,assigned,valid", [
    ("or", ["research"], True), ("and", ["research", "reference"], True),
    ("or", None, False), ("and", ["research"], False),
])
async def test_tag_filters_forward_and_reject_out_of_scope_results(settings, tool, mode, assigned, valid):
    selected = ["research", "reference"]
    args = {"project_id": PROJECT_ID, "tags": selected, "tag_mode": mode}
    if tool == "search":
        args = {"project_id": PROJECT_ID, "q": "report",
                "filters": {"artifacts": {"tags": selected, "tag_mode": mode}}}
    elif tool == "search_artifact_contents":
        args["q"] = "report"

    def handler(request):
        if tool == "list_artifacts":
            assert request.url.params.get_list("tags") == selected
            assert request.url.params.get("tag_mode", "or") == mode
            body = {"items": [artifact(tags=assigned)], "total": 1, "limit": 50, "offset": 0}
        else:
            requested = json.loads(request.content)
            filters = requested["filters"]["artifacts"] if tool == "search" else requested
            assert filters["tags"] == selected and filters.get("tag_mode", "or") == mode
            if tool == "search":
                hit = artifact_hit()
                hit["artifact"]["artifact"]["tags"] = assigned
                body = page([hit])
            else:
                body = search_page(items=[{"artifact": artifact(tags=assigned), "score": 1.0,
                                           "snippet": None, "matched_fields": ["metadata"]}], total=1)
        return httpx.Response(200, json=body)

    if valid:
        result = await call(settings, tool, args, handler)
        assert len(result["items"]) == 1
    else:
        with pytest.raises(ToolError):
            await call(settings, tool, args, handler)


@pytest.mark.parametrize("tags", [None, ["research"]])
async def test_upload_grant_binds_tags_and_gateway_forwards_them(settings, tags):
    uploaded = intent()
    raw = json.loads(canonical(uploaded))
    raw["metadata"]["tags"] = tags
    uploaded = type(uploaded).model_validate(raw)
    token, _ = issue_token(uploaded, "fixture-key")
    changed = copy.deepcopy(raw)
    changed["metadata"].pop("tags")
    assert not verify_token(token, type(uploaded).model_validate(changed), "fixture-key")
    response, requests = await send(settings, uploaded)
    assert response.status_code == 201
    assert json.loads(requests[0].headers["X-Artifact-Metadata"])["tags"] == tags
    assert response.json()["tags"] == tags


def upload_helper():
    return runpy.run_path(str(Path(__file__).parents[2] / "plugin/scripts/upload_artifact.py"))


@pytest.mark.parametrize("tags", [[], ["UPPER"], ["same", "same"], "research", ["a" * 51]])
def test_helper_rejects_invalid_tag_metadata(tags):
    helper = upload_helper()
    with pytest.raises(helper["UploadError"], match="Tags"):
        helper["validate_metadata"]({"tags": tags})


@pytest.mark.parametrize("tags", [None, ["research"]])
def test_helper_requires_tag_receipt_match(tags):
    helper = upload_helper()
    manifest = {"artifact_id": ARTIFACT_ID, "metadata": {"tags": tags}}
    helper["verify_metadata"]({"tags": tags}, manifest)
    with pytest.raises(ValueError, match="metadata mismatch"):
        helper["verify_metadata"]({}, manifest)
