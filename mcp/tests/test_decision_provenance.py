"""Declared code use is explicit and receipt-bound, including integration mappings."""

import copy
import json

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, NOW, PROJECT_ID, WORK_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from test_knowledge_tools import DECISION_UUID, SCOPE_UUID
from test_phase12 import REPORT_INPUT, closeout_fixture, report
from test_tools import adapter, structured

from mnemonic_mcp.phase12_models import JobCompletionReportInput, JobCompletionReportRead
from mnemonic_mcp.phase12_tools import report_matches_request
from mnemonic_mcp.provenance_models import DecisionProvenance
from mnemonic_mcp.repository_identity import canonical_provenance

REPOSITORY = "github.com/team/repository"
DECLARATION = {
    "disposition": "cited", "reason": None,
    "citations": [{"decision_id": "ADR-00001", "revision": 0}],
    "commits": [{"repository": REPOSITORY, "commit_hash": "a" * 40}],
}


def scope(**changes):
    return {
        "id": SCOPE_UUID, "project_id": PROJECT_ID, "document_id": DECISION_UUID,
        "decision_id": "ADR-00001", "revision": 0, "commit_hash": "a" * 40,
        "work_item_id": WORK_ID, "agent_session_id": "native-thread", "created_at": NOW,
        "repository": REPOSITORY, "merged_commit_hashes": ["b" * 40], **changes,
    }


@pytest.mark.parametrize("declaration", [
    {"disposition": "cited", "citations": [], "commits": []},
    {"disposition": "no-applicable-decisions", "reason": "  "},
    {"disposition": "not-code", "reason": "Planning", "commits": DECLARATION["commits"]},
    {"disposition": "no-applicable-decisions", "reason": "None", "citations": DECLARATION["citations"]},
    DECLARATION | {"citations": DECLARATION["citations"] * 2},
    DECLARATION | {"commits": [{"repository": "https://secret@github.com/team/repo", "commit_hash": "a" * 40}]},
    DECLARATION | {"commits": [{"repository": "github.com/team/\ud800", "commit_hash": "a" * 40}]},
    DECLARATION | {"citations": [{"decision_id": "ADR-00001-0", "revision": 0}]},
])
def test_incoherent_provenance_is_rejected(declaration):
    with pytest.raises(ValidationError):
        DecisionProvenance.model_validate(declaration)


def test_pair_bound_prevents_an_unbounded_atomic_scope_fanout():
    declaration = DECLARATION | {
        "citations": [{"decision_id": f"ADR-{number:05d}", "revision": 0} for number in range(1, 27)],
        "commits": [{"repository": REPOSITORY, "commit_hash": f"{number:040x}"}
                    for number in range(1, 21)],
    }
    with pytest.raises(ValidationError, match="500"):
        DecisionProvenance.model_validate(declaration)


def test_historical_report_omission_remains_exact_and_unknown():
    parsed = JobCompletionReportInput.model_validate(REPORT_INPUT)
    assert parsed.decision_provenance is None
    assert parsed.model_dump(mode="json") == REPORT_INPUT
    assert report_matches_request(JobCompletionReportRead.model_validate(report()), parsed)


def test_report_receipt_validation_detects_changed_provenance():
    requested = JobCompletionReportInput.model_validate(REPORT_INPUT | {
        "decision_provenance": DECLARATION,
    })
    actual = report() | {"decision_provenance": copy.deepcopy(DECLARATION)}
    assert report_matches_request(JobCompletionReportRead.model_validate(actual), requested)
    actual["decision_provenance"]["citations"][0]["revision"] = 1
    assert not report_matches_request(JobCompletionReportRead.model_validate(actual), requested)


async def test_complete_work_forwards_provenance_in_the_same_receipt_intent(
    settings, work_item, checkpoint,
):
    tool, args, response = closeout_fixture("done", work_item, checkpoint)
    args["job_completion_report"]["decision_provenance"] = DECLARATION
    response["job_completion_report"]["decision_provenance"] = canonical_provenance(DECLARATION)

    def handler(request):
        body = json.loads(request.content)
        assert body["job_completion_report"]["decision_provenance"] == DECLARATION
        assert body["client_operation_id"] == CLIENT_OPERATION_ID
        return httpx.Response(200, json=response)

    result = structured(await adapter(settings, handler).call_tool(tool, args))
    assert result["job_completion_report"]["decision_provenance"] == canonical_provenance(DECLARATION)


