"""Deterministic client adapters for the durable Mnemonic conversation representation.

Native bytes remain immutable provenance. Each block is a separately stored record;
search extraction budgets never truncate this representation. Unsupported binary or
future blocks are explicit dispositions, never guessed conversation text.
"""

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from uuid import UUID

from mnemonic_api.artifact_tika import ExtractionError, normalize_extracted_text
from mnemonic_api.transcript_claude_context import (
    BOOKKEEPING_RECORDS,
    CONTEXT_ATTACHMENTS,
    SYSTEM_RECORDS,
    context_payload,
)
from mnemonic_api.transcript_native_content import INFORMATIONAL_DISPOSITIONS, native_content
from mnemonic_api.transcript_parsers import (
    _CODEX_MIRRORED_ITEMS,
    CodexParser,
    TranscriptParserFactory,
    _collect_codex_metadata,
    _collect_metadata,
    _encoded_tool_input,
    _jsonl,
    _records,
    _validate_tool_input,
)

SCHEMA_VERSION = 1
NORMALIZER_VERSION = 2
_SEGMENT_LIMIT = 250_000
_PG_STRING_INVALID = re.compile("[\x00\ud800-\udfff]")
ContentKind = Literal[
    "human_text", "assistant_text", "tool_call", "tool_result", "system_text",
    "reasoning", "summary", "unsupported",
]


@dataclass(frozen=True)
class Segment:
    segment_id: str
    event_id: str
    ordinal: int
    source_record: int
    source_block: str
    role: str | None
    content_kind: ContentKind
    text: str
    timestamp: str | None = None
    native_event_id: str | None = None
    native_parent_id: str | None = None
    native_branch_id: str | None = None
    channel: str | None = None
    is_sidechain: bool | None = None
    is_error: bool | None = None
    tool_name: str | None = None
    call_id: str | None = None
    payload: Any = None
    dispositions: list[str] = field(default_factory=list)
    related_segment_id: str | None = None


@dataclass(frozen=True)
class NormalizedConversation:
    revision: str
    sha256: str
    snapshot_id: UUID
    source_sha256: str
    format: str
    mime_type: str
    metadata: dict[str, list[str]]
    segments: list[Segment]
    incomplete: bool
    schema_version: int = SCHEMA_VERSION
    normalizer_version: int = NORMALIZER_VERSION
    stored_segment_count: int | None = None
    stored_size_bytes: int | None = None
    extraction_partial: bool = False


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode()
    except (RecursionError, ValueError, TypeError) as error:
        raise ExtractionError("transcript_invalid_format") from error


def revision_for(snapshot_id: UUID, source_sha256: str) -> str:
    return hashlib.sha256(canonical_json([
        str(snapshot_id), source_sha256, SCHEMA_VERSION, NORMALIZER_VERSION,
    ])).hexdigest()


def _string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) > 4096 or _PG_STRING_INVALID.search(value):
        raise ExtractionError("transcript_invalid_metadata")
    return value


def _authored_kind(role: str | None) -> ContentKind:
    if role == "user":
        return "human_text"
    if role == "assistant":
        return "assistant_text"
    return "system_text" if role in {"system", "developer"} else "unsupported"


def _validate_payload_strings(value: Any) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str) and _PG_STRING_INVALID.search(item):
            raise ExtractionError("transcript_invalid_format")
        if isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _structured_arguments(value: Any, *, encoded_json: bool = False) -> Any:
    if encoded_json and isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            pass
    _validate_tool_input(value)
    _validate_payload_strings(value)
    # Retain original numeric/boolean/list/object types rather than flattening them.
    return value


def _call_block(block: dict) -> dict:
    encoded_json = block.get("type") == "function_call"
    arguments = block.get("arguments" if encoded_json else "input", {})
    payload = _structured_arguments(arguments, encoded_json=encoded_json)
    text = payload if isinstance(payload, str) else "".join(_encoded_tool_input(payload))
    return {"content_kind": "tool_call", "text": normalize_extracted_text(text),
            "payload": payload, "tool_name": _string(block.get("name")),
            "call_id": _string(block.get("call_id", block.get("id"))),
            "dispositions": ["encrypted_content_omitted"]
            if block.get("encrypted_function_args") else []}


def _text_block(block: Any, role: str | None, *, codex: bool = False) -> dict:
    kind = block.get("type") if isinstance(block, dict) else None
    if isinstance(kind, str) and kind in {"tool_use", "function_call", "custom_tool_call"}:
        return _call_block(block)
    if kind == "fallback":
        return _context_block(block)
    fragment = native_content(block, codex=codex)
    content_kind = _authored_kind(role)
    if kind in ("thinking", "reasoning_text", "summary_text"):
        content_kind = "reasoning"
    return {"content_kind": content_kind if fragment.text else "unsupported",
            "text": fragment.text,
            "dispositions": list(fragment.dispositions)}


