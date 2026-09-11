"""Synthetic formats and malformed payloads; private local samples never enter fixtures."""

import json

import pytest

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.transcript_parsers import TranscriptParserFactory


def records():
    return [
        {"type": "queue-operation", "content": "ignored queue entry"},
        {"type": "user", "sessionId": "session-a", "message": {
            "role": "user", "content": "Find the synthetic needle"}},
        {"type": "assistant", "isSidechain": True, "agentId": "synthetic-subagent",
         "message": {"role": "assistant", "model": "test-model", "content": [
             {"type": "thinking", "thinking": "consider the cache"},
             {"type": "tool_use", "name": "Read", "input": {"path": "/synthetic/cache.py"}},
             {"type": "text", "text": "Fixed the needle"},
             {"type": "image", "source": {"data": "BINARY_MUST_NOT_BE_INDEXED"}},
         ]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": [{"type": "text", "text": "tool output"}]}]}},
        {"type": "summary", "summary": "compact summary"},
    ]


@pytest.mark.parametrize("encoding", ["jsonl", "array", "envelope", "pretty-envelope"])
def test_native_subagent_stream_and_export_formats(encoding):
    rows = records()
    if encoding == "jsonl":
        content = "\n".join(json.dumps(row) for row in rows)
    elif encoding == "array":
        content = json.dumps(rows)
    else:
        content = json.dumps({"messages": rows},
                             indent=2 if encoding == "pretty-envelope" else None)
    result = TranscriptParserFactory.create("Claude Code").parse(content.encode(), 10000)
    assert all(value in result.text for value in ["needle", "tool output", "cache.py", "summary"])
    assert "BINARY_MUST_NOT_BE_INDEXED" not in result.text
    assert result.metadata["transcript:session_id"] == ["session-a"]
    assert result.metadata["transcript:model"] == ["test-model"]
    assert result.metadata["transcript:message_count"] == ["4"]
    assert result.format == ("claude-code-jsonl" if encoding == "jsonl" else "claude-code-json")
    assert result.truncated  # The embedded image was deliberately omitted.


@pytest.mark.parametrize("content,code", [
    (b"\xff", "transcript_invalid_encoding"),
    (b'{"message":', "transcript_invalid_format"),
    (b'{"type": "queue-operation"}', "transcript_unsupported_format"),
    (b'{"type": [], "message": {"role": {}}}', "transcript_unsupported_format"),
    (b'[false]', "transcript_invalid_format"),
    (b'{"type":"user","message":{"content":"ok"}}\n{"unfinished":',
     "transcript_invalid_format"),
])
def test_malformed_input_has_a_safe_terminal_error(content, code):
    with pytest.raises(ExtractionError, match=code):
        TranscriptParserFactory.create("claude-code").parse(content, 10000)


def test_unknown_client_does_not_fall_back_to_arbitrary_document_parsing():
    with pytest.raises(ExtractionError, match="transcript_unsupported_client"):
        TranscriptParserFactory.create("other-agent")


def test_bounded_text_and_unhashable_block_type():
    content = json.dumps([{"role": "assistant", "content": [
        {"type": {}}, {"type": "text", "text": "a" * 10000}]}]).encode()
    result = TranscriptParserFactory.create("claude-code").parse(content, 40)
    assert len(result.text) == 40
    assert result.truncated


def test_record_limit_is_enforced_before_json_parsing(monkeypatch):
    monkeypatch.setattr("mnemonic_api.transcript_parsers._RECORD_MAX_BYTES", 30)
    content = b'{"type":"user","message":{"content":"' + b"x" * 100 + b'"}}\n'
    with pytest.raises(ExtractionError, match="transcript_record_too_large"):
        TranscriptParserFactory.create("claude-code").parse(content, 50)


@pytest.mark.parametrize("prefix", [" " * 100, "\x00" * 100, "\u200e" * 100])
@pytest.mark.parametrize("shape", ["string", "text", "thinking", "tool-result", "summary"])
def test_normalization_precedes_text_budget(prefix, shape):
    text = prefix + "needle"
    content = {
        "string": text,
        "text": [{"type": "text", "text": text}],
        "thinking": [{"type": "thinking", "thinking": text}],
        "tool-result": [{"type": "tool_result", "content": text}],
    }
    row = ({"type": "summary", "summary": text} if shape == "summary"
           else {"role": "user", "content": content[shape]})
    result = TranscriptParserFactory.create("claude-code").parse(json.dumps([row]).encode(), 40)
    assert "needle" in result.text
    assert not result.truncated


def test_nested_content_omission_is_reported_even_when_marker_fits():
    content = "needle"
    for _ in range(15):
        content = [{"type": "tool_result", "content": content}]
    row = {"role": "user", "content": content}
    result = TranscriptParserFactory.create("claude-code").parse(json.dumps([row]).encode(), 100)
    assert "omitted" in result.text
    assert result.truncated


def test_budget_truncation_inside_normalized_block_is_reported():
    row = {"role": "user", "content": [{"type": "text", "text": " " * 100 + "x" * 100}]}
    result = TranscriptParserFactory.create("claude-code").parse(json.dumps([row]).encode(), 40)
    assert result.text.startswith("user: xxx")
    assert len(result.text) == 40
    assert result.truncated


@pytest.mark.parametrize("depth", [65, 100, 200])
def test_deep_tool_arguments_fail_with_a_safe_terminal_code(depth):
    arguments = "leaf"
    for _ in range(depth):
        arguments = {"nested": arguments}
    message = {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Synthetic", "input": arguments}]}
    content = json.dumps([message]).encode()
    with pytest.raises(ExtractionError, match="transcript_nesting_limit"):
        TranscriptParserFactory.create("claude-code").parse(content, 10000)


def test_unexpected_encoder_recursion_is_translated(monkeypatch):
    content = json.dumps([{"role": "assistant", "content": [
        {"type": "tool_use", "name": "Synthetic", "input": {"key": "value"}}]}]).encode()

    def overflow(*_args, **_kwargs):
        raise RecursionError("private encoder diagnostics")

    monkeypatch.setattr(json.JSONEncoder, "iterencode", overflow)
    with pytest.raises(ExtractionError, match="transcript_invalid_format"):
        TranscriptParserFactory.create("claude-code").parse(content, 10000)
