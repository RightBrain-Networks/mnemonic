"""Synthetic Codex rollout fixtures; never copy private session content into tests."""

import json

import pytest

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.transcript_parsers import TranscriptParserFactory


def rollout(*items):
    return b"\n".join(json.dumps(item).encode() for item in items)


def response(kind, **payload):
    return {"type": "response_item", "payload": {"type": kind, **payload}}


def parse(*items, maximum=10000, client="codex"):
    return TranscriptParserFactory.create(client).parse(rollout(*items), maximum)


@pytest.mark.parametrize("client", [
    "codex", "OpenAI-Codex", "openai_codex", "OpenAI Codex",
    "codex-cli", "codex_cli", " Codex CLI ",
])
@pytest.mark.parametrize("source", ["cli", {"subagent": {"thread_spawn": {
    "parent_thread_id": "parent-session", "depth": 1}}}])
def test_native_parent_and_subagent_rollouts(client, source):
    result = parse(
        {"type": "session_meta", "payload": {
            "id": "synthetic-session", "source": source, "cwd": "/synthetic/project"}},
        {"type": "turn_context", "payload": {"model": "synthetic-model"}},
        response("message", role="developer", content=[
            {"type": "input_text", "text": "Use the synthetic configuration"}]),
        response("message", role="user", content=[
            {"type": "input_text", "text": "Find the regression needle"}]),
        {"type": "event_msg", "payload": {
            "type": "user_message", "message": "Find the regression needle"}},
        response("message", role="assistant", content=[
            {"type": "output_text", "text": "The fix is complete"}]),
        {"type": "event_msg", "payload": {
            "type": "agent_message", "message": "The fix is complete"}},
        response("agent_message", author="child", recipient="parent", content=[
            {"type": "input_text", "text": "The subagent verified the result"}]),
        {"type": "compacted", "payload": {"message": "A readable compact summary"}},
        client=client,
    )
    assert result.format == "codex-jsonl"
    assert result.mime_type == "application/x-ndjson"
    assert result.metadata == {
        "transcript:session_id": ["synthetic-session"],
        "transcript:model": ["synthetic-model"],
        "transcript:message_count": ["5"],
    }
    assert result.text.count("Find the regression needle") == 1
    assert result.text.count("The fix is complete") == 1
    assert "developer: Use the synthetic configuration" in result.text
    assert "agent: The subagent verified the result" in result.text
    assert "summary: A readable compact summary" in result.text
    assert not result.truncated


def test_function_and_custom_tool_arguments_and_results_are_searchable():
    result = parse(
        response("function_call", name="read_file", arguments='{"path":"/synthetic/cache.py"}'),
        response("function_call_output", output="The function found a cache entry"),
        response("custom_tool_call", name="apply_patch", input="*** Begin Patch\n+synthetic patch"),
        response("custom_tool_call_output", output=[
            {"type": "input_text", "text": "The custom tool applied the patch"}]),
    )
    for text in ("read_file", "cache.py", "cache entry", "apply_patch", "synthetic patch",
                 "The custom tool applied the patch"):
        assert text in result.text
    assert result.metadata["transcript:message_count"] == ["4"]
    assert not result.truncated


def test_only_readable_reasoning_and_text_blocks_are_indexed():
    result = parse(
        response("reasoning", summary=[{"type": "summary_text", "text": "Readable summary"}],
                 content=[{"type": "reasoning_text", "text": "Readable reasoning"}],
                 encrypted_content="ENCRYPTED_MUST_NOT_BE_INDEXED"),
        response("message", role="user", content=[
            {"type": "input_text", "text": "Visible prompt"},
            {"type": "input_image", "image_url": "BINARY_MUST_NOT_BE_INDEXED"},
            {"type": "input_audio", "data": "AUDIO_MUST_NOT_BE_INDEXED"}]),
        response("agent_message", author="child", recipient="parent", content=[
            {"type": "input_text", "text": "Visible subagent reply"},
            {"type": "encrypted_content", "encrypted_content": "SECRET_MUST_NOT_BE_INDEXED"}]),
        response("function_call_output", output=[
            {"type": "input_text", "text": "Visible tool reply"},
            {"type": "input_image", "image_url": "TOOL_IMAGE_MUST_NOT_BE_INDEXED"}]),
    )
    for text in ("Readable summary", "Readable reasoning", "Visible prompt",
                 "Visible subagent reply", "Visible tool reply"):
        assert text in result.text
    assert "MUST_NOT_BE_INDEXED" not in result.text
    assert result.truncated


