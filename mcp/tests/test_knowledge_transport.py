"""Decision text gets a bounded transfer allowance; object metadata never does."""

import json

import pytest

from mnemonic_mcp.knowledge_transport import DECISION_CONTENT_MAX_BYTES
from mnemonic_mcp.transport import (
    MCP_REQUEST_MAX_BYTES,
    MCPRequestTooLarge,
    MCPTransportViolation,
    validated_jsonrpc_document,
)


def frame(content, *, name="decision_put", **extra):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": {"content": content, **extra}}}


@pytest.mark.parametrize("character,ensure_ascii", [
    ("a", False), ("é", True), ("é", False), ("\x01", True), ("\\", False), ('"', False),
])
def test_maximum_utf8_document_survives_json_framing_and_escaping(character, ensure_ascii):
    content = character * (DECISION_CONTENT_MAX_BYTES // len(character.encode()))
    document = frame(content, title="A full-size decision")
    raw = json.dumps(document, ensure_ascii=ensure_ascii).encode()
    assert len(raw) > MCP_REQUEST_MAX_BYTES
    assert validated_jsonrpc_document(raw) == document


@pytest.mark.parametrize("name", ["help", "create_work", "decision_get", "upload_artifact"])
def test_other_tools_cannot_borrow_the_decision_text_allowance(name):
    raw = json.dumps(frame("a" * DECISION_CONTENT_MAX_BYTES, name=name)).encode()
    with pytest.raises(MCPRequestTooLarge):
        validated_jsonrpc_document(raw)


def test_oversized_utf8_document_fails_even_when_escaped_literal_fits():
    raw = json.dumps(frame("é" * (DECISION_CONTENT_MAX_BYTES // 2 + 1))).encode()
    with pytest.raises(MCPRequestTooLarge):
        validated_jsonrpc_document(raw)


def test_decision_metadata_does_not_receive_the_text_allowance():
    raw = json.dumps(frame("a" * DECISION_CONTENT_MAX_BYTES,
                           metadata="m" * MCP_REQUEST_MAX_BYTES)).encode()
    with pytest.raises(MCPRequestTooLarge):
        validated_jsonrpc_document(raw)


def test_oversized_nested_content_cannot_substitute_for_top_level_text():
    raw = json.dumps(frame("small", nested={"content": "a" * DECISION_CONTENT_MAX_BYTES})).encode()
    with pytest.raises(MCPRequestTooLarge):
        validated_jsonrpc_document(raw)


def test_large_decision_duplicate_keys_and_malformed_strings_remain_invalid():
    raw = json.dumps(frame("a" * DECISION_CONTENT_MAX_BYTES)).encode()
    duplicate = raw.replace(b'"content":', b'"content":"old","content":')
    with pytest.raises(MCPTransportViolation):
        validated_jsonrpc_document(duplicate)
    malformed = raw.replace(b'"content": "', b'"content": "\\x')
    with pytest.raises(MCPTransportViolation):
        validated_jsonrpc_document(malformed)
