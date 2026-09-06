import asyncio
import json

import httpx
import pytest
from conftest import API_KEY, OTHER_WORK_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from mnemonic_mcp import __version__
from mnemonic_mcp import api as api_module
from mnemonic_mcp.api import (
    UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME,
    MnemonicAPI,
    TransportEffect,
)
from mnemonic_mcp.config import Settings
from mnemonic_mcp.models import DuplicateSuggestionPage, DuplicateSuggestionRequest
from mnemonic_mcp.server import build_server

THIRD_WORK_ID = "94b31fa0-b078-4ab3-8df2-f4844f55a2a2"
NOW = "2026-09-02T12:00:00Z"


def adapter(settings: Settings, handler) -> object:
    return build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))


def structured(result):
    if isinstance(result, tuple):
        return result[1]
    return result


def suggestion(
    *,
    rank: int = 1,
    canonical_id: str = WORK_ID,
    matched_id: str = OTHER_WORK_ID,
    signals: list[str] | None = None,
) -> dict[str, object]:
    canonical_title = "Canonical retained objective"
    canonical_status = "done"
    matched_title = "Draft objective"
    matched_status = "pending"
    if matched_id == canonical_id:
        matched_title = canonical_title
        matched_status = canonical_status
    return {
        "canonical_work": {
            "work_item_id": canonical_id,
            "title": canonical_title,
            "summary": "The retained objective already covers this draft.",
            "status": canonical_status,
            "updated_at": NOW,
            "duplicate_member_count": 1,
        },
        "matched_member": {
            "id": matched_id,
            "title": matched_title,
            "status": matched_status,
        },
        "rank": rank,
        "signals": signals or ["exact_title", "lexical", "semantic"],
    }


def suggestion_page(
    *,
    items: list[dict[str, object]] | None = None,
    limit: int = 5,
    mode: str = "hybrid_full",
    semantic_available: bool = True,
    semantic_scope: str = "full_project",
    exact_title_group_total: int = 1,
    omitted_exact_title_group_count: int = 0,
) -> dict[str, object]:
    return {
        "items": [suggestion()] if items is None else items,
        "limit": limit,
        "mode": mode,
        "semantic_available": semantic_available,
        "semantic_scope": semantic_scope,
        "composition_version": "duplicate-suggestion-v1",
        "exact_title_group_total": exact_title_group_total,
        "omitted_exact_title_group_count": omitted_exact_title_group_count,
    }


def required_arguments() -> dict[str, object]:
    return {
        "project_id": PROJECT_ID,
        "title": "Draft objective",
        "summary": "Check whether this objective already exists.",
        "initial_prompt": "Retain the full initial creation context.",
    }


def test_advisory_package_version_is_coordinated():
    assert __version__ == "0.13.0"


async def test_advisory_tool_schema_is_exact_and_capability_free(settings):
    tools = {tool.name: tool for tool in await build_server(settings).list_tools()}
    tool = tools["suggest_duplicate_work"]
    schema = tool.inputSchema
    properties = schema["properties"]

    assert len(tools) == 38
    assert set(properties) == {
        "project_id",
        "title",
        "summary",
        "initial_prompt",
        "tags",
        "exclude_work_item_id",
        "external_candidates",
        "limit",
    }
    assert set(schema["required"]) == {
        "project_id",
        "title",
        "summary",
        "initial_prompt",
    }
    assert properties["title"]["maxLength"] == 200
    assert properties["summary"]["maxLength"] == 1000
    assert properties["initial_prompt"]["maxLength"] == 100000
    assert properties["tags"]["maxItems"] == 20
    assert properties["tags"]["items"]["maxLength"] == 50
    assert properties["tags"]["default"] == []
    assert properties["exclude_work_item_id"]["default"] is None
    assert properties["limit"] == {
        "default": 5,
        "maximum": 10,
        "minimum": 1,
        "title": "Limit",
        "type": "integer",
    }
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.openWorldHint is False

    output = tool.outputSchema
    assert set(output["properties"]) == {
        "items",
        "limit",
        "mode",
        "semantic_available",
        "semantic_scope",
        "composition_version",
        "exact_title_group_total",
        "omitted_exact_title_group_count",
        "external_items",
        "external_scope",
        "external_candidate_count",
    }
    candidate = output["$defs"]["DuplicateCandidateSummary"]
    assert set(candidate["properties"]) == {
        "work_item_id",
        "title",
        "summary",
        "status",
        "updated_at",
        "duplicate_member_count",
        "external_references",
    }
    serialized_output = json.dumps(output).lower()
    for forbidden in (
        "checkpoint",
        "lease",
        "readiness",
        "provenance",
        "actor",
        "session",
        "raw_score",
        "vector",
        "merge_control",
    ):
        assert forbidden not in serialized_output

    description = (tool.description or "").lower()
    for required in (
        "explicit user or client action",
        "categorical signals",
        "never confidence",
        "never blocks create_work",
        "explicit safe read",
        "no operation uuid",
        "retry the same comparison ordinarily",
    ):
        assert required in description