def test_encrypted_compaction_and_replacement_context_are_not_duplicated():
    result = parse(
        response("message", role="user", content=[{"type": "input_text", "text": "Original task"}]),
        {"type": "compacted", "payload": {"message": "", "replacement_history": [
            {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "Original task"}]},
            {"type": "compaction", "encrypted_content": "ENCRYPTED_MUST_NOT_BE_INDEXED"},
        ]}},
        response("compaction", encrypted_content="OTHER_ENCRYPTED_MUST_NOT_BE_INDEXED"),
    )
    assert result.text.count("Original task") == 1
    assert "MUST_NOT_BE_INDEXED" not in result.text
    assert result.truncated


def test_metadata_is_bounded_and_telemetry_is_not_indexed():
    result = parse(
        *[{"type": "session_meta", "payload": {
            "id": str(number) + "s" * 300, "base_instructions": "OMITTED_SETTINGS"}}
          for number in range(12)],
        *[{"type": "turn_context", "payload": {
            "model": str(number) + "m" * 200, "cwd": "OMITTED_SETTINGS"}}
          for number in range(12)],
        {"type": "world_state", "payload": {"state": "OMITTED_SETTINGS"}},
        {"type": "event_msg", "payload": {"type": "token_count", "message": "OMITTED_SETTINGS"}},
        response("message", role="assistant", content=[{"type": "output_text", "text": "Answer"}]),
    )
    assert len(result.metadata["transcript:session_id"]) == 8
    assert all(len(value) <= 200 for value in result.metadata["transcript:session_id"])
    assert len(result.metadata["transcript:model"]) == 8
    assert all(len(value) <= 120 for value in result.metadata["transcript:model"])
    assert "OMITTED_SETTINGS" not in result.text
    assert not result.truncated


@pytest.mark.parametrize("content,code", [
    (b"\xff", "transcript_invalid_encoding"),
    (b'{"type":', "transcript_invalid_format"),
    (b"[]", "transcript_invalid_format"),
    (b'{"type":"session_meta","payload":{"id":"empty"}}', "transcript_unsupported_format"),
    (b'{"type":"event_msg","payload":{"type":"agent_message","message":"mirror"}}',
     "transcript_unsupported_format"),
    (b'{"type":"response_item","payload":{"type":[],"role":"user"}}',
     "transcript_unsupported_format"),
    (b'{"type":"response_item","payload":{"type":"message","role":{}}}',
     "transcript_unsupported_format"),
    (b'{"type":"response_item","payload":{"type":"message","role":"user"}}',
     "transcript_unsupported_format"),
    (b'{"type":"user","message":{"role":"user","content":"Claude only"}}',
     "transcript_unsupported_format"),
])
def test_malformed_or_foreign_input_has_a_safe_terminal_error(content, code):
    with pytest.raises(ExtractionError, match=code):
        TranscriptParserFactory.create("codex").parse(content, 100)


def test_jsonl_record_limit_is_shared_with_existing_parser(monkeypatch):
    monkeypatch.setattr("mnemonic_api.transcript_parsers._RECORD_MAX_BYTES", 30)
    with pytest.raises(ExtractionError, match="transcript_record_too_large"):
        parse(response("message", role="user", content="x" * 100))


def test_invalid_record_after_exhausted_budget_still_fails():
    content = rollout(response("message", role="user", content="x" * 100)) + b'\n{"unfinished":'
    with pytest.raises(ExtractionError, match="transcript_invalid_format"):
        TranscriptParserFactory.create("codex").parse(content, 10)


@pytest.mark.parametrize("prefix", [" " * 100, "\x00" * 100, "\u200e" * 100])
@pytest.mark.parametrize("shape", ["message", "reasoning", "call", "output", "summary"])
def test_normalization_precedes_codex_text_budget(prefix, shape):
    text = prefix + "needle"
    row = {
        "message": response("message", role="user", content=[{"type": "input_text", "text": text}]),
        "reasoning": response("reasoning", summary=[{"type": "summary_text", "text": text}]),
        "call": response("function_call", name="read", arguments=text),
        "output": response("custom_tool_call_output", output=text),
        "summary": {"type": "compacted", "payload": {"message": text}},
    }[shape]
    result = parse(row, maximum=40)
    assert "needle" in result.text
    assert not result.truncated


def test_text_and_unknown_blocks_report_incomplete_coverage():
    result = parse(response("message", role="user", content=[
        {"type": {}}, {"type": "input_text", "text": " " * 100 + "x" * 10000}]), maximum=40)
    assert result.text.startswith("user: xxx")
    assert len(result.text) == 40
    assert result.truncated


def test_nested_content_is_bounded_without_recursive_failure():
    content = {"type": "input_text", "text": "Too deeply nested"}
    for _ in range(20):
        content = [content]
    result = parse(response("function_call_output", output=content), maximum=100)
    assert "omitted" in result.text
    assert "Too deeply nested" not in result.text
    assert result.truncated


