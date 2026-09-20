"""Regressions for native Claude records found in retained, valid sessions."""

import json

import pytest

from .test_transcript_native_formats import normalize


@pytest.mark.parametrize("row", [
    {"type": "custom-title", "customTitle": "Session label", "sessionId": "session-one"},
    {"type": "agent-name", "agentName": "Worker label", "sessionId": "session-one"},
])
def test_session_labels_are_bookkeeping_instead_of_unknown_messages(row):
    result = normalize(row, {"type": "user", "timestamp": "2026-09-20T11:33:03.271Z", "message": {
        "role": "user", "content": "Retained conversation"}})
    assert [(part.content_kind, part.text) for part in result.segments] == [
        ("human_text", "Retained conversation")]
    assert result.metadata["transcript:session_id"] == ["session-one"]
    assert result.metadata["transcript:message_count"] == ["1"]
    assert result.metadata["transcript:normalization_notes"] == ["bookkeeping_or_mirrored_event=1"]
    assert "transcript:normalization_warnings" not in result.metadata
    assert not result.incomplete


@pytest.mark.parametrize("attachment", [
    {"type": "task_status", "taskId": "task-one", "taskType": "local_bash",
     "description": "Background validation finished", "status": "completed",
     "deltaSummary": None, "outputFilePath": "/example/task.output"},
    {"type": "thinking_drop", "requestId": "request-one", "model": "example-model",
     "querySource": "example-source", "thinkingBlocksSent": 4, "thinkingTurnsSent": 2,
     "newlyDropped": {"blockCount": 1, "turnCount": 1, "reason": "context changed",
                      "first": {"messageIndex": 0, "blockIndex": 0},
                      "last": {"messageIndex": 1, "blockIndex": 0}},
     "blockHashes": ["example-hash"], "firstReportForThreadInProcess": True,
     "clientChange": {"kinds": "example-change", "firstChangedMessageIndex": 0,
                      "baseline": "previous-request", "callNumber": 3}},
])
def test_runtime_attachments_retain_searchable_context_and_native_field_types(attachment):
    result = normalize({"type": "attachment", "uuid": "event-one", "parentUuid": "parent-one",
                        "timestamp": "2026-09-20T11:33:03.271Z", "attachment": attachment})
    segment, = result.segments
    assert segment.role == "system" and segment.content_kind == "system_text"
    assert segment.payload == attachment and json.loads(segment.text) == attachment
    assert segment.native_event_id == "event-one" and segment.native_parent_id == "parent-one"
    assert segment.timestamp == "2026-09-20T11:33:03.271Z"
    assert segment.source_record == 1 and segment.source_block == "attachment"
    assert not segment.dispositions and not result.incomplete
    assert "transcript:normalization_warnings" not in result.metadata
