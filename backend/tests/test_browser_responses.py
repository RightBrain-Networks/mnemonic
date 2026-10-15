"""Browser response bounds and strict prompt/evidence wire contracts."""

import asyncio
import json

import pytest

from mnemonic_api.application.browser_prompt_responses import validate_prompt_response
from mnemonic_api.application.browser_responses import BrowserResponse

PROJECT = "00000000-0000-4000-8000-000000000001"


def capture(path, body, *, status=200, headers=()):
    async def run():
        result = BrowserResponse(path, "GET")
        await result.capture({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"), *headers,
        ]})
        await result.capture({"type": "http.response.body", "body": body})
        return result.response()

    return asyncio.run(run())


@pytest.mark.parametrize("status,value,expected", [
    (429, "3", "3"), (503, "3", None), (429, "private", None),
    (429, "Wed, 21 Oct 2015 07:28:00 GMT", "Wed, 21 Oct 2015 07:28:00 GMT"),
    (429, "9" * 129, None),
])
def test_only_bounded_valid_429_retry_after_reaches_browser(status, value, expected):
    response = capture("projects", b'{"detail":"busy"}', status=status,
                       headers=[(b"retry-after", value.encode()), (b"set-cookie", b"secret=x")])
    assert response.headers.get("retry-after") == expected
    assert "set-cookie" not in response.headers


def test_bom_validation_preserves_response_bytes_and_duplicate_encodings_are_rejected():
    body = b'\xef\xbb\xbf{"id":9223372036854775806}'
    path = f"projects/{PROJECT}/work-items/{PROJECT}/completion-evidence"
    response = capture(path, body)
    assert response.status_code == 200 and response.body == body
    response = capture(path, body, headers=[
        (b"content-encoding", b"identity"), (b"content-encoding", b"gzip"),
    ])
    assert response.status_code == 502


@pytest.mark.parametrize("suffix,limit", [
    ("activity", 524_288), ("settings", 4_194_304), ("priority-rubric", 2_097_152),
    ("code-reviews", 524_288), ("job-completion-reports/count", 1_024),
    ("job-completion-reports", 2_097_152),
])
def test_route_response_caps_are_applied_before_any_browser_bytes(suffix, limit):
    path = f"projects/{PROJECT}/{suffix}"
    response = capture(path, b'"' + b"x" * (limit - 1) + b'"')
    assert response.status_code == 502
    assert response.body == b'{"detail":"Mnemonic\'s API returned an incomplete response."}'
    declared = capture(path, b"{}", headers=[(b"content-length", str(limit + 1).encode())])
    assert declared.status_code == 502


def prompt():
    return {
        "id": "recall-pointer", "name": "Recall", "description": "Recall this work.",
        "size_bytes": 5, "content": "hello", "revision": "a" * 64,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }


@pytest.mark.parametrize("field,value", [
    ("content", ""), ("size_bytes", 4), ("name", ""), ("description", ""),
    ("revision", "invalid"), ("created_at", 0), ("id", "resume-work"),
    ("extra", "private"),
])
def test_malformed_successful_prompt_details_remain_uncertain(field, value):
    path = f"projects/{PROJECT}/prompts/recall-pointer"
    valid = prompt()
    assert capture(path, json.dumps(valid).encode()).status_code == 200
    invalid = {**valid, field: value}
    response = capture(path, json.dumps(invalid).encode())
    assert response.status_code == 502
    assert b"private" not in response.body


def test_rendered_report_requires_decimal_revision_and_other_prompts_require_null():
    path = f"projects/{PROJECT}/prompts/job-completion-report/render"
    row = {
        "project_id": PROJECT, "prompt_id": "job-completion-report", "work_item_id": None,
        "code_review_id": None, "content": "hello", "revision": "a" * 64,
        "prompt_revision": "9223372036854775807",
    }
    validate_prompt_response(path, "POST", row)
    with pytest.raises(ValueError):
        validate_prompt_response(path, "POST", {**row, "prompt_revision": 1})
    with pytest.raises(ValueError):
        validate_prompt_response(path, "POST", {**row, "prompt_id": "recall-pointer"})
