"""Synthetic regressions for native client formats, coverage, and compaction fidelity."""

import json
from uuid import uuid4

import pytest

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.transcript_claude_context import BOOKKEEPING_RECORDS, CONTEXT_ATTACHMENTS
from mnemonic_api.transcript_normalization import conversation_text, normalize_transcript

from .test_transcript_normalization import source


def normalize(*rows, client="claude-code"):
    return normalize_transcript(source(*rows), client, uuid4())


@pytest.mark.parametrize("kind", sorted(BOOKKEEPING_RECORDS))
def test_claude_runtime_records_are_not_missing_conversation(kind):
    result = normalize({"type": kind, "state": {}}, {"role": "user", "content": "real message"})
    assert [(part.content_kind, part.text) for part in result.segments] == [
        ("human_text", "real message")]
    assert not result.incomplete
    assert "bookkeeping_or_mirrored_event=1" in result.metadata["transcript:normalization_notes"]


@pytest.mark.parametrize("kind", sorted(CONTEXT_ATTACHMENTS))
def test_every_supported_attachment_retains_all_readable_and_typed_fields(kind):
    attachment = {"type": kind, "context": {"instructions": "retain instructions\nand details",
                                           "enabled": True, "count": 3, "items": ["a", "b"]}}
    result = normalize({"type": "attachment", "attachment": attachment})
    segment, = result.segments
    assert segment.role == "system" and segment.content_kind == "system_text"
    assert segment.payload == attachment
    assert json.loads(segment.text) == attachment
    assert not result.incomplete


@pytest.mark.parametrize("attachment", [
    {"type": "hook_additional_context", "content": ["hook line one", "hook line two"]},
    {"type": "instructions", "files": [{"path": "/example/AGENTS.md", "content": "project rule"}]},
    {"type": "file", "content": {"type": "text", "file": {"content": "file excerpt"}}},
    {"type": "nested_memory", "content": {"content": "nested memory", "path": "/example/note"}},
    {"type": "deferred_tools_record", "entries": [{"name": "test", "input_schema": {
        "type": "object", "properties": {"required": {"type": "boolean"}}}}]},
    {"type": "prompt_snapshot", "systemPrompt": ["first prompt", "second prompt"]},
    {"type": "hook_blocking_error", "blockingError": {"blockingError": "hook failed"}},
])
def test_realistic_context_shapes_survive_as_typed_searchable_content(attachment):
    result = normalize({"type": "attachment", "attachment": attachment})
    assert json.loads(result.segments[0].text) == attachment
    assert result.segments[0].payload == attachment
    assert not result.incomplete


def test_context_binary_data_is_retained_only_in_native_capture():
    attachment = {"type": "file", "content": {"source": {
        "type": "base64", "media_type": "image/png", "data": "PRIVATE_BINARY"}},
        "description": "readable label"}
    result = normalize({"type": "attachment", "attachment": attachment})
    assert "readable label" in result.segments[0].text
    assert "PRIVATE_BINARY" not in str(result)
    assert result.incomplete
    assert result.metadata["transcript:normalization_warnings"] == [
        "attachment_content_not_searchable=1"]


@pytest.mark.parametrize("subtype", ["stop_hook_summary", "api_error", "turn_duration"])
def test_claude_system_diagnostics_are_retained(subtype):
    row = {"type": "system", "subtype": subtype, "details": {"message": "runtime detail", "ms": 3}}
    result = normalize(row)
    assert json.loads(result.segments[0].text) == row
    assert not result.incomplete


def test_tool_references_and_model_fallback_preserve_meaning():
    result = normalize({"role": "assistant", "content": [
        {"type": "fallback", "from": {"model": "one"}, "to": {"model": "two"}},
        {"type": "tool_use", "id": "call", "name": "discover", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call", "content": [
            {"type": "text", "text": "available tools"},
            {"type": "tool_reference", "tool_name": "tools.search"}]}]})
    assert json.loads(result.segments[0].text)["to"] == {"model": "two"}
    assert result.segments[2].text == "available tools\nTool available: tools.search"
    assert result.segments[2].related_segment_id == result.segments[1].segment_id
    assert not result.incomplete


def codex(payload, kind="response_item"):
    return {"type": kind, "payload": payload, "timestamp": "2026-01-01T00:00:00Z"}


