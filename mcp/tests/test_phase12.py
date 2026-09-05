"""Independent malformed-wire, exact-retry and authoring-boundary regressions."""

import base64
import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from conftest import CHECKPOINT_ID, CLIENT_OPERATION_ID, NOW, OTHER_WORK_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import TypeAdapter, ValidationError

from mnemonic_mcp.api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME, MnemonicAPI
from mnemonic_mcp.models import WorkCompletion, WorkItemRead, WorkUpdateRead
from mnemonic_mcp.phase12_models import (
    ActivityCursor,
    JobCompletionReportArgument,
    JobCompletionReportInput,
    ProjectActivityRead,
    ReportCursor,
    ReportPrompt,
)
from mnemonic_mcp.server import build_server

STREAM_ID = "17780b88-6968-4717-983a-9b235962be13"
REPORT_ID = "4aa58b4c-07b2-4869-a1d5-201912c67578"
AUTHORING_PROMPT = "Write concise summaries and useful FYIs for a multitasking human."
REPORT_INPUT = {
    "summary": "The dashboard font is consistent and ready for review. It has not been deployed.",
    "fyi_items": ["I chose Arial for broad availability; request another font if you prefer."],
    "prompt_revision": "3",
}
ACTOR = {
    "actor_client": "claude-code", "actor_session_id": "phase12-session", "actor_model": None,
}


def cursor(kind="activity", **fields):
    document = {"v": 1, "kind": kind, "project_id": PROJECT_ID, "stream_id": STREAM_ID, **fields}
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def report(outcome="done"):
    return {
        **copy.deepcopy(REPORT_INPUT),
        "id": REPORT_ID, "project_id": PROJECT_ID, "work_item_id": WORK_ID,
        "closeout_event_id": "44", "closeout_work_version": 4, "closeout_status": outcome,
        "completion_checkpoint_id": CHECKPOINT_ID if outcome == "done" else None,
        "work_title_at_closeout": "Investigate empty results", **ACTOR,
        "prompt_sha256": hashlib.sha256(AUTHORING_PROMPT.encode()).hexdigest(), "created_at": NOW,
    }


def envelope(outcome="done", *, detail=False):
    return {
        "report": {**report(outcome), **({"authoring_prompt": AUTHORING_PROMPT} if detail else {})},
        "created_sequence": "4", "human_dismissed": False, "human_dismissal": None,
        "source_work_state": {
            "work_item_id": WORK_ID, "status": "pending", "canonical_work_item_id": OTHER_WORK_ID,
            "deleted": True,
        },
        "follow_up_count": "2",
    }


def report_page():
    return {
        "project_id": PROJECT_ID, "stream_id": STREAM_ID, "dismissal": "undismissed",
        "work_item_id": None, "as_of_sequence": "8", "items": [envelope()],
        "has_more": False, "next_cursor": None,
    }


def activity(sequence="1", **changes):
    return {
        "sequence": sequence, "kind": "project_created", "work_event_id": None,
        "event_type": None, "work_item_id": None, "job_completion_report_id": None,
        "human_dismissal_id": None, "follow_up_id": None, "settings_revision": None,
        "lease_generation_id": None, "recorded_at": NOW, "origin": "live", **changes,
    }


def activity_page():
    return {
        "project_id": PROJECT_ID, "stream_id": STREAM_ID, "items": [activity()],
        "next_cursor": cursor(after="1"), "has_more": False,
        "through_sequence": "1", "historical_through_sequence": "0",
        "historical_coverage": "recorded_work_events_only",
    }


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.pulls = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.pulls += 1
            yield chunk

    async def aclose(self):
        self.closed = True