async def test_suggestion_forwards_exact_normalized_six_field_body_and_binds_limit(settings):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/duplicate-suggestions"
        assert request.url.query == b""
        assert request.extensions["timeout"]["connect"] == 5.0
        assert request.extensions["timeout"]["read"] == 60.0
        assert json.loads(request.content) == {
            "title": "Draft objective",
            "summary": "Check existing work",
            "initial_prompt": "  Preserve prompt whitespace.\n",
            "tags": ["api", "search"],
            "exclude_work_item_id": OTHER_WORK_ID,
            "limit": 7,
        }
        return httpx.Response(
            200,
            json=suggestion_page(
                items=[suggestion(matched_id=THIRD_WORK_ID, signals=["lexical"])],
                limit=7,
                exact_title_group_total=0,
            ),
        )

    result = structured(
        await adapter(settings, handler).call_tool(
            "suggest_duplicate_work",
            {
                "project_id": PROJECT_ID,
                "title": "  Draft objective  ",
                "summary": "  Check existing work  ",
                "initial_prompt": "  Preserve prompt whitespace.\n",
                "tags": [" API ", "api", " Search "],
                "exclude_work_item_id": OTHER_WORK_ID,
                "limit": 7,
            },
        )
    )

    assert len(seen) == 1
    assert result["limit"] == 7
    assert result["items"][0]["canonical_work"]["work_item_id"] == WORK_ID
    assert result["items"][0]["matched_member"]["id"] == THIRD_WORK_ID
    assert result["items"][0]["signals"] == ["lexical"]


async def test_suggestion_always_forwards_all_defaults_as_six_body_fields(settings):
    def handler(request):
        assert json.loads(request.content) == {
            "title": "Draft objective",
            "summary": "Check whether this objective already exists.",
            "initial_prompt": "Retain the full initial creation context.",
            "tags": [],
            "exclude_work_item_id": None,
            "limit": 5,
        }
        return httpx.Response(
            200,
            json=suggestion_page(
                items=[],
                mode="lexical",
                semantic_available=False,
                semantic_scope="unavailable",
                exact_title_group_total=0,
            ),
        )

    result = structured(
        await adapter(settings, handler).call_tool(
            "suggest_duplicate_work", required_arguments()
        )
    )
    assert result["items"] == []
    assert result["mode"] == "lexical"


async def test_suggestion_dispatch_is_explicitly_safe_read_and_never_receipt_protected(settings):
    calls = []

    class CapturingAPI:
        async def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            raise ToolError("captured suggestion request")

    with pytest.raises(ToolError, match="captured suggestion request"):
        await build_server(settings, CapturingAPI()).call_tool(
            "suggest_duplicate_work", required_arguments()
        )

    assert len(calls) == 1
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == f"projects/{PROJECT_ID}/duplicate-suggestions"
    assert kwargs["effect"] == TransportEffect.SAFE_READ
    assert kwargs["extended_read_timeout"] is True
    assert kwargs["strict_wire_response"] is True
    assert kwargs["expected_status_code"] == 200
    assert "client_operation_id" not in kwargs["payload"]