def test_empty_tool_arguments_preserve_the_tool_name():
    result = parse(response("function_call", name="synthetic_ping", arguments=""))
    assert "synthetic_ping" in result.text
    assert not result.truncated


def test_encrypted_function_arguments_report_incomplete_coverage():
    content = json.dumps({"type": "response_item", "payload": {
        "type": "function_call", "name": "exec_command", "arguments": "{}",
        "encrypted_function_args": "omitted-encrypted-arguments",
    }}).encode()
    parsed = TranscriptParserFactory.create("codex").parse(content, 1000)
    assert "exec_command" in parsed.text
    assert "omitted-encrypted-arguments" not in parsed.text
    assert parsed.truncated


def completed(item_type, **item):
    return {"type": "event_msg", "payload": {
        "type": "item_completed", "thread_id": "synthetic-thread", "turn_id": "synthetic-turn",
        "item": {"type": item_type, "id": "synthetic-item", **item},
    }}


def test_event_only_completed_plan_is_searchable_without_raw_response_item():
    result = parse(completed("Plan", text="Plan-only regression needle"))
    assert result.text == "plan: Plan-only regression needle"
    assert result.metadata["transcript:message_count"] == ["1"]
    assert not result.truncated


@pytest.mark.parametrize("output", [
    "Event-only tool result needle",
    [{"type": "input_text", "text": "Event-only tool result needle"}],
])
def test_event_only_completed_function_output_is_searchable(output):
    row = completed("FunctionCallOutput", name="synthetic_tool",
                    output=output, namespace="functions")
    row["payload"]["completed_at_ms"] = 1
    result = parse(row)
    assert result.text == "tool result synthetic_tool: Event-only tool result needle"
    assert result.metadata["transcript:message_count"] == ["1"]
    assert not result.truncated


def test_completed_canonical_message_and_tool_mirrors_are_not_duplicated():
    result = parse(
        response("message", role="user", content=[
            {"type": "input_text", "text": "Unique user needle"}]),
        completed("UserMessage", content=[{"type": "text", "text": "Unique user needle"}]),
        response("message", role="assistant", content=[
            {"type": "output_text", "text": "Unique assistant needle"}]),
        completed("AgentMessage", content=[{"type": "Text", "text": "Unique assistant needle"}]),
        response("reasoning", summary=[{"type": "summary_text", "text": "Unique reasoning"}]),
        completed("Reasoning", summary_text=["Unique reasoning"]),
        response("function_call_output", output="Unique shell result"),
        completed("CommandExecution", aggregated_output="Unique shell result"),
        completed("SubAgentActivity", kind="completed",
                  agent_thread_id="child", agent_path="/child"),
    )
    for text in ("Unique user needle", "Unique assistant needle", "Unique reasoning",
                 "Unique shell result"):
        assert result.text.count(text) == 1
    assert result.metadata["transcript:message_count"] == ["4"]
    assert not result.truncated


@pytest.mark.parametrize("item", [
    {"type": "FutureDurableContent", "text": "Unknown text must not be indexed"},
    {"type": [], "text": "Unknown text must not be indexed"},
    {"type": "Plan", "text": {}},
    {"type": "FunctionCallOutput", "name": "malformed_tool", "output": None},
    None,
])
def test_unsupported_completed_items_report_incomplete_coverage(item):
    result = parse(
        response("message", role="user", content=[{"type": "input_text", "text": "Known text"}]),
        {"type": "event_msg", "payload": {"type": "item_completed", "item": item}},
    )
    assert "Known text" in result.text
    assert "Unknown text" not in result.text
    assert result.truncated


def test_completed_function_output_omits_binary_and_encrypted_content():
    result = parse(completed("FunctionCallOutput", name="synthetic_tool", output=[
        {"type": "input_text", "text": "Visible result"},
        {"type": "input_image", "image_url": "BINARY_MUST_NOT_BE_INDEXED"},
        {"type": "encrypted_content", "encrypted_content": "SECRET_MUST_NOT_BE_INDEXED"},
    ]))
    assert "Visible result" in result.text
    assert "MUST_NOT_BE_INDEXED" not in result.text
    assert result.truncated


@pytest.mark.parametrize("item", [
    completed("Plan", text=" " * 100 + "needle"),
    completed("FunctionCallOutput", name="tool", output="\u200e" * 100 + "needle"),
])
def test_completed_item_normalization_precedes_text_budget(item):
    result = parse(item, maximum=40)
    assert "needle" in result.text
    assert not result.truncated


@pytest.mark.parametrize("kind,field", [("Plan", "text"), ("FunctionCallOutput", "output")])
def test_completed_item_text_respects_budget(kind, field):
    result = parse(completed(kind, name="tool", **{field: "x" * 1000}), maximum=40)
    assert len(result.text) == 40
    assert result.truncated