async def call(settings, tool, document, arguments=None, *, raw=None, headers=None, status=200):
    calls = []
    body = json.dumps(document).encode() if raw is None else raw
    stream = TrackingStream([body])

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            assert request.headers["accept-encoding"] == "identity"
            return httpx.Response(status, stream=stream, headers=headers)
        return httpx.Response(status, content=body, headers=headers)

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    try:
        result = await server.call_tool(tool, {"project_id": PROJECT_ID, **(arguments or {})})
        return result[1] if isinstance(result, tuple) else result
    finally:
        assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"summary": ""}, {"summary": "\u00a0"}, {"summary": "a\nb"}, {"summary": "a\u2028b"},
    {"summary": "a\u2029b"}, {"summary": "a\u202eb"}, {"summary": "a\u206fb"},
    {"summary": "a\x00b"}, {"summary": "a\ud800b"}, {"summary": "x" * 2001},
    {"summary": None}, {"fyi_items": None}, {"fyi_items": [None]}, {"fyi_items": [{}]},
    {"fyi_items": ["x"] * 11}, {"fyi_items": ["x" * 601]}, {"fyi_items": ["a\tb"]},
    {"prompt_revision": "0"}, {"prompt_revision": "01"}, {"prompt_revision": "1.0"},
    {"prompt_revision": 1}, {"prompt_revision": True}, {"prompt_revision": "9223372036854775808"},
    {"prompt_revision": "١"}, {"prompt_revision": "+1"}, {"unexpected": "private-marker"},
])
def test_report_rejects_invalid_structure(changes):
    with pytest.raises(ValidationError):
        JobCompletionReportInput.model_validate({**REPORT_INPUT, **changes})


def test_report_requires_explicit_fyi_array_preserves_prose_order_and_unicode():
    with pytest.raises(ValidationError):
        JobCompletionReportInput.model_validate({"summary": "Done.", "prompt_revision": "1"})
    authored = {
        "summary": "  العربية עברית 👩\u200d💻. Dr. Smith reviewed it.  ",
        "fyi_items": ["A preference. Another sentence.", "Repeated.", "Repeated."],
        "prompt_revision": "9223372036854775807",
    }
    assert JobCompletionReportInput.model_validate(authored).model_dump() == authored
    assert JobCompletionReportInput.model_validate({**authored, "fyi_items": []}).fyi_items == []


def test_aggregate_utf8_bound_and_prompt_independent_multiline_policy():
    largest = {"summary": "😀" * 2000, "fyi_items": ["😀" * 600] * 3, "prompt_revision": "1"}
    assert JobCompletionReportInput.model_validate(largest)
    with pytest.raises(ValidationError):
        JobCompletionReportInput.model_validate({**largest, "fyi_items": ["😀" * 600] * 4})
    prompt = "A\nB\r\nC\tD\u2028E"
    assert TypeAdapter(ReportPrompt).validate_python(prompt) == prompt
    for invalid in (" ", "x\x01", "x\u206a", "x" * 8001, "😀" * 4097):
        with pytest.raises(ValidationError):
            TypeAdapter(ReportPrompt).validate_python(invalid)


@pytest.mark.parametrize("value", [None, {}, {"summary": "Done.", "prompt_revision": "1"}])
def test_present_report_never_normalizes_null_or_missing_fyis(value):
    with pytest.raises(ValidationError):
        TypeAdapter(JobCompletionReportArgument).validate_python(value)


@pytest.mark.parametrize("adapter,value", [
    (ActivityCursor, cursor(after="01")), (ActivityCursor, cursor(after=1)),
    (ActivityCursor, cursor(after="9223372036854775808")),
    (ActivityCursor, cursor(after="0", extra="private")),
    (ActivityCursor, cursor(after="0", v=True)),
    (ActivityCursor, cursor(after="0") + "="),
    (ActivityCursor, "A" * 513), (ActivityCursor, "private\nmarker"),
    (ActivityCursor, cursor("reports", dismissal="all", work_item_id=None, upper="8", last="4")),
    (ReportCursor, cursor(after="0")),
    (ReportCursor, cursor("reports", dismissal="all", work_item_id=None, upper="3", last="4")),
])
def test_cursor_strict_encoding_variants_and_bounds(adapter, value):
    with pytest.raises(ValidationError):
        TypeAdapter(adapter).validate_python(value)