@pytest.mark.parametrize(
    ("patch", "expected_kind"),
    [
        ({"limit": True}, "int_type"),
        ({"limit": 11}, "less_than_equal"),
        ({"title": " \t\n "}, "string_too_short"),
        ({"initial_prompt": " \t\n "}, "value_error"),
        ({"tags": [f"tag-{index}" for index in range(21)]}, "too_long"),
        ({"exclude_work_item_id": "private-invalid-uuid"}, "uuid_parsing"),
        ({"client_operation_id": "private-operation-control"}, "extra_forbidden"),
        ({"lease_token": "private-lease-control"}, "extra_forbidden"),
        ({"create": True}, "extra_forbidden"),
    ],
)
async def test_suggestion_rejects_invalid_or_authority_bearing_input_locally(
    settings, patch, expected_kind
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    with pytest.raises(ToolError, match=expected_kind) as caught:
        await adapter(settings, handler).call_tool(
            "suggest_duplicate_work", {**required_arguments(), **patch}
        )
    assert calls == []
    message = str(caught.value)
    assert "private-invalid-uuid" not in message
    assert "private-operation-control" not in message
    assert "private-lease-control" not in message


def test_strict_request_model_normalizes_creation_fields_and_forbids_extras():
    request = DuplicateSuggestionRequest.model_validate(
        {
            "title": "  Draft objective  ",
            "summary": "  Summary  ",
            "initial_prompt": "  Prompt body  ",
            "tags": [" API ", "api", " Search "],
        }
    )
    assert request.model_dump(mode="json") == {
        "title": "Draft objective",
        "summary": "Summary",
        "initial_prompt": "  Prompt body  ",
        "tags": ["api", "search"],
        "exclude_work_item_id": None,
        "limit": 5,
    }
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DuplicateSuggestionRequest.model_validate(
            {**required_arguments(), "source_session_id": "must-not-be-accepted"}
        )
    with pytest.raises(ValidationError, match="Normalized tags"):
        DuplicateSuggestionRequest.model_validate(
            {**required_arguments(), "tags": ["İ" * 50]}
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda page: page["items"][0].update(rank=2), "ranks"),
        (
            lambda page: page["items"][0].update(signals=["semantic", "lexical"]),
            "signals",
        ),
        (
            lambda page: page["items"][0].update(signals=["lexical", "lexical"]),
            "signals",
        ),
        (lambda page: page.update(exact_title_group_total=2), "fill available"),
        (lambda page: page.update(omitted_exact_title_group_count=1), "counts"),
        (lambda page: page.update(semantic_scope="unavailable"), "mode and scope"),
        (
            lambda page: page.update(
                mode="lexical", semantic_available=False, semantic_scope="unavailable"
            ),
            "cannot claim semantic",
        ),
        (
            lambda page: page["items"][0]["matched_member"].update(
                id=WORK_ID, title="Wrong root title"
            ),
            "root match",
        ),
        (
            lambda page: page["items"][0]["canonical_work"].update(
                duplicate_member_count=0
            ),
            "alias match",
        ),
        (lambda page: page.update(composition_version="future-v2"), "literal_error"),
        (lambda page: page.update(limit=0), "greater than or equal"),
    ],
)
def test_suggestion_model_rejects_incoherent_candidate_pages(mutate, message):
    page = suggestion_page()
    mutate(page)
    with pytest.raises(ValidationError, match=message):
        DuplicateSuggestionPage.model_validate(page)


def test_suggestion_model_rejects_duplicate_roots_and_nonexact_before_exact():
    duplicate_roots = suggestion_page(
        items=[
            suggestion(rank=1, signals=["exact_title"]),
            suggestion(rank=2, matched_id=THIRD_WORK_ID, signals=["lexical"]),
        ]
    )
    with pytest.raises(ValidationError, match="unique canonical roots"):
        DuplicateSuggestionPage.model_validate(duplicate_roots)

    duplicate_members = suggestion_page(
        items=[
            suggestion(rank=1, signals=["exact_title"]),
            suggestion(
                rank=2,
                canonical_id=THIRD_WORK_ID,
                matched_id=OTHER_WORK_ID,
                signals=["lexical"],
            ),
        ]
    )
    with pytest.raises(ValidationError, match="unique matched members"):
        DuplicateSuggestionPage.model_validate(duplicate_members)

    nonexact_first = suggestion_page(
        items=[
            suggestion(
                rank=1,
                canonical_id=THIRD_WORK_ID,
                matched_id=THIRD_WORK_ID,
                signals=["lexical"],
            ),
            suggestion(rank=2, signals=["exact_title"]),
        ]
    )
    with pytest.raises(ValidationError, match="returned first"):
        DuplicateSuggestionPage.model_validate(nonexact_first)


@pytest.mark.parametrize(
    "corruption",
    [
        "extra",
        "rank",
        "numeric_string",
        "timestamp_number",
        "noncanonical_uuid",
        "wrong_limit",
        "wrong_exact_title",
    ],
)
async def test_tool_rejects_malformed_or_unbound_success_without_leaking_body(
    settings, corruption
):
    private_marker = "private-upstream-suggestion-content"
    response = suggestion_page()
    if corruption == "extra":
        response["private_candidate_details"] = private_marker
    elif corruption == "rank":
        response["items"][0]["rank"] = 2
    elif corruption == "numeric_string":
        response["exact_title_group_total"] = "1"
    elif corruption == "timestamp_number":
        response["items"][0]["canonical_work"]["updated_at"] = 0
    elif corruption == "noncanonical_uuid":
        response["items"][0]["canonical_work"]["work_item_id"] = WORK_ID.upper()
    elif corruption == "wrong_limit":
        response["limit"] = 4
    else:
        response["items"][0]["matched_member"]["title"] = "Different title"

    def handler(request):
        return httpx.Response(200, json=response)

    with pytest.raises(ToolError, match="unexpected response") as caught:
        await adapter(settings, handler).call_tool(
            "suggest_duplicate_work", required_arguments()
        )
    assert private_marker not in str(caught.value)
    assert WORK_ID not in str(caught.value)


