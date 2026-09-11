"""Factories detect native Claude parent/subagent JSONL and JSON/stream exports.

Source content remains untrusted. Binary attachments are omitted, text is bounded,
JSONL is decoded one record at a time, and exported JSON has a lower memory cap.
"""

import io
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol

from mnemonic_api.artifact_tika import ExtractionError, normalize_extracted_text

_RECORD_MAX_BYTES = 8_388_608
_JSON_MAX_BYTES = 16_777_216


@dataclass(frozen=True)
class ParsedTranscript:
    text: str
    format: str
    mime_type: str
    metadata: dict[str, list[str]]
    truncated: bool


class TranscriptParser(Protocol):
    def parse(self, content: bytes, maximum_chars: int) -> ParsedTranscript: ...


def _decode(content: bytes) -> object:
    try:
        return json.loads(content.decode("utf-8-sig"))
    except UnicodeDecodeError as error:
        raise ExtractionError("transcript_invalid_encoding") from error
    except (ValueError, RecursionError) as error:
        raise ExtractionError("transcript_invalid_format") from error


def _jsonl(content: bytes) -> Iterator[dict]:
    stream = io.BytesIO(content)
    count = 0
    while line := stream.readline(_RECORD_MAX_BYTES + 1):
        if len(line) > _RECORD_MAX_BYTES:
            raise ExtractionError("transcript_record_too_large")
        if not line.strip():
            continue
        count += 1
        if count > 250_000:
            raise ExtractionError("transcript_too_many_records")
        row = _decode(line)
        if not isinstance(row, dict):
            raise ExtractionError("transcript_invalid_format")
        yield row


def _records(content: bytes) -> tuple[Iterable[dict], str, str]:
    first = content.lstrip(b"\xef\xbb\xbf \t\r\n")[:_RECORD_MAX_BYTES + 1].split(b"\n", 1)[0]
    if len(first) > _RECORD_MAX_BYTES:
        raise ExtractionError("transcript_record_too_large")
    try:
        row = _decode(first)
    except ExtractionError:
        row = None
    if isinstance(row, dict) and not isinstance(row.get("messages"), list):
        return _jsonl(content), "claude-code-jsonl", "application/x-ndjson"
    if len(content) > _JSON_MAX_BYTES:
        raise ExtractionError("transcript_export_too_large")
    value = _decode(content)
    if isinstance(value, dict) and isinstance(value.get("messages"), list):
        rows = value["messages"]
    else:
        rows = value if isinstance(value, list) else [value]
    if not rows or len(rows) > 250_000 or any(not isinstance(row, dict) for row in rows):
        raise ExtractionError("transcript_invalid_format")
    return rows, "claude-code-json", "application/json"


@dataclass(frozen=True)
class TextFragment:
    text: str
    truncated: bool = False


def _normalized_fragment(value: str, maximum: int) -> TextFragment:
    clean = normalize_extracted_text(value)
    return TextFragment(clean[:maximum], len(clean) > maximum)


def _validate_tool_input(value: object) -> None:
    # Use an explicit stack before Python's recursive JSON encoder. A valid JSON
    # decoder result can still overflow that encoder's deeper Python call stack.
    pending = [(iter((value,)), 0)]
    while pending:
        iterator, depth = pending[-1]
        try:
            item = next(iterator)
        except StopIteration:
            pending.pop()
            continue
        if depth > 64:
            raise ExtractionError("transcript_nesting_limit")
        if isinstance(item, dict):
            pending.append((iter(item.values()), depth + 1))
        elif isinstance(item, list):
            pending.append((iter(item), depth + 1))


def _encoded_tool_input(value: object) -> Iterator[str]:
    _validate_tool_input(value)
    try:
        yield from json.JSONEncoder(ensure_ascii=True).iterencode(value)
    except (RecursionError, ValueError, TypeError) as error:
        raise ExtractionError("transcript_invalid_format") from error


def _tool_text(block: dict, maximum: int) -> TextFragment:
    name = block.get("name", "tool")
    label = normalize_extracted_text(name) if isinstance(name, str) else "tool"
    parts = [(label + " ")[:maximum]]
    remaining = maximum - len(parts[0])
    truncated = len(label) + 1 > maximum
    for chunk in _encoded_tool_input(block.get("input", {})):
        if not remaining:
            truncated = True
            break
        parts.append(chunk[:remaining])
        truncated |= len(chunk) > remaining
        remaining = max(0, remaining - len(chunk))
    return TextFragment("".join(parts), truncated)


