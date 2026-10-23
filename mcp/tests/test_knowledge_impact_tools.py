"""Impact reads retain revision evidence; assessment writes preserve exact intent."""

import copy
import json

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, NOW, PROJECT_ID, WORK_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_knowledge_tools import arguments, decision
from test_tools import adapter, structured

ASSESSMENT_ID = "a15e657a-1143-45fe-865c-512cdbac7804"
DISPOSITION_ID = "b7e78d38-3278-4dc1-ae18-f33eaf00060d"
ACTOR = {"actor_client": "codex", "actor_session_id": "native-thread", "actor_model": "model"}
PIN = {"decision_id": "ADR-00002", "revision": 3, "rationale": "Queue ordering constraint"}


def candidate(**changes):
    return {
        "decision_id": "ADR-00001", "reference": "ADR-00001-0", "revision": 0,
        "title": "Queue order", "state": "accepted", "is_current": False,
        "relationship": "origin", "path": ["ADR-00001-0"], "rationale": None,
        "scopes": [], "related_work_item_ids": [], "related_artifact_ids": [],
        "evidence_truncated": False, **changes,
    }


def coverage():
    return {
        "traversal_complete": True, "provenance_complete": False, "max_depth": 8,
        "candidate_limit": 500, "visited_revisions": 1, "reasons": [], "warnings": [],
        "related_semantics": "unknown",
        "snapshot_at": NOW,
        "notes": [
            "Only recorded dependencies and implementation citations are discoverable.",
            "Related-only records have unknown causal relevance.",
        ],
        "path_policy": "one deterministic shortest path per revision",
    }


def author(reference):
    return {"reference": reference, "author_kind": "agent", "agent_session_id": "native-thread",
            "model_id": "test-model", "authored_at": NOW, "work_observed_at": NOW,
            "active_work_items": [], "active_work_items_truncated": False}


def impact(**changes):
    return {
        "project_id": PROJECT_ID, "origin_reference": "ADR-00001-0",
        "origin_author": author("ADR-00001-0"), "kind": "code-impact",
        "candidate_count": 1, "context_count": 0,
        "items": [candidate()], "total": 1, "limit": 50, "offset": 0,
        "next_offset": None, "coverage": coverage(), **changes,
    }


def assessment(**changes):
    return {
        "id": ASSESSMENT_ID, "project_id": PROJECT_ID, "trigger": "revision",
        "before_reference": "ADR-00001-0", "after_reference": "ADR-00001-1",
        "extent": None, "reason": None, "candidate_count": 1, "context_count": 0,
        "before_author": author("ADR-00001-0"), "after_author": author("ADR-00001-1"),
        "diff": {"changed_fields": ["content"], "before_sha256": "a" * 64,
                 "after_sha256": "b" * 64, "unified_diff": "-old\n+new\n", "truncated": False},
        "coverage": coverage(), "created_at": NOW, "version": 0,
        "unassessed_count": 1, "change_required_count": 0, "status": "pending", **changes,
    }


def disposition(**changes):
    return {
        "id": DISPOSITION_ID, "project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID,
        "version": 1, "candidate_reference": "ADR-00001-0", "disposition": "change-required",
        "reason": "Queue order changed; inspect consumers", "remediation_work_item_id": WORK_ID,
        "actor": ACTOR, "created_at": NOW, **changes,
    }


def disposition_arguments():
    return {
        "project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID,
        "candidate_reference": "ADR-00001-0", "expected_version": 0,
        "disposition": "change-required", "reason": "Queue order changed; inspect consumers",
        "remediation_work_item_id": WORK_ID, "actor": ACTOR,
        "client_operation_id": CLIENT_OPERATION_ID,
    }


async def test_put_retains_target_revision_and_rationale(settings):
    def handler(request):
        body = json.loads(request.content)
        assert body["depends_on"] == [PIN]
        assert "supersession_details" not in body
        return httpx.Response(200, json={"decision": decision(depends_on=[PIN]), "created": True})

    result = structured(await adapter(settings, handler).call_tool(
        "decision_put", arguments() | {"depends_on": [PIN]},
    ))
    assert result["decision"]["depends_on"][0]["revision"] == 3


async def test_put_cannot_confirm_a_success_that_retargets_a_dependency(settings):
    server = adapter(settings, lambda request: httpx.Response(200, json={
        "decision": decision(depends_on=[PIN | {"revision": 4}]), "created": True,
    }))
    with pytest.raises(ToolError, match="operation may already have committed"):
        await server.call_tool("decision_put", arguments() | {"depends_on": [PIN]})


