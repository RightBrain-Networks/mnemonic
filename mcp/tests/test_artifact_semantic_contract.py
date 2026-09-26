"""Native semantic artifact evidence remains scoped, pinned and explicitly incomplete."""

import copy
import hashlib
import json
from uuid import UUID

import pytest
from conftest import NOW, PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from search_ranking_fixtures import add_ranking, semantic_disposition
from test_artifact_text import text_page
from test_compact_search import native_call, response_page
from test_multi_project_search import OTHER_PROJECT
from test_unified_search import page as unified_page

from mnemonic_mcp.artifact_semantic import EMBEDDING_COUNTS
from mnemonic_mcp.search_disclosure import ArtifactAppliedFilters, search_disclosure

MODEL, CONFIG, TEXT_HASH = "semantic-test-model", "chunks-test-v1", "b" * 64
TEXT = "A paraphrased decision about cookies."


def embedding(**overrides):
    return {"model": MODEL, "chunk_config": CONFIG, "state": "ready",
            **dict.fromkeys(EMBEDDING_COUNTS, 0), "ready": 1, "passages": 1,
            "score_type": "semantic_reciprocal_rank", "total_kind": "ranked_candidates",
            **overrides}


def passage(identity, revision=1):
    source = [identity, revision, TEXT_HASH, MODEL, CONFIG, 7, 7 + len(TEXT)]
    return {"passage_id": hashlib.sha256(json.dumps(source, separators=(",", ":")).encode()).hexdigest(),
            "artifact_revision": revision, "text_sha256": TEXT_HASH, "model": MODEL,
            "chunk_config": CONFIG, "start_offset": 7, "end_offset": 7 + len(TEXT),
            "token_count": 12, "token_limit": 512, "cosine_similarity": -0.25,
            "score_is_probability": False, "score_type": "cosine_similarity"}


def semantic_page(tool, summary, *, projects=False):
    page = response_page("search_artifact_contents", summary)
    item = page["items"][0]
    item["artifact"]["extraction"].update(status="ready", error_code=None)
    item.update(evidence="semantic", passage=passage(item["artifact"]["id"]),
                snippet=TEXT, matched_fields=["content"], score=1.0,
                score_type="semantic_reciprocal_rank")
    page.update(fulltext=True, match_mode="semantic_passages", embedding=embedding(),
                indexing={"ready": 1, "pending": 0, "failed": 0, "truncated": 0},
                semantic=semantic_disposition("completed", scope="full_scope"),
                score_type="semantic_reciprocal_rank", total_kind="ranked_candidates")
    selection = tuple(sorted([UUID(PROJECT_ID), UUID(OTHER_PROJECT)], key=str)) if projects else UUID(PROJECT_ID)
    if tool == "search":
        hit = {"facet": "artifacts", "id": item["artifact"]["id"], "project_id": PROJECT_ID,
               "created_at": NOW, "updated_at": NOW, "score": 1 / 61, "artifact": item}
        page = unified_page([hit], limit=20, detail="compact", work_rank_scope="work_items",
                            tag_counts=None)
        page["search_scope"].update(searched_facets=["artifacts"], transcripts="not_selected")
        page["coverage"]["artifacts"]["embedding"] = embedding()
        page["project_coverage"] = [{
            "project_id": PROJECT_ID, "project_name": "Owner", "project_slug": "owner",
            "facet_totals": copy.deepcopy(page["facet_totals"]),
            "coverage": copy.deepcopy(page["coverage"]), "indexing_incomplete": False,
        }]
        if projects:
            empty = copy.deepcopy(page["project_coverage"][0])
            empty.update(project_id=OTHER_PROJECT, project_name="Empty", project_slug="empty",
                         facet_totals={"work_items": 0, "artifacts": 0, "transcripts": 0})
            empty["coverage"]["artifacts"]["embedding"] = embedding(ready=0, passages=0)
            empty["coverage"]["artifacts"]["indexing"]["ready"] = 0
            page["project_coverage"].append(empty)
        add_ranking(page, "search", "paraphrase")
        page["semantic"] = semantic_disposition("completed", scope="full_scope")
        page["total_kind"] = "ranked_candidates"
        page["facet_total_kinds"]["artifacts"] = "ranked_candidates"
        page["facet_score_types"]["artifacts"] = "semantic_reciprocal_rank"
    page.update(search_disclosure(selection, "paraphrase", fulltext=True,
                                 artifacts=ArtifactAppliedFilters(semantic=True)).model_dump(mode="json"))
    return page