async def test_commit_lookup_resolves_merged_hash_without_replacing_original(settings):
    def handler(request):
        assert request.url.path.endswith("/knowledge/commits")
        assert request.url.params["repository"] == REPOSITORY
        assert request.url.params["commit_hash"] == "b" * 40
        return stream_json({"items": [scope()], "total": 1, "limit": 50, "offset": 0})

    result = structured(await adapter(settings, handler).call_tool("decision_commit_lookup", {
        "project_id": PROJECT_ID, "repository": REPOSITORY, "commit_hash": "B" * 40,
    }))
    assert result["items"][0]["commit_hash"] == "a" * 40


@pytest.mark.parametrize("changes", [{"repository": "github.com/another/repo"},
                                     {"merged_commit_hashes": []}, {"project_id": WORK_ID}])
async def test_commit_lookup_rejects_other_repository_or_hash_evidence(settings, changes):
    response = {"items": [scope(**changes)], "total": 1, "limit": 50, "offset": 0}
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(response)).call_tool(
            "decision_commit_lookup", {
                "project_id": PROJECT_ID, "repository": REPOSITORY, "commit_hash": "b" * 40,
            },
        )


async def test_reconcile_receipt_keeps_original_and_integrated_commits(settings):
    args = {
        "project_id": PROJECT_ID, "repository": REPOSITORY,
        "original_commit_hash": "a" * 40, "merged_commit_hash": "b" * 40,
        "client_operation_id": CLIENT_OPERATION_ID,
    }

    def handler(request):
        assert request.url.path.endswith("/knowledge/commits/reconcile")
        assert json.loads(request.content) == {key: value for key, value in args.items()
                                              if key != "project_id"}
        return httpx.Response(200, json={
            **{key: value for key, value in args.items() if key != "client_operation_id"},
            "scope_ids": [SCOPE_UUID], "created": True,
        })

    result = structured(await adapter(settings, handler).call_tool("decision_scope_reconcile", args))
    assert result["original_commit_hash"] == "a" * 40
    assert result["merged_commit_hash"] == "b" * 40


@pytest.mark.parametrize("stored", [REPOSITORY, "https://github.com/team/repository",
                                     "HTTP://GitHub.com/Team/Repository.git/"])
async def test_scope_append_matches_alias_response_without_rewriting_intent(settings, stored):
    args = {
        "project_id": PROJECT_ID, "decision_id": "ADR-00001", "revision": 0,
        "commit_hash": "a" * 40, "repository": REPOSITORY,
        "work_item_id": WORK_ID, "agent_session_id": "native-thread",
        "client_operation_id": CLIENT_OPERATION_ID,
    }

    def handler(request):
        assert json.loads(request.content)["repository"] == REPOSITORY
        return httpx.Response(200, json={"scope": scope(repository=stored), "created": True})

    result = structured(await adapter(settings, handler).call_tool("decision_scope_append", args))
    assert result["scope"]["repository"] == stored
    assert args["repository"] == REPOSITORY


async def test_lookup_accepts_legacy_alias_but_returns_its_original_spelling(settings):
    stored = "HTTP://GitHub.com/Team/Repository.git/"
    response = {"items": [scope(repository=stored)], "total": 1, "limit": 50, "offset": 0}
    result = structured(await adapter(settings, lambda request: stream_json(response)).call_tool(
        "decision_commit_lookup", {
            "project_id": PROJECT_ID, "repository": REPOSITORY, "commit_hash": "a" * 40,
        },
    ))
    assert result["items"][0]["repository"] == stored


@pytest.mark.parametrize("repository", [None, "https://gitlab.com/team/repository",
                                        "ssh://github.com/team/repository"])
async def test_lookup_never_matches_unknown_or_other_host_identity(settings, repository):
    response = {"items": [scope(repository=repository)], "total": 1, "limit": 50, "offset": 0}
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(response)).call_tool(
            "decision_commit_lookup", {
                "project_id": PROJECT_ID, "repository": REPOSITORY, "commit_hash": "a" * 40,
            },
        )


async def test_duplicate_canonical_pair_error_gives_new_intent_guidance(
    settings, work_item, checkpoint,
):
    tool, args, _ = closeout_fixture("done", work_item, checkpoint)
    args["job_completion_report"]["decision_provenance"] = DECLARATION
    response = {"detail": {"code": "decision_provenance_duplicate_commits",
                           "message": "Duplicate commit aliases", "context": {}}}
    with pytest.raises(ToolError, match="Remove the duplicate pair.*new closeout intent"):
        await adapter(settings, lambda request: httpx.Response(422, json=response)).call_tool(
            tool, args,
        )
