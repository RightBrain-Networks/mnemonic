"""Decision discovery preserves domain scope, historical revisions, and exact evidence pins."""

import copy
import json
from uuid import UUID

import pytest
from conftest import NOW, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from search_ranking_fixtures import add_ranking
from test_compact_search import native_call
from test_knowledge_tools import DOMAIN_ID, decision
from test_unified_search import page

from mnemonic_mcp.search_disclosure import DecisionAppliedFilters, search_disclosure
from mnemonic_mcp.search_models import DecisionSearchMatch


def decision_page(*, fulltext=False, all_revisions=False):
    source = {key: value for key, value in decision().items()
              if key in DecisionSearchMatch.model_fields}
    source.update(id="27a113cd-6bf4-47a3-a692-f75c3aa8cdee", rank=1, score=1.0,
                  score_type="postgresql_lexical", snippet="Use café queues." if fulltext else None,
                  matched_fields=["content"] if fulltext else ["metadata"])
    hit = {"facet": "decisions", "id": source["id"], "project_id": PROJECT_ID,
           "created_at": NOW, "updated_at": NOW, "score": 1 / 61, "decision": source}
    result = page([hit], detail="compact", limit=20, work_rank_scope="work_items", tag_counts=None)
    result["search_scope"].update(searched_facets=["decisions"], transcripts="not_selected")
    result["project_coverage"] = [{
        "project_id": PROJECT_ID, "project_name": "Example", "project_slug": "example",
        "facet_totals": result["facet_totals"], "coverage": result["coverage"],
        "indexing_incomplete": False,
    }]
    result.update(search_disclosure(
        UUID(PROJECT_ID), "queues", fulltext=fulltext,
        decisions=DecisionAppliedFilters(domain_ids=[UUID(DOMAIN_ID)],
                                         all_revisions=all_revisions, state="accepted"),
    ).model_dump(mode="json"))
    # The API uses PostgreSQL for decisions; do not derive this wire value from
    # the MCP helper whose contract the round-trip test is checking.
    result["query_interpretation"]["decisions"]["match_mode"] = (
        "postgresql_plain_terms_or_substring"
    )
    return add_ranking(result, "search", "queues")


@pytest.mark.parametrize("fulltext", [False, True])
@pytest.mark.parametrize("all_revisions", [False, True])
async def test_decision_search_filters_and_revision_pins_survive_round_trip(
    settings, fulltext, all_revisions,
):
    response = decision_page(fulltext=fulltext, all_revisions=all_revisions)
    filters = {"domain_ids": [DOMAIN_ID], "all_revisions": all_revisions, "state": "accepted"}
    actual, requests = await native_call(settings, "search", {
        "q": "queues", "facets": ["decisions"], "fulltext": fulltext,
        "filters": {"decisions": filters},
    }, response)
    assert actual == response
    assert json.loads(requests[0].content)["filters"]["decisions"] == filters
    assert actual["items"][0]["decision"]["reference"] == "ADR-00001-0"
    assert "content" not in actual["items"][0]["decision"]


@pytest.mark.parametrize("changes", [
    {"domain_id": WORK_ID}, {"reference": "ADR-00001-7"}, {"state": "rejected"},
    {"project_id": WORK_ID}, {"snippet": "private content", "matched_fields": ["content"]},
])
async def test_decision_search_rejects_wrong_domain_revision_or_content_scope(settings, changes):
    response = decision_page()
    response["items"][0]["decision"].update(changes)
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "search", {
            "q": "queues", "facets": ["decisions"],
            "filters": {"decisions": {"domain_ids": [DOMAIN_ID], "state": "accepted"}},
        }, response)


async def test_historical_decision_revisions_have_distinct_discovery_identities(settings):
    response = decision_page(all_revisions=True)
    second = copy.deepcopy(response["items"][0])
    second.update(id="610787f8-8bfe-44e9-ad01-ec3d07887f40", rank=2, score=1 / 62)
    second["decision"].update(id=second["id"], revision=1, reference="ADR-00001-1", rank=2)
    response["items"].append(second)
    response["total"] = response["facet_totals"]["decisions"] = 2
    response["project_coverage"][0]["facet_totals"]["decisions"] = 2
    actual, _ = await native_call(settings, "search", {
        "q": "queues", "facets": ["decisions"],
        "filters": {"decisions": {"domain_ids": [DOMAIN_ID], "state": "accepted",
                                   "all_revisions": True}},
    }, response)
    assert [hit["decision"]["revision"] for hit in actual["items"]] == [0, 1]