def _output_content(content: Any, *, codex: bool = False) -> dict:
    if isinstance(content, dict) and "type" not in content:
        payload = _structured_arguments(content)
        return {"text": "".join(_encoded_tool_input(payload)), "payload": payload,
                "dispositions": []}
    fragment = native_content(content, codex=codex)
    return {"text": fragment.text,
            "dispositions": list(fragment.dispositions)}


def _blocks(content: Any, role: str | None, *, codex: bool = False):
    values = content if isinstance(content, list) else [content]
    for index, block in enumerate(values):
        if isinstance(block, dict) and block.get("type") == "tool_result":
            yield str(index), {"content_kind": "tool_result",
                "call_id": _string(block.get("tool_use_id")),
                "is_error": block.get("is_error")
                if isinstance(block.get("is_error"), bool) else None,
                **_output_content(block.get("content", ""))}
        else:
            yield str(index), _text_block(block, role, codex=codex)


def _unsupported_event(role: str | None = None, disposition: str = "unsupported_content"):
    return role, [("0", {"content_kind": "unsupported", "text": "",
                         "dispositions": [disposition]})]


def _context_block(value: dict) -> dict:
    _structured_arguments(value)
    codes: set[str] = set()
    payload = context_payload(value, codes)
    return {"content_kind": "system_text", "payload": payload,
            "text": normalize_extracted_text(json.dumps(payload, ensure_ascii=False, indent=2)),
            "dispositions": sorted(codes)}


def _claude_event(row: dict) -> tuple[str | None, list[tuple[str, dict]]] | None:
    message = row.get("message", row)
    if not isinstance(message, dict):
        return _unsupported_event(disposition="unsupported_record")
    role = _string(message.get("role", row.get("type")))
    if role in {"user", "assistant", "system"} and message.get("content") is not None:
        return role, list(_blocks(message["content"], role))
    return _claude_context(row, role)


def _claude_context(row: dict, role: str | None):
    kind = row.get("type")
    if kind == "summary" and isinstance(row.get("summary"), str):
        return None, [("0", {"content_kind": "summary",
                             "text": normalize_extracted_text(row["summary"])})]
    if isinstance(kind, str) and kind in BOOKKEEPING_RECORDS:
        return None
    if kind == "attachment":
        attachment = row.get("attachment")
        if (isinstance(attachment, dict) and isinstance(attachment.get("type"), str)
                and attachment["type"] in CONTEXT_ATTACHMENTS):
            return "system", [("attachment", _context_block(attachment))]
        return _unsupported_event(disposition="unsupported_attachment")
    if (kind == "system" and isinstance(row.get("subtype"), str)
            and row["subtype"] in SYSTEM_RECORDS):
        return "system", [("0", _context_block(row))]
    if role is not None or "message" in row:
        return _unsupported_event(role, "unsupported_role")
    return _unsupported_event(disposition="unsupported_record")


def _codex_output(payload: dict) -> dict:
    return {"content_kind": "tool_result", "call_id": _string(payload.get("call_id")),
            "tool_name": _string(payload.get("name")),
            **_output_content(payload.get("output"), codex=True)}


def _codex_response(payload: dict) -> tuple[str | None, list[tuple[str, dict]]] | None:
    kind = payload.get("type")
    if kind in ("message", "agent_message"):
        role = "assistant" if kind == "agent_message" else _string(payload.get("role"))
        if role not in {"user", "assistant", "system", "developer"} or "content" not in payload:
            return _unsupported_event(role, "unsupported_role")
        return role, list(_blocks(payload["content"], role, codex=True))
    if kind in ("function_call", "custom_tool_call"):
        return "assistant", [("0", _call_block(payload))]
    if kind in ("function_call_output", "custom_tool_call_output"):
        return "tool", [("0", _codex_output(payload))]
    if kind in ("reasoning", "compaction"):
        fragment = native_content([payload.get("summary"), payload.get("content")], codex=True)
        codes = list(fragment.dispositions)
        if payload.get("encrypted_content"):
            codes.append("encrypted_content_omitted")
        return "assistant", [("0", {"content_kind": "reasoning", "text": fragment.text,
                                    "dispositions": codes})]
    return _unsupported_event()