def test_codex_encryption_and_telemetry_are_disclosed_without_claiming_parser_failure():
    result = normalize(codex({"type": "reasoning", "summary": [
        {"type": "summary_text", "text": "readable reasoning"}], "encrypted_content": "OPAQUE"}),
        codex({"type": "agent_message", "content": [
            {"type": "input_text", "text": "answer"},
            {"type": "encrypted_content", "encrypted_content": "OPAQUE"}]}),
        codex({"trigger_turn": True}, "inter_agent_communication_metadata"),
        codex({"turn_usage": {"input_tokens": 3}}, "token_usage_record"), client="codex")
    assert [s.text for s in result.segments] == ["readable reasoning", "answer", ""]
    assert "OPAQUE" not in str(result)
    assert "encrypted_content_omitted=2" in result.metadata["transcript:normalization_notes"]
    assert "transcript:normalization_warnings" not in result.metadata
    assert not result.incomplete


def test_codex_web_extension_results_preserve_search_action_and_evidence():
    item = {"type": "Extension", "kind": "web.search", "id": "search-1", "action": {
        "type": "search", "queries": ["synthetic query"]}, "results": [
            {"type": "search_result", "title": "Evidence", "snippet": "retained result",
             "url": "https://example.test/source"}]}
    result = normalize(codex({"type": "item_completed", "item": item}, "event_msg"), client="codex")
    segment, = result.segments
    assert segment.content_kind == "tool_result" and segment.tool_name == "web.search"
    assert segment.payload == item and json.loads(segment.text) == item
    assert not result.incomplete


def test_compaction_retains_new_context_and_deduplicates_only_replayed_history():
    user = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "ask"}]}
    history = [user, {"type": "message", "role": "developer", "id": "context-id", "content": [
        {"type": "input_text", "text": "new compacted context"}]},
        {"type": "agent_message", "id": "summary-id", "content": "retained answer"},
        {"type": "compaction", "encrypted_content": "OPAQUE"}]
    compacted = codex({"message": "", "replacement_history": history}, "compacted")
    result = normalize(codex(user), compacted, compacted, codex(user), client="codex")
    texts = [s.text for s in result.segments if s.text]
    assert texts == ["ask", "new compacted context", "retained answer", "ask"]
    context = next(s for s in result.segments if s.text == "new compacted context")
    assert context.role == "developer" and context.native_event_id == "context-id"
    assert context.source_record == 2 and context.source_block == "replacement_history.1.0"
    assert "compaction_context" in context.dispositions
    assert not result.incomplete and "OPAQUE" not in str(result)


def test_out_of_order_call_resolves_but_missing_or_ambiguous_call_is_honest():
    result = normalize({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "later", "content": "result"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "later", "name": "read", "input": {}}]})
    assert result.segments[0].related_segment_id == result.segments[1].segment_id
    assert not result.incomplete
    missing = normalize({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "absent", "content": "result"}]})
    assert missing.incomplete
    assert missing.metadata["transcript:normalization_warnings"] == ["call_reference_unresolved=1"]


@pytest.mark.parametrize("row", [
    {"type": "attachment", "attachment": {"type": "future-attachment", "content": "unrecognized"}},
    {"type": "attachment", "attachment": {"type": ["malformed"]}},
    {"type": "system", "subtype": {"malformed": True}},
    {"type": "future-record", "content": "unrecognized"},
    {"role": "user", "content": [{"type": "future-block", "text": "unrecognized"}]},
])
def test_future_or_malformed_types_keep_explicit_warnings(row):
    result = normalize({"role": "user", "content": "known"}, row)
    assert result.incomplete
    assert result.metadata["transcript:normalization_warnings"]
    assert "unrecognized" not in conversation_text(result.segments, 1000)[0]


@pytest.mark.parametrize("client", ["claude-code", "codex"])
def test_unknown_only_journals_do_not_become_valid_transcripts(client):
    with pytest.raises(ExtractionError, match="transcript_unsupported_format"):
        normalize({"type": "started"}, {"type": "result"}, client=client)


@pytest.mark.parametrize("row", [
    codex({"type": "future_event", "text": "unrecognized"}, "event_msg"),
    codex({"type": "item_completed", "item": {
        "type": "Extension", "kind": "future.extension", "text": "unrecognized"}}, "event_msg"),
    {"type": "compacted", "payload": None},
    codex({"replacement_history": {"invalid": "shape"}}, "compacted"),
])
def test_unknown_codex_events_and_malformed_compactions_are_not_silently_ignored(row):
    result = normalize(codex({"type": "message", "role": "user", "content": "known"}),
                       row, client="codex")
    assert result.incomplete
    assert "unrecognized" not in conversation_text(result.segments, 1000)[0]
