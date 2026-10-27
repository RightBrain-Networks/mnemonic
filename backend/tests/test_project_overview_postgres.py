"""Project orientation uses complete, coherent aggregates and canonical display states."""

from uuid import uuid4

import pytest
from sqlalchemy import event, text

from .code_review_fixtures import claim_review, configure, mandatory
from .test_duplicate_handling_postgres import merge_work
from .test_human_gates_postgres import gate_request
from .test_knowledge_management_postgres import HEADERS, browser_path, deletion_body, preview
from .test_knowledge_postgres import domain, put, put_body
from .test_precedence_postgres import _insert_reverse
from .test_project_reports_postgres import close, create
from .test_relationships_postgres import add_relationship, relationship_payload

pytestmark = pytest.mark.postgres
STATES = {
    "pending", "active", "to-review", "dropped", "blocked", "waiting", "duplicate",
    "deadlocked", "deferred", "done", "wont-do", "promoted",
}


def overview(api, project):
    response = api.get(f"/api/v1/projects/{project['id']}/overview")
    assert response.status_code == 200, response.text
    return response.json()


def claim(api, project, work):
    response = api.post(f"/api/v1/projects/{project['id']}/work-items/{work['id']}/claim", json={
        "holder_client": "codex", "holder_session_id": "project-overview-session",
        "claim_request_id": str(uuid4()), "session_transcript": None,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_project_has_all_zero_states_and_empty_domains(api, project):
    result = overview(api, project)
    assert result["project_id"] == project["id"]
    expected = api.get(f"/api/v1/projects/{project['id']}/settings").json()
    for field in ("recall_pointer_template", "job_completion_report_prompt"):
        expected.pop(field)
    assert result["settings"] == expected
    summary = result["work_items_summary"]
    assert summary["total"] == 0 and summary["active"] == []
    assert summary["counts_by_state"] == dict.fromkeys(STATES, 0)
    assert set(summary["counts_by_tag"]) == {
        "code-review", "remediation", "dir", "bug-suspected", "bug-proven", "infra",
        "tests", "ux-ui", "security",
    }
    assert all(values == dict.fromkeys(STATES, 0) for values in summary["counts_by_tag"].values())
    assert result["needs_attention"] == {"open_count": 0}
    assert [(d["prefix"], d["decision_document_count"])
            for d in result["knowledge_domains"]] == [("PDR", 0)]
    assert api.get(f"/api/v1/projects/{uuid4()}/overview").status_code == 404


def test_work_counts_tags_active_metadata_and_attention(api, project, work_payload,
                                                       checkpoint_fields, postgres_engine):
    configure(api, project, code_review_required_min_priority=100,
              code_review_optional_min_priority=100)
    works = {name: create(api, project, {**work_payload, "title": name,
             "tags": ["code-review", "dir"]})
             for name in ("pending", "active", "dropped", "waiting", "blocked", "deferred",
                          "done", "wont-do", "promoted", "deleted")}
    for state in ("active", "dropped", "waiting"):
        claim(api, project, works[state])
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE work_leases SET acquired_at=now()-interval '2 hours', "
                                "renewed_at=now()-interval '2 hours', "
                                "expires_at=now()-interval '1 hour' WHERE work_item_id=:id"),
                           {"id": works["dropped"]["id"]})
        connection.execute(text("UPDATE work_items SET status='deferred' WHERE id=:id"),
                           {"id": works["deferred"]["id"]})
        connection.execute(text("UPDATE work_items SET deleted_at=now() WHERE id=:id"),
                           {"id": works["deleted"]["id"]})
    base = f"/api/v1/projects/{project['id']}/work-items"
    for question in ("Choose the protocol", "Choose the interface"):
        response = api.post(f"{base}/{works['waiting']['id']}/gates",
                            json=gate_request(question=question))
        assert response.status_code == 201, response.text
    add_relationship(api, project, relationship_payload(
        works["pending"], works["blocked"], "blocks",
    ))
    for state in ("done", "wont-do", "promoted"):
        response, _ = close(api, project, works[state], checkpoint_fields, state)
        assert response.status_code == 200, response.text
    other = api.post("/api/v1/projects", json={"name": "Other project"}).json()
    claim(api, other, create(api, other, work_payload))
    result = overview(api, project)
    summary = result["work_items_summary"]
    expected = dict.fromkeys(STATES, 0) | {state: 1 for state in works if state != "deleted"}
    assert summary["counts_by_state"] == expected
    assert summary["total"] == 9
    assert summary["counts_by_tag"]["code-review"] == expected
    assert summary["counts_by_tag"]["dir"] == expected
    assert summary["active"] == [{key: works["active"][key]
                                  for key in ("id", "title", "created_at", "updated_at", "tags")}
                                 | {"session_id": "project-overview-session"}]
    attention = api.get(f"/api/v1/projects/{project['id']}/human-attention").json()
    assert result["needs_attention"]["open_count"] == attention["total"] == 2


