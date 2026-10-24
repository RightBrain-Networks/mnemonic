"""Human questions are ordinary, searchable, revision-pinned knowledge records."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from mnemonic_api.knowledge_integrity import KNOWLEDGE_FINDINGS

from .test_human_gates_postgres import create_work, gate_path, gate_request, resolution_payload
from .test_knowledge_postgres import domain, path, put, put_body

pytestmark = pytest.mark.postgres


def test_projects_seed_distinct_default_product_domains(api, project):
    first = api.get(path(project) + "/domains").json()["items"]
    assert len(first) == 1
    assert first[0]["prefix"] == "PDR"
    assert first[0]["name"] == "Product Decision Records"
    assert first[0]["is_product_decision_domain"] is True
    assert first[0]["decision_count"] == 0
    other = api.post("/api/v1/projects", json={"name": "Other", "slug": "other-pdr"}).json()
    second = api.get(path(other) + "/domains").json()["items"]
    assert second[0]["id"] != first[0]["id"]
    renamed = api.patch(path(project) + f"/domains/{first[0]['id']}", json={
        "name": "Owner decisions", "expected_version": 1, "client_operation_id": str(uuid4()),
    })
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["is_product_decision_domain"] is True


def test_question_revisions_and_human_answer_are_searchable_citable_records(
    api, project, work_payload, postgres_engine,
):
    work = create_work(api, project, work_payload, title="Choose the product behavior")
    endpoint = gate_path(project, work)
    intent = gate_request(operation_id=uuid4(), question="Should we allow the violet export?")
    original_response = api.post(endpoint, json=intent)
    assert original_response.status_code == 201, original_response.text
    original = original_response.json()
    assert original["decision"]["reference"] == "PDR-00001-0"
    assert original["decision"]["metadata"]["related_work_item_ids"] == [work["work_item"]["id"]]
    assert original["decision"]["metadata"]["depends_on"] == []
    decision_path = path(project) + "/decisions/PDR-00001"
    initial = api.get(decision_path).json()
    assert initial["content"] == intent["question"]
    assert initial["author_kind"] == "agent"
    assert initial["human_gate"]["gate_id"] == original["id"]
    assert initial["human_gate"]["status"] == "unresolved"
    updated_intent = {
        **intent, "gate_id": original["id"], "expected_question_version": 1,
        "question": "Violet export is ready. Make it available?",
        "client_operation_id": str(uuid4()),
    }
    current = api.post(endpoint, json=updated_intent).json()
    assert current["decision"]["reference"] == "PDR-00001-1"
    answer = {**resolution_payload(revision=current["current_context_revision"],
                                  operation_id=uuid4()),
              "expected_question_version": 2, "resolution": "No. Keep violet export private."}
    answered = api.post(endpoint + f"/{original['id']}/resolve", json=answer)
    assert answered.status_code == 200, answered.text
    assert answered.json()["decision"]["reference"] == "PDR-00001-2"
    assert answered.json()["decision"]["state"] == "accepted"
    final = api.get(decision_path).json()
    assert final["content"] == (
        "## Question\n\n" + updated_intent["question"]
        + "\n\n## Product decision\n\n" + answer["resolution"]
    )
    assert final["author_kind"] == "human"
    assert final["agent_session_id"] is None
    assert api.get(decision_path, params={"revision": 0}).json()["content"] == intent["question"]
    assert api.post(endpoint, json=intent).json() == original
    assert api.post(endpoint, json=updated_intent).json() == current
    assert api.post(endpoint + f"/{original['id']}/resolve", json=answer).json() == answered.json()
    assert api.get(f"/api/v1/projects/{project['id']}/human-attention").json()["total"] == 0
    search = api.post(f"/api/v1/projects/{project['id']}/search", json={
        "q": "violet", "facets": ["decisions"], "fulltext": True,
    })
    assert search.status_code == 200, search.text
    assert "PDR-00001-2" in search.text
    scoped = api.post(decision_path + "/scope", json={
        "revision": 2, "commit_hash": "a" * 40, "work_item_id": work["work_item"]["id"],
        "agent_session_id": "implementation-session", "client_operation_id": str(uuid4()),
    })
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["scope"]["revision"] == 2
    with postgres_engine.connect() as connection:
        for name, query in KNOWLEDGE_FINDINGS.items():
            assert connection.scalar(text(query)) == 0, name


def test_pdr_metadata_revisions_retain_omissions_clear_explicit_lists_and_pin_dependencies(
    api, project, work_payload,
):
    adr = put(api, project, put_body(domain(api, project)))["decision"]
    work = create_work(api, project, work_payload, title="Owner choice with context")
    other = create_work(api, project, work_payload, title="Related implementation")
    endpoint = gate_path(project, work)
    intent = {**gate_request(operation_id=uuid4()), "decision_metadata": {
        "title": "Choose the export boundary", "related_decision_ids": [adr["decision_id"]],
        "related_work_item_ids": [other["work_item"]["id"]],
        "depends_on": [{"decision_id": adr["decision_id"], "revision": 0,
                        "rationale": "The owner choice uses the architecture boundary."}],
    }}
    original = api.post(endpoint, json=intent).json()
    revised = api.post(endpoint, json={**gate_request(operation_id=uuid4()),
        "gate_id": original["id"], "expected_question_version": 1,
        "decision_metadata": {"title": "Choose the final boundary", "related_decision_ids": []},
    })
    assert revised.status_code == 201, revised.text
    metadata = revised.json()["decision"]["metadata"]
    assert metadata["title"] == "Choose the final boundary"
    assert metadata["related_decision_ids"] == []
    assert metadata["depends_on"] == intent["decision_metadata"]["depends_on"]
    assert set(metadata["related_work_item_ids"]) == {
        work["work_item"]["id"], other["work_item"]["id"],
    }
    assert api.get(path(project) + "/decisions/PDR-00001-0").json()["title"] == \
        "Choose the export boundary"
    assessments = api.get(path(project) + "/assessments",
                          params={"decision_id": "PDR-00001"})
    assert assessments.status_code == 200, assessments.text
    # Proposed owner questions retain metadata without enacting a changed ruling.
    assert assessments.json()["total"] == 0


def test_identical_questions_get_distinct_records_and_invalid_links_roll_back_allocation(
    api, project, work_payload,
):
    work = create_work(api, project, work_payload, title="Two distinct owner choices")
    endpoint = gate_path(project, work)
    invalid = api.post(endpoint, json={**gate_request(operation_id=uuid4()),
        "decision_metadata": {"related_decision_ids": ["ADR-99999"]},
    })
    assert invalid.status_code == 404, invalid.text
    assert api.get(endpoint).json()["total"] == 0
    first = api.post(endpoint, json=gate_request(operation_id=uuid4())).json()
    second = api.post(endpoint, json=gate_request(operation_id=uuid4())).json()
    assert first["decision"]["reference"] == "PDR-00001-0"
    assert second["decision"]["reference"] == "PDR-00002-0"
    assert first["decision"]["sha256"] == second["decision"]["sha256"]


def test_knowledge_put_cannot_rewrite_or_resolve_a_managed_pdr(api, project, work_payload):
    work = create_work(api, project, work_payload, title="Only the owner can answer")
    gate = api.post(gate_path(project, work), json=gate_request(operation_id=uuid4())).json()
    body = put_body({"id": gate["decision"]["domain_id"]}, "Bypassed owner?",
                    decision_id=gate["decision"]["decision_id"], expected_revision=0,
                    state="accepted")
    rejected = api.put(path(project) + "/decisions", json=body)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "decision_managed_by_human_gate"
    assert api.get(path(project) + "/decisions/PDR-00001").json()["state"] == "proposed"
    standalone = put(api, project, put_body({"id": gate["decision"]["domain_id"]},
                                           "Independent product record"))["decision"]
    assert standalone["decision_id"] == "PDR-00002"
    assert "human_gate" not in standalone


def test_fresh_agent_questions_require_model_but_human_questions_do_not(api, project, work_payload):
    work = create_work(api, project, work_payload, title="Truthful author metadata")
    intent = {**gate_request(operation_id=uuid4()), "requested_by_model": None}
    rejected = api.post(gate_path(project, work), json=intent)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "decision_author_model_required"
    accepted = api.post(gate_path(project, work), json={**intent,
        "requested_by_client": "dashboard", "client_operation_id": str(uuid4()),
    })
    assert accepted.status_code == 201, accepted.text
    assert api.get(path(project) + "/decisions/PDR-00001").json()["author_kind"] == "human"


def test_fresh_rest_question_and_answer_require_exact_retry_identity(api, project, work_payload):
    work = create_work(api, project, work_payload, title="Receipted product decisions")
    endpoint = gate_path(project, work)
    intent = gate_request(operation_id=uuid4())
    unkeyed = {key: value for key, value in intent.items() if key != "client_operation_id"}
    rejected = api.post(endpoint, json=unkeyed)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "client_operation_id_required"
    assert api.get(endpoint).json()["total"] == 0
    original = api.post(endpoint, json=intent).json()
    answer = resolution_payload(revision=original["current_context_revision"])
    answer.pop("client_operation_id")
    rejected = api.post(endpoint + f"/{original['id']}/resolve", json=answer)
    assert rejected.status_code == 422, rejected.text
    assert api.get(endpoint).json()["items"][0]["status"] == "unresolved"
