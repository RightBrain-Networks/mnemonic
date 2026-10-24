"""Atomic per-episode dispositions retain exact versioning and Done obligations."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text

from mnemonic_api.impact_integrity import IMPACT_FINDINGS

from .test_decision_impact_postgres import assessments, changed, decision, dependency
from .test_decision_reviews_postgres import context, finish, reviews, settings
from .test_knowledge_postgres import domain, path
from .test_leases_postgres import create_work

pytestmark = pytest.mark.postgres
ACTOR = {"actor_client": "codex", "actor_session_id": "batch-investigation"}


def episode(api, project, count=3, automatic=False):
    if automatic:
        settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = decision(api, project, target, "Original governing rule")
    candidates = [first]
    for index in range(count - 1):
        candidates.append(decision(api, project, target, f"Consumer {index}",
                                   depends_on=[dependency(first)]))
    changed(api, project, target, first)
    return assessments(api, project)[0], candidates


def batch_body(candidates, **changes):
    return {
        "expected_version": 0, "actor": ACTOR, "client_operation_id": str(uuid4()),
        "items": [{"candidate_reference": item["reference"], "disposition": "still-valid",
                   "reason": f"Inspected implementation of {item['reference']}.",
                   "remediation_work_item_id": None} for item in candidates], **changes,
    }


def endpoint(project, assessment):
    return path(project) + f"/assessments/{assessment['id']}"


def audit_dispositions(engine):
    with engine.connect() as connection:
        findings = {name: connection.scalar(text(sql)) for name, sql in IMPACT_FINDINGS.items()}
    assert not any(findings.values()), findings


@pytest.mark.parametrize("count", [1, 3, 100])
def test_batch_commits_ordered_consecutive_versions_and_exact_replay(api, project, postgres_engine,
                                                                   count):
    assessment, candidates = episode(api, project, count)
    body = batch_body(list(reversed(candidates)))
    url = endpoint(project, assessment) + "/dispositions/batch"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: api.post(url, json=body), range(2)))
    assert [response.status_code for response in responses] == [201, 201]
    result = responses[0].json()
    assert responses[1].json() == result
    assert result["version"] == count
    assert [item["version"] for item in result["items"]] == list(range(1, count + 1))
    assert [item["candidate_reference"] for item in result["items"]] == [
        item["candidate_reference"] for item in body["items"]
    ]
    assert all(item["actor"] == {**ACTOR, "actor_model": None} for item in result["items"])
    detail = api.get(endpoint(project, assessment)).json()
    assert detail["version"] == count and detail["unassessed_count"] == 0
    assert detail["status"] == "resolved"
    assert api.post(url, json={**body, "client_operation_id": str(uuid4())}).status_code == 409
    assert api.post(url, json={**body, "expected_version": count}).status_code == 409
    audit_dispositions(postgres_engine)


@pytest.mark.parametrize("invalid", ["candidate", "foreign-remediation", "duplicate", "reason",
                                    "empty", "oversized"])
def test_invalid_batch_rolls_back_every_disposition_and_receipt(api, project, postgres_engine,
                                                               work_payload, invalid):
    assessment, candidates = episode(api, project)
    body = batch_body(candidates)
    if invalid == "candidate":
        body["items"][-1]["candidate_reference"] = "ADR-99999-0"
    elif invalid == "foreign-remediation":
        other = api.post("/api/v1/projects", json={"name": "Foreign remediation"}).json()
        work = create_work(api, other, work_payload)["work_item"]
        body["items"][-1]["remediation_work_item_id"] = work["id"]
    elif invalid == "duplicate":
        body["items"][-1]["candidate_reference"] = body["items"][0]["candidate_reference"]
    elif invalid == "reason":
        body["items"][-1]["reason"] = " "
    elif invalid == "empty":
        body["items"] = []
    else:
        body["items"] *= 34
    result = api.post(endpoint(project, assessment) + "/dispositions/batch", json=body)
    assert result.status_code == (404 if invalid == "foreign-remediation" else 422), result.text
    assert api.get(endpoint(project, assessment)).json()["version"] == 0
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM client_operations "
                                      "WHERE client_operation_id=:id"),
                                 {"id": body["client_operation_id"]}) == 0
    audit_dispositions(postgres_engine)


def test_batches_and_singletons_share_versions_and_unchanged_done_requirements(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    assessment, candidates = episode(api, project, automatic=True)
    driver, = reviews(api, project)
    work = context(api, project, driver["work_item_id"])["work_item"]
    url = endpoint(project, assessment)
    body = batch_body(candidates[:2])
    body["items"][0]["disposition"] = "change-required"
    saved = api.post(url + "/dispositions/batch", json=body)
    assert saved.status_code == 201, saved.text
    assert api.get(url).json()["unassessed_count"] == 1
    refused = finish(api, project, work, checkpoint_fields)
    assert refused.status_code == 409
    one = {**batch_body(candidates[2:])["items"][0], "expected_version": 2,
           "actor": ACTOR, "client_operation_id": str(uuid4())}
    assert api.post(url + "/dispositions", json=one).status_code == 201
    assert api.get(url).json()["unassessed_count"] == 0
    refused = finish(api, project, work, checkpoint_fields)
    assert refused.status_code == 409 and "remediation" in refused.text
    remediation = create_work(api, project, work_payload)["work_item"]
    final = batch_body(candidates[:1], expected_version=3)
    final["items"][0].update(disposition="change-required",
                              remediation_work_item_id=remediation["id"])
    assert api.post(url + "/dispositions/batch", json=final).status_code == 201
    done = finish(api, project, work, checkpoint_fields)
    assert done.status_code == 200, done.text
    assert api.get(url).json()["change_required_count"] == 1
    audit_dispositions(postgres_engine)


def test_batch_is_not_exposed_as_a_browser_mutation(api, project):
    assessment, candidates = episode(api, project, 1)
    url = (endpoint(project, assessment) + "/dispositions/batch").replace(
        "/api/v1/", "/api/mnemonic/",
    )
    response = api.post(url, json=batch_body(candidates),
                        headers={"host": "localhost:3000", "origin": "http://localhost:3000"})
    assert response.status_code in {403, 404, 405}, response.text
    assert api.get(endpoint(project, assessment)).json()["version"] == 0
