"""Replay backend-serialized assessment reads through the strict MCP tool boundary."""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from mnemonic_api.impact_schemas import (
    ImpactAssessmentDetail,
    ImpactAssessmentRead,
    ImpactCandidate,
    ImpactDispositionRead,
    ImpactProjection,
    ReusedDispositionSource,
)
from mnemonic_api.knowledge_schemas import KnowledgePage
from mnemonic_api.revision_author_schemas import RevisionAuthorContext
from mnemonic_api.services.impact_read_projections import (
    author_read,
    candidate_read,
    diff_read,
    disposition_projection,
    projection,
)

REPO = Path(__file__).resolve().parents[2]
PROJECT_ID = UUID("ca501b3f-860b-4f88-bca8-2f22a06359ab")
ASSESSMENT_ID = UUID("a15e657a-1143-45fe-865c-512cdbac7804")
SOURCE_ID = UUID("9b0e1e53-2a04-443d-a37c-d0a19cf760a8")
DISPOSITION_ID = UUID("b7e78d38-3278-4dc1-ae18-f33eaf00060d")
NOW = datetime(2026, 10, 26, tzinfo=UTC)

# A separate process keeps the backend and MCP dependency environments isolated.
# Mock only HTTP transport: call the registered tools and retain strict _read validation.
MCP_REPLAY = """
import asyncio, json, sys
import httpx
from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.config import Settings
from mnemonic_mcp.server import build_server

async def main():
    cases = json.load(sys.stdin)
    settings = Settings(api_url="http://mnemonic.test",
                        api_key="mnemonic-contract-test-" + "x" * 32)
    for case in cases:
        requests = []
        def respond(request):
            requests.append(request)
            assert request.method == "GET"
            assert request.url.path == case["path"]
            stream = httpx.ByteStream(json.dumps(case["response"]).encode())
            return httpx.Response(200, stream=stream, headers={"content-type": "application/json"})
        api = MnemonicAPI(settings, httpx.MockTransport(respond))
        result = await build_server(settings, api).call_tool(case["tool"], case["arguments"])
        assert result[1] == case["response"]
        assert len(requests) == 1

asyncio.run(main())
"""


def assessment(reused_count: int, options: ImpactProjection) -> ImpactAssessmentRead:
    def author(reference: str):
        return author_read(RevisionAuthorContext(
            reference=reference, author_kind="agent", agent_session_id="contract-test",
            model_id=None, authored_at=NOW, work_observed_at=NOW,
            active_work_items=[], active_work_items_truncated=False,
        ), options)

    return ImpactAssessmentRead(
        id=ASSESSMENT_ID, project_id=PROJECT_ID,
        trigger="supersession" if reused_count else "revision",
        before_reference="ADR-00001-0", after_reference="ADR-00001-1",
        before_author=author("ADR-00001-0"), after_author=author("ADR-00001-1"),
        extent=None, reason=None, candidate_count=1, context_count=0,
        diff=diff_read({
            "changed_fields": ["content"], "before_sha256": "a" * 64,
            "after_sha256": "b" * 64, "unified_diff": "-old\n+new\n", "truncated": False,
        }, options),
        projection=projection(options),
        coverage={
            "traversal_complete": True, "max_depth": 8, "candidate_limit": 500,
            "visited_revisions": 1, "reasons": [],
        },
        created_at=NOW, version=0, unassessed_count=1 - reused_count,
        reused_candidate_count=reused_count, change_required_count=0,
        status="resolved" if reused_count else "pending",
    )


def detail(item: ImpactAssessmentRead, options: ImpactProjection) -> ImpactAssessmentDetail:
    candidate = candidate_read(ImpactCandidate(
        decision_id="ADR-00001", reference="ADR-00001-0", revision=0, title="Original rule",
        state="accepted", is_current=False, relationship="origin", path=["ADR-00001-0"],
        rationale=None, scopes=[], related_work_item_ids=[], related_artifact_ids=[],
        evidence_truncated=False,
    ), options)
    dispositions = []
    if item.reused_candidate_count:
        disposition = disposition_projection(ImpactDispositionRead(
            id=DISPOSITION_ID, project_id=PROJECT_ID, assessment_id=SOURCE_ID, version=7,
            candidate_reference=candidate.reference, disposition="still-valid",
            reason="The captured evidence is unchanged", remediation_work_item_id=None,
            actor={"actor_client": "codex", "actor_session_id": "contract-test"}, created_at=NOW,
        ), options)
        disposition.reused_from = ReusedDispositionSource(
            assessment_id=SOURCE_ID, disposition_id=DISPOSITION_ID, disposition_version=7,
        )
        dispositions.append(disposition)
    return ImpactAssessmentDetail(
        **item.model_dump(), kind="code-impact", total=1, candidates=[candidate],
        dispositions=dispositions, disposition_history_total=0,
        limit=50, offset=0, next_offset=None,
    )


@pytest.mark.parametrize("options", [
    ImpactProjection(),
    ImpactProjection(detail="compact", include_diff=False, include_scopes="summary"),
    ImpactProjection(detail="compact", include_diff=False, include_scopes="none"),
], ids=["full", "compact-summary", "compact-none"])
@pytest.mark.parametrize("reused_count", [0, 1], ids=["zero-reuse", "reused"])
@pytest.mark.parametrize("tool", ["decision_assessments", "decision_assessment_get"])
def test_backend_assessment_reads_survive_strict_mcp(options, reused_count, tool):
    item = assessment(reused_count, options)
    page = KnowledgePage[ImpactAssessmentRead](items=[item], total=1, limit=50, offset=0)
    path = f"/api/v1/projects/{PROJECT_ID}/knowledge/assessments"
    arguments = {"project_id": str(PROJECT_ID), **options.model_dump()}
    cases = [
        {"tool": "decision_assessments", "path": path, "arguments": arguments,
         "response": page.model_dump(mode="json")},
        {"tool": "decision_assessment_get", "path": f"{path}/{ASSESSMENT_ID}",
         "arguments": {**arguments, "assessment_id": str(ASSESSMENT_ID)},
         "response": detail(item, options).model_dump(mode="json")},
    ]
    python = REPO / "mcp/.venv/bin/python"
    assert python.is_file(), "Install the separate MCP environment: uv sync --project mcp --frozen"
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("MNEMONIC_")}
    result = subprocess.run(
        [str(python), "-c", MCP_REPLAY],
        input=json.dumps([case for case in cases if case["tool"] == tool]), capture_output=True,
        text=True, timeout=30, check=False, cwd=REPO / "mcp",
        env={**environment, "PYTHONPATH": str(REPO / "mcp/src")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for value in (cases[0]["response"]["items"][0], cases[1]["response"]):
        assert ("reused_candidate_count" in value) == bool(reused_count)
        assert value.get("reused_candidate_count", 0) == reused_count
