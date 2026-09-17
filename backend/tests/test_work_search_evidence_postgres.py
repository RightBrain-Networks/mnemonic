"""Query intent and supporting evidence keep the same work/checkpoint matching unit."""

import pytest

from .test_duplicate_handling_postgres import merge_work
from .test_semantic_postgres import DeterministicEmbedder
from .test_work_items_postgres import checkpoint_payload, collection, create_work, item_path

pytestmark = pytest.mark.postgres


def find(api, project, q, *, unified=False, work_fields=None, query_mode="terms", **options):
    fields = {"work_fields": work_fields} if work_fields is not None else {}
    if unified:
        response = api.post(
            f"/api/v1/projects/{project['id']}/search",
            json={
                "q": q,
                "query_mode": query_mode,
                "facets": ["work_items"],
                "filters": {"work_items": {**fields, **options}},
            },
        )
    else:
        response = api.get(
            collection(project),
            params={
                "q": q,
                "query_mode": query_mode,
                **fields,
                **options,
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    return [row["work_item"] for row in body["items"]] if unified else body["items"]


def make(api, project, payload, *, prompt="Routine checkpoint", **changes):
    return create_work(
        api,
        project,
        payload,
        title="Ordinary task",
        summary="Ordinary summary",
        initial_checkpoint={**payload["initial_checkpoint"], "prompt": prompt},
        **changes,
    )


def identity(hit):
    return hit["id"] if "id" in hit else hit["summary"]["work_item"]["id"]


@pytest.mark.parametrize("unified", [False, True])
def test_checkpoint_match_explains_stemming_and_field_exclusion(
    api, project, work_payload, unified
):
    created = make(api, project, work_payload, prompt="Telemetry is distributed across shards.")
    hits = find(api, project, "telemetry sharding", unified=unified)
    assert len(hits) == 1
    assert hits[0]["evidence_mode"] == "lexical"
    assert hits[0]["matched_fields"] == ["checkpoint"]
    excerpt = hits[0]["excerpts"][0]
    assert excerpt["checkpoint_id"] == created["initial_checkpoint"]["id"]
    assert excerpt["matched_member_id"] == created["work_item"]["id"]
    assert excerpt["match_type"] == "lexical"
    assert "shards" in excerpt["text"] and "Telemetry" in excerpt["text"]
    assert not find(
        api, project, "telemetry sharding", unified=unified, work_fields=["title", "summary"]
    )


@pytest.mark.parametrize("unified", [False, True])
def test_work_row_conjunction_attributes_both_contributing_fields(
    api, project, work_payload, unified
):
    create_work(api, project, work_payload, title="Telemetry", summary="Shards")
    hit = find(api, project, "telemetry sharding", unified=unified)[0]
    assert hit["matched_fields"] == ["title", "summary"]
    assert {excerpt["field"] for excerpt in hit["excerpts"]} == {"title", "summary"}
    assert not find(api, project, "telemetry sharding", unified=unified, work_fields=["summary"])


@pytest.mark.parametrize("unified", [False, True])
def test_queries_do_not_combine_unrelated_checkpoints(api, project, work_payload, unified):
    created = make(api, project, work_payload, prompt="Telemetry")
    response = api.post(
        item_path(project, created["work_item"]) + "/checkpoints",
        json=checkpoint_payload("Shards", "second-session"),
    )
    assert response.status_code == 201, response.text
    assert not find(api, project, "telemetry sharding", unified=unified)
    assert not find(api, project, '"telemetry shards"', unified=unified)


@pytest.mark.parametrize(
    "query_mode,q", [("terms", '"admission cookie"'), ("phrase", "admission cookie")]
)
@pytest.mark.parametrize("unified", [False, True])
def test_phrase_order_and_field_boundaries(api, project, work_payload, unified, query_mode, q):
    forward = create_work(api, project, work_payload, title="Admission cookie", summary="Granted")
    create_work(api, project, work_payload, title="Cookie admission", summary="Granted")
    create_work(api, project, work_payload, title="Admission", summary="Cookie")
    hits = find(api, project, q, unified=unified, query_mode=query_mode)
    assert [identity(hit) for hit in hits] == [forward["work_item"]["id"]]
    assert hits[0]["excerpts"][0]["match_type"] == "phrase"
    assert hits[0]["matched_fields"] == ["title"]


@pytest.mark.parametrize("unified", [False, True])
def test_mixed_phrase_conjunction_stays_within_one_matching_unit(
    api, project, work_payload, unified
):
    created = create_work(
        api, project, work_payload, title="Admission cookie", summary="Permission granted"
    )
    hit = find(api, project, 'permission "admission cookie"', unified=unified)[0]
    assert identity(hit) == created["work_item"]["id"]
    assert hit["matched_fields"] == ["title", "summary"]
    assert not find(api, project, 'permission "cookie admission"', unified=unified)


@pytest.mark.parametrize("unified", [False, True])
def test_literal_identifiers_preserve_case_punctuation_and_contiguity(
    api,
    project,
    work_payload,
    unified,
):
    exact = create_work(api, project, work_payload, title="lease_token_mismatch")
    create_work(api, project, work_payload, title="lease token mismatch")
    create_work(api, project, work_payload, title="LEASE_TOKEN_MISMATCH")
    hits = find(api, project, "lease_token_mismatch", unified=unified, query_mode="literal")
    assert [identity(hit) for hit in hits] == [exact["work_item"]["id"]]
    assert hits[0]["excerpts"][0]["text"] == "lease_token_mismatch"
    assert hits[0]["excerpts"][0]["match_type"] == "literal"


@pytest.mark.parametrize("field,query", [("tags", "correctness"), ("provenance", "feature/cache")])
def test_existing_non_vector_fields_keep_substring_evidence(
    api, project, work_payload, field, query
):
    make(api, project, work_payload)
    hit = find(api, project, query, work_fields=[field])[0]
    assert hit["matched_fields"] == [field]
    assert query in hit["excerpts"][0]["text"]
    assert hit["excerpts"][0]["match_type"] == "substring"
    assert not find(api, project, query, work_fields=["title", "summary"])


def test_identifier_scope_and_literal_sql_wildcards(api, project, work_payload):
    created = make(api, project, work_payload, prompt="A 100%_done boundary")
    work_id = created["work_item"]["id"]
    assert find(api, project, work_id[:8], work_fields=["identifiers"])[0]["matched_fields"] == [
        "identifiers"
    ]
    assert find(api, project, "100%_done", query_mode="literal")[0]["matched_fields"] == [
        "checkpoint"
    ]
    assert not find(api, project, "100%_missing", query_mode="literal")


@pytest.mark.parametrize("q", ["the", "++"])
def test_stop_words_and_punctuation_keep_the_substring_fallback(api, project, work_payload, q):
    create_work(api, project, work_payload, title="The C++ compiler")
    hit = find(api, project, q, work_fields=["title"])[0]
    assert hit["matched_fields"] == ["title"]
    assert hit["excerpts"][0]["match_type"] == "substring"
    assert q in hit["excerpts"][0]["text"].lower()


def test_phrase_does_not_join_distinct_tags(api, project, work_payload):
    initial = {**work_payload["initial_checkpoint"], "tags": ["admission", "cookie"]}
    create_work(api, project, work_payload, initial_checkpoint=initial)
    assert not find(api, project, "admission cookie", query_mode="phrase", work_fields=["tags"])
    assert not find(api, project, "admission cookie", query_mode="literal", work_fields=["tags"])


def test_excerpts_remain_bounded_and_contain_late_stemmed_match(api, project, work_payload):
    make(
        api,
        project,
        work_payload,
        prompt="<b>irrelevant</b> " + "Unrelated readiness JSON. " * 500 + "Telemetry uses shards.",
    )
    hit = find(api, project, "sharding", work_fields=["checkpoint"])[0]
    assert "shards" in hit["excerpts"][0]["text"]
    assert len(hit["excerpts"]) <= 3
    assert sum(len(excerpt["text"]) for excerpt in hit["excerpts"]) <= 320


def test_alias_checkpoint_evidence_points_to_the_matched_member(api, project, work_payload):
    root = make(api, project, work_payload)
    alias = make(api, project, work_payload, prompt="Telemetry shards explain the error.")
    merge_work(api, project, alias["work_item"], root["work_item"])
    hit = find(api, project, "telemetry sharding")[0]
    assert identity(hit) == root["work_item"]["id"]
    assert hit["matched_member"]["id"] == alias["work_item"]["id"]
    assert hit["excerpts"][0]["matched_member_id"] == alias["work_item"]["id"]
    assert hit["excerpts"][0]["checkpoint_id"] == alias["initial_checkpoint"]["id"]


def test_semantic_only_evidence_does_not_invent_literal_matches(api, project, work_payload):
    make(api, project, work_payload)
    api.app.state.semantic_embedder = DeterministicEmbedder()
    hit = find(api, project, "unrelated paraphrase", semantic=True)[0]
    assert hit["evidence_mode"] == "semantic"
    assert hit["matched_fields"] == hit["excerpts"] == []


@pytest.mark.parametrize(
    "params,code",
    [
        ({"q": '"quoted phrase"'}, "semantic_requires_unconstrained_work_query"),
        ({"q": "literal", "query_mode": "literal"}, "semantic_requires_unconstrained_work_query"),
        ({"q": "query", "work_fields": ["title"]}, "semantic_requires_all_work_fields"),
    ],
)
def test_semantic_rejects_constraints_it_cannot_enforce(api, project, params, code):
    response = api.get(collection(project), params={"semantic": True, **params})
    assert response.status_code == 422, response.text
    assert code in response.text


@pytest.mark.parametrize("unified", [False, True])
def test_phrase_excerpt_shows_qualifying_span_after_repeated_scattered_terms(
    api,
    project,
    work_payload,
    unified,
):
    make(
        api,
        project,
        work_payload,
        prompt="Admission then cookie. " * 500 + "The admission cookie was finally granted.",
    )
    hit = find(api, project, "admission cookie", query_mode="phrase", unified=unified)[0]
    assert hit["excerpts"] and not hit["excerpts_truncated"]
    assert "admission cookie" in hit["excerpts"][0]["text"].lower()


def test_phrase_excerpts_use_postgresql_stems_and_preserve_word_distances(
    api, project, work_payload
):
    make(api, project, work_payload, prompt="The workers inspected shards.")
    hit = find(api, project, "worker inspected shard", query_mode="phrase")[0]
    assert "workers inspected shards" in hit["excerpts"][0]["text"]
    assert not find(api, project, "workers shards", query_mode="phrase")


def test_overlong_phrase_support_is_omitted_and_disclosed(api, project, work_payload):
    phrase = " ".join("token" + str(index) for index in range(60))
    assert 320 < len(phrase) < 500
    make(api, project, work_payload, prompt=phrase)
    hit = find(api, project, phrase, query_mode="phrase")[0]
    assert hit["matched_fields"] == ["checkpoint"]
    assert hit["excerpts"] == [] and hit["excerpts_truncated"]


def test_mixed_phrase_and_tag_contributions_share_one_checkpoint(api, project, work_payload):
    make(api, project, work_payload, prompt="Admission cookie")
    hit = find(api, project, 'correctness "admission cookie"')[0]
    assert hit["matched_fields"] == ["tags", "checkpoint"]
    assert {item["field"] for item in hit["excerpts"]} == {"tags", "checkpoint"}
    assert sum(len(item["text"]) for item in hit["excerpts"]) <= 320


@pytest.mark.parametrize(
    "params,code",
    [
        ({"q": '"quoted phrase"'}, "semantic_requires_unconstrained_work_query"),
        ({"q": "literal", "query_mode": "literal"}, "semantic_requires_unconstrained_work_query"),
        ({"q": "query", "work_fields": ["title"]}, "semantic_requires_all_work_fields"),
    ],
)
def test_unified_semantic_rejects_constraints_before_search(api, project, params, code):
    request = {key: value for key, value in params.items() if key != "work_fields"}
    filters = {"semantic": True}
    if "work_fields" in params:
        filters["work_fields"] = params["work_fields"]
    response = api.post(
        f"/api/v1/projects/{project['id']}/search",
        json={
            **request,
            "facets": ["work_items"],
            "filters": {"work_items": filters},
        },
    )
    assert response.status_code == 422, response.text
    assert code in response.text


@pytest.mark.parametrize("prefix,ending,query", [
    ("admission_then_cookie ", "admission_cookie", "admission cookie"),
    ("admission-then-cookie ", "admission-cookie", "admission-cookie"),
    ("<p>admission then cookie</p>", "<p>admission cookie</p>", "admission cookie"),
    ("https://example.com/readiness ", "https://example.com/admission", "example.com/admission"),
])
def test_native_phrase_windows_preserve_separators_compounds_and_urls(
    postgres_engine, prefix, ending, query,
):
    from sqlalchemy import literal, select

    from mnemonic_api.services.work_phrase_windows import phrase_matches, qualifying_phrase_text

    with postgres_engine.connect() as database:
        value = literal(prefix * 500 + ending)
        assert database.scalar(select(phrase_matches(value, query)))
        window = database.scalar(select(qualifying_phrase_text(value, query)))
        assert ending in window


def test_work_evidence_retains_selection_snapshot_while_a_writer_commits(
    api, project, work_payload, monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor

    from mnemonic_api.services import work_evidence

    created = create_work(api, project, work_payload, title="Telemetry", summary="Shards")
    original = work_evidence._winning_units
    observed = []

    def write_after_selection(database, identities, work, checkpoint):
        with ThreadPoolExecutor(max_workers=1) as executor:
            response = executor.submit(api.patch, item_path(project, created["work_item"]), json={
                "expected_version": 1, "title": "Revised headline", "summary": "New explanation",
            }).result(timeout=5)
        assert response.status_code == 200, response.text
        observed.append(len(identities))
        return original(database, identities, work, checkpoint)

    monkeypatch.setattr(work_evidence, "_winning_units", write_after_selection)
    hit = find(api, project, "telemetry sharding", limit=1)[0]
    assert observed == [1]
    assert hit["matched_fields"] == ["title", "summary"]
    assert {row["text"] for row in hit["excerpts"]} == {"Telemetry", "Shards"}


@pytest.mark.parametrize("unified", [False, True])
def test_one_complete_field_excerpt_proves_query_without_losing_contributing_fields(
    api, project, work_payload, unified,
):
    create_work(api, project, work_payload, title="Copper reference",
                summary="Copper zircon decision")
    hits = find(api, project, "copper zircon", unified=unified, work_fields=["title", "summary"])
    assert len(hits) == 1
    assert hits[0]["matched_fields"] == ["title", "summary"]
    assert [excerpt["field"] for excerpt in hits[0]["excerpts"]] == ["summary"]
    assert "Copper zircon" in hits[0]["excerpts"][0]["text"]
    assert hits[0]["excerpts_truncated"] is False


@pytest.mark.parametrize("unified", [False, True])
def test_cross_field_conjunction_keeps_both_supporting_excerpts(
    api, project, work_payload, unified,
):
    create_work(api, project, work_payload, title="Copper reference", summary="Zircon decision")
    hits = find(api, project, "copper zircon", unified=unified, work_fields=["title", "summary"])
    assert len(hits) == 1 and hits[0]["matched_fields"] == ["title", "summary"]
    assert [excerpt["field"] for excerpt in hits[0]["excerpts"]] == ["title", "summary"]