def _block_text(block: object, maximum: int, depth: int = 0) -> TextFragment:
    if isinstance(block, str):
        return _normalized_fragment(block, maximum)
    if depth > 12:
        return TextFragment("[nested content omitted]"[:maximum], True)
    if isinstance(block, list):
        return _list_text(block, maximum, depth)
    if not isinstance(block, dict):
        return TextFragment("", block is not None)
    kind = block.get("type")
    if not isinstance(kind, str):
        return TextFragment("", True)
    if kind in {"text", "thinking"}:
        value = block.get("text" if kind == "text" else "thinking", "")
        return (_normalized_fragment(value, maximum) if isinstance(value, str)
                else TextFragment("", True))
    if kind == "tool_use":
        return _tool_text(block, maximum)
    if kind == "tool_result":
        return _block_text(block.get("content", ""), maximum, depth + 1)
    return TextFragment("", True)


def _list_text(blocks: list, maximum: int, depth: int) -> TextFragment:
    parts = []
    remaining = maximum
    truncated = False
    for block in blocks:
        if remaining <= 0:
            truncated = True
            break
        fragment = _block_text(block, remaining, depth + 1)
        parts.append(fragment.text)
        remaining -= len(fragment.text) + 1
        truncated |= fragment.truncated
    return TextFragment("\n".join(parts)[:maximum], truncated)


def _message_text(row: dict, maximum: int) -> tuple[TextFragment, bool]:
    message = row.get("message", row)
    if not isinstance(message, dict):
        return TextFragment(""), False
    role = message.get("role", row.get("type"))
    if not isinstance(role, str) or role not in {"user", "assistant", "system"}:
        summary = row.get("summary") if row.get("type") == "summary" else None
        if isinstance(summary, str):
            value = _normalized_fragment(summary, maximum)
            return TextFragment("summary: " + value.text, value.truncated), True
        return TextFragment(""), False
    content = message.get("content")
    if content is None:
        return TextFragment(""), False
    value = _block_text(content, maximum)
    return TextFragment(f"{role}: {value.text}", value.truncated), True


class ClaudeCodeParser:
    def parse(self, content: bytes, maximum_chars: int) -> ParsedTranscript:
        rows, format_name, mime = _records(content)
        parts: list[str] = []
        remaining, recognized = maximum_chars, 0
        truncated = False
        sessions: set[str] = set()
        models: set[str] = set()
        for row in rows:
            fragment, known = _message_text(row, remaining + 1)
            recognized += known
            clean = normalize_extracted_text(fragment.text)
            truncated |= fragment.truncated or len(clean) > remaining
            if clean and remaining:
                parts.append(clean[:remaining])
                remaining = max(0, remaining - len(clean) - 2)
            _collect_metadata(row, sessions, models)
        if not recognized:
            raise ExtractionError("transcript_unsupported_format")
        metadata = {"transcript:message_count": [str(recognized)]}
        if sessions:
            metadata["transcript:session_id"] = sorted(sessions)
        if models:
            metadata["transcript:model"] = sorted(models)
        return ParsedTranscript("\n\n".join(parts)[:maximum_chars], format_name, mime,
                                metadata, truncated)


def _collect_metadata(row: dict, sessions: set[str], models: set[str]) -> None:
    session = row.get("sessionId", row.get("session_id"))
    if isinstance(session, str) and len(sessions) < 8:
        sessions.add(normalize_extracted_text(session)[:200])
    message = row.get("message")
    model = message.get("model") if isinstance(message, dict) else None
    if isinstance(model, str) and len(models) < 8:
        models.add(normalize_extracted_text(model)[:120])


class TranscriptParserFactory:
    """Client dispatch is explicit; each adapter independently detects its formats."""

    _clients: dict[str, type[ClaudeCodeParser]] = {
        "claude-code": ClaudeCodeParser,
        "claude_code": ClaudeCodeParser,
        "claude code": ClaudeCodeParser,
    }

    @classmethod
    def create(cls, client: str) -> TranscriptParser:
        parser = cls._clients.get(client.strip().casefold())
        if parser is None:
            raise ExtractionError("transcript_unsupported_client")
        return parser()