@pytest.mark.parametrize("legacy_links", [[], ["ADR-00002"], ["ADR-00003"]])
async def test_put_supersession_details_add_legacy_links(settings, legacy_links):
    detail = {"decision_id": "ADR-00002", "revision": 0, "extent": "partial",
              "reason": "Replaces only the queue ordering rule"}
    returned_links = sorted(set(legacy_links) | {detail["decision_id"]})

    def handler(request):
        body = json.loads(request.content)
        assert body["supersession_details"] == [detail]
        assert body["supersedes"] == legacy_links
        return httpx.Response(200, json={
            "decision": decision(supersedes=returned_links, supersession_details=[detail]),
            "created": True,
        })

    result = structured(await adapter(settings, handler).call_tool(
        "decision_put", arguments() | {
            "supersedes": legacy_links, "supersession_details": [detail],
        },
    ))
    assert result["decision"]["supersedes"] == returned_links


async def test_put_rejects_unrequested_supersedes_link(settings):
    detail = {"decision_id": "ADR-00002", "revision": 0, "extent": "partial",
              "reason": "Replaces only the queue ordering rule"}
    response = {"decision": decision(supersedes=["ADR-00002", "ADR-00003"],
                                     supersession_details=[detail]), "created": True}
    with pytest.raises(ToolError, match="operation may already have committed"):
        await adapter(settings, lambda request: httpx.Response(200, json=response)).call_tool(
            "decision_put", arguments() | {"supersession_details": [detail]},
        )


async def test_impact_fetch_pins_origin_and_discloses_incomplete_provenance(settings):
    def handler(request):
        assert request.url.path.endswith("/decisions/ADR-00001-0/impact")
        assert dict(request.url.params) == {"max_depth": "8", "limit": "50", "offset": "0", "kind": "code-impact"}
        return stream_json(impact())

    result = structured(await adapter(settings, handler).call_tool("decision_impact", {
        "project_id": PROJECT_ID, "decision_id": "ADR-00001-0",
    }))
    assert result["coverage"]["traversal_complete"]
    assert result["coverage"]["provenance_complete"] is False


@pytest.mark.parametrize("changes", [
    {"project_id": WORK_ID}, {"origin_reference": "ADR-00001-1"}, {"next_offset": 1},
    {"items": [candidate(reference="ADR-00001-1")]},
    {"items": [candidate(path=["ADR-00002-0", "ADR-00001-0"])]},
])
async def test_impact_rejects_response_scope_or_pagination_drift(settings, changes):
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(impact(**changes))).call_tool(
            "decision_impact", {"project_id": PROJECT_ID, "decision_id": "ADR-00001-0"},
        )


async def test_assessment_reads_and_history_stay_with_exact_episode(settings):
    def handler(request):
        if request.url.path.endswith("/dispositions"):
            return stream_json({"items": [disposition()], "total": 1, "limit": 50, "offset": 0})
        if request.url.path.endswith(ASSESSMENT_ID):
            return stream_json(assessment(
                version=1, kind="code-impact", total=1, candidates=[candidate()],
                dispositions=[disposition()],
                disposition_history_total=1, limit=50, offset=0, next_offset=None,
                unassessed_count=0, change_required_count=1, status="change-required",
            ))
        assert request.url.params["decision_id"] == "ADR-00001"
        return stream_json({"items": [assessment()], "total": 1, "limit": 50, "offset": 0})

    server = adapter(settings, handler)
    listed = structured(await server.call_tool("decision_assessments", {
        "project_id": PROJECT_ID, "decision_id": "ADR-00001",
    }))
    detail = structured(await server.call_tool("decision_assessment_get", {
        "project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID,
    }))
    history = structured(await server.call_tool("decision_assessment_history", {
        "project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID,
    }))
    assert listed["items"][0]["id"] == detail["id"]
    assert detail["dispositions"] == history["items"]


async def test_assessment_filter_can_match_a_captured_candidate(settings):
    # The selected decision need not be the episode's origin or replacement.
    response = {"items": [assessment()], "total": 1, "limit": 50, "offset": 0}
    result = structured(await adapter(settings, lambda request: stream_json(response)).call_tool(
        "decision_assessments", {"project_id": PROJECT_ID, "decision_id": "BDR-00004"},
    ))
    assert result["items"][0]["before_reference"] == "ADR-00001-0"


