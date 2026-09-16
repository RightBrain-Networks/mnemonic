"""Equivalent native fixtures preserve structure without sharing client parsers on reads."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from mnemonic_api.transcript_normalization import conversation_text, normalize_transcript


def source(*rows):
    return b"\n".join(json.dumps(row).encode() for row in rows)


def test_native_clients_share_authored_and_tool_content_kinds():
    claude = source(
        {"uuid": "one", "timestamp": "2026-01-01T00:00:00Z", "message": {
            "role": "user", "content": "find the needle"}},
        {"uuid": "two", "parentUuid": "one", "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": "call",
                "name": "read", "input": {"path": "file"}}]}},
        {"message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call", "content": "needle located"}]}},
        {"message": {"role": "assistant", "content": "finished"}},
    )
    codex = source(
        {"type": "response_item", "payload": {
            "type": "message", "role": "user", "content": "find the needle"}},
        {"type": "response_item", "payload": {
            "type": "function_call", "call_id": "call", "name": "read",
            "arguments": '{"path": "file"}'}},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call", "output": "needle located"}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": "finished"}},
    )
    first = normalize_transcript(claude, "claude-code", uuid4())
    second = normalize_transcript(codex, "codex", uuid4())
    assert [item.content_kind for item in first.segments] == [
        "human_text", "tool_call", "tool_result", "assistant_text"]
    assert [(s.content_kind, s.text, s.payload) for s in first.segments[:2]] == [
        (s.content_kind, s.text, s.payload) for s in second.segments[:2]]
    assert first.segments[2].role == "user"
    assert second.segments[2].role == "tool"
    for result in (first, second):
        assert result.segments[2].related_segment_id == result.segments[1].segment_id
        assert not result.incomplete
    assert first.segments[1].native_parent_id == "one"
    assert first.segments[0].timestamp == "2026-01-01T00:00:00Z"


def test_normalization_is_deterministic_and_search_limits_do_not_truncate_storage():
    data = source({"role": "user", "content": "x" * 10000})
    snapshot = uuid4()
    first = normalize_transcript(data, "claude-code", snapshot)
    second = normalize_transcript(data, "claude-code", snapshot)
    assert first == second
    assert len(first.segments[0].text) == 10000
    text, truncated = conversation_text(first.segments, 20)
    assert len(text) == 20 and truncated
    changed_capture = normalize_transcript(data, "claude-code", uuid4())
    assert changed_capture.revision != first.revision
    assert changed_capture.segments[0].segment_id != first.segments[0].segment_id
    assert replace(first, revision="other").source_sha256 == first.source_sha256


def test_mirrored_codex_items_are_omitted_but_durable_exceptions_survive():
    data = source(
        {"type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": "answer"}},
        {"type": "event_msg", "payload": {"type": "item_completed",
            "item": {"type": "AgentMessage", "text": "answer"}}},
        {"type": "event_msg", "payload": {"type": "item_completed",
            "item": {"type": "Plan", "text": "plan"}}},
        {"type": "event_msg", "payload": {"type": "item_completed",
            "item": {"type": "FunctionCallOutput", "output": "result"}}},
    )
    result = normalize_transcript(data, "codex", uuid4())
    assert [item.text for item in result.segments] == ["answer", "plan", "result"]
    assert not result.incomplete


def test_unsupported_and_broken_references_have_explicit_dispositions():
    data = source({"role": "user", "content": [
        {"type": "image", "data": "PRIVATE_BINARY"},
        {"type": "tool_result", "tool_use_id": "missing", "content": "error"},
    ]})
    result = normalize_transcript(data, "claude-code", uuid4())
    assert result.incomplete
    assert result.segments[0].content_kind == "unsupported"
    assert "unsupported_content" in result.segments[0].dispositions
    assert "call_reference_unresolved" in result.segments[1].dispositions
    assert "PRIVATE_BINARY" not in str(result.segments)


def test_unknown_roles_and_records_are_explicitly_unsupported():
    data = source({"role": "user", "content": "known"},
                  {"role": "future-role", "content": "unknown prose"},
                  {"type": "future_record", "content": "unknown prose"})
    result = normalize_transcript(data, "claude-code", uuid4())
    assert result.incomplete
    assert [item.content_kind for item in result.segments] == [
        "human_text", "unsupported", "unsupported"]
    assert "unsupported_role" in result.segments[1].dispositions
    assert "unknown prose" not in conversation_text(result.segments, 1000)[0]


def test_codex_parent_session_and_claude_sidechain_relationships_survive():
    codex = source({"type": "session_meta", "payload": {"id": "child", "source": {
        "subagent": {"thread_spawn": {"parent_thread_id": "parent", "depth": 1}}}}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                "content": "child answer"}})
    result = normalize_transcript(codex, "codex", uuid4())
    assert result.metadata["transcript:parent_session_id"] == ["parent"]
    claude = source({"role": "user", "content": "branch", "isSidechain": True,
                     "parentUuid": "native-parent", "branch_id": "native-branch"})
    segment = normalize_transcript(claude, "claude-code", uuid4()).segments[0]
    assert segment.is_sidechain
    assert segment.native_parent_id == "native-parent"
    assert segment.native_branch_id == "native-branch"


@pytest.mark.parametrize("native_input", [
    '"quoted code"', '{"command": "echo hi"}', 'false', '[1, "two", null]',
])
@pytest.mark.parametrize("client", ["codex", "claude-code"])
def test_raw_tool_inputs_preserve_json_looking_strings(client, native_input):
    if client == "codex":
        row = {"type": "response_item", "payload": {
            "type": "custom_tool_call", "call_id": "call", "name": "custom",
            "input": native_input}}
    else:
        row = {"role": "assistant", "content": [{
            "type": "tool_use", "id": "call", "name": "custom", "input": native_input}]}
    result = normalize_transcript(source(row), client, uuid4())
    assert result.segments[0].payload == native_input
    assert result.segments[0].text == native_input
    assert not result.incomplete


@pytest.mark.parametrize("arguments", [
    {"command": "echo hi"}, [1, "two", None], False, "quoted code",
])
def test_codex_function_arguments_decode_their_native_json_encoding(arguments):
    row = {"type": "response_item", "payload": {
        "type": "function_call", "call_id": "call", "name": "function",
        "arguments": json.dumps(arguments)}}
    result = normalize_transcript(source(row), "codex", uuid4())
    assert result.segments[0].payload == arguments
    assert type(result.segments[0].payload) is type(arguments)
    assert not result.incomplete


@pytest.mark.parametrize("kind", ["queue-operation", "file-history-snapshot", "progress"])
def test_known_claude_bookkeeping_does_not_report_missing_conversation_content(kind):
    result = normalize_transcript(source(
        {"type": kind, "data": {"details": "non-conversation bookkeeping"}},
        {"type": "user", "message": {"role": "user", "content": "hello"}},
    ), "claude-code", uuid4())
    assert [(row.content_kind, row.text) for row in result.segments] == [("human_text", "hello")]
    assert not result.incomplete
