"""Named findings summarize the latest ledger without replacing any obligation."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.test_decision_changes_postgres import group
from tests.test_decision_impact_postgres import changed, decision
from tests.test_decision_reviews_postgres import context, reviews, settings
from tests.test_knowledge_postgres import domain, path
from tests.test_work_items_postgres import create_work

pytestmark = pytest.mark.postgres


def review(api, project, work_payload, *, citations=0, revisions=2):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = decision(api, project, target, "Before investigation")
    for index in range(citations):
        decision(api, project, target, f"Incoming citation {index}",
                 related_decision_ids=[first["decision_id"]])
    current = changed(api, project, target, first)
    driver, = reviews(api, project)
    work = context(api, project, driver["work_item_id"])["work_item"]
    change_id = current["change"]["change_id"]
    for index in range(1, revisions):
        current = changed(api, project, target, current, f"Next ruling {index}",
                          source_work_item_id=work["id"])
    remediation = create_work(api, project, {
        **work_payload, "discovered_from_work_item_id": work["id"],
    }, title="Correct the evidenced finding")["work_item"]
    listed = api.get(path(project) + "/assessments", params={"change_id": change_id}).json()
    episodes = [detail(api, project, item["id"]) for item in listed["items"]]
    return change_id, episodes, remediation


def detail(api, project, assessment_id):
    response = api.get(path(project) + f"/assessments/{assessment_id}")
    assert response.status_code == 200, response.text
    return response.json()


def append(api, project, episode, remediation, **changes):
    current = detail(api, project, episode["id"])
    body = {
        "client_operation_id": str(uuid4()), "expected_version": current["version"],
        "candidate_reference": episode["candidates"][0]["reference"],
        "disposition": "change-required", "reason": "The pinned wording contradicts the code.",
        "remediation_work_item_id": remediation["id"],
        "actor": {"actor_client": "codex", "actor_session_id": "finding-review"},
        **changes,
    }
    response = api.post(path(project) + f"/assessments/{episode['id']}/dispositions", json=body)
    assert response.status_code == 201, response.text
    return response, body


def summary(named, unlabelled, rows):
    return {"named_finding_count": named, "unlabelled_change_required_count": unlabelled,
            "change_required_row_count": rows}


def test_one_finding_across_nineteen_rows_and_three_episodes_stays_one(
    api, project, work_payload,
):
    change_id, episodes, remediation = review(api, project, work_payload, citations=6, revisions=3)
    assert len(episodes) == 3
    count = 0
    for episode in episodes:
        for candidate in episode["candidates"]:
            append(api, project, episode, remediation, candidate_reference=candidate["reference"],
                   **({"finding_id": "wording:transport", "remediation_target": "decision-record"}
                      if count < 19 else {}))
            count += 1
    assert count == 21
    assert group(api, project, change_id)["finding_summary"] == summary(1, 2, 21)
    results = [detail(api, project, episode["id"]) for episode in episodes]
    assert sum(item["change_required_count"] for item in results) == 21
    assert sum(item["unassessed_count"] for item in results) == 0
    assert all(item["finding_summary"]["named_finding_count"] == 1 for item in results)
    assert all(item["finding_summary"]["change_required_row_count"] == 7 for item in results)


def test_latest_corrections_change_summary_but_preserve_history_and_exact_replay(
    api, project, work_payload,
):
    change_id, episodes, remediation = review(api, project, work_payload)
    original, intent = append(api, project, episodes[0], remediation,
                              finding_id="same-cause", remediation_target="both")
    append(api, project, episodes[1], remediation, finding_id="same-cause")
    assert group(api, project, change_id)["finding_summary"] == summary(1, 0, 2)
    append(api, project, episodes[0], remediation, disposition="resolved", finding_id="same-cause")
    assert group(api, project, change_id)["finding_summary"] == summary(1, 0, 1)
    append(api, project, episodes[1], remediation, finding_id="corrected-cause",
           remediation_target="code")
    assert group(api, project, change_id)["finding_summary"] == summary(1, 0, 1)
    endpoint = path(project) + f"/assessments/{episodes[0]['id']}/dispositions"
    replay = api.post(endpoint, json=intent)
    assert replay.status_code == 201 and replay.json() == original.json()
    conflict = api.post(endpoint, json={**intent, "finding_id": "another-cause"})
    assert conflict.status_code == 409
    history = api.get(endpoint).json()["items"]
    assert {row["version"]: row["disposition"] for row in history} == {
        1: "change-required", 2: "resolved",
    }
    assert next(row for row in history if row["version"] == 1) == original.json()
    append(api, project, episodes[1], remediation)
    assert group(api, project, change_id)["finding_summary"] == summary(0, 1, 1)


@pytest.mark.parametrize("changes", [
    {"finding_id": "invalid label"}, {"remediation_target": "documentation"},
    {"disposition": "resolved", "remediation_target": "code"},
    {"expected_version": 19}, {"candidate_reference": "ADR-99999-0"},
])
def test_invalid_finding_write_cannot_append_or_change_summary(
    api, project, work_payload, postgres_engine, changes,
):
    change_id, episodes, remediation = review(api, project, work_payload, revisions=1)
    _, intent = append(api, project, episodes[0], remediation, finding_id="one-cause")
    before = detail(api, project, episodes[0]["id"])
    endpoint = path(project) + f"/assessments/{episodes[0]['id']}/dispositions"
    response = api.post(endpoint, json={**intent, "client_operation_id": str(uuid4()),
                                       "expected_version": 1, **changes})
    assert response.status_code in (409, 422), response.text
    after = detail(api, project, episodes[0]["id"])
    assert (after["version"], after["dispositions"]) == (before["version"], before["dispositions"])
    assert group(api, project, change_id)["finding_summary"] == summary(1, 0, 1)
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM decision_impact_dispositions")) == 1
