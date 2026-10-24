"""Grouped navigation retains every separate required episode occurrence and its snapshot."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import text

from mnemonic_api.services import decision_candidates

from .test_decision_change_storage_postgres import _change
from .test_decision_changes_postgres import update
from .test_decision_impact_postgres import assessments, changed, decision, dependency
from .test_decision_reviews_postgres import assess, context
from .test_knowledge_postgres import domain, path
from .test_leases_postgres import create_work

pytestmark = pytest.mark.postgres


def supersede(api, project, target, old, label):
    replacement = decision(api, project, target, label, supersedes=[old["decision_id"]])
    return next(row for row in assessments(api, project)
                if row["after_reference"] == replacement["reference"])


def adopt(api, project, work_payload, episodes):
    work = create_work(api, project, work_payload)["work_item"]
    result, _ = update(api, project, "adopt", work_item_id=work["id"],
                       expected_work_version=work["version"],
                       assessment_ids=[row["id"] for row in episodes])
    assert result.status_code == 200, result.text
    return work, result.json()


def url(project, change):
    return path(project) + f"/changes/{change['id']}/candidates"


def fixture(api, project, work_payload):
    target = domain(api, project)
    prerequisite = decision(api, project, target, "Prerequisite")
    outward = decision(api, project, target, "Outward context")
    origin = decision(api, project, target, "Origin", depends_on=[dependency(prerequisite)],
                      related_decision_ids=[outward["decision_id"]])
    child = decision(api, project, target, "Dependent", depends_on=[dependency(origin)])
    incoming = decision(api, project, target, "Incoming association",
                        related_decision_ids=[origin["decision_id"]])
    episodes = [supersede(api, project, target, origin, label) for label in ("First", "Second")]
    work, change = adopt(api, project, work_payload, episodes)
    return target, origin, child, incoming, episodes, work, change


def test_grouped_candidates_retain_per_episode_latest_results_and_exclude_context(
    api, project, work_payload, postgres_engine,
):
    _, origin, child, incoming, episodes, _, change = fixture(api, project, work_payload)
    assess(api, project, episodes[0]["id"], origin["reference"])
    assess(api, project, episodes[0]["id"], origin["reference"], disposition="resolved")
    assess(api, project, episodes[1]["id"], child["reference"], disposition="change-required")
    with postgres_engine.connect() as connection:
        operations = connection.scalar(text("SELECT count(*) FROM client_operations"))
    response = api.get(url(project, change))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["scope_version"] == result["assessment_count"] == 2
    assert tuple(result[key] for key in (
        "decision_count", "revision_count", "occurrence_count",
    )) == (3, 3, 6)
    assert result["unassessed_count"] == 4 and result["change_required_count"] == 1
    groups = {row["decision_id"]: row for row in result["items"]}
    assert set(groups) == {row["decision_id"] for row in (origin, child, incoming)}
    assert all(row["occurrence_count"] == 2 and row["revision_count"] == 1
               and row["references_complete"] for row in groups.values())
    occurrences = groups[origin["decision_id"]]["occurrences"]
    by_episode = {row["assessment_id"]: row for row in occurrences}
    judged = by_episode[episodes[0]["id"]]
    assert judged["assessment_version"] == judged["latest_disposition"]["version"] == 2
    assert judged["latest_disposition"]["disposition"] == "resolved"
    assert set(judged["latest_disposition"]) == {
        "id", "version", "disposition", "remediation_work_item_id",
    }
    assert by_episode[episodes[1]["id"]]["latest_disposition"] is None
    assert all(row["relationship"] == "related-only" and row["related_direction"] == "incoming"
               for row in groups[incoming["decision_id"]]["occurrences"])
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM client_operations")) == operations


def test_group_and_occurrence_pagination_reaches_every_obligation(api, project, work_payload):
    _, _, _, _, episodes, _, change = fixture(api, project, work_payload)
    group_offset, seen = 0, set()
    while True:
        page = api.get(url(project, change), params={"limit": 1, "offset": group_offset,
                                                     "occurrence_limit": 1}).json()
        assert page["total"] == page["decision_count"] == 3
        group, = page["items"]
        assert len(group["occurrences"]) == 1 and group["next_occurrence_offset"] == 1
        rest = api.get(url(project, change), params={
            "decision_id": group["decision_id"], "limit": 1,
            "occurrence_limit": 1, "occurrence_offset": group["next_occurrence_offset"]}).json()
        assert rest["total"] == 1 and rest["decision_count"] == 3
        assert rest["items"][0]["next_occurrence_offset"] is None
        seen.update((row["assessment_id"], row["candidate_reference"])
                    for row in [*group["occurrences"], *rest["items"][0]["occurrences"]])
        if page["next_offset"] is None:
            break
        group_offset = page["next_offset"]
    assert len(seen) == 6 and {row[0] for row in seen} == {row["id"] for row in episodes}
    absent = api.get(url(project, change), params={"decision_id": "ADR-99999"}).json()
    assert absent["total"] == 0 and absent["items"] == [] and absent["decision_count"] == 3


def test_references_are_explicitly_partial_when_paged_occurrences_omit_a_revision(
    api, project, work_payload,
):
    target = domain(api, project)
    first = decision(api, project, target, "First ruling")
    before = supersede(api, project, target, first, "First replacement")
    second = changed(api, project, target, first)
    after = supersede(api, project, target, second, "Second replacement")
    _, change = adopt(api, project, work_payload, [before, after])
    for offset, reference in enumerate([first["reference"], second["reference"]]):
        page = api.get(url(project, change), params={"occurrence_limit": 1,
                                                     "occurrence_offset": offset}).json()
        group, = page["items"]
        assert group["captured_references"] == [reference]
        assert group["revision_count"] == page["revision_count"] == 2
        assert not group["references_complete"]
    complete = api.get(url(project, change)).json()["items"][0]
    assert complete["references_complete"] and complete["captured_references"] == [
        first["reference"], second["reference"],
    ]


def test_counts_distinguish_fifty_documents_seventy_one_revisions_and_191_occurrences(
    api, project, work_payload,
):
    target = domain(api, project)
    origin = decision(api, project, target, "Root rule")
    nested = decision(api, project, target, "Nested rule", depends_on=[dependency(origin)])
    for index in range(27):
        decision(api, project, target, f"Nested consumer {index}",
                 depends_on=[dependency(nested)])
    direct = [decision(api, project, target, f"Direct consumer {index}",
                       depends_on=[dependency(origin)]) for index in range(21)]
    episodes = [supersede(api, project, target, origin, label)
                for label in ("First full capture", "Second full capture")]
    revised = changed(api, project, target, origin, "Revised root rule")
    for consumer in direct[:20]:
        changed(api, project, target, consumer, "Updated direct consumer",
                depends_on=[dependency(revised)])
    episodes.extend(supersede(api, project, target, revised, label)
                    for label in ("First revised capture", "Second revised capture",
                                  "Third revised capture"))
    episodes.append(supersede(api, project, target, nested, "Nested capture"))
    _, change = adopt(api, project, work_payload, episodes)
    result = api.get(url(project, change)).json()
    assert result["scope_version"] == result["assessment_count"] == 6
    assert result["total"] == result["decision_count"] == 50
    assert result["revision_count"] == 71
    assert result["occurrence_count"] == result["unassessed_count"] == 191
    assert len(result["items"]) == 20 and result["next_offset"] == 20
    groups = api.get(url(project, change), params={"limit": 100, "occurrence_limit": 2}).json()
    assert len(groups["items"]) == 50 and groups["next_offset"] is None
    assert sum(group["revision_count"] for group in groups["items"]) == 71
    assert sum(group["occurrence_count"] for group in groups["items"]) == 191
    assert {count: sum(group["occurrence_count"] == count for group in groups["items"])
            for count in (2, 3, 5)} == {2: 1, 3: 28, 5: 21}


def test_composite_grouped_read_remains_coherent_during_disposition_and_scope_growth(
    api, project, work_payload, monkeypatch,
):
    target, origin, _, _, episodes, work, change = fixture(api, project, work_payload)
    extra = supersede(api, project, target, origin, "Later supersession")
    captured, release = Event(), Event()
    original = decision_candidates._groups

    def pause(database, parameters):
        captured.set()
        assert release.wait(10)
        return original(database, parameters)

    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as pool:
        patch.setattr(decision_candidates, "_groups", pause)
        future = pool.submit(api.get, url(project, change))
        assert captured.wait(10)
        try:
            assess(api, project, episodes[0]["id"], origin["reference"])
            current = context(api, project, work["id"])["work_item"]
            result, _ = update(api, project, "adopt", work_item_id=work["id"],
                expected_work_version=current["version"], assessment_ids=[extra["id"]])
            assert result.status_code == 200, result.text
        finally:
            release.set()
        response = future.result(10)
    assert response.status_code == 200, response.text
    old = response.json()
    assert old["scope_version"] == 2 and old["occurrence_count"] == old["unassessed_count"] == 6
    assert all(row["latest_disposition"] is None for group in old["items"]
               for row in group["occurrences"])
    new = api.get(url(project, change)).json()
    assert new["scope_version"] == 3 and new["occurrence_count"] == 9
    assert new["unassessed_count"] == 8


def test_empty_unknown_and_oversized_group_queries_are_explicit(api, project, postgres_engine):
    change = {"id": str(_change(postgres_engine, project))}
    result = api.get(url(project, change))
    assert result.status_code == 200
    assert result.json()["decision_count"] == result.json()["occurrence_count"] == 0
    assert result.json()["items"] == [] and result.json()["next_offset"] is None
    assert api.get(url(project, {"id": str(uuid4())})).status_code == 404
    assert api.get(url({"id": str(uuid4())}, change)).status_code == 404
    for params in ({"limit": 0}, {"limit": 101}, {"occurrence_limit": 0},
                   {"limit": 21, "occurrence_limit": 10}, {"occurrence_offset": -1}):
        assert api.get(url(project, change), params=params).status_code == 422


def test_new_live_citations_do_not_reclassify_captured_grouping(api, project, work_payload):
    target, origin, _, _, _, _, change = fixture(api, project, work_payload)
    decision(api, project, target, "Later incoming reference",
             related_decision_ids=[origin["decision_id"]])
    result = api.get(url(project, change)).json()
    assert result["decision_count"] == 3 and result["occurrence_count"] == 6


def test_historical_related_only_snapshots_without_direction_remain_optional_context(
    api, project, work_payload, postgres_engine,
):
    _, _, _, incoming, _, _, change = fixture(api, project, work_payload)
    with postgres_engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE decision_impact_assessments DISABLE TRIGGER knowledge_immutable"
        ))
        connection.execute(text("""
            UPDATE decision_impact_assessments assessment SET candidates=(
                SELECT jsonb_agg(CASE WHEN candidate->>'reference'=:reference
                    THEN candidate-'related_direction' ELSE candidate END ORDER BY ordinal)
                FROM jsonb_array_elements(assessment.candidates) WITH ORDINALITY
                    AS captures(candidate, ordinal))
        """), {"reference": incoming["reference"]})
        connection.execute(text(
            "ALTER TABLE decision_impact_assessments ENABLE TRIGGER knowledge_immutable"
        ))
    result = api.get(url(project, change)).json()
    assert result["decision_count"] == 2 and result["occurrence_count"] == 4
    assert incoming["decision_id"] not in {item["decision_id"] for item in result["items"]}