def args(tool, projects=False):
    result = {"q": "paraphrase", "fulltext": True}
    if tool == "search":
        result.update(facets=["artifacts"], filters={"artifacts": {"semantic": True}})
    else:
        result["semantic"] = True
    if projects:
        result.update(project_id=None, project_ids=[OTHER_PROJECT, PROJECT_ID])
    return result


@pytest.mark.parametrize("tool", ["search_artifact_contents", "search"])
async def test_semantic_evidence_is_not_lexical_and_negative_cosine_is_not_probability(settings, work_summary, tool):
    page = semantic_page(tool, work_summary)
    actual, requests = await native_call(settings, tool, args(tool), page)
    assert actual == page
    assert actual["query_interpretation"]["artifacts"]["fields"] == ["content"]
    body = json.loads(requests[0].content)
    assert (body["filters"]["artifacts"] if tool == "search" else body)["semantic"] is True
    assert requests[0].extensions["timeout"]["read"] == 60.0


@pytest.mark.parametrize("mutation", ["revision", "identity", "span", "tokens", "probability", "boolean", "cosine", "model", "snippet", "sensitive", "lexical"])
async def test_semantic_passage_inconsistencies_never_cross_the_agent_boundary(settings, work_summary, mutation):
    page = semantic_page("search", work_summary)
    item = page["items"][0]["artifact"]
    passage_changes = {
        "revision": {"artifact_revision": 2}, "identity": {"passage_id": "c" * 64},
        "span": {"end_offset": 1}, "tokens": {"token_count": 513},
        "probability": {"score_is_probability": True}, "boolean": {"score_is_probability": 0},
        "cosine": {"cosine_similarity": 1.01}, "model": {"model": "other-model"},
    }
    if mutation in passage_changes:
        item["passage"].update(passage_changes[mutation])
    elif mutation == "snippet": item["snippet"] = None
    elif mutation == "sensitive": item["artifact"]["sensitive"] = True
    else: item["evidence"] = "lexical"
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "search", args("search"), page)


async def test_semantic_coverage_is_bound_to_each_project_and_summed(settings, work_summary):
    page = semantic_page("search", work_summary, projects=True)
    actual, requests = await native_call(settings, "search", args("search", True), page)
    assert actual == page and requests[0].url.path == "/api/v1/search"
    for mutation in ("owner", "count", "model", "omitted"):
        wrong = copy.deepcopy(page)
        owners = wrong["project_coverage"]
        if mutation == "owner":
            owners[0]["coverage"]["artifacts"]["embedding"], owners[1]["coverage"]["artifacts"]["embedding"] = owners[1]["coverage"]["artifacts"]["embedding"], owners[0]["coverage"]["artifacts"]["embedding"]
        elif mutation == "count": owners[0]["coverage"]["artifacts"]["embedding"]["passages"] += 1
        elif mutation == "model": owners[1]["coverage"]["artifacts"]["embedding"]["model"] = "other-model"
        else: owners[1]["coverage"]["artifacts"]["embedding"] = None
        with pytest.raises(ToolError, match="unexpected response"):
            await native_call(settings, "search", args("search", True), wrong)


@pytest.mark.parametrize("field", ["pending", "processing", "failed", "unavailable", "withheld", "truncated"])
async def test_incomplete_embedding_coverage_is_distinct_from_missing_vector_generations(settings, work_summary, field):
    page = semantic_page("search_artifact_contents", work_summary)
    page["embedding"].update({field: 1, "state": "incomplete"})
    partial = field in {"pending", "processing", "failed", "unavailable"}
    page["semantic"] = semantic_disposition("completed", scope="full_scope", partial=partial)
    if field == "withheld":
        page["sensitive_content_withheld"] = 1
        page["warnings"] = [{
            "code": "sensitive_content_withheld", "sources": ["artifacts"],
            "message": "Sensitive artifact contents were withheld; zero matches do not establish absence.",
        }]
    assert (await native_call(settings, "search_artifact_contents", args("search_artifact_contents"), page))[0] == page
    page["embedding"]["state"] = "ready"
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "search_artifact_contents", args("search_artifact_contents"), page)


@pytest.mark.parametrize("tool", ["search", "search_artifact_contents"])
@pytest.mark.parametrize("patch,code", [({"fulltext": False}, "artifact_semantic_requires_fulltext"),
    ({"query_mode": "phrase"}, "semantic_requires_unconstrained_artifact_query"),
    ({"q": '"PRIVATE-QUERY"'}, "semantic_requires_unconstrained_artifact_query")])
async def test_semantic_artifact_rules_are_local_and_value_free(settings, tool, patch, code):
    with pytest.raises(ToolError, match=code) as caught:
        await native_call(settings, tool, {**args(tool), **patch}, {})
    assert "PRIVATE-QUERY" not in str(caught.value)


