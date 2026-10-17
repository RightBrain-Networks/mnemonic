"""Composed constraints, retained graph diagnostics, and atomic parent replacement."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .report_fixtures import reported
from .test_relationships_postgres import (
    add_relationship,
    completion_payload,
    create_work,
    relationship_collection,
    relationship_payload,
    work_path,
)

pytestmark = pytest.mark.postgres


def create(api, project, payload, title):
    return create_work(api, project, payload, title)["work_item"]


def state(api, project, work):
    response = api.get(work_path(project, work), params={"status_only": True})
    assert response.status_code == 200, response.text
    return response.json()["readiness"]


@pytest.mark.parametrize("parent_last", [False, True])
def test_five_edge_composed_cycle_refused_in_either_order(api, project, work_payload, parent_last):
    parent, ingress, cutover_a, cutover_b, child = [
        create(api, project, work_payload, name)
        for name in ["Umbrella", "Ingress", "Cutover A", "Cutover B", "Child"]
    ]
    parent_edge = {
        **relationship_payload(parent, child, "parent-child"),
        "requires_child_closeout": True,
    }
    edges = [
        parent_edge,
        relationship_payload(parent, ingress, "blocks"),
        relationship_payload(ingress, cutover_a, "blocks"),
        relationship_payload(cutover_a, cutover_b, "blocks"),
        relationship_payload(cutover_b, child, "blocks"),
    ]
    if parent_last:
        edges = edges[1:] + edges[:1]
    for edge in edges[:-1]:
        add_relationship(api, project, edge)
    response = api.post(relationship_collection(project), json=edges[-1])
    assert response.status_code == 409, response.text
    error = response.json()["detail"]
    assert error["code"] == "relationship_cycle"
    issue = error["context"]["dependency_issue"]
    assert issue["cycle_length"] == 5
    assert len(issue["cycle"]) == 5
    assert {step["relationship_type"] for step in issue["cycle"]} == {"blocks", "parent-child"}
    for work in [parent, ingress, cutover_a, cutover_b, child]:
        assert work["id"] in error["message"]


def test_descriptive_parent_cycle_is_a_warning_and_keeps_existing_semantics(
    api, project, work_payload
):
    parent = create(api, project, work_payload, "Descriptive parent")
    child = create(api, project, work_payload, "Descriptive child")
    add_relationship(api, project, relationship_payload(parent, child, "parent-child"))
    add_relationship(api, project, relationship_payload(parent, child, "blocks"))
    parent_state = state(api, project, parent)
    assert parent_state["is_ready"] is True
    assert parent_state["dependency_issue"]["kind"] == "planning-cycle"
    ready = api.get(f"/api/v1/projects/{project['id']}/ready-work").json()
    assert ready["planning_cycle_count"] == 2
    assert set(ready["dependency_issue_work_item_ids"]) == {parent["id"], child["id"]}
    completed = api.post(
        f"{work_path(project, parent)}/complete", json=reported(completion_payload())
    )
    assert completed.status_code == 200, completed.text
    assert "dependency_issue" not in state(api, project, child)


def test_required_child_closeout_and_exact_replacement_receipt(api, project, work_payload):
    parent = create(api, project, work_payload, "Closing parent")
    child = create(api, project, work_payload, "Closing child")
    old = add_relationship(api, project, relationship_payload(parent, child, "parent-child"))
    payload = {
        **relationship_payload(parent, child, "parent-child"),
        "requires_child_closeout": True,
        "replace_relationship_id": old["relationship"]["id"],
        "client_operation_id": str(uuid4()),
    }
    replacement = add_relationship(api, project, payload)
    assert replacement["replaced_relationship_id"] == old["relationship"]["id"]
    assert replacement["relationship"]["requires_child_closeout"] is True
    context = api.get(f"{work_path(project, parent)}/context").json()
    assert context["outgoing_relationships"][0]["relationship"]["requires_child_closeout"] is True
    assert add_relationship(api, project, payload) == replacement
    blocked = api.post(
        f"{work_path(project, parent)}/complete", json=reported(completion_payload())
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "work_children_open"
    assert child["id"] in blocked.json()["detail"]["message"]
    retired = api.patch(
        work_path(project, child),
        json=reported(
            {"expected_version": 1, "status": "wont-do"},
            retirement=True,
        ),
    )
    assert retired.status_code == 200, retired.text
    completed = api.post(
        f"{work_path(project, parent)}/complete", json=reported(completion_payload())
    )
    assert completed.status_code == 200, completed.text


def test_cyclic_reparent_rolls_back_old_edge_and_events(api, project, work_payload):
    old_parent, new_parent, child = [
        create(api, project, work_payload, name)
        for name in ["Old parent", "New parent", "Reparent child"]
    ]
    old = add_relationship(api, project, relationship_payload(old_parent, child, "parent-child"))
    add_relationship(api, project, relationship_payload(new_parent, child, "blocks"))
    response = api.post(
        relationship_collection(project),
        json={
            **relationship_payload(new_parent, child, "parent-child"),
            "requires_child_closeout": True,
            "replace_relationship_id": old["relationship"]["id"],
            "client_operation_id": str(uuid4()),
        },
    )
    assert response.status_code == 409, response.text
    old_path = f"{relationship_collection(project)}/{old['relationship']['id']}"
    assert api.get(old_path).status_code == 200


def _insert_reverse(connection, project, left, right):
    connection.execute(
        text("""
        INSERT INTO work_relationships(id,project_id,relationship_type,source_work_item_id,
            target_work_item_id,created_by_client,created_by_session_id)
        VALUES(gen_random_uuid(),:project,'blocks',:source,:target,'test','restored-cycle')
    """),
        {"project": project["id"], "source": right["id"], "target": left["id"]},
    )


def test_database_refuses_cycle_without_application_guard(
    api,
    project,
    work_payload,
    postgres_engine,
):
    left = create(api, project, work_payload, "SQL left")
    right = create(api, project, work_payload, "SQL right")
    add_relationship(api, project, relationship_payload(left, right, "blocks"))
    with pytest.raises(IntegrityError, match="precedence cycle refused"):
        with postgres_engine.begin() as connection:
            _insert_reverse(connection, project, left, right)


def test_retained_cycle_diagnostics_exclude_only_cycle_and_downstream(
    api, project, work_payload, postgres_engine
):
    upstream, left, right, downstream = [
        create(api, project, work_payload, name)
        for name in ["Upstream", "Cycle left", "Cycle right", "Downstream"]
    ]
    for source, target in [(upstream, left), (left, right), (right, downstream)]:
        add_relationship(api, project, relationship_payload(source, target, "blocks"))
    # Emulate a bad graph retained from an older release or imported restore.
    # Only this test fixture bypasses the new guard; the separate test above
    # verifies direct writes with every guard enabled are refused.
    with postgres_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE work_relationships DISABLE TRIGGER precedence_cycle_guard")
        )
        _insert_reverse(connection, project, left, right)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        connection.execute(
            text("ALTER TABLE work_relationships ENABLE TRIGGER precedence_cycle_guard")
        )
    assert state(api, project, upstream)["is_ready"] is True
    for work in [left, right, downstream]:
        current = state(api, project, work)
        assert current["display_state"] == "deadlocked"
        assert current["is_ready"] is False
        assert current["dependency_issue"]["role"] == (
            "depends_on_cycle" if work is downstream else "cycle_member"
        )
        full = api.get(f"{work_path(project, work)}/context")
        assert full.status_code == 200, full.text
        assert full.json()["readiness"] == current
    ready = api.get(f"/api/v1/projects/{project['id']}/ready-work").json()
    assert ready["deadlocked_count"] == 3
    assert [item["work_item"]["id"] for item in ready["items"]] == [upstream["id"]]
    claim = api.post(
        f"{work_path(project, left)}/claim",
        json={
            "holder_client": "test",
            "holder_session_id": "graph-test",
            "claim_request_id": str(uuid4()),
            "session_transcript": None,
            "force": True,
        },
    )
    assert claim.status_code == 409, claim.text
    assert claim.json()["detail"]["code"] == "work_deadlocked"
    completed = api.post(
        f"{work_path(project, left)}/complete", json=reported(completion_payload())
    )
    assert completed.status_code == 409, completed.text
    assert completed.json()["detail"]["code"] == "work_deadlocked"


def test_initial_relationship_batch_is_atomic_on_mixed_cycle(api, project, work_payload):
    parent = create(api, project, work_payload, "Initial parent")
    response = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={
            **work_payload,
            "title": "Rejected child",
            "initial_relationships": [
                {
                    "type": "parent-child",
                    "direction": "incoming",
                    "other_work_item_id": parent["id"],
                    "requires_child_closeout": True,
                },
                {"type": "blocks", "direction": "incoming", "other_work_item_id": parent["id"]},
            ],
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "relationship_cycle"
    context = api.get(f"{work_path(project, parent)}/context").json()
    assert context["relationship_counts"]["total"] == 0


def test_cross_project_concurrent_mixed_cycle_has_one_winner(api, project, work_payload):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    other = api.post("/api/v1/projects", json={"name": "Other graph project"}).json()
    parent = create(api, project, work_payload, "Concurrent parent")
    child = create(api, other, work_payload, "Concurrent child")
    barrier = Barrier(2)

    def submit(authority, body):
        barrier.wait(timeout=10)
        return api.post(relationship_collection(authority), json=body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            submit,
            project,
            {
                **relationship_payload(parent, child, "parent-child"),
                "requires_child_closeout": True,
            },
        )
        second = pool.submit(submit, other, relationship_payload(parent, child, "blocks"))
        responses = [first.result(timeout=20), second.result(timeout=20)]
    assert sorted(response.status_code for response in responses) == [200, 409]
    refused = next(response for response in responses if response.status_code == 409)
    assert refused.json()["detail"]["code"] == "relationship_cycle"


def test_retired_prerequisite_is_not_a_topological_deadlock(api, project, work_payload):
    parent = create(api, project, work_payload, "Retired prerequisite")
    child = create(api, project, work_payload, "Still blocked")
    add_relationship(api, project, relationship_payload(parent, child, "blocks"))
    response = api.patch(
        work_path(project, parent),
        json=reported(
            {"expected_version": 1, "status": "wont-do"},
            retirement=True,
        ),
    )
    assert response.status_code == 200, response.text
    current = state(api, project, child)
    assert current["display_state"] == "blocked"
    assert "dependency_issue" not in current


def test_upgrade_preserves_legacy_cycle_for_read_diagnosis(
    api,
    project,
    work_payload,
    postgres_engine,
):
    from alembic import command
    from alembic.config import Config

    from .conftest import BACKEND_DIR

    left = create(api, project, work_payload, "Legacy cycle left")
    right = create(api, project, work_payload, "Legacy cycle right")
    add_relationship(api, project, relationship_payload(left, right, "blocks"))
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0051_priority_rubrics")
        _insert_reverse(connection, project, left, right)
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    assert state(api, project, left)["display_state"] == "deadlocked"


def test_downgrade_cannot_erase_a_required_child_contract(
    api,
    project,
    work_payload,
    postgres_engine,
):
    from alembic import command
    from alembic.config import Config

    from .conftest import BACKEND_DIR

    parent = create(api, project, work_payload, "Protected parent")
    child = create(api, project, work_payload, "Protected child")
    add_relationship(
        api,
        project,
        {
            **relationship_payload(parent, child, "parent-child"),
            "requires_child_closeout": True,
        },
    )
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with pytest.raises(RuntimeError, match="cannot erase their semantics"):
        with postgres_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0051_priority_rubrics")
    context = api.get(f"{work_path(project, parent)}/context").json()
    assert context["outgoing_relationships"][0]["relationship"]["requires_child_closeout"] is True
