"""Domain policy gates new reviews without discarding existing review obligations."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .test_artifact_extraction_migration_postgres import migrate
from .test_decision_impact_postgres import assessments, changed, decision, dependency
from .test_decision_reviews_postgres import context, reviews
from .test_human_gates_postgres import create_work, gate_path, gate_request, resolution_payload
from .test_knowledge_postgres import domain, path
from .test_project_activity_audit_postgres import _audit

pytestmark = pytest.mark.postgres


def policy(api, project, target, value, **changes):
    result = api.patch(path(project) + f"/domains/{target['id']}", json={
        "automatic_decision_reviews": value, "expected_version": target["version"],
        "client_operation_id": str(uuid4()), **changes,
    })
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize("domain_policy,expected", [("on", True), ("off", False)])
def test_local_domain_policy_gates_reviews_but_retains_assessments(
    api, project, domain_policy, expected,
):
    target = domain(api, project)
    assert target["automatic_decision_reviews"] == "off"
    assert target["decision_review_priority"] == 40
    target = policy(api, project, target, domain_policy, decision_review_priority=37)
    original = decision(api, project, target, "Initial decision")
    changed(api, project, target, original, "Revised decision")
    spawned = reviews(api, project)
    assert len(spawned) == int(expected)
    if expected:
        assert context(api, project, spawned[0]["work_item_id"])["work_item"]["priority"] == 37
    assert len(assessments(api, project)) == 1


def test_domain_policy_writes_are_versioned_receipted_and_project_scoped(api, project):
    target = domain(api, project)
    endpoint = path(project) + f"/domains/{target['id']}"
    intent = {"automatic_decision_reviews": "on", "expected_version": 1,
              "client_operation_id": str(uuid4())}
    saved = api.patch(endpoint, json=intent)
    assert saved.status_code == 200, saved.text
    assert saved.json()["automatic_decision_reviews"] == "on"
    assert saved.json()["name"] == target["name"] and saved.json()["version"] == 2
    assert api.patch(endpoint, json=intent).json() == saved.json()
    stale = api.patch(endpoint, json={**intent, "client_operation_id": str(uuid4())})
    assert stale.status_code == 409
    unchanged = policy(api, project, saved.json(), "on")
    assert unchanged == saved.json()
    renamed = policy(api, project, unchanged, "off", name="Engineering")
    assert renamed["version"] == 3 and renamed["name"] == "Engineering"
    assert renamed["prefix"] == "ADR"
    reset = policy(api, project, renamed, "off", decision_review_priority=32)
    assert reset["version"] == 4 and reset["decision_review_priority"] == 32
    other = api.post("/api/v1/projects", json={"name": "Other domain policies"}).json()
    assert api.patch(path(other) + f"/domains/{target['id']}", json={
        **intent, "client_operation_id": str(uuid4()),
    }).status_code == 404
    assert all(item["automatic_decision_reviews"] == "off"
               for item in api.get(path(other) + "/domains").json()["items"])


def test_create_domain_can_explicitly_enable_reviews(api, project):
    intent = {"name": "Architecture", "prefix": "ADR", "automatic_decision_reviews": "on",
              "client_operation_id": str(uuid4())}
    response = api.post(path(project) + "/domains", json=intent)
    assert response.status_code == 201, response.text
    target = response.json()
    assert target["automatic_decision_reviews"] == "on" and target["version"] == 1
    assert api.post(path(project) + "/domains", json=intent).json() == target
    first = decision(api, project, target, "Original")
    changed(api, project, target, first)
    assert len(reviews(api, project)) == 1


def test_domain_policy_changes_only_future_creation_and_preserves_review_scope(api, project):
    target = policy(api, project, domain(api, project), "off")
    first = decision(api, project, target, "Initial policy")
    second = changed(api, project, target, first, "Before enablement")
    target = policy(api, project, target, "on")
    assert reviews(api, project) == []
    third = changed(api, project, target, second, "After enablement")
    review, = reviews(api, project)
    review_id = review["work_item_id"]
    target = policy(api, project, target, "off")
    changed(api, project, target, third, "Extend existing investigation",
            source_work_item_id=review_id)
    assert [item["work_item_id"] for item in reviews(api, project)] == [review_id]
    change_id = third["change"]["change_id"]
    group = api.get(path(project) + f"/changes/{change_id}").json()
    assert group["scope_version"] == 2
    assert context(api, project, review_id)["work_item"]["status"] == "pending"


@pytest.mark.parametrize("source_policy,target_policy,expected", [
    ("off", "on", False), ("on", "off", True),
])
def test_cross_domain_impact_uses_only_changed_document_domain(
    api, project, source_policy, target_policy, expected,
):
    adr = policy(api, project, domain(api, project), target_policy)
    bdr = policy(api, project, domain(api, project, "BDR", "Business"), source_policy)
    original = decision(api, project, bdr, "Business rule")
    dependent = decision(api, project, adr, "Architecture rule", depends_on=[dependency(original)])
    changed(api, project, bdr, original, "Replacement business rule")
    assert len(reviews(api, project)) == int(expected)
    episode, = assessments(api, project)
    detail = api.get(path(project) + f"/assessments/{episode['id']}").json()
    assert dependent["reference"] in [item["reference"] for item in detail["candidates"]]


@pytest.mark.parametrize("domain_policy,expected", [("off", False), ("on", True)])
def test_pdr_answer_uses_domain_override_and_questions_remain_maintenance(
    api, project, work_payload, domain_policy, expected,
):
    pdr, = api.get(path(project) + "/domains").json()["items"]
    policy(api, project, pdr, domain_policy)
    work = create_work(api, project, work_payload, title="Choose review behavior")
    endpoint = gate_path(project, work)
    request = gate_request()
    original = api.post(endpoint, json=request).json()
    revision = api.post(endpoint, json={
        **request, "gate_id": original["id"], "expected_question_version": 1,
        "question": "Choose the final review behavior?", "client_operation_id": str(uuid4()),
    })
    assert revision.status_code == 201, revision.text
    assert reviews(api, project) == [] and assessments(api, project) == []
    answer = {**resolution_payload(revision=revision.json()["current_context_revision"]),
              "expected_question_version": 2}
    resolved = api.post(endpoint + f"/{original['id']}/resolve", json=answer)
    assert resolved.status_code == 200, resolved.text
    assert len(reviews(api, project)) == int(expected)
    assert len(assessments(api, project)) == 1


@pytest.mark.parametrize("assignment", [
    "automatic_decision_reviews='on'",
    "automatic_decision_reviews='invalid',version=version+1",
    "automatic_decision_reviews=NULL,version=version+1",
    "version=version+1",
    "prefix='OTHER',version=version+1",
])
def test_domain_database_guard_requires_valid_policy_and_exact_version(
    api, project, postgres_engine, assignment,
):
    target = domain(api, project)
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        connection.execute(text(f"UPDATE knowledge_domains SET {assignment} WHERE id=:id"),
                           {"id": target["id"]})


@pytest.mark.parametrize("enabled", [False, True])
def test_migration_preserves_existing_effective_behavior(api, project, postgres_engine, enabled):
    target = domain(api, project)
    migrate(postgres_engine, "0064_work_change_inheritance", downgrade=True)
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE project_settings SET automatic_decision_reviews=:enabled, "
                                "decision_review_priority=47, revision=revision+1 "
                                "WHERE project_id=:id"),
                           {"enabled": enabled, "id": project["id"]})
    migrate(postgres_engine, "head")
    domains = api.get(path(project) + "/domains").json()["items"]
    expected = "on" if enabled else "off"
    assert all(item["automatic_decision_reviews"] == expected for item in domains)
    assert all(item["decision_review_priority"] == 47 for item in domains)
    original = decision(api, project, target, "Existing project")
    changed(api, project, target, original)
    assert len(reviews(api, project)) == int(enabled)
    audit = _audit(postgres_engine)
    assert audit["result"] == "pass", audit["blocking_findings"]
    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        migrate(postgres_engine, "0064_work_change_inheritance", downgrade=True)


def test_new_domains_do_not_inherit_another_domain_policy(api, project):
    target = policy(api, project, domain(api, project), "on", decision_review_priority=17)
    new = domain(api, project, "BDR", "Business")
    assert new["automatic_decision_reviews"] == "off" and new["decision_review_priority"] == 40
    before = decision(api, project, new, "Independent domain")
    changed(api, project, new, before)
    assert reviews(api, project) == []
    assert target["automatic_decision_reviews"] == "on"


def test_fresh_domain_inheritance_is_rejected(api, project):
    target = domain(api, project)
    result = api.patch(path(project) + f"/domains/{target['id']}", json={
        "automatic_decision_reviews": "inherit", "expected_version": target["version"],
        "client_operation_id": str(uuid4()),
    })
    assert result.status_code == 422, result.text


def test_historical_domain_receipts_replay_the_exact_sparse_response(api, project, postgres_engine):
    create = {"name": "Architecture", "prefix": "ADR", "client_operation_id": str(uuid4())}
    created = api.post(path(project) + "/domains", json=create).json()
    endpoint = path(project) + f"/domains/{created['id']}"
    rename = {"name": "Engineering", "expected_version": 1, "client_operation_id": str(uuid4())}
    renamed = api.patch(endpoint, json=rename).json()
    with postgres_engine.begin() as connection:
        connection.execute(text("ALTER TABLE client_operations DISABLE TRIGGER USER"))
        connection.execute(text("""
            UPDATE client_operations SET response_body=response_body-'automatic_decision_reviews'
            WHERE operation_kind IN ('create_knowledge_domain','rename_knowledge_domain')
        """))
        connection.execute(text("ALTER TABLE client_operations ENABLE TRIGGER USER"))
    policy(api, project, renamed, "on")
    created.pop("automatic_decision_reviews")
    renamed.pop("automatic_decision_reviews")
    assert api.post(path(project) + "/domains", json=create).json() == created
    assert api.patch(endpoint, json=rename).json() == renamed


@pytest.mark.parametrize("table", ["knowledge_domains", "decision_documents"])
def test_tombstone_updates_require_a_deletion_receipt(api, project, postgres_engine, table):
    target = domain(api, project)
    original = decision(api, project, target, "Retained decision")
    identity = target["id"] if table == "knowledge_domains" else original["id"]
    version = ",version=version+1" if table == "knowledge_domains" else ""
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        connection.execute(text(f"UPDATE {table} SET deleted_at=clock_timestamp(){version} "
                                "WHERE id=:id"), {"id": identity})
    assert api.get(path(project) + f"/decisions/{original['decision_id']}").status_code == 200


def test_domains_cannot_be_inserted_as_tombstones(project, postgres_engine):
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO knowledge_domains(id,project_id,name,prefix,deleted_at)
            VALUES (:id,:project,'Never existed','DEL',clock_timestamp())
        """), {"id": uuid4(), "project": project["id"]})
