"""Untrusted extraction responses and settings stay bounded and non-executable."""

import io
import json

import anyio
import httpx
import pytest
from pydantic import ValidationError

import mnemonic_api.artifact_tika as artifact_tika
from mnemonic_api.artifact_tika import (
    EXTRACTION_METADATA_MAX_BYTES,
    ExtractionError,
    TikaExtractor,
    _parse_result,
)
from mnemonic_api.config import Settings


def settings(**changes):
    return Settings(
        database_url="postgresql://localhost/mnemonic_test",
        api_key="test-only-" * 4,
        **changes,
    )


class RawStream(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def response(body, status=200, **headers):
    return httpx.Response(
        status,
        headers={"Content-Type": "application/json", **headers},
        stream=RawStream(body if isinstance(body, bytes) else json.dumps(body).encode()),
    )


def extract_result(result, **changes):
    extractor = TikaExtractor(settings(**changes), transport=httpx.MockTransport(lambda _: result))
    return extractor.extract(io.BytesIO(b"test"), filename="private.txt", size_bytes=4)


def test_extract_streams_only_bytes_and_safe_extension_without_secrets(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://invalid-proxy:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://invalid-proxy:1")
    payload = b"synthetic document" * 10000

    async def handler(request):
        assert request.url == "http://tika:9998/rmeta/text"
        assert request.method == "PUT"
        assert await request.aread() == payload
        assert request.headers["content-disposition"] == 'attachment; filename="artifact.txt"'
        assert request.headers["content-length"] == str(len(payload))
        assert request.headers["accept-encoding"] == "identity"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return response(
            [{"tk:content": "  cafe\u0301\r\n\x00\ud800\u202e text  ", "dc:title": "Title"}]
        )

    result = TikaExtractor(settings(), transport=httpx.MockTransport(handler)).extract(
        io.BytesIO(payload),
        filename="private\\\r\nAuthorization: secret.txt",
        size_bytes=len(payload),
    )
    assert result.text == "café\n text"
    assert result.metadata == {"dc:title": ["Title"]}
    assert result.truncated is False


def test_root_metadata_only_and_embedded_content_are_normalized():
    result = extract_result(
        response(
            [
                {
                    "tk:content": "root",
                    "dc:creator": [" Alice ", "Alice"],
                    "tk:source-path": "/tmp/private",
                    "tk:exception:embedded-exception": "secret trace",
                    "X-TIKA:content": "not metadata",
                    "\x00tk:content": "not metadata after normalization either",
                    " X-TIKA:content ": "not metadata after stripping whitespace",
                },
                {"tk:content": "child", "dc:title": "not root title"},
            ]
        )
    )
    assert result.text == "root\n\nchild"
    assert result.metadata == {"dc:creator": ["Alice"]}
    assert result.truncated is True


def test_text_metadata_and_unicode_escape_envelopes_are_bounded():
    document = {
        "tk:content": "abcdefghijk",
        **{f"field{i}": ["🌈" * 1000] * 10 for i in range(100)},
    }
    result = _parse_result(json.dumps([document]).encode(), 8)
    assert result.text == "abcdefgh"
    assert result.truncated is True
    assert len(result.metadata) <= 64
    assert (
        len(json.dumps(result.metadata, ensure_ascii=True).encode())
        <= EXTRACTION_METADATA_MAX_BYTES
    )
    assert all(len(item) <= 512 for items in result.metadata.values() for item in items)


@pytest.mark.parametrize(
    "body",
    [
        b"bad json",
        b"[" * 1000,
        {},
        [],
        [1],
        [{"tk:content": []}],
        [{"dc:title": {"nested": "bad"}}],
    ],
)
def test_invalid_response_is_terminal_and_diagnostics_are_not_reflected(body):
    with pytest.raises(ExtractionError, match="extraction_invalid_response") as caught:
        extract_result(response(body))
    assert caught.value.retryable is False
    assert str(caught.value) == "extraction_invalid_response"


def test_too_many_embedded_documents_are_rejected():
    with pytest.raises(ExtractionError, match="extraction_invalid_response"):
        _parse_result(json.dumps([{"tk:content": "x"}] * 66).encode(), 100)


def test_metadata_inspection_work_is_bounded_even_when_all_properties_are_filtered():
    document = {f"tk:ignored{i}": "value" for i in range(300)}
    document["dc:title"] = "after the work bound"
    document["tk:content"] = "Still searchable"
    result = _parse_result(json.dumps([document]).encode(), 100)
    assert result.text == "Still searchable"
    assert result.metadata == {}
    assert result.truncated


@pytest.mark.parametrize(
    "status,body,code,retryable",
    [
        (302, b"secret", "extraction_parse_failed", False),
        (413, b"secret", "extraction_resource_limit", False),
        (422, b"secret", "extraction_parse_failed", False),
        (429, b"secret", "extraction_unavailable", True),
        (503, {"status": "TIMEOUT", "message": "secret"}, "extraction_resource_limit", False),
        (503, {"status": "OOM", "message": "secret"}, "extraction_resource_limit", False),
        (503, b"unavailable", "extraction_unavailable", True),
    ],
)
def test_status_uses_fixed_errors_without_redirects_or_poison_retries(
    status, body, code, retryable
):
    with pytest.raises(ExtractionError) as caught:
        extract_result(response(body, status=status, Location="https://untrusted.invalid"))
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert "secret" not in str(caught.value)


def test_content_encoding_is_rejected_without_decompression():
    with pytest.raises(ExtractionError, match="extraction_invalid_response"):
        extract_result(response(b"not decoded", **{"Content-Encoding": "gzip"}))


@pytest.mark.parametrize("status", [[], {}, None, 7, False])
def test_malformed_unavailable_status_is_a_handled_terminal_error(status):
    with pytest.raises(ExtractionError, match="extraction_invalid_response") as caught:
        extract_result(response({"status": status}, status=503))
    assert caught.value.retryable is False


def test_streaming_response_bound_is_enforced_without_content_length():
    with pytest.raises(ExtractionError, match="extraction_output_too_large"):
        extract_result(response(b"x" * 1_048_583), artifact_extraction_max_chars=1)


def test_empty_encrypted_or_malformed_document_is_failed_not_success():
    with pytest.raises(ExtractionError, match="extraction_parse_failed"):
        extract_result(response([{"tk:exception:container-exception": "private diagnostic"}]))


@pytest.mark.parametrize("actual,declared", [(b"abc", 4), (b"abcde", 4)])
def test_changed_input_length_is_terminal(actual, declared):
    async def handler(request):
        await request.aread()
        return response([{"tk:content": "never"}])

    with pytest.raises(ExtractionError, match="extraction_content_changed"):
        TikaExtractor(settings(), transport=httpx.MockTransport(handler)).extract(
            io.BytesIO(actual), filename="test.txt", size_bytes=declared
        )


def test_oversize_input_is_rejected_without_reading():
    class Unreadable(io.BytesIO):
        def read(self, *args):
            raise AssertionError("input must not be read")

    with pytest.raises(ExtractionError, match="extraction_resource_limit"):
        TikaExtractor(settings()).extract(Unreadable(), filename="test", size_bytes=1_073_741_825)


def test_absolute_timeout_interrupts_slow_response(monkeypatch):
    # Tiny test-only deadline; public settings enforce a minimum five seconds.
    configuration = settings().model_copy(update={"artifact_extraction_timeout_seconds": 0.01})
    monkeypatch.setattr(artifact_tika, "_TRANSPORT_GRACE_SECONDS", 0)

    async def handler(request):
        await anyio.sleep(1)
        return response([{"tk:content": "never"}])

    with pytest.raises(ExtractionError, match="extraction_timeout") as caught:
        TikaExtractor(configuration, transport=httpx.MockTransport(handler)).extract(
            io.BytesIO(b"x"), filename="test.txt", size_bytes=1
        )
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp",
        "http://user:secret@tika:9998",
        "http://tika/x",
        "http://tika?fetch=secret",
        "http://tika#secret",
        "http://tika:bad",
        "http://tika\n",
        "https://",
    ],
)
def test_tika_url_accepts_only_operator_configured_origin(url):
    with pytest.raises(ValidationError):
        settings(artifact_tika_url=url)


def test_extraction_config_environment_is_validated(monkeypatch):
    monkeypatch.setenv("MNEMONIC_ARTIFACT_TIKA_URL", "http://localhost:9998/")
    monkeypatch.setenv("MNEMONIC_ARTIFACT_EXTRACTION_MAX_CHARS", "8000000")
    monkeypatch.setenv("MNEMONIC_ARTIFACT_EXTRACTION_TIMEOUT_SECONDS", "300")
    configured = settings()
    assert configured.artifact_tika_url == "http://localhost:9998"
    assert configured.artifact_extraction_max_chars == 8_000_000
    assert configured.artifact_extraction_timeout_seconds == 300
    with pytest.raises(ValidationError):
        settings(artifact_extraction_max_chars=8_000_001)
