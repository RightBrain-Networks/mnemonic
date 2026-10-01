"""Fresh agent creation declares its session origin and records it atomically."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.models import ClientOperation, WorkItem, WorkRelationship
from mnemonic_api.schemas import WorkCreation, WorkItemCreate
from mnemonic_api.services.client_operations import (
    complete_client_operation,
    prepare_client_operation,
    reserve_client_operation,
)

pytestmark = pytest.mark.postgres


def collection(project):
    return f"/api/v1/projects/{project['id']}/work-items"


def create(api, project, work_payload, **changes):
    result = api.post(collection(project), json={**work_payload, **changes})
    assert result.status_code == 201, result.text
    return result.json()


@pytest.mark.parametrize("keyed", [False, True])
def test_fresh_agent_creation_requires_explicit_origin(
    api, project, work_payload, postgres_engine, keyed,
):
    payload = dict(work_payload)
    payload.pop("discovered_from_work_item_id")
    if keyed:
        payload["client_operation_id"] = str(uuid4())
    result = api.post(collection(project), json=payload)
    assert result.status_code == 422
    assert result.json()["detail"]["code"] == "discovered_from_work_item_id_required"
    with Session(postgres_engine) as database:
        assert database.scalar(select(func.count()).select_from(WorkItem)) == 0
        assert database.scalar(select(func.count()).select_from(ClientOperation)) == 0


def test_explicit_null_and_dashboard_creation_remain_roots(api, project, work_payload):
    assert create(api, project, work_payload)["initial_relationships"] == []
    payload = dict(work_payload)
    payload.pop("discovered_from_work_item_id")
    payload["initial_checkpoint"] = {**payload["initial_checkpoint"], "source_client": "dashboard"}
    assert create(api, project, payload)["initial_relationships"] == []


@pytest.mark.parametrize("cross_project", [False, True])
def test_origin_adds_discovery_with_events_and_exact_retry(
    api, project, work_payload, postgres_engine, cross_project,
):
    origin_project = project
    if cross_project:
        response = api.post("/api/v1/projects", json={"name": "Origin", "slug": "origin"})
        assert response.status_code == 201
        origin_project = response.json()
    origin = create(api, origin_project, work_payload)
    payload = {**work_payload, "discovered_from_work_item_id": origin["work_item"]["id"],
               "client_operation_id": str(uuid4())}
    created = create(api, project, payload)
    edge, = created["initial_relationships"]
    assert edge["relationship_type"] == "discovered-from"
    assert edge["source_work_item_id"] == created["work_item"]["id"]
    assert edge["target_work_item_id"] == origin["work_item"]["id"]
    assert edge["context_checkpoint_id"] == origin["initial_checkpoint"]["id"]
    assert edge["context_checkpoint_work_item_id"] == origin["work_item"]["id"]
    assert edge["project_id"] == project["id"]
    assert create(api, project, payload) == created
    with Session(postgres_engine) as database:
        assert database.scalar(select(func.count()).select_from(WorkRelationship)) == 1
    for endpoint_project, endpoint_id in (
        (project, created["work_item"]["id"]), (origin_project, origin["work_item"]["id"]),
    ):
        events = api.get(f"{collection(endpoint_project)}/{endpoint_id}/events").json()
        assert any(event["event_type"] == "relationship_added" for event in events["items"])
    changed = api.post(collection(project), json={**payload, "discovered_from_work_item_id": None})
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "client_operation_conflict"


def test_explicit_discovery_is_reused_alongside_hierarchy(api, project, work_payload):
    origin = create(api, project, work_payload)
    origin_id = origin["work_item"]["id"]
    created = create(
        api, project, work_payload, discovered_from_work_item_id=origin_id,
        initial_relationships=[
            {"type": "discovered-from", "direction": "outgoing", "other_work_item_id": origin_id,
             "context_checkpoint_id": origin["initial_checkpoint"]["id"]},
            {"type": "parent-child", "direction": "incoming", "other_work_item_id": origin_id},
        ],
    )
    assert [edge["relationship_type"] for edge in created["initial_relationships"]] == [
        "discovered-from", "parent-child",
    ]


def test_unknown_origin_rolls_back_receipt_and_work(api, project, work_payload, postgres_engine):
    result = api.post(collection(project), json={
        **work_payload, "discovered_from_work_item_id": str(uuid4()),
        "client_operation_id": str(uuid4()),
    })
    assert result.status_code == 404
    with Session(postgres_engine) as database:
        assert database.scalar(select(func.count()).select_from(WorkItem)) == 0
        assert database.scalar(select(func.count()).select_from(ClientOperation)) == 0


def test_sparse_historical_create_receipt_replays_unchanged(
    api, project, work_payload, postgres_engine,
):
    created = create(api, project, work_payload)
    old_payload = dict(work_payload)
    old_payload.pop("discovered_from_work_item_id")
    old_payload["client_operation_id"] = str(uuid4())
    with Session(postgres_engine) as database:
        prepared = prepare_client_operation(
            "create_work", UUID(project["id"]), {}, WorkItemCreate.model_validate(old_payload),
        )
        reserved = reserve_client_operation(database, prepared, wait_seconds=1)
        complete_client_operation(
            database, reserved, WorkCreation.model_validate(created), mutation_applied=True,
        )
        database.commit()
    assert create(api, project, old_payload) == created
    changed = api.post(
        collection(project), json={**old_payload, "discovered_from_work_item_id": None},
    )
    assert changed.status_code == 409


def test_generated_lineage_keeps_ten_explicit_relationship_slots(api, project, work_payload):
    origin = create(api, project, work_payload)
    peers = [create(api, project, work_payload) for _ in range(10)]
    created = create(
        api, project, work_payload,
        discovered_from_work_item_id=origin["work_item"]["id"],
        initial_relationships=[
            {"type": "related", "direction": "outgoing",
             "other_work_item_id": peer["work_item"]["id"]}
            for peer in peers
        ],
    )
    assert len(created["initial_relationships"]) == 11
    assert sum(edge["relationship_type"] == "discovered-from"
               for edge in created["initial_relationships"]) == 1


@pytest.mark.parametrize("context_fault", ["absent", "wrong_owner"])
def test_receipt_validation_rejects_invalid_generated_context(
    api, project, work_payload, context_fault,
):
    from mnemonic_api.services.client_operations import _response_matches_operation, operation_spec

    origin = create(api, project, work_payload)
    payload = {**work_payload, "discovered_from_work_item_id": origin["work_item"]["id"]}
    result = WorkCreation.model_validate(create(api, project, payload))
    edge = result.initial_relationships[0]
    if context_fault == "absent":
        edge.context_checkpoint_id = None
        edge.context_checkpoint_work_item_id = None
    else:
        edge.context_checkpoint_work_item_id = result.work_item.id
    assert not _response_matches_operation(
        operation_spec("create_work"), UUID(project["id"]), {},
        WorkItemCreate.model_validate(payload), result, True,
    )