async def test_four_safe_reads_and_strict_projection_boundaries(settings):
    page = activity_page()
    assert await call(settings, "get_activity", page) == page
    report_list = report_page()
    assert await call(settings, "list_job_completion_reports", report_list) == report_list
    detail = envelope(detail=True)
    assert await call(settings, "get_job_completion_report", detail, {"report_id": REPORT_ID}) == detail
    settings_response = {
        "project_id": PROJECT_ID, "revision": "3", "recall_pointer_template": None,
        "job_completion_report_prompt": AUTHORING_PROMPT,
    }
    assert await call(settings, "get_project_settings", settings_response) == settings_response


@pytest.mark.parametrize("changes", [
    {"sequence": "0"}, {"sequence": 1}, {"kind": "progress"}, {"summary": "secret prose"},
    {"work_item_id": WORK_ID}, {"event_type": "work_created"}, {"origin": "history_import"},
    {"recorded_at": "2026-08-30T08:00:00-04:00"},
    {"kind": "job_completion_report_created", "work_item_id": WORK_ID},
])
def test_activity_exact_reference_matrix(changes):
    with pytest.raises(ValidationError):
        ProjectActivityRead.model_validate_json(json.dumps(activity(**changes)), strict=True)


@pytest.mark.parametrize("change", [
    "wrong-project", "wrong-stream", "wrong-tail", "head-behind", "missing-row", "duplicate-row",
    "gap", "wrong-origin", "hidden-more", "false-more", "count-number", "boolean-version",
])
async def test_activity_malformed_success_rejected_once(settings, change):
    page = activity_page()
    mutations = {
        "wrong-project": lambda: page.update(project_id=OTHER_WORK_ID),
        "wrong-stream": lambda: page.update(stream_id=OTHER_WORK_ID),
        "wrong-tail": lambda: page.update(next_cursor=cursor(after="0")),
        "head-behind": lambda: page.update(through_sequence="0"),
        "missing-row": lambda: page.update(items=[]),
        "duplicate-row": lambda: page.update(items=page["items"] * 2),
        "gap": lambda: page.update(items=[activity("2")], next_cursor=cursor(after="2"), through_sequence="2"),
        "wrong-origin": lambda: page.update(historical_through_sequence="1"),
        "hidden-more": lambda: page.update(through_sequence="2"),
        "false-more": lambda: page.update(has_more=True),
        "count-number": lambda: page.update(through_sequence=1),
        "boolean-version": lambda: page.update(next_cursor=cursor(after="1", v=True)),
    }
    mutations[change]()
    with pytest.raises(ToolError):
        await call(settings, "get_activity", page)


async def test_activity_resumes_from_exact_cursor_and_deliberate_now(settings):
    page = activity_page()
    page.update(items=[activity("9")], next_cursor=cursor(after="9"), through_sequence="9")
    arguments = {"after": cursor(after="8"), "limit": 1}
    assert await call(settings, "get_activity", page, arguments) == page
    page["items"] = []
    assert await call(settings, "get_activity", page, {"start": "now"}) == page
    assert await call(settings, "get_activity", page, {"after": cursor(after="9")}) == page


@pytest.mark.parametrize("tool,args", [
    ("get_activity", {"after": cursor(after="0"), "start": "now"}),
    ("get_activity", {"after": cursor(after="0", project_id=OTHER_WORK_ID)}),
    ("get_activity", {"after": None}),
    ("get_activity", {"limit": True}),
    ("list_job_completion_reports", {"limit": 51}),
    ("list_job_completion_reports", {"cursor": None}),
    ("list_job_completion_reports", {"cursor": cursor(
        "reports", dismissal="all", work_item_id=None, upper="8", last="4"
    )}),
])
async def test_invalid_read_inputs_do_not_dispatch(settings, tool, args):
    def handler(request):
        pytest.fail("invalid read was dispatched")
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError):
        await server.call_tool(tool, {"project_id": PROJECT_ID, **args})