async def test_passage_text_read_pins_extracted_hash_separately_from_original_bytes(settings):
    page = text_page(text_sha256=TEXT_HASH)
    actual, requests = await native_call(settings, "get_artifact_text", {
        "artifact_id": page["artifact_id"], "expected_revision": page["revision"],
        "expected_text_sha256": TEXT_HASH, "limit": 3,
    }, page)
    assert actual["sha256"] != actual["text_sha256"]
    assert requests[0].url.params["expected_text_sha256"] == TEXT_HASH
    page["text_sha256"] = "c" * 64
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "get_artifact_text", {"artifact_id": page["artifact_id"],
            "expected_revision": page["revision"], "expected_text_sha256": TEXT_HASH, "limit": 3}, page)


@pytest.mark.parametrize("tool", ["search", "search_artifact_contents"])
@pytest.mark.parametrize("pending", [False, True])
async def test_empty_semantic_pages_keep_ranked_totals_and_coverage(settings, work_summary, tool, pending):
    page = semantic_page(tool, work_summary)
    page.update(items=[], total=0)
    coverage = embedding(ready=0, passages=0, pending=int(pending), empty=int(not pending),
                         state="incomplete" if pending else "ready")
    page["semantic"] = semantic_disposition("completed", scope="full_scope", partial=pending)
    if tool == "search":
        page["facet_totals"]["artifacts"] = 0
        page["coverage"]["artifacts"]["embedding"] = coverage
        page["indexing_incomplete"] = pending
        owner = page["project_coverage"][0]
        owner.update(facet_totals=copy.deepcopy(page["facet_totals"]),
                     coverage=copy.deepcopy(page["coverage"]), indexing_incomplete=pending)
    else:
        page["embedding"] = coverage
    assert (await native_call(settings, tool, args(tool), page))[0] == page
    coverage["passages"] = 1
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, tool, args(tool), page)


async def test_disabled_artifact_semantics_do_not_claim_an_inference(settings, work_summary):
    page = semantic_page("search", work_summary)
    page.update(items=[], total=0, total_kind="lexical_matches", indexing_incomplete=True)
    page.pop("semantic")
    page["search_scope"]["searched_facets"] = []
    page["facet_totals"]["artifacts"] = 0
    page["facet_total_kinds"]["artifacts"] = None
    page["facet_score_types"]["artifacts"] = None
    page["coverage"]["artifacts"].update(enabled=False, embedding=None)
    page["coverage"]["artifacts"]["indexing"]["ready"] = 0
    page["project_coverage"][0].update(facet_totals=copy.deepcopy(page["facet_totals"]),
        coverage=copy.deepcopy(page["coverage"]), indexing_incomplete=True)
    page.update(search_disclosure(UUID(PROJECT_ID), "paraphrase", fulltext=True).model_dump(mode="json"))
    assert (await native_call(settings, "search", args("search"), page))[0] == page
    page["semantic"] = semantic_disposition("completed", scope="full_scope")
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "search", args("search"), page)


@pytest.mark.parametrize("tool", ["search", "search_artifact_contents"])
async def test_full_semantic_results_keep_the_passage_after_summary_projection(settings, work_summary, tool):
    from test_artifacts import artifact

    page = semantic_page(tool, work_summary)
    page["detail"] = "full"
    match = page["items"][0]["artifact"] if tool == "search" else page["items"][0]
    full = artifact()
    full["extraction"].update(status="ready", error_code=None)
    match["artifact"] = full
    actual, _ = await native_call(settings, tool, {**args(tool), "detail": "full"}, page)
    output = actual["items"][0]["artifact"] if tool == "search" else actual["items"][0]
    assert output["passage"] == match["passage"] and output["evidence"] == "semantic"
    assert "description" not in output["artifact"]


def test_semantic_artifact_dtos_match_published_schema():
    from pathlib import Path

    from mnemonic_mcp.artifact_semantic import ArtifactEmbeddingCoverage, ArtifactPassageEvidence
    from mnemonic_mcp.artifact_text_tools import ArtifactTextPage

    components = json.loads((Path(__file__).resolve().parents[2] / "docs/openapi.json").read_text())["components"]["schemas"]
    for model, name in ((ArtifactEmbeddingCoverage, "ArtifactEmbeddingCoverage"),
                        (ArtifactPassageEvidence, "ArtifactPassageEvidence"),
                        (ArtifactTextPage, "ArtifactTextRead")):
        actual, expected = model.model_json_schema(), components[name]
        assert set(actual["properties"]) == set(expected["properties"]), name
        assert set(actual.get("required", [])) == set(expected.get("required", [])), name