async def test_tool_rejects_a_directly_returned_excluded_identity(settings):
    def handler(request):
        return httpx.Response(200, json=suggestion_page())

    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, handler).call_tool(
            "suggest_duplicate_work",
            {**required_arguments(), "exclude_work_item_id": OTHER_WORK_ID},
        )


async def test_tool_rejects_unexpected_success_status_for_safe_read(settings):
    def handler(request):
        return httpx.Response(201, json=suggestion_page())

    with pytest.raises(ToolError, match="unexpected response") as caught:
        await adapter(settings, handler).call_tool(
            "suggest_duplicate_work", required_arguments()
        )
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (429, "duplicate_suggestion_busy", "Retry after one second"),
        (413, "request_body_too_large", "exceeded the request limit"),
        (503, "duplicate_suggestion_unavailable", "suggestions are unavailable"),
    ],
)
async def test_typed_suggestion_failures_are_retryable_safe_read_guidance(
    settings, status, code, expected
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={
                "detail": {
                    "code": code,
                    "message": f"private {API_KEY}",
                    "context": {
                        "private": "private-suggestion-context",
                        "work_item_id": WORK_ID,
                    },
                }
            },
            headers={"Retry-After": "1"} if status == 429 else None,
        )

    with pytest.raises(ToolError, match=expected) as caught:
        await adapter(settings, handler).call_tool(
            "suggest_duplicate_work", required_arguments()
        )
    message = str(caught.value)
    assert "creating" in message or "creation" in message
    assert "outcome" not in message
    assert API_KEY not in message
    assert WORK_ID not in message
    assert "private-suggestion-context" not in message
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "untyped_503"])
async def test_suggestion_transport_failure_has_no_structural_uncertainty_or_retry(
    settings, failure
):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 60.0
        if failure == "timeout":
            raise httpx.ReadTimeout("private-timeout-diagnostic", request=request)
        return httpx.Response(
            503,
            json={"detail": {"message": f"private {API_KEY}"}},
        )

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(
            "suggest_duplicate_work", required_arguments()
        )
    message = str(caught.value)
    assert "outcome" not in message
    assert "client_operation_id" not in message
    assert "private-timeout-diagnostic" not in message
    assert API_KEY not in message
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 503])
async def test_unknown_typed_suggestion_failure_uses_value_free_safe_read_guidance(
    settings, status
):
    def handler(request):
        return httpx.Response(
            status,
            json={
                "detail": {
                    "code": "private_future_error",
                    "message": f"private {API_KEY}",
                    "context": {"work_item_id": WORK_ID},
                }
            },
        )

    with pytest.raises(ToolError, match="safe read") as caught:
        await adapter(settings, handler).call_tool(
            "suggest_duplicate_work", required_arguments()
        )
    message = str(caught.value)
    assert "Check service health and try again" in message
    assert "Recall the current work state" not in message
    assert "outcome" not in message
    assert API_KEY not in message
    assert WORK_ID not in message


async def test_suggestion_has_a_hard_end_to_end_timeout_and_cancels_transport(
    settings, monkeypatch
):
    transport_cancelled = asyncio.Event()

    async def handler(request):
        try:
            await asyncio.sleep(10)
        finally:
            transport_cancelled.set()
        return httpx.Response(200, json=suggestion_page())

    monkeypatch.setattr(api_module, "_EXTENDED_READ_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(ToolError, match="safe read") as caught:
        await adapter(settings, handler).call_tool(
            "suggest_duplicate_work", required_arguments()
        )

    assert transport_cancelled.is_set()
    assert "outcome" not in str(caught.value)


async def test_response_binding_runs_for_safe_reads_not_only_protected_writes(settings):
    validated = []

    def handler(request):
        return httpx.Response(200, json=suggestion_page())

    def reject_response(result):
        validated.append(result)
        return False

    api = MnemonicAPI(settings, httpx.MockTransport(handler))
    with pytest.raises(ToolError, match="unexpected response"):
        await api.request(
            "POST",
            f"projects/{PROJECT_ID}/duplicate-suggestions",
            payload={},
            response_model=DuplicateSuggestionPage,
            effect=TransportEffect.SAFE_READ,
            response_validator=reject_response,
            strict_wire_response=True,
        )
    assert len(validated) == 1