@pytest.mark.parametrize("change", [
    "wrong-filter", "wrong-work", "wrong-source", "wrong-dismissal", "duplicate", "wrong-order",
    "above-upper", "wrong-cursor-tail", "missing-continuation", "numeric-count", "prompt-leak",
    "wrong-checkpoint", "nullable-actor", "hidden-null", "bidi",
])
async def test_report_list_rejects_malformed_success(settings, change):
    page = report_page()
    item = page["items"][0]
    another = copy.deepcopy(item)
    another["report"]["id"] = OTHER_WORK_ID
    another["created_sequence"] = "5"
    mutations = {
        "wrong-filter": lambda: page.update(dismissal="all"),
        "wrong-work": lambda: page.update(work_item_id=OTHER_WORK_ID),
        "wrong-source": lambda: item["source_work_state"].update(work_item_id=OTHER_WORK_ID),
        "wrong-dismissal": lambda: item.update(human_dismissed=True),
        "duplicate": lambda: page.update(items=page["items"] * 2),
        "wrong-order": lambda: page["items"].append(another),
        "above-upper": lambda: item.update(created_sequence="9"),
        "wrong-cursor-tail": lambda: page.update(has_more=True, next_cursor=cursor(
            "reports", dismissal="undismissed", work_item_id=None, upper="8", last="3"
        )),
        "missing-continuation": lambda: page.update(has_more=True),
        "numeric-count": lambda: item.update(follow_up_count=2),
        "prompt-leak": lambda: item["report"].update(authoring_prompt="private prompt"),
        "wrong-checkpoint": lambda: item["report"].update(completion_checkpoint_id=None),
        "nullable-actor": lambda: item["report"].pop("actor_model"),
        "hidden-null": lambda: item["report"].update(fyi_items=None),
        "bidi": lambda: item["report"].update(fyi_items=["hidden\u206btext"]),
    }
    mutations[change]()
    with pytest.raises(ToolError):
        await call(settings, "list_job_completion_reports", page)


async def test_report_page_cursor_order_and_filter_binding(settings):
    page = report_page()
    args = {"cursor": cursor("reports", dismissal="undismissed", work_item_id=None, upper="8", last="5")}
    assert await call(settings, "list_job_completion_reports", page, args) == page
    page["items"][0]["created_sequence"] = "6"
    with pytest.raises(ToolError):
        await call(settings, "list_job_completion_reports", page, args)


@pytest.mark.parametrize("change", ["wrong-id", "wrong-prompt-hash", "missing-prompt"])
async def test_report_detail_preserves_exact_identity_and_prompt_provenance(settings, change):
    result = envelope(detail=True)
    if change == "wrong-id":
        result["report"]["id"] = OTHER_WORK_ID
    elif change == "wrong-prompt-hash":
        result["report"]["authoring_prompt"] = "Different prompt"
    else:
        del result["report"]["authoring_prompt"]
    with pytest.raises(ToolError):
        await call(settings, "get_job_completion_report", result, {"report_id": REPORT_ID})


def closeout_fixture(outcome, work_item, checkpoint, *, include_report=True):
    work = {**work_item, "status": outcome, "version": 4}
    report_document = report(outcome)
    checkpoint = {
        **checkpoint, "kind": "completion", "affected_paths": [],
        "source_session_id": ACTOR["actor_session_id"],
    }
    request_checkpoint = {key: checkpoint[key] for key in (
        "prompt", "source_client", "source_session_id", "source_model", "source_session_url",
        "repository_branch", "verified_against", "tags", "source_metadata", "affected_paths",
    )}
    del checkpoint["affected_paths"]
    args = {
        "project_id": PROJECT_ID, "work_item_id": WORK_ID, "expected_version": 3,
        "client_operation_id": CLIENT_OPERATION_ID,
    }
    if outcome == "done":
        tool = "complete_work"
        args["checkpoint"] = request_checkpoint
        response = {"work_item": work, "checkpoint": checkpoint}
    else:
        tool = "update_work"
        args.update(changes={"status": outcome}, **ACTOR)
        response = work
    if include_report:
        args["job_completion_report"] = copy.deepcopy(REPORT_INPUT)
        response["job_completion_report"] = report_document
    return tool, args, response