def test_review_duplicates_and_deadlocks_follow_display_precedence(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    claim_review(api, project, completion, checkpoint_fields)
    source, destination, left, right = [create(api, project, {**work_payload, "title": name})
                                       for name in ("source", "destination", "left", "right")]
    merge_work(api, project, source, destination)
    add_relationship(api, project, relationship_payload(left, right, "blocks"))
    with postgres_engine.begin() as connection:
        connection.execute(text("ALTER TABLE work_relationships DISABLE TRIGGER "
                                "precedence_cycle_guard"))
        _insert_reverse(connection, project, left, right)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        connection.execute(text("ALTER TABLE work_relationships ENABLE TRIGGER "
                                "precedence_cycle_guard"))
    result = overview(api, project)["work_items_summary"]
    assert result["total"] == 5
    assert result["counts_by_state"] == dict.fromkeys(STATES, 0) | {
        "pending": 1, "to-review": 1, "duplicate": 1, "deadlocked": 2,
    }
    assert result["active"] == []
    assert completion["code_review_request"] is not None


def test_domains_count_documents_once_and_exclude_deleted(api, project):
    architecture = domain(api, project)
    empty = domain(api, project, "RFC", "Proposals")
    deleted = domain(api, project, "OLD", "Old domain")
    first = put(api, project, put_body(architecture))["decision"]
    put(api, project, put_body(architecture, "A revised decision", decision_id=first["decision_id"],
                               expected_revision=0))
    second = put(api, project, put_body(architecture, "A deleted decision"))["decision"]
    for suffix in (f"/decisions/{second['decision_id']}", f"/domains/{deleted['id']}"):
        snapshot = preview(api, project, suffix)
        response = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS,
                            json=deletion_body(snapshot))
        assert response.status_code == 200, response.text
    other = api.post("/api/v1/projects", json={"name": "Separate knowledge"}).json()
    put(api, other, put_body(domain(api, other)))
    result = {row["prefix"]: row for row in overview(api, project)["knowledge_domains"]}
    assert set(result) == {"ADR", "PDR", "RFC"}
    assert result["ADR"] == {"id": architecture["id"], "name": "Architecture", "prefix": "ADR",
                             "decision_document_count": 1}
    assert result["RFC"]["id"] == empty["id"] and result["RFC"]["decision_document_count"] == 0


def test_counts_use_one_snapshot_without_project_mutation_lock(
    api, project, work_payload, postgres_engine,
):
    work = create(api, project, {**work_payload, "tags": ["code-review"]})
    changed = False
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal changed
        statements.append(statement)
        if not changed and "FROM project_settings" in statement:
            changed = True
            with postgres_engine.begin() as connection:
                connection.execute(text("UPDATE work_items SET status='deferred' WHERE id=:id"),
                                   {"id": work["id"]})

    event.listen(postgres_engine, "after_cursor_execute", observe)
    try:
        summary = overview(api, project)["work_items_summary"]
    finally:
        event.remove(postgres_engine, "after_cursor_execute", observe)
    assert changed
    assert summary["counts_by_state"]["pending"] == 1
    assert summary["counts_by_tag"]["code-review"]["pending"] == 1
    assert not any("pg_advisory" in statement or "FOR UPDATE" in statement
                   for statement in statements)
    assert overview(api, project)["work_items_summary"]["counts_by_state"]["deferred"] == 1


def test_active_collection_and_counts_are_not_paginated(api, project, work_payload,
                                                       postgres_engine):
    works = [create(api, project, {**work_payload, "title": f"Concurrent work {index}",
                                  "tags": ["code-review"]}) for index in range(105)]
    with postgres_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO work_leases(work_item_id, holder_client, holder_session_id,
                                    claim_request_id, lease_token,
                                    acquired_at, renewed_at, expires_at)
            VALUES(:id, 'codex', :session, :request, :token,
                   now(), now(), now()+interval '1 hour')
        """), [{"id": work["id"], "session": f"session-{index}", "request": str(uuid4()),
                "token": str(uuid4())} for index, work in enumerate(works)])
    summary = overview(api, project)["work_items_summary"]
    assert summary["total"] == summary["counts_by_state"]["active"] == 105
    assert summary["counts_by_tag"]["code-review"]["active"] == 105
    assert [work["id"] for work in summary["active"]] == [work["id"] for work in works]
    assert [work["session_id"] for work in summary["active"]] == [
        f"session-{index}" for index in range(105)
    ]