@pytest.mark.parametrize("tool", ["decision_impact", "decision_assessment_get"])
async def test_context_pages_have_independent_counts_and_exact_kind(settings, tool):
    context = candidate(relationship="depends-on-prerequisite")
    response = (
        impact(kind="context", context_count=1, items=[context])
        if tool == "decision_impact" else assessment(
            kind="context", context_count=1, total=1, candidates=[context], dispositions=[],
            disposition_history_total=0, limit=50, offset=0, next_offset=None,
        )
    )

    def handler(request):
        assert request.url.params["kind"] == "context"
        return stream_json(response)

    arguments = {"project_id": PROJECT_ID, "kind": "context"}
    arguments.update({"decision_id": "ADR-00001-0"} if tool == "decision_impact"
                     else {"assessment_id": ASSESSMENT_ID})
    result = structured(await adapter(settings, handler).call_tool(tool, arguments))
    assert result["kind"] == "context" and result["context_count"] == 1
    arguments["kind"] = "code-impact"
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(response)).call_tool(tool, arguments)


@pytest.mark.parametrize("changes", [
    {"reference": "ADR-00001-9"},
    {"author_kind": "human"},
    {"active_work_items": [{"work_item_id": WORK_ID, "title": "Expired", "holder_client": "codex",
                            "purpose": "implementation", "expires_at": NOW}]},
])
async def test_author_context_rejects_mismatched_or_expired_evidence(settings, changes):
    response = impact(origin_author={**author("ADR-00001-0"), **changes})
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(response)).call_tool(
            "decision_impact", {"project_id": PROJECT_ID, "decision_id": "ADR-00001-0"},
        )


async def test_missing_commit_warning_must_match_pinned_origin(settings):
    response = impact(coverage={**coverage(), "warnings": [{
        "code": "origin_has_no_linked_commits", "reference": "ADR-00001-9",
        "message": "An unrelated revision has no linked commits.",
    }]})
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(response)).call_tool(
            "decision_impact", {"project_id": PROJECT_ID, "decision_id": "ADR-00001-0"},
        )


async def test_assessment_cannot_claim_resolved_while_candidates_are_unassessed(settings):
    response = {"items": [assessment(status="resolved")], "total": 1, "limit": 50, "offset": 0}
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(response)).call_tool(
            "decision_assessments", {"project_id": PROJECT_ID},
        )


async def test_assessment_append_freezes_actor_version_and_remediation(settings):
    def handler(request):
        assert request.url.path.endswith(f"/assessments/{ASSESSMENT_ID}/dispositions")
        body = json.loads(request.content)
        assert body == {key: value for key, value in disposition_arguments().items()
                        if key not in {"project_id", "assessment_id"}}
        return httpx.Response(201, json=disposition())

    result = structured(await adapter(settings, handler).call_tool(
        "decision_assessment_append", disposition_arguments(),
    ))
    assert result["version"] == 1 and result["actor"] == ACTOR


@pytest.mark.parametrize("changes", [
    {"version": 2}, {"candidate_reference": "ADR-00001-1"},
    {"actor": ACTOR | {"actor_session_id": "another-thread"}},
    {"disposition": "resolved"}, {"assessment_id": WORK_ID},
])
async def test_assessment_append_mismatch_remains_unknown_outcome(settings, changes):
    with pytest.raises(ToolError, match="operation may already have committed"):
        await adapter(settings, lambda request: httpx.Response(201, json=disposition(**changes))) \
            .call_tool("decision_assessment_append", disposition_arguments())


async def test_assessment_uncertain_write_keeps_generated_id_without_replaying(settings):
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        raise httpx.ReadTimeout("private service detail")

    args = copy.deepcopy(disposition_arguments())
    args.pop("client_operation_id")
    result = await adapter(settings, handler).call_tool("decision_assessment_append", args)
    assert result.isError
    assert len(attempts) == 1
    assert attempts[0]["client_operation_id"] == result.meta["mnemonic_generated_ids"][
        "client_operation_id"
    ]


async def test_assessment_version_conflict_requires_a_read_before_new_intent(settings):
    response = {"detail": {"code": "decision_assessment_version_conflict",
                           "message": "private backend detail", "context": {}}}
    with pytest.raises(ToolError, match="decision_assessment_get") as caught:
        await adapter(settings, lambda request: httpx.Response(409, json=response)).call_tool(
            "decision_assessment_append", disposition_arguments(),
        )
    assert "private backend detail" not in str(caught.value)
    assert "operation may already have committed" not in str(caught.value)