@pytest.mark.parametrize("outcome", ["done", "wont-do", "promoted"])
async def test_all_closeouts_bind_exact_authored_report(settings, outcome, work_item, checkpoint):
    tool, args, response = closeout_fixture(outcome, work_item, checkpoint)
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response)
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    result = await server.call_tool(tool, args)
    assert (result[1] if isinstance(result, tuple) else result) == response
    assert calls[0]["job_completion_report"] == REPORT_INPUT
    assert calls[0]["client_operation_id"] == CLIENT_OPERATION_ID


@pytest.mark.parametrize("outcome", ["done", "wont-do", "promoted"])
async def test_report_insert_time_is_independent_of_work_and_checkpoint_time(
    settings, outcome, work_item, checkpoint
):
    tool, args, response = closeout_fixture(outcome, work_item, checkpoint)
    work = response["work_item"] if outcome == "done" else response
    work["updated_at"] = "2026-09-05T22:28:58.151057Z"
    if outcome == "done":
        response["checkpoint"]["created_at"] = "2026-09-05T22:28:58.151698Z"
    response["job_completion_report"]["created_at"] = "2026-09-05T22:28:58.166911Z"
    server = build_server(settings, MnemonicAPI(
        settings, httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    ))
    result = await server.call_tool(tool, args)
    assert (result[1] if isinstance(result, tuple) else result) == response


@pytest.mark.parametrize("outcome", ["done", "wont-do", "promoted"])
@pytest.mark.parametrize("change", [
    "missing", "null", "summary", "fyi-order", "revision", "status", "work", "version", "actor",
])
async def test_report_bearing_mutation_never_accepts_incoherent_report(
    settings, outcome, change, work_item, checkpoint
):
    tool, args, response = closeout_fixture(outcome, work_item, checkpoint)
    actual = response["job_completion_report"]
    if change == "missing":
        del response["job_completion_report"]
    elif change == "null":
        response["job_completion_report"] = None
    elif change == "summary":
        actual["summary"] = "A different assertion."
    elif change == "fyi-order":
        args["job_completion_report"]["fyi_items"] = ["First.", "Second."]
        actual["fyi_items"] = ["Second.", "First."]
    elif change == "revision":
        actual["prompt_revision"] = "4"
    elif change == "status":
        actual["closeout_status"] = "promoted" if outcome != "promoted" else "wont-do"
    elif change == "work":
        actual["work_item_id"] = OTHER_WORK_ID
    elif change == "version":
        actual["closeout_work_version"] = 5
    else:
        actual["actor_session_id"] = "different-session"
    with pytest.raises(ToolError, match="operation may already have committed"):
        await call(settings, tool, response, args)


@pytest.mark.parametrize("outcome", ["done", "wont-do", "promoted"])
async def test_old_report_free_closeout_is_forwarded_for_exact_replay(
    settings, outcome, work_item, checkpoint
):
    tool, args, response = closeout_fixture(outcome, work_item, checkpoint, include_report=False)
    attempts = []
    def handler(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(200, json=response)
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    for _ in range(2):
        result = await server.call_tool(tool, args)
        assert (result[1] if isinstance(result, tuple) else result) == response
    assert attempts[0] == attempts[1]
    assert "job_completion_report" not in attempts[0]
    model = WorkCompletion if outcome == "done" else WorkUpdateRead
    assert model.model_validate_json(json.dumps(response), strict=True).model_dump(mode="json") == response
    assert "job_completion_report" not in WorkItemRead.model_fields


@pytest.mark.parametrize("outcome", ["done", "wont-do", "promoted"])
async def test_fresh_missing_report_gets_backend_definitive_guard(
    settings, outcome, work_item, checkpoint
):
    tool, args, _ = closeout_fixture(outcome, work_item, checkpoint, include_report=False)
    error = {"detail": {"code": "job_completion_report_required", "message": "private-server-text"}}
    with pytest.raises(ToolError, match="Every fresh Done") as raised:
        await call(settings, tool, error, args, status=422)
    assert "private-server-text" not in str(raised.value)


async def test_unknown_report_outcome_retains_unchanged_body_despite_prompt_edit(
    settings, work_item, checkpoint
):
    tool, args, response = closeout_fixture("done", work_item, checkpoint)
    attempts = []
    def handler(request):
        attempts.append(request.content)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("private timeout body", request=request)
        return httpx.Response(200, json=response)
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as raised:
        await server.call_tool(tool, args)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in str(raised.value)
    assert "private timeout body" not in str(raised.value)
    await server.call_tool(tool, args)
    assert attempts[0] == attempts[1]
    assert json.loads(attempts[-1])["job_completion_report"]["prompt_revision"] == "3"


@pytest.mark.parametrize("tool,arguments,maximum", [
    ("get_activity", {}, 512 * 1024),
    ("list_job_completion_reports", {}, 2 * 1024 * 1024),
    ("get_job_completion_report", {"report_id": REPORT_ID}, 256 * 1024),
])
async def test_phase12_read_caps_before_body_parse(settings, tool, arguments, maximum):
    stream = TrackingStream([b"private payload"])
    def handler(request):
        return httpx.Response(200, headers={"Content-Length": str(maximum + 1)}, stream=stream)
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError, match="safe read"):
        await server.call_tool(tool, {"project_id": PROJECT_ID, **arguments})
    assert stream.pulls == 0 and stream.closed