def _codex_completed(payload: dict) -> tuple[str | None, list[tuple[str, dict]]] | None:
    item = payload.get("item")
    if isinstance(item, dict):
        kind = _string(item.get("type"))
        if kind in _CODEX_MIRRORED_ITEMS:
            return None
        if kind == "Plan" and isinstance(item.get("text"), str):
            return "assistant", [("0", {"content_kind": "summary",
                "text": normalize_extracted_text(item["text"])})]
        if kind == "Extension" and item.get("kind") == "web.search":
            block = _context_block(item)
            return "tool", [("0", block | {"content_kind": "tool_result",
                                          "tool_name": "web.search"})]
        if kind == "FunctionCallOutput":
            return "tool", [("0", _codex_output(item))]
    return _unsupported_event()


def _codex_event(row: dict) -> tuple[str | None, list[tuple[str, dict]]] | None:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return _unsupported_event(disposition="unsupported_record")
    if row.get("type") == "response_item":
        return _codex_response(payload)
    if row.get("type") == "compacted":
        text = payload.get("message")
        return None, [("0", {"content_kind": "summary",
            "text": normalize_extracted_text(text) if isinstance(text, str) else ""})]
    if row.get("type") == "event_msg":
        return _codex_notification(payload)
    if row.get("type") in ("session_meta", "turn_context", "world_state",
                           "inter_agent_communication_metadata", "token_usage_record"):
        return None
    return _unsupported_event(disposition="unsupported_record")


def _codex_notification(payload: dict):
    kind = payload.get("type")
    if kind == "item_completed":
        return _codex_completed(payload)
    if kind in ("token_count", "task_started", "task_complete", "thread_settings_applied",
                "user_message", "agent_message", "context_compacted", "turn_aborted"):
        return None
    return _unsupported_event(disposition="unsupported_record")


def _segment(revision: str, ordinal: int, number: int, path: str,
             role: str | None, row: dict, block: dict) -> Segment:
    event_id = hashlib.sha256(f"{revision}:{number}".encode()).hexdigest()[:24]
    segment_id = hashlib.sha256(f"{event_id}:{path}".encode()).hexdigest()[:24]
    payload = row.get("payload", row)
    payload = payload if isinstance(payload, dict) else {}
    timestamp = _string(row.get("timestamp"))
    dispositions = block.get("dispositions", []) + ([] if timestamp else ["timestamp_unavailable"])
    return Segment(segment_id=segment_id, event_id=event_id, ordinal=ordinal,
        source_record=number, source_block=path, role=role, timestamp=timestamp,
        native_event_id=_string(row.get("uuid", payload.get("id"))),
        native_parent_id=_string(row.get("parentUuid", payload.get("parent_id"))),
        native_branch_id=_string(row.get("branch_id", payload.get("branch_id"))),
        channel=_string(payload.get("channel")),
        is_sidechain=row.get("isSidechain") if isinstance(row.get("isSidechain"), bool) else None,
        **(block | {"dispositions": dispositions}))


def _resolve_calls(segments: list[Segment]) -> list[Segment]:
    from dataclasses import replace

    calls: dict[str, list[Segment]] = {}
    for segment in segments:
        if segment.content_kind == "tool_call" and segment.call_id:
            calls.setdefault(segment.call_id, []).append(segment)
    result = []
    preceding: dict[str, Segment] = {}
    for segment in segments:
        if segment.content_kind == "tool_call" and segment.call_id:
            preceding[segment.call_id] = segment
        if segment.content_kind == "tool_result" and segment.call_id:
            candidates = calls.get(segment.call_id, [])
            call = preceding.get(segment.call_id)
            if call is None and len(candidates) == 1:
                call = candidates[0]
            segment = replace(segment, related_segment_id=call.segment_id if call else None,
                dispositions=segment.dispositions
                + ([] if call else ["call_reference_unresolved"]))
        result.append(segment)
    return result


def _events(row: dict, codex: bool):
    event = (_codex_event if codex else _claude_event)(row)
    if event is not None:
        yield "", row, event
    if not codex or row.get("type") != "compacted":
        return
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return
    history = payload.get("replacement_history")
    if history is None:
        return
    if not isinstance(history, list):
        yield "replacement_history.", row, _unsupported_event()
        return
    for index, payload in enumerate(history):
        native = row | {"type": "response_item", "payload": payload}
        event = _codex_event(native)
        if event is not None:
            yield f"replacement_history.{index}.", native, event


