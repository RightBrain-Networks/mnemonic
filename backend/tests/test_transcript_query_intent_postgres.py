"""Exact transcript search respects canonical boundaries and revision-bound evidence."""

import json
from uuid import UUID

import pytest
from sqlalchemy import update

from mnemonic_api.models import Transcript

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import collection, read, register, run
from .test_transcript_search_postgres import seed_transcripts

pytestmark = pytest.mark.postgres


def capture(api, project, work_payload, tmp_path, postgres_engine, texts, client="claude-code"):
    work, _, record, source = register(api, project, work_payload, tmp_path, client=client)
    rows = [{"role": "user", "content": text} for text in texts]
    if client == "codex":
        rows = [{"type": "response_item", "payload": {"type": "message", **row}} for row in rows]
    source.write_text("\n".join(json.dumps(row) for row in rows))
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    return read(api, project, record)


def query(api, project, q, *, endpoint="content", **kwargs):
    payload = {"query": q, "fulltext": True, **kwargs}
    if endpoint == "unified":
        payload["q"] = payload.pop("query")
        response = api.post(f"/api/v1/projects/{project['id']}/search",
                            json={"facets": ["transcripts"], **payload})
    else:
        response = api.post(collection(project) + "/search-content", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    if endpoint == "unified":
        body["items"] = [item["transcript"] for item in body["items"]]
        body.update(body["coverage"]["transcripts"])
    return body


@pytest.mark.parametrize("client", ["claude-code", "codex"])
@pytest.mark.parametrize("endpoint", ["content", "unified"])
def test_phrase_order_boundaries_and_compact_revision_evidence(
    api, project, work_payload, tmp_path, postgres_engine, client, endpoint,
):
    record = capture(api, project, work_payload, tmp_path, postgres_engine,
                     ["prefix admission cookie suffix", "boundarytoken", "nexttoken"], client)
    result = query(api, project, '"admission cookie"', endpoint=endpoint)
    assert result["total"] == 1 and result["detail"] == "compact"
    hit = result["items"][0]
    assert hit["normalized_revision"] == record["normalized_revision"]
    assert hit["segment_id"] and hit["matched_fields"] == ["content"]
    assert "admission cookie" in hit["snippet"]
    full = query(api, project, '"admission cookie"', endpoint=endpoint, detail="full")
    for field in ("id", "segment_id", "normalized_revision", "snippet", "matched_fields"):
        assert full["items"][0][field] == hit[field]
    assert query(api, project, '"cookie admission"', endpoint=endpoint)["total"] == 0
    assert query(api, project, "admission cookie", endpoint=endpoint,
                 query_mode="phrase")["total"] == 1
    assert query(api, project, "boundarytoken nexttoken", endpoint=endpoint)["total"] == 1
    assert query(api, project, '"boundarytoken nexttoken"', endpoint=endpoint)["total"] == 0


@pytest.mark.parametrize("client", ["claude-code", "codex"])
def test_literal_case_punctuation_whitespace_and_late_repeated_identifier(
    api, project, work_payload, tmp_path, postgres_engine, client,
):
    literal = "lease_token_mismatch: ERROR [100%] a_b"
    capture(api, project, work_payload, tmp_path, postgres_engine,
            ["lease " * 500 + literal, "   !!!   "], client)
    result = query(api, project, literal, query_mode="literal")
    hit = result["items"][0]
    assert literal in hit["snippet"] and hit["segment_id"]
    assert query(api, project, literal.lower(), query_mode="literal")["total"] == 0
    assert query(api, project, literal.replace("a_b", "axb"), query_mode="literal")["total"] == 0
    assert query(api, project, "!!!", query_mode="literal")["total"] == 1
    assert query(api, project, "lease token mismatch", query_mode="phrase")["total"] == 1


def test_phrase_excerpt_uses_qualifying_occurrence_with_repeated_terms(
    api, project, work_payload, tmp_path, postgres_engine,
):
    capture(api, project, work_payload, tmp_path, postgres_engine,
            ["alpha " * 500 + "beta far away " * 300 + "alpha alpha beta actual evidence"])
    result = query(api, project, '"alpha alpha beta"')
    assert result["total"] == 1
    assert "alpha alpha beta" in result["items"][0]["snippet"]
    assert len(result["items"][0]["snippet"]) <= 320


@pytest.mark.parametrize("endpoint", ["content", "unified"])
def test_legacy_exact_omission_and_metadata_only_attribution(api, project, endpoint):
    identity = seed_transcripts(api, project, "admission cookie legacy body")[0]
    with api.app.state.session_factory.begin() as database:
        database.execute(update(Transcript).where(Transcript.id == identity).values(
            extracted_metadata={"title": ["admission cookie"], "split": ["split", "value"]}))
    result = query(api, project, '"admission cookie"', endpoint=endpoint)
    assert result["total"] == 1 and result["unsegmented_content_omitted"] == 1
    assert result["indexing_incomplete"] is True
    hit = result["items"][0]
    assert hit["matched_fields"] == ["metadata"]
    assert hit["snippet"] is None and hit["segment_id"] is None
    assert query(api, project, '"split value"', endpoint=endpoint)["total"] == 0
    assert query(api, project, '"legacy body"', endpoint=endpoint)["total"] == 0
    assert query(api, project, "legacy body", endpoint=endpoint)["total"] == 1


def test_exact_segment_search_enforces_budget_and_published_revision(
    api, project, work_payload, tmp_path, postgres_engine,
):
    record = capture(api, project, work_payload, tmp_path, postgres_engine, ["!!! " * 1000])
    api.app.state.settings.transcript_search_max_bytes = 100
    response = api.post(collection(project) + "/search-content", json={
        "query": "!!!", "query_mode": "literal", "fulltext": True})
    assert response.status_code == 503 and "transcript_search_capacity" in response.text
    api.app.state.settings.transcript_search_max_bytes = 10000
    with api.app.state.session_factory.begin() as database:
        database.execute(update(Transcript).where(Transcript.id == UUID(record["id"])).values(
            normalized_revision=None, normalized_sha256=None, normalization_schema_version=None,
            normalizer_version=None, normalized_size_bytes=None, segment_count=0,
            normalization_incomplete=False, normalization_status="pending"))
    assert query(api, project, "!!!", query_mode="literal")["total"] == 0


def test_exact_metadata_phrase_cannot_cross_individual_fields(api, project):
    identity = seed_transcripts(api, project, "ordinary text")[0]
    with api.app.state.session_factory.begin() as database:
        database.execute(update(Transcript).where(Transcript.id == identity).values(
            source_path="/synthetic/metadatafirst", client="codex"))
    assert query(api, project, "metadatafirst codex", fulltext=False)["total"] == 1
    assert query(api, project, '"metadatafirst codex"', fulltext=False)["total"] == 0
    assert query(api, project, "metadatafirst\ncodex", fulltext=False,
                 query_mode="literal")["total"] == 0


def test_metadata_phrase_does_not_attribute_half_phrase_in_content(
    api, project, work_payload, tmp_path, postgres_engine,
):
    record = capture(api, project, work_payload, tmp_path, postgres_engine,
                     ["admission is mentioned here but the other word is absent"])
    with api.app.state.session_factory.begin() as database:
        database.execute(update(Transcript).where(Transcript.id == UUID(record["id"])).values(
            extracted_metadata={"description": ["admission cookie"]}))
    result = query(api, project, '"admission cookie"')
    assert result["items"][0]["matched_fields"] == ["metadata"]
    assert result["items"][0]["segment_id"] is None
    assert result["items"][0]["snippet"] is None


def test_literal_hit_uses_bounded_sql_excerpt_without_body_or_payload_hydration(
    api, project, work_payload, tmp_path, postgres_engine,
):
    from sqlalchemy import event

    capture(api, project, work_payload, tmp_path, postgres_engine,
            ["padding " * 10000 + "ExactLiteralNeedle end"])
    statements = []

    def collect(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(postgres_engine, "before_cursor_execute", collect)
    try:
        hit = query(api, project, "ExactLiteralNeedle", query_mode="literal")["items"][0]
    finally:
        event.remove(postgres_engine, "before_cursor_execute", collect)
    assert "ExactLiteralNeedle" in hit["snippet"] and len(hit["snippet"]) <= 320
    assert not any("segment_data" in statement for statement in statements)
    assert not any(statement.startswith("SELECT transcript_segments.text")
                   for statement in statements)
    assert not any(statement.startswith("SELECT transcripts.normalized_text")
                   for statement in statements)


@pytest.mark.parametrize("mode", ["phrase", "literal"])
def test_long_exact_match_keeps_locator_without_inventing_short_supporting_excerpt(
    api, project, work_payload, tmp_path, postgres_engine, mode,
):
    phrase = " ".join(f"word{number}" for number in range(60))
    capture(api, project, work_payload, tmp_path, postgres_engine, [phrase])
    hit = query(api, project, phrase, query_mode=mode)["items"][0]
    assert hit["matched_fields"] == ["content"] and hit["segment_id"]
    assert hit["snippet"] is None
    assert hit["snippet_omission_reason"] == "matched_span_exceeds_budget"


@pytest.mark.parametrize("mode", ["phrase", "literal"])
def test_kind_filtered_exact_search_preserves_legacy_coverage_before_exclusion(
    api, project, work_payload, tmp_path, postgres_engine, mode,
):
    capture(api, project, work_payload, tmp_path, postgres_engine, ["bounded exact evidence"])
    seed_transcripts(api, project, "bounded exact evidence in legacy flat text")
    kinds = {"content_kinds": ["human_text"]}
    shared = {"fulltext": True, "query_mode": mode}
    responses = [
        api.post(collection(project) + "/search-content", json={
            **shared, **kinds, "query": "bounded exact evidence"}),
        api.post(f"/api/v1/projects/{project['id']}/search", json={
            **shared, "q": "bounded exact evidence", "facets": ["transcripts"],
            "filters": {"transcripts": kinds}}),
    ]
    for response in responses:
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == 1
        coverage = page["coverage"]["transcripts"] if "coverage" in page else page
        assert coverage["unsegmented_content_omitted"] == 1
        assert coverage["indexing_incomplete"] is True