async def test_duplicate_keys_and_raw_prompt_never_normalize_into_success(settings):
    raw = json.dumps(report_page()).replace('"follow_up_count": "2"', '"follow_up_count":"2","follow_up_count":"2"').encode()
    with pytest.raises(ToolError) as raised:
        await call(settings, "list_job_completion_reports", None, raw=raw)
    assert REPORT_INPUT["summary"] not in str(raised.value)


async def test_report_input_errors_hide_prose_and_arbitrary_keys(settings, work_item, checkpoint):
    tool, args, _ = closeout_fixture("done", work_item, checkpoint)
    args["job_completion_report"] = {
        "summary": "private-prose\ninvalid", "fyi_items": [{"private-key": "private-value"}],
        "prompt_revision": "private-revision", "private-unknown": "private-content",
    }
    def handler(request):
        pytest.fail("invalid report was dispatched")
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as raised:
        await server.call_tool(tool, args)
    message = str(raised.value)
    assert "job_completion_report" in message
    assert "private-" not in message


async def test_catalog_is_32_tools_11_protected_and_report_omission_is_replay_only(settings):
    tools = {tool.name: tool for tool in await build_server(settings).list_tools()}
    assert len(tools) == 32
    protected = [tool for tool in tools.values() if "client_operation_id" in tool.inputSchema["properties"]]
    assert len(protected) == 11
    assert "dismiss_job_completion_report" not in tools
    assert "create_job_completion_report_follow_up" not in tools
    for name in ("complete_work", "update_work"):
        schema = tools[name].inputSchema
        assert "job_completion_report" not in schema["required"]
        assert "default" not in schema["properties"]["job_completion_report"]
        assert set(schema["$defs"]["JobCompletionReportInput"]["required"]) == {
            "summary", "fyi_items", "prompt_revision",
        }
        assert "historical" in tools[name].description
        assert "get_project_settings" in tools[name].description
    for name in ("get_activity", "get_project_settings", "list_job_completion_reports", "get_job_completion_report"):
        assert tools[name].annotations.readOnlyHint
        assert tools[name].annotations.idempotentHint


def test_plugin_fixed_authoring_and_human_action_contract():
    root = Path(__file__).resolve().parents[2] / "plugin"
    reference = (root / "reference/job-completion-reports.md").read_text()
    for expected in (
        "no other LLM output", "multitasking", "get_project_settings", "prompt_revision",
        "never more than three", "Needs Attention", "Won’t do", "Promoted", "same UUID",
        "immutable", "no separate", "does not dismiss", "not authority",
    ):
        assert expected in reference, expected
    for skill in ("mnemonic-save", "mnemonic-recall", "mnemonic-search"):
        guidance = (root / "skills" / skill / "SKILL.md").read_text()
        assert "reference/job-completion-reports.md" in guidance
        assert "get_project_settings" in guidance