def _event_segments(row: dict, codex: bool, seen: set[bytes], stats: Counter):
    found = False
    for prefix, native, (role, blocks) in _events(row, codex):
        found = True
        for path, block in blocks:
            fingerprint = hashlib.sha256(canonical_json([role, block])).digest()
            if prefix and fingerprint in seen:
                stats["compaction_replay_deduplicated"] += 1
                continue
            seen.add(fingerprint)
            if prefix:
                block = block | {"dispositions": block.get("dispositions", [])
                                 + ["compaction_context"]}
            yield prefix + path, native, role, block
    if not found:
        stats["bookkeeping_or_mirrored_event"] += 1


def _coverage_metadata(segments: list[Segment], stats: Counter) -> dict[str, list[str]]:
    warnings: Counter = Counter()
    notes: Counter = Counter(stats)
    for segment in segments:
        for code in segment.dispositions:
            (notes if code in INFORMATIONAL_DISPOSITIONS else warnings)[code] += 1
    return {key: [f"{code}={count}" for code, count in sorted(values.items())]
            for key, values in (("transcript:normalization_warnings", warnings),
                                ("transcript:normalization_notes", notes)) if values}


def _collect_parent_session(row: dict, parents: set[str]) -> None:
    payload = row.get("payload", {})
    source = payload.get("source", {}) if isinstance(payload, dict) else {}
    subagent = source.get("subagent", {}) if isinstance(source, dict) else {}
    spawned = subagent.get("thread_spawn", {}) if isinstance(subagent, dict) else {}
    parent = _string(spawned.get("parent_thread_id")) if isinstance(spawned, dict) else None
    if parent:
        if len(parent) > 200 or (parent not in parents and len(parents) >= 8):
            raise ExtractionError("transcript_invalid_metadata")
        parents.add(parent)


def normalize_transcript(content: bytes, client: str, snapshot_id: UUID) -> NormalizedConversation:
    parser = TranscriptParserFactory.create(client)
    codex = isinstance(parser, CodexParser)
    rows, format_name, mime = ((_jsonl(content), "codex-jsonl", "application/x-ndjson")
                               if codex else _records(content))
    source_sha256 = hashlib.sha256(content).hexdigest()
    revision = revision_for(snapshot_id, source_sha256)
    segments: list[Segment] = []
    sessions: set[str] = set()
    models: set[str] = set()
    parents: set[str] = set()
    count = 0
    seen: set[bytes] = set()
    stats: Counter = Counter()
    for number, row in enumerate(rows, start=1):
        (_collect_codex_metadata if codex else _collect_metadata)(row, sessions, models)
        _collect_parent_session(row, parents)
        recognized = False
        for path, native, role, block in _event_segments(row, codex, seen, stats):
            recognized |= not {"unsupported_record", "unsupported_role",
                               "unsupported_attachment"}.intersection(block.get("dispositions", []))
            if len(segments) >= _SEGMENT_LIMIT:
                raise ExtractionError("transcript_too_many_segments")
            segments.append(_segment(revision, len(segments), number, path, role, native, block))
        count += recognized
    if not count:
        raise ExtractionError("transcript_unsupported_format")
    segments = _resolve_calls(segments)
    metadata = {"transcript:message_count": [str(count)], **_coverage_metadata(segments, stats)}
    if sessions:
        metadata["transcript:session_id"] = sorted(sessions)
    if models:
        metadata["transcript:model"] = sorted(models)
    if parents:
        metadata["transcript:parent_session_id"] = sorted(parents)
    digest = hashlib.sha256()
    for segment in segments:
        digest.update(canonical_json(asdict(segment)) + b"\n")
    incomplete = any(set(segment.dispositions) - INFORMATIONAL_DISPOSITIONS for segment in segments)
    return NormalizedConversation(revision, digest.hexdigest(), snapshot_id, source_sha256,
                                  format_name, mime, metadata, segments, incomplete)


def segment_text(segment: Segment) -> str:
    if not segment.text:
        return ""
    label = {"tool_call": "tool call", "tool_result": "tool result",
             "reasoning": "reasoning", "summary": "summary"}.get(segment.content_kind)
    label = label or segment.role or "system"
    if segment.tool_name:
        label += " " + segment.tool_name
    return f"{label}: {segment.text}"


def conversation_text(segments: list[Segment], maximum_chars: int) -> tuple[str, bool]:
    parts = []
    remaining = maximum_chars
    truncated = False
    for segment in segments:
        value = segment_text(segment)
        if not value:
            continue
        truncated |= len(value) > remaining
        if remaining:
            parts.append(value[:remaining])
            remaining = max(0, remaining - len(value) - 2)
    return "\n\n".join(parts)[:maximum_chars], truncated
