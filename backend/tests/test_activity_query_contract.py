"""Activity cursor failures never silently change project, scope or stream."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from mnemonic_api.activity_query_schemas import QueryActivityRequest
from mnemonic_api.errors import ApplicationError
from mnemonic_api.services.activity_query_cursors import (
    ActivityWindow,
    activity_window,
    encode_position,
    query_scope,
)


def test_poll_scope_survives_since_window_but_rejects_filter_change():
    project, stream = uuid4(), uuid4()
    initial = QueryActivityRequest(since="2026-11-10T00:00:00Z", facets=["work_items"])
    checkpoint = ActivityWindow(project, stream, 0, 7, 0).checkpoint(initial)
    poll = QueryActivityRequest(after=checkpoint, facets=["work_items"])
    assert activity_window(poll, project, stream, 10).lower == 7
    with pytest.raises(ApplicationError) as changed:
        activity_window(QueryActivityRequest(after=checkpoint, facets=["artifacts"]),
                        project, stream, 10)
    assert changed.value.detail["code"] == "invalid_activity_query_cursor"


def test_restored_stream_is_reported_even_when_head_is_lower():
    project, stream = uuid4(), uuid4()
    request = QueryActivityRequest()
    checkpoint = ActivityWindow(project, stream, 0, 8, 0).checkpoint(request)
    with pytest.raises(ApplicationError) as restored:
        activity_window(QueryActivityRequest(after=checkpoint), project, uuid4(), 2)
    assert restored.value.detail["code"] == "activity_stream_changed"


@pytest.mark.parametrize("malformed_stream", [{}, [], 1, None])
def test_cursor_rejects_nonstring_stream_ids(malformed_stream):
    project, stream = uuid4(), uuid4()
    request = QueryActivityRequest()
    checkpoint = encode_position({
        "v": 1, "kind": "checkpoint", "project_id": str(project),
        "stream_id": malformed_stream, "scope": query_scope(request, polling=True),
        "after": "0",
    })
    with pytest.raises(ApplicationError) as invalid:
        activity_window(QueryActivityRequest(after=checkpoint), project, stream, 0)
    assert invalid.value.status_code == 422
    assert invalid.value.detail["code"] == "invalid_activity_query_cursor"


def test_page_cursor_requires_same_original_window_but_accepts_new_limit():
    project, stream = uuid4(), uuid4()
    original = QueryActivityRequest(since="2026-11-10T00:00:00Z", limit=1)
    cursor = ActivityWindow(project, stream, 0, 8, 0).page_cursor(original, 2)
    continued = QueryActivityRequest(since="2026-11-10T00:00:00Z", cursor=cursor, limit=80)
    window = activity_window(continued, project, stream, 15)
    assert (window.lower, window.last, window.upper) == (0, 2, 8)
    with pytest.raises(ApplicationError):
        activity_window(QueryActivityRequest(cursor=cursor), project, stream, 15)


@pytest.mark.parametrize("fields", [
    {"since": "2026-11-10T00:00:00"},
    {"since": "2026-11-10T00:00:00Z", "until": "2026-11-10T00:00:00Z"},
    {"after": "token", "until": "2026-11-10T00:00:00Z"},
    {"start": "now", "cursor": "token"},
    {"facets": ["decisions"], "filters": {"work_items": {"statuses": ["pending"]}}},
])
def test_activity_request_rejects_ambiguous_or_unscoped_arguments(fields):
    with pytest.raises(ValidationError):
        QueryActivityRequest.model_validate(fields)
