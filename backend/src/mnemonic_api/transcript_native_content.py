"""Readable native content and explicit reasons why a block has no searchable text."""

from dataclasses import dataclass

from mnemonic_api.artifact_tika import normalize_extracted_text
from mnemonic_api.transcript_parsers import _block_text, _codex_block_text

INFORMATIONAL_DISPOSITIONS = frozenset({
    "timestamp_unavailable", "encrypted_content_omitted", "compaction_context",
})
_MEDIA = {
    "image": "image_content_not_searchable", "input_image": "image_content_not_searchable",
    "document": "document_content_not_searchable", "audio": "audio_content_not_searchable",
    "input_audio": "audio_content_not_searchable", "video": "video_content_not_searchable",
}


@dataclass(frozen=True)
class NativeContent:
    text: str = ""
    dispositions: tuple[str, ...] = ()


def native_content(value: object, *, codex: bool = False, depth: int = 0) -> NativeContent:
    if depth > 12:
        return NativeContent("[nested content omitted]", ("content_nesting_limit",))
    if isinstance(value, list):
        parts = [native_content(item, codex=codex, depth=depth + 1) for item in value]
        return NativeContent("\n".join(part.text for part in parts if part.text),
                             tuple(sorted({code for part in parts for code in part.dispositions})))
    if isinstance(value, dict):
        special = _special_content(value, codex=codex, depth=depth)
        if special is not None:
            return special
    extractor = _codex_block_text if codex else _block_text
    fragment = extractor(value, 1_073_741_824, depth)
    return NativeContent(fragment.text, ("unsupported_content",) if fragment.truncated else ())


def _special_content(value: dict, *, codex: bool, depth: int) -> NativeContent | None:
    kind = value.get("type")
    if not isinstance(kind, str):
        return None
    if kind in _MEDIA:
        # Native media stays in the immutable capture. Do not index base64 or URLs
        # as if they represented the image/document/audio's contents.
        return NativeContent(dispositions=(_MEDIA[kind],))
    if kind in {"encrypted_content", "redacted_thinking"}:
        return NativeContent(dispositions=("encrypted_content_omitted",))
    if kind == "tool_reference" and isinstance(value.get("tool_name"), str):
        return NativeContent("Tool available: " + normalize_extracted_text(value["tool_name"]))
    if kind == "tool_result":
        return native_content(value.get("content", ""), codex=codex, depth=depth + 1)
    return None
