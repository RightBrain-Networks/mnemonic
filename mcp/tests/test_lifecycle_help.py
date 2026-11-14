"""Progressive runtime help preserves conditional closeout and review mechanics."""

import re

import httpx
import pytest

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import build_server


@pytest.fixture
def lifecycle_server(settings):
    def forbidden(_request):
        pytest.fail("Lifecycle help must not read project data")

    return build_server(settings, MnemonicAPI(settings, httpx.MockTransport(forbidden)))


async def complete_guidance(server, command):
    usage = (await server.call_tool("help", {"topic": command + " usage"}))[0].text
    assert f'"topic":"{command} details"' in usage
    first = (await server.call_tool("help", {"topic": command + " details"}))[0].text
    match = re.search(r"\(1/(\d+)\)", first)
    assert match is not None
    pages = [first]
    for number in range(2, int(match.group(1)) + 1):
        assert f'"topic":"{command} details {number}"' in pages[-1]
        pages.append((await server.call_tool("help", {
            "topic": f"{command} details {number}",
        }))[0].text)
    return " ".join(page.splitlines()[1] for page in pages)


async def test_closeout_help_exposes_conditional_review_and_provenance_rules(lifecycle_server):
    text = await complete_guidance(lifecycle_server, "complete_work")
    for concept in (
        "code_review_required_min_priority", "code_review_optional_min_priority",
        "0=Always", "100=Never", "manual_review_request", "allow_remediation_code_reviews",
        "code_review_handoff", "repository_key", "repository_url", "checkout_path",
        "object_format=sha1", "base_commit/head_commit", "65,536-byte",
        "decision_provenance", "500 citation/commit pairs", "no-applicable-decisions",
        "not-code", "forbids both citations and commits",
    ):
        assert concept in text
    assert "mandatory review requires code_review_handoff" in text
    assert "other policy outcomes reject that argument" in text
    assert "makes handoff optional at closeout" in text


async def test_closeout_and_follow_up_help_connect_returned_questions(lifecycle_server):
    for command in ("complete_work", "respond_to_work_follow_up"):
        text = await complete_guidance(lifecycle_server, command)
        for concept in (
            "agent_follow_ups", "code_review_recommendation", "expected_follow_up_version",
            "recommend_review=true", "false forbids", "originating", "list_work_follow_ups",
            "historical pending snapshot", "implementation lease",
        ):
            assert concept in text


async def test_decision_review_done_rules_are_discoverable_from_closeout(lifecycle_server):
    text = await complete_guidance(lifecycle_server, "complete_work")
    for concept in (
        "decision_change_update(action='acknowledge'", "expected_scope_version",
        "Scope growth invalidates", "decision_assessments(change_id=...)",
        "code-impact", "undeleted remediation work item", "review driver itself",
    ):
        assert concept in text


@pytest.mark.parametrize("command", ["claim_work", "claim_and_recall"])
async def test_claim_help_preserves_mode_and_native_enrollment_contract(lifecycle_server, command):
    text = await complete_guidance(lifecycle_server, command)
    for concept in (
        "human-specified review mode", "first warm claim", "cold mode requires",
        "later fresh claims", "exact retries", "explicit null creates no transcript entry",
        "backend's approved roots", "associated Active lease ends",
    ):
        assert concept in text


async def test_transcript_help_separates_copy_index_and_normalization_coverage(lifecycle_server):
    text = await complete_guidance(lifecycle_server, "get_transcript")
    for concept in ("copy_status/copy_error_code", "index_status/index_error_code",
                    "truncated", "normalization_incomplete", "audited path recovery"):
        assert concept in text


async def test_review_completion_help_exposes_cross_field_scope_constraints(lifecycle_server):
    text = await complete_guidance(lifecycle_server, "complete_code_review")
    for concept in (
        "result.coverage", "exactly the same order", "scope.repositories", "scope_sha256",
        "expected_review_version", "result.mode", "actor client/session", "finding_key",
        "end_line requires start_line", "already completed episode", "exact receipt replay",
    ):
        assert concept in text


@pytest.mark.parametrize("command", ["complete_work", "update_work"])
async def test_report_help_exposes_text_budgets_and_prompt_identity(lifecycle_server, command):
    text = await complete_guidance(lifecycle_server, command)
    for concept in (
        "8,000 UTF-8 bytes", "2,400 UTF-8 bytes", "16,384 UTF-8 bytes", "Line/paragraph",
        "control characters", "bidi", "positive decimal string", "not its SHA-256 revision",
    ):
        assert concept in text
