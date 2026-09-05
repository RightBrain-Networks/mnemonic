"""Strict scalar and cursor boundaries independent of the HTTP framework."""

import base64
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import ProjectActivityHead
from mnemonic_api.phase12_schemas import JobCompletionReportInput
from mnemonic_api.services.activity_cursors import cursor_base, decode_cursor, encode_cursor


@pytest.mark.parametrize(
    "revision", [0, 1, True, 1.0, "01", "0", "-1", "1e2", "9223372036854775808"]
)
def test_report_revision_is_a_positive_canonical_bounded_string(revision):
    with pytest.raises(ValidationError):
        JobCompletionReportInput(
            summary="The work is complete.", fyi_items=[], prompt_revision=revision
        )


@pytest.mark.parametrize(
    "text",
    ["", " \t", "two\nparagraphs", "hidden\u202econtrol", "bad\ud800text", "line\u2028break"],
)
def test_report_prose_rejects_blank_controls_bidi_and_surrogates(text):
    with pytest.raises(ValidationError):
        JobCompletionReportInput(summary=text, fyi_items=[], prompt_revision="1")


def head():
    return ProjectActivityHead(
        project_id=uuid4(), stream_id=uuid4(), last_sequence=20, historical_through_sequence=5
    )


@pytest.mark.parametrize(
    "change",
    [{"stream_id": 1}, {"stream_id": None}, {"stream_id": []}, {"stream_id": {}},
     {"v": True}, {"v": 1.0}, {"after": 4}, {"after": "04"}, {"after": "21"}, {"extra": "private"}],
)
def test_activity_cursor_rejects_noncanonical_or_ahead_values(change):
    stream = head()
    body = {**cursor_base(stream, "activity"), "after": "4", **change}
    with pytest.raises(ApplicationError) as error:
        decode_cursor(encode_cursor(body), stream, "activity", {})
    assert error.value.detail["code"] == "invalid_activity_cursor"


def test_stream_rotation_forces_explicit_rebootstrap():
    stream = head()
    cursor = encode_cursor({**cursor_base(stream, "activity"), "after": "4"})
    stream.stream_id = uuid4()
    with pytest.raises(ApplicationError) as error:
        decode_cursor(cursor, stream, "activity", {})
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "activity_stream_changed"


def test_cursor_rejects_duplicate_keys_padding_and_noncanonical_json():
    stream = head()
    body = {**cursor_base(stream, "activity"), "after": "4"}
    valid = encode_cursor(body)
    assert decode_cursor(valid, stream, "activity", {}) == body
    spaced = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    duplicate = (
        base64.urlsafe_b64encode((json.dumps(body)[:-1] + ',"after":"4"}').encode())
        .decode()
        .rstrip("=")
    )
    for cursor in (valid + "=", spaced, duplicate, "a" * 513):
        with pytest.raises(ApplicationError):
            decode_cursor(cursor, stream, "activity", {})
