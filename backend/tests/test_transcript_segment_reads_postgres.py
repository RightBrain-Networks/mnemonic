"""Public context windows stay bounded while retained native metadata remains exact."""

import hashlib
import json

import pytest
from sqlalchemy import event, select, update

from mnemonic_api.services.transcript_segment_projection import (
    NATIVE_FIELDS,
    OPTIONAL_METADATA_BYTES,
)
from mnemonic_api.transcript_normalized_storage import SEGMENTS

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import collection, read, register, run

pytestmark = pytest.mark.postgres


def _ready(api, project, work_payload, tmp_path, postgres_engine, rows):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    source.write_text("\n".join(json.dumps(row) for row in rows))
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    ready = read(api, project, record)
    assert ready["normalization_status"] == "ready"
    with api.app.state.session_factory() as database:
        segments = database.execute(select(SEGMENTS).order_by(SEGMENTS.c.ordinal)).mappings().all()
    return ready, segments


def _params(ready, segment, **extra):
    return {"segment_id": segment["segment_id"],
            "expected_normalized_revision": ready["normalized_revision"], **extra}


def _text(api, project, ready, params):
    response = api.get(collection(project) + "/" + ready["id"] + "/text", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("field", ["native_event_id", "tool_name"])
def test_oversized_native_metadata_is_projected_out_before_database_hydration(
    api, project, work_payload, tmp_path, postgres_engine, field,
):
    ready, segments = _ready(api, project, work_payload, tmp_path, postgres_engine, [
        {"role": "assistant", "content": "small useful text"},
    ])
    canonical = segments[0]["segment_data"] | {field: "private-oversized-native-" * 10000}
    # A historical/future adapter's native metadata still cannot defeat a bounded read.
    with api.app.state.session_factory.begin() as database:
        database.execute(update(SEGMENTS).values(segment_data=canonical))
    statements = []

    def observed(_connection, _cursor, statement, _parameters, _context, _many):
        if "transcript_segments" in statement:
            statements.append(statement)

    event.listen(postgres_engine, "before_cursor_execute", observed)
    try:
        page = _text(api, project, ready, _params(ready, segments[0], limit=100))
    finally:
        event.remove(postgres_engine, "before_cursor_execute", observed)
    segment = page["segments"][0]
    assert segment[field] is None
    assert "metadata_omitted_for_budget" in segment["dispositions"]
    assert "private-oversized-native" not in json.dumps(page)
    assert segment["text"] == canonical["text"]
    event_id = hashlib.sha256(
        f"{ready['normalized_revision']}:{canonical['source_record']}".encode()).hexdigest()[:24]
    assert segment["event_id"] == event_id
    assert segment["segment_id"] == hashlib.sha256(
        f"{event_id}:{canonical['source_block']}".encode()).hexdigest()[:24]
    assert any("CASE WHEN" in statement and "octet_length" in statement for statement in statements)
    assert not any("SELECT transcript_segments.segment_data \n" in sql or " - " in sql
                   for sql in statements)
    with api.app.state.session_factory() as database:
        assert database.scalar(select(SEGMENTS.c.segment_data)) == canonical


def test_optional_native_metadata_budget_is_shared_by_the_entire_window(
    api, project, work_payload, tmp_path, postgres_engine,
):
    ready, segments = _ready(api, project, work_payload, tmp_path, postgres_engine, [
        {"role": "assistant", "content": [{"type": "tool_use", "name": "n" * 1700,
                                             "input": {"flag": True}}]} for _ in range(21)
    ])
    page = _text(api, project, ready, _params(ready, segments[0], after=20, limit=2000))
    assert len(page["segments"]) == 21
    retained = [{key: item[key] for key in NATIVE_FIELDS if item[key] is not None}
                for item in page["segments"]]
    assert sum(len(json.dumps(item, ensure_ascii=False).encode()) for item in retained) \
        <= OPTIONAL_METADATA_BYTES + 2 * 21
    assert sum(item["tool_name"] is not None for item in page["segments"]) == 2
    assert all("metadata_omitted_for_budget" in item["dispositions"]
               for item in page["segments"][2:])
    assert page["total_chars"] == len("\n\n".join(row["text"] for row in segments))
    assert page["next_segment_id"] is None
    assert len(page["text"]) <= page["limit"]


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 7])
def test_tiny_budgets_and_empty_segments_reconstruct_the_logical_joined_window(
    api, project, work_payload, tmp_path, postgres_engine, limit,
):
    ready, segments = _ready(api, project, work_payload, tmp_path, postgres_engine, [
        {"role": "user", "content": ["", "A", "", "B"]},
    ])
    params = _params(ready, segments[2], before=2, after=1, limit=limit)
    collected = {row["segment_id"]: "" for row in segments}
    visited = set()
    for call in range(12):
        page = _text(api, project, ready, params)
        assert len(page["text"]) <= limit
        if call == 0:
            assert page["total_chars"] == 8
            assert page["segment_window"] == {
                "anchor_segment_id": segments[2]["segment_id"], "anchor_ordinal": 2,
                "first_ordinal": 0, "last_ordinal": 3,
            }
        for item in page["segments"]:
            assert item["text_offset"] == len(collected[item["segment_id"]])
            collected[item["segment_id"]] += item["text"]
            visited.add(item["segment_id"])
        if page["next_segment_id"] is None:
            break
        params.update(segment_id=page["next_segment_id"], offset=page["next_segment_offset"],
                      before=0, after=page["next_segment_after"])
    else:
        pytest.fail("The selected window did not make bounded forward progress")
    assert visited == set(collected)
    assert "\n\n".join(collected[row["segment_id"]] for row in segments) == "\n\nA\n\n\n\nB"


def test_structured_offsets_can_reach_content_beyond_the_search_extraction_cap(
    api, project, work_payload, tmp_path, postgres_engine,
):
    api.app.state.settings.artifact_extraction_max_chars = 100
    ready, segments = _ready(api, project, work_payload, tmp_path, postgres_engine, [
        {"role": "user", "content": "x" * 8_000_001 + "late evidence"},
    ])
    page = _text(api, project, ready, _params(ready, segments[0], offset=8_000_001, limit=100))
    assert page["text"] == "late evidence"
    assert page["total_chars"] == len("late evidence")
    assert page["segments"][0]["text_offset"] == 8_000_001
    assert page["next_segment_id"] is None
