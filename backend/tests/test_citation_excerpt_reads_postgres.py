"""Read-time citation evidence pins historical text without changing review obligations."""

import pytest
from sqlalchemy import text

from .test_decision_impact_postgres import assessments, changed, decision, impact
from .test_knowledge_postgres import domain, path

pytestmark = pytest.mark.postgres


def test_excerpts_pin_captured_revision_without_mutating_snapshot(api, project, postgres_engine):
    target = domain(api, project)
    origin = decision(api, project, target, "Foundational rule")
    citation = f"Related (2026-10-23): Superseded in part by {origin['decision_id']}."
    citing = decision(api, project, target, citation,
                      related_decision_ids=[origin["decision_id"]])
    changed(api, project, target, origin)
    episode = next(item for item in assessments(api, project)
                   if item["before_reference"] == origin["reference"])
    changed(api, project, target, citing, "The later text does not repeat the citation.")
    endpoint = path(project) + f"/assessments/{episode['id']}"
    with postgres_engine.connect() as connection:
        before = connection.execute(text(
            "SELECT candidates FROM decision_impact_assessments WHERE id=:id"
        ), {"id": episode["id"]}).scalar_one()
    default = api.get(endpoint).json()
    assert all("citation_excerpts" not in item for item in default["candidates"])
    enriched = api.get(endpoint, params={"include_citation_excerpts": True})
    assert enriched.status_code == 200, enriched.text
    result = enriched.json()
    evidence = next(item["citation_excerpts"] for item in result["candidates"]
                    if item["reference"] == citing["reference"])
    assert evidence == {"source_sha256": citing["sha256"], "excerpts": [citation],
                        "truncated": False}
    assert result["unassessed_count"] == default["unassessed_count"] == 2
    assert result["version"] == 0 and result["dispositions"] == []
    with postgres_engine.connect() as connection:
        assert connection.execute(text(
            "SELECT candidates FROM decision_impact_assessments WHERE id=:id"
        ), {"id": episode["id"]}).scalar_one() == before


def test_live_excerpts_are_optional_and_follow_candidate_pagination(api, project):
    target = domain(api, project)
    origin = decision(api, project, target, "Root")
    citing = decision(api, project, target, "Text without an identifier.",
                      related_decision_ids=[origin["decision_id"]])
    default = impact(api, project, origin["reference"], limit=1, offset=1)
    assert "citation_excerpts" not in default["items"][0]
    enriched = impact(api, project, origin["reference"], limit=1, offset=1,
                      include_citation_excerpts=True)
    assert enriched["items"][0]["reference"] == citing["reference"]
    assert enriched["items"][0]["citation_excerpts"] == {
        "source_sha256": citing["sha256"], "excerpts": [], "truncated": False,
    }
    assert enriched["candidate_count"] == 2
    assert enriched["items"][0]["related_direction"] == "incoming"
