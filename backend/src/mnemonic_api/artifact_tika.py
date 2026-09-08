"""Bounded, credential-free extraction of untrusted bytes in the private Tika service.

Only application-owned settings select the origin. Neither files nor callers can
supply a fetch URL, parser configuration, content type, or additional HTTP headers.
Do not log response bodies: even Tika exceptions can contain document fragments.
"""

import json
import re
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import BinaryIO

import anyio
import httpx

from mnemonic_api.config import Settings

EXTRACTION_INPUT_MAX_BYTES = 1_073_741_824
EXTRACTION_METADATA_MAX_BYTES = 8192
EXTRACTION_METADATA_MAX_KEYS = 64
EXTRACTION_METADATA_KEY_MAX_CHARS = 128
EXTRACTION_METADATA_VALUE_MAX_CHARS = 512
EXTRACTION_METADATA_MAX_VALUES = 8
EXTRACTION_MAX_DOCUMENTS = 65
_CHUNK_BYTES = 65_536
_TRANSPORT_GRACE_SECONDS = 10
_METADATA_INSPECTION_MAX_KEYS = 256
_CONTENT_KEY = "tk:content"
_SAFE_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,16}\Z")
_CONTROL = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff"
    "\u061c\u200e\u200f\u202a-\u202e\u2066-\u206f\ufeff]"
)
_EXCEPTION_PREFIX = "tk:exception:"


@dataclass(frozen=True)
class ExtractedArtifact:
    text: str
    metadata: dict[str, list[str]]
    truncated: bool


class ExtractionError(Exception):
    """A fixed machine-readable code, never upstream diagnostics or file content."""

    def __init__(self, code: str, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def normalize_extracted_text(value: str) -> str:
    clean = _CONTROL.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))
    return unicodedata.normalize("NFC", clean).strip()


def _metadata_key_allowed(key: str) -> bool:
    lowered = key.casefold()
    # Native content, paths and diagnostics are not artifact metadata. Keep useful
    # document properties (author/title/page count/etc.) as explicitly untrusted.
    return not (
        lowered.startswith(("tk:", "x-tika:"))
        or lowered in {"resourceName".casefold(), "content", "exception", "stacktrace"}
    )


def _metadata_values(value: object) -> tuple[list[str], bool]:
    items = value if isinstance(value, list) else [value]
    result: list[str] = []
    truncated = len(items) > EXTRACTION_METADATA_MAX_VALUES
    for item in items[:EXTRACTION_METADATA_MAX_VALUES]:
        if not isinstance(item, str):
            raise ExtractionError("extraction_invalid_response")
        clean = normalize_extracted_text(item)
        truncated |= len(clean) > EXTRACTION_METADATA_VALUE_MAX_CHARS
        clean = clean[:EXTRACTION_METADATA_VALUE_MAX_CHARS]
        if clean and clean not in result:
            result.append(clean)
    return result, truncated


def _normalize_metadata(document: dict[str, object]) -> tuple[dict[str, list[str]], bool]:
    result: dict[str, list[str]] = {}
    truncated = False
    for position, (key, value) in enumerate(document.items()):
        # A crafted document can contain huge numbers of tiny properties. Bound
        # normalization/serialization work as well as the resulting JSON bytes.
        if position >= _METADATA_INSPECTION_MAX_KEYS:
            truncated = True
            break
        if not _metadata_key_allowed(key):
            continue
        clean_key = normalize_extracted_text(key)
        if not _metadata_key_allowed(clean_key):
            continue
        if not clean_key or len(clean_key) > EXTRACTION_METADATA_KEY_MAX_CHARS:
            truncated = True
            continue
        values, cut = _metadata_values(value)
        truncated |= cut
        if not values:
            continue
        candidate = result | {clean_key: values}
        encoded = json.dumps(candidate, ensure_ascii=True).encode("ascii")
        if (
            len(candidate) > EXTRACTION_METADATA_MAX_KEYS
            or len(encoded) > EXTRACTION_METADATA_MAX_BYTES
        ):
            truncated = True
            continue
        result = candidate
    return result, truncated


def _parse_result(body: bytes, maximum_chars: int) -> ExtractedArtifact:
    try:
        documents = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise ExtractionError("extraction_invalid_response") from exc
    if not isinstance(documents, list) or not 1 <= len(documents) <= EXTRACTION_MAX_DOCUMENTS:
        raise ExtractionError("extraction_invalid_response")
    if any(not isinstance(document, dict) for document in documents):
        raise ExtractionError("extraction_invalid_response")
    metadata, truncated = _normalize_metadata(documents[0])
    text_parts: list[str] = []
    remaining = maximum_chars
    for document in documents:
        value = document.get(_CONTENT_KEY, "")
        if not isinstance(value, str):
            raise ExtractionError("extraction_invalid_response")
        text = normalize_extracted_text(value)
        truncated |= len(text) > remaining or any(
            key.startswith((_EXCEPTION_PREFIX, "tk:warn:")) for key in document
        )
        if text and remaining:
            text_parts.append(text[:remaining])
            remaining = max(0, remaining - len(text) - 2)
    combined = "\n\n".join(text_parts)[:maximum_chars]
    if not combined and any(
        _EXCEPTION_PREFIX + "container-exception" in document for document in documents
    ):
        raise ExtractionError("extraction_parse_failed")
    return ExtractedArtifact(combined, metadata, truncated)


async def _content_chunks(content: BinaryIO, size_bytes: int) -> AsyncIterator[bytes]:
    remaining = size_bytes
    while remaining:
        chunk = content.read(min(_CHUNK_BYTES, remaining))
        if not chunk:
            raise ExtractionError("extraction_content_changed")
        remaining -= len(chunk)
        yield chunk
        await anyio.sleep(0)
    if content.read(1):
        raise ExtractionError("extraction_content_changed")


async def _bounded_body(response: httpx.Response, maximum: int) -> bytes:
    if response.headers.get("content-encoding", "identity").casefold() != "identity":
        raise ExtractionError("extraction_invalid_response")
    body = bytearray()
    async for chunk in response.aiter_raw():
        if len(body) + len(chunk) > maximum:
            raise ExtractionError("extraction_output_too_large")
        body.extend(chunk)
    return bytes(body)


def _check_status(status: int, body: bytes) -> None:
    if status == 200:
        return
    if status == 413:
        raise ExtractionError("extraction_resource_limit")
    if status == 503:
        try:
            result = json.loads(body)
        except ValueError, RecursionError:
            result = None
        if isinstance(result, dict) and "status" in result:
            parser_status = result["status"]
            if not isinstance(parser_status, str):
                raise ExtractionError("extraction_invalid_response")
            if parser_status in {"TIMEOUT", "OOM", "UNSPECIFIED_CRASH"}:
                raise ExtractionError("extraction_resource_limit")
    if status in {429, 500, 502, 503, 504}:
        raise ExtractionError("extraction_unavailable", retryable=True)
    raise ExtractionError("extraction_parse_failed")


class TikaExtractor:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    def extract(self, content: BinaryIO, *, filename: str, size_bytes: int) -> ExtractedArtifact:
        if not 0 <= size_bytes <= EXTRACTION_INPUT_MAX_BYTES:
            raise ExtractionError("extraction_resource_limit")
        # The extension is enough as a detector hint: never disclose the original
        # filename to parser logs or allow header/control injection through it.
        match = _SAFE_EXTENSION.search(filename)
        safe_name = "artifact" + (match.group(0).lower() if match else "")
        try:
            return anyio.run(self._extract, content, safe_name, size_bytes)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ExtractionError("extraction_timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ExtractionError("extraction_unavailable", retryable=True) from exc

    async def _extract(
        self, content: BinaryIO, filename: str, size_bytes: int
    ) -> ExtractedArtifact:
        # Give the isolated parser time to return its terminal timeout/OOM result
        # instead of turning a poison document into an ambiguous network retry.
        timeout = self.settings.artifact_extraction_timeout_seconds + _TRANSPORT_GRACE_SECONDS
        maximum = self.settings.artifact_extraction_max_chars
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size_bytes),
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
        with anyio.fail_after(timeout):
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, trust_env=False, transport=self.transport
            ) as client:
                async with client.stream(
                    "PUT",
                    self.settings.artifact_tika_url + "/rmeta/text",
                    headers=headers,
                    content=_content_chunks(content, size_bytes),
                ) as response:
                    body = await _bounded_body(
                        response, maximum * 6 + 1_048_576 if response.status_code == 200 else 65_536
                    )
                    _check_status(response.status_code, body)
                    if (
                        response.headers.get("content-type", "").split(";", 1)[0]
                        != "application/json"
                    ):
                        raise ExtractionError("extraction_invalid_response")
        return _parse_result(body, maximum)
