"""Adversarial Phase 5 event semantics against real PostgreSQL."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from inspect import signature
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import mnemonic_api.services.leases as lease_service
import mnemonic_api.services.relationships as relationship_service
import mnemonic_api.services.work_items as work_item_service
from mnemonic_api.models import WorkEvent, WorkLease, WorkRelationship
from mnemonic_api.services.work_events import stage_work_claimed, stage_work_released
from tests.conftest import TEST_API_KEY

from .report_fixtures import reported

pytestmark = pytest.mark.postgres


def collection(project):
    return f"/api/v1/projects/{project['id']}/work-items"


def item_path(project, work_item):
    return f"{collection(project)}/{work_item['id']}"


def create_work(api, project, work_payload, *, title, session):
    payload = deepcopy(work_payload)
    payload["title"] = title
    payload["initial_checkpoint"]["source_session_id"] = session
    response = api.post(collection(project), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def actor(session):
    return {
        "actor_client": "pytest",
        "actor_session_id": session,
        "actor_model": "test-model",
    }


def event_page(api, project, work_item, **params):
    response = api.get(f"{item_path(project, work_item)}/events", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def claim_payload(request_id, *, session):
    return {
        "holder_client": "pytest",
        "holder_session_id": session,
        "claim_request_id": request_id,
    }


def expire_lease(postgres_engine, work_item_id):
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE work_leases
                SET expires_at = renewed_at + interval '1 microsecond'
                WHERE work_item_id = :work_item_id
                """
            ),
            {"work_item_id": work_item_id},
        )


def completion_payload(work_payload, expected_version, *, session, lease_token=None):
    checkpoint = deepcopy(work_payload["initial_checkpoint"])
    checkpoint["prompt"] = "Completion fact with its own immutable checkpoint."
    checkpoint["source_session_id"] = session
    payload = {"expected_version": expected_version, "checkpoint": checkpoint}
    if lease_token is not None:
        payload["lease_token"] = lease_token
    return payload


def test_lifecycle_completion_reopen_and_delete_emit_one_exact_fact(
    api, project, work_payload, postgres_engine
):
    created = create_work(
        api,
        project,
        work_payload,
        title="Lifecycle event exactness",
        session="lifecycle-created",
    )
    work_item = created["work_item"]
    endpoint = item_path(project, work_item)

    retired = api.patch(
        endpoint,
        json=reported({
            "expected_version": 1,
            "summary": "Retired with one companion change.",
            "status": "wont-do",
            "actor": actor("lifecycle-retired"),
        }, retirement=True),
    )
    assert retired.status_code == 200, retired.text
    reopened = api.patch(
        endpoint,
        json={"expected_version": 2, "status": "pending", "actor": actor("lifecycle-reopened")},
    )
    assert reopened.status_code == 200, reopened.text
    completed = api.post(
        f"{endpoint}/complete",
        json=reported(completion_payload(work_payload, 3, session="lifecycle-completed")),
    )
    assert completed.status_code == 200, completed.text
    completion_checkpoint_id = completed.json()["checkpoint"]["id"]
    reopened_again = api.patch(
        endpoint,
        json={
            "expected_version": 4,
            "status": "pending",
            "actor": actor("lifecycle-reopened-again"),
        },
    )
    assert reopened_again.status_code == 200, reopened_again.text
    deleted = api.post(
        f"{endpoint}/delete",
        json={"expected_version": 5, "actor": actor("lifecycle-deleted")},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["version"] == 6
    assert api.get(f"{endpoint}/events").status_code == 404

    with Session(postgres_engine) as database:
        rows = list(
            database.scalars(
                select(WorkEvent)
                .where(WorkEvent.work_item_id == UUID(work_item["id"]))
                .order_by(WorkEvent.created_at, WorkEvent.id)
            )
        )
    assert [row.event_type for row in rows] == [
        "work_created",
        "work_status_changed",
        "work_reopened",
        "work_completed",
        "work_reopened",
        "work_deleted",
    ]
    status_event = rows[1]
    assert status_event.event_metadata == {
        "from_status": "pending",
        "to_status": "wont-do",
        "changes": {
            "status": {"before": "pending", "after": "wont-do"},
            "summary": {
                "before": work_item["summary"],
                "after": "Retired with one companion change.",
            },
        },
        "work_version": 2,
    }
    completion_events = [row for row in rows if row.checkpoint_id == UUID(completion_checkpoint_id)]
    assert len(completion_events) == 1
    assert completion_events[0].event_type == "work_completed"
    assert completion_events[0].event_metadata == {
        "from_status": "pending",
        "to_status": "done",
        "work_version": 4,
    }
    assert rows[-1].event_metadata == {"final_status": "pending", "final_version": 6}


def test_claim_replay_expired_replacement_renew_and_release_provenance(
    api, project, work_payload, postgres_engine
):
    work_item = create_work(
        api,
        project,
        work_payload,
        title="Lease event provenance",
        session="lease-event-created",
    )["work_item"]
    endpoint = item_path(project, work_item)
    original_payload = claim_payload("lease-event-original", session="lease-event-holder-a")
    original = api.post(f"{endpoint}/claim", json=original_payload)
    assert original.status_code == 200, original.text
    original = original.json()
    first_page = event_page(api, project, work_item)
    assert [item["event_type"] for item in first_page["items"]] == [
        "work_created",
        "work_claimed",
    ]
    first_claim = first_page["items"][1]
    assert first_claim["metadata"] == {"expires_at": original["expires_at"]}

    replay = api.post(f"{endpoint}/claim", json=original_payload)
    assert replay.status_code == 200
    assert replay.json() == original
    renewed = api.post(
        f"{endpoint}/renew-claim", json={"lease_token": original["lease_token"]}
    )
    assert renewed.status_code == 200, renewed.text
    assert event_page(api, project, work_item)["total"] == 2

    expire_lease(postgres_engine, work_item["id"])
    wrong_expired_release = api.post(
        f"{endpoint}/release-claim", json={"lease_token": "different-expired-token"}
    )
    assert wrong_expired_release.status_code == 200
    assert wrong_expired_release.json()["released"] is False
    assert event_page(api, project, work_item)["total"] == 2

    replacement_payload = claim_payload(
        "lease-event-replacement", session="lease-event-holder-b"
    )
    replacement = api.post(f"{endpoint}/claim", json=replacement_payload)
    assert replacement.status_code == 200, replacement.text
    replacement = replacement.json()
    claims = event_page(api, project, work_item, event_type="work_claimed")["items"]
    assert len(claims) == 2
    assert claims[0]["lease_generation_id"] != claims[1]["lease_generation_id"]
    assert claims[1]["metadata"] == {"expires_at": replacement["expires_at"]}
    all_events = event_page(api, project, work_item)["items"]
    assert all(item["event_type"] != "lease_expired" for item in all_events)

    renewed_replacement = api.post(
        f"{endpoint}/renew-claim", json={"lease_token": replacement["lease_token"]}
    )
    assert renewed_replacement.status_code == 200, renewed_replacement.text
    assert event_page(api, project, work_item)["total"] == 3

    released = api.post(
        f"{endpoint}/release-claim",
        json={"lease_token": replacement["lease_token"], "actor": actor("lease-event-release")},
    )
    assert released.status_code == 200, released.text
    page = event_page(api, project, work_item)
    assert page["total"] == 4
    release = page["items"][-1]
    assert release["event_type"] == "work_released"
    assert release["lease_generation_id"] == claims[1]["lease_generation_id"]
    assert release["lease_release_id"] is not None
    assert release["actor_session_id"] == "lease-event-release"
    assert release["metadata"] == {
        "lease_holder_kind": "client",
        "lease_holder_client": "pytest",
        "lease_holder_session_id": "lease-event-holder-b",
    }
    assert original["lease_token"] not in json.dumps(page)
    assert replacement["lease_token"] not in json.dumps(page)
    assert "lease-event-original" not in json.dumps(page)
    repeated = api.post(
        f"{endpoint}/release-claim", json={"lease_token": replacement["lease_token"]}
    )
    assert repeated.status_code == 200
    assert repeated.json()["released"] is False
    assert event_page(api, project, work_item)["total"] == 4


def test_release_of_legacy_whitespace_holder_uses_unattributed_subject(
    api, project, work_payload, postgres_engine
):
    work_item = create_work(
        api,
        project,
        work_payload,
        title="Legacy release subject",
        session="legacy-release-created",
    )["work_item"]
    endpoint = item_path(project, work_item)
    receipt = api.post(
        f"{endpoint}/claim",
        json=claim_payload("legacy-release-request", session="valid-before-cutover"),
    ).json()
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE work_leases
                SET holder_session_id = :invalid_holder
                WHERE work_item_id = :work_item_id
                """
            ),
            {"invalid_holder": "\u2003", "work_item_id": work_item["id"]},
        )

    released = api.post(
        f"{endpoint}/release-claim", json={"lease_token": receipt["lease_token"]}
    )
    assert released.status_code == 200, released.text
    release = event_page(api, project, work_item, event_type="work_released")["items"][0]
    assert release["actor_kind"] == "unattributed"
    assert release["metadata"] == {"lease_holder_kind": "unattributed"}
    assert "\u2003" not in json.dumps(release, ensure_ascii=False)



def test_release_of_legacy_padded_holder_preserves_exact_subject(
    api, project, work_payload, postgres_engine
):
    work_item = create_work(
        api,
        project,
        work_payload,
        title="Legacy padded release subject",
        session="legacy-padded-release-created",
    )["work_item"]
    endpoint = item_path(project, work_item)
    receipt = api.post(
        f"{endpoint}/claim",
        json=claim_payload("legacy-padded-release-request", session="before-cutover"),
    ).json()
    exact_client = " legacy-client "
    exact_session_id = "\tlegacy-session\n"
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE work_leases
                SET holder_client = :holder_client,
                    holder_session_id = :holder_session_id
                WHERE work_item_id = :work_item_id
                """
            ),
            {
                "holder_client": exact_client,
                "holder_session_id": exact_session_id,
                "work_item_id": work_item["id"],
            },
        )

    released = api.post(
        f"{endpoint}/release-claim", json={"lease_token": receipt["lease_token"]}
    )
    assert released.status_code == 200, released.text
    listed_release = event_page(
        api, project, work_item, event_type="work_released"
    )["items"][0]
    expected_metadata = {
        "lease_holder_kind": "client",
        "lease_holder_client": exact_client,
        "lease_holder_session_id": exact_session_id,
    }
    assert listed_release["metadata"] == expected_metadata

    recalled = api.get(f"{endpoint}/context")
    assert recalled.status_code == 200, recalled.text
    recalled_release = next(
        event
        for event in recalled.json()["recent_events"]
        if event["event_type"] == "work_released"
    )
    assert recalled_release["metadata"] == expected_metadata


def test_lease_event_constructors_cannot_receive_capability_bearing_rows():
    for constructor in (stage_work_claimed, stage_work_released):
        parameters = set(signature(constructor).parameters)
        assert "lease" not in parameters
        assert parameters.isdisjoint({"lease_token", "claim_request_id", "bearer_key"})


def test_progress_rejects_every_request_secret_echo_location_without_writes(
    api, project, work_payload, caplog
):
    work_item = create_work(
        api,
        project,
        work_payload,
        title="Progress secret scanning",
        session="secret-scan-created",
    )["work_item"]
    endpoint = item_path(project, work_item)
    receipt = api.post(
        f"{endpoint}/claim",
        json=claim_payload("secret-scan-request", session="secret-scan-holder"),
    ).json()
    token = receipt["lease_token"]
    baseline = event_page(api, project, work_item)["total"]
    cases = [
        ("body", {"body": f"echo {token}"}),
        ("actor.actor_client", {"actor": {**actor("safe"), "actor_client": token}}),
        (
            "actor.actor_session_id",
            {"actor": {**actor("safe"), "actor_session_id": token}},
        ),
        ("actor.actor_model", {"actor": {**actor("safe"), "actor_model": token}}),
        ("metadata.key", {"metadata": {"outer": [{token: "safe"}]}}),
        ("metadata.value", {"metadata": {"outer": [f"echo {token}"]}}),
    ]
    for expected_location, changes in cases:
        payload = {
            "event_type": "progress",
            "body": "Safe progress body.",
            "metadata": {},
            "actor": actor("secret-scan-actor"),
            "lease_token": token,
            **changes,
        }
        response = api.post(f"{endpoint}/events", json=payload)
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == {
            "code": "event_secret_echo",
            "message": "Progress content contains a request credential or capability.",
            "context": {"fields": [expected_location]},
        }
        assert token not in response.text
        assert event_page(api, project, work_item)["total"] == baseline

    bearer_echo = api.post(
        f"{endpoint}/events",
        json={
            "event_type": "progress",
            "body": f"echo {TEST_API_KEY}",
            "metadata": {},
            "actor": actor("bearer-secret-scan"),
            "lease_token": token,
        },
    )
    assert bearer_echo.status_code == 422
    assert TEST_API_KEY not in bearer_echo.text
    assert event_page(api, project, work_item)["total"] == baseline
    assert token not in caplog.text
    assert TEST_API_KEY not in caplog.text


def test_relationship_events_preserve_endpoint_order_direction_context_and_noops(
    api, project, work_payload, postgres_engine
):
    source = create_work(
        api,
        project,
        work_payload,
        title="Discovery source",
        session="discovery-source-created",
    )["work_item"]
    target_creation = create_work(
        api,
        project,
        work_payload,
        title="Discovery target",
        session="discovery-target-created",
    )
    target = target_creation["work_item"]
    context_id = target_creation["initial_checkpoint"]["id"]
    relationship_payload = {
        "relationship_type": "discovered-from",
        "source_work_item_id": source["id"],
        "target_work_item_id": target["id"],
        "created_by_client": "pytest",
        "created_by_session_id": "discovery-add",
        "created_by_model": "test-model",
        "context_checkpoint_id": context_id,
    }
    added = api.post(
        f"/api/v1/projects/{project['id']}/relationships", json=relationship_payload
    )
    assert added.status_code == 200, added.text
    relationship = added.json()["relationship"]
    assert added.json()["created"] is True

    projections = []
    for work_item, direction, counterpart in (
        (source, "outgoing", target),
        (target, "incoming", source),
    ):
        item = event_page(
            api, project, work_item, event_type="relationship_added"
        )["items"][0]
        projections.append(item)
        assert item["relationship_id"] == relationship["id"]
        assert item["relationship_direction"] == direction
        assert item["counterpart_work_item_id"] == counterpart["id"]
        assert item["relationship_source_work_item_id"] == source["id"]
        assert item["relationship_target_work_item_id"] == target["id"]
        assert item["relationship_context_checkpoint_work_item_id"] == target["id"]
        assert item["relationship_context_checkpoint_id"] == context_id
        assert item["metadata"] == {"relationship_type": "discovered-from"}

    with Session(postgres_engine) as database:
        inserted = database.execute(
            select(WorkEvent.work_item_id, WorkEvent.id)
            .where(
                WorkEvent.relationship_id == UUID(relationship["id"]),
                WorkEvent.event_type == "relationship_added",
            )
            .order_by(WorkEvent.id)
        ).all()
    assert inserted == [
        (UUID(source["id"]), projections[0]["id"]),
        (UUID(target["id"]), projections[1]["id"]),
    ]

    replay = api.post(
        f"/api/v1/projects/{project['id']}/relationships", json=relationship_payload
    )
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert event_page(api, project, source, event_type="relationship_added")["total"] == 1
    assert event_page(api, project, target, event_type="relationship_added")["total"] == 1

    removed = api.request(
        "DELETE",
        f"/api/v1/projects/{project['id']}/relationships/{relationship['id']}",
        json={"actor": actor("discovery-remove")},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["removed"] is True
    for work_item, direction in ((source, "outgoing"), (target, "incoming")):
        item = event_page(
            api, project, work_item, event_type="relationship_removed"
        )["items"][0]
        assert item["relationship_direction"] == direction
        assert item["relationship_context_checkpoint_work_item_id"] == target["id"]
        assert item["relationship_context_checkpoint_id"] == context_id
        assert item["actor_session_id"] == "discovery-remove"

    absent = api.request(
        "DELETE",
        f"/api/v1/projects/{project['id']}/relationships/{relationship['id']}",
        json={"actor": actor("discovery-remove-replay")},
    )
    assert absent.status_code == 200
    assert absent.json()["removed"] is False
    assert event_page(api, project, source, event_type="relationship_removed")["total"] == 1
    assert event_page(api, project, target, event_type="relationship_removed")["total"] == 1

    related_left = create_work(
        api,
        project,
        work_payload,
        title="Related left",
        session="related-left-created",
    )["work_item"]
    related_right = create_work(
        api,
        project,
        work_payload,
        title="Related right",
        session="related-right-created",
    )["work_item"]
    related_add = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "related",
            "source_work_item_id": related_right["id"],
            "target_work_item_id": related_left["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "related-add",
        },
    )
    assert related_add.status_code == 200, related_add.text
    for work_item, counterpart in ((related_left, related_right), (related_right, related_left)):
        item = event_page(
            api, project, work_item, event_type="relationship_added"
        )["items"][0]
        assert item["relationship_direction"] == "undirected"
        assert item["counterpart_work_item_id"] == counterpart["id"]


def test_event_staging_faults_roll_back_work_lease_checkpoint_and_relationship(
    api, project, work_payload, postgres_engine, monkeypatch
):
    update_work = create_work(
        api,
        project,
        work_payload,
        title="Update rollback",
        session="update-rollback-created",
    )["work_item"]
    update_endpoint = item_path(project, update_work)
    before = api.get(update_endpoint).json()

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("synthetic event staging failure")

    monkeypatch.setattr(work_item_service, "stage_work_changed", fail_event)
    with pytest.raises(RuntimeError, match="synthetic event staging failure"):
        api.patch(
            update_endpoint,
            json={"expected_version": 1, "title": "Must roll back"},
        )
    assert api.get(update_endpoint).json() == before
    assert event_page(api, project, update_work)["total"] == 1
    conflict = api.patch(
        update_endpoint,
        json={"expected_version": 99, "title": "Domain rejection"},
    )
    assert conflict.status_code == 409
    assert event_page(api, project, update_work)["total"] == 1
    monkeypatch.undo()

    completed_work = create_work(
        api,
        project,
        work_payload,
        title="Completion rollback",
        session="completion-rollback-created",
    )["work_item"]
    completed_endpoint = item_path(project, completed_work)
    claim = api.post(
        f"{completed_endpoint}/claim",
        json=claim_payload("completion-rollback-claim", session="completion-rollback-holder"),
    ).json()
    monkeypatch.setattr(work_item_service, "stage_work_completed", fail_event)
    with pytest.raises(RuntimeError, match="synthetic event staging failure"):
        api.post(
            f"{completed_endpoint}/complete",
            json=reported(completion_payload(
                work_payload,
                1,
                session="completion-rollback-checkpoint",
                lease_token=claim["lease_token"],
            )),
        )
    retained = api.get(completed_endpoint).json()["work_item"]
    assert retained["status"] == "pending"
    assert retained["version"] == 1
    assert api.get(f"{completed_endpoint}/checkpoints").json()["total"] == 1
    assert api.post(
        f"{completed_endpoint}/claim",
        json=claim_payload("completion-rollback-claim", session="completion-rollback-holder"),
    ).json() == claim
    assert [
        item["event_type"] for item in event_page(api, project, completed_work)["items"]
    ] == ["work_created", "work_claimed"]
    monkeypatch.undo()

    release_work = create_work(
        api,
        project,
        work_payload,
        title="Release rollback",
        session="release-rollback-created",
    )["work_item"]
    release_endpoint = item_path(project, release_work)
    release_claim = api.post(
        f"{release_endpoint}/claim",
        json=claim_payload("release-rollback-claim", session="release-rollback-holder"),
    ).json()
    monkeypatch.setattr(lease_service, "stage_work_released", fail_event)
    with pytest.raises(RuntimeError, match="synthetic event staging failure"):
        api.post(
            f"{release_endpoint}/release-claim",
            json={"lease_token": release_claim["lease_token"]},
        )
    assert api.post(
        f"{release_endpoint}/claim",
        json=claim_payload("release-rollback-claim", session="release-rollback-holder"),
    ).json() == release_claim
    assert event_page(api, project, release_work, event_type="work_released")["total"] == 0
    with Session(postgres_engine) as database:
        lease = database.get(WorkLease, UUID(release_work["id"]))
        assert lease is not None
        assert lease.pending_release_id is None
    monkeypatch.undo()

    relationship_source = create_work(
        api,
        project,
        work_payload,
        title="Relationship rollback source",
        session="relationship-rollback-source",
    )["work_item"]
    relationship_target = create_work(
        api,
        project,
        work_payload,
        title="Relationship rollback target",
        session="relationship-rollback-target",
    )["work_item"]
    relationship_payload = {
        "relationship_type": "blocks",
        "source_work_item_id": relationship_source["id"],
        "target_work_item_id": relationship_target["id"],
        "created_by_client": "pytest",
        "created_by_session_id": "relationship-rollback-add",
    }
    monkeypatch.setattr(relationship_service, "stage_relationship_events", fail_event)
    with pytest.raises(RuntimeError, match="synthetic event staging failure"):
        api.post(
            f"/api/v1/projects/{project['id']}/relationships",
            json=relationship_payload,
        )
    with Session(postgres_engine) as database:
        edge_count = database.query(WorkRelationship).filter_by(
            project_id=UUID(project["id"]),
            source_work_item_id=UUID(relationship_source["id"]),
            target_work_item_id=UUID(relationship_target["id"]),
        ).count()
    assert edge_count == 0
    source_events = event_page(api, project, relationship_source, event_type="dependency_added")
    target_events = event_page(api, project, relationship_target, event_type="dependency_added")
    assert source_events["total"] == 0
    assert target_events["total"] == 0


def test_simultaneous_checkpoint_and_progress_events_both_commit_in_total_order(
    api, project, work_payload
):
    work_item = create_work(
        api,
        project,
        work_payload,
        title="Concurrent event appenders",
        session="concurrent-events-created",
    )["work_item"]
    endpoint = item_path(project, work_item)
    barrier = Barrier(2)

    def append_checkpoint():
        barrier.wait(timeout=5)
        return api.post(
            f"{endpoint}/checkpoints",
            json={
                "kind": "progress",
                "prompt": "Concurrent checkpoint body.",
                "source_client": "pytest",
                "source_session_id": "concurrent-checkpoint",
            },
        )

    def append_progress():
        barrier.wait(timeout=5)
        return api.post(
            f"{endpoint}/events",
            json={
                "event_type": "progress",
                "body": "Concurrent progress body.",
                "metadata": {"source": "concurrency-test"},
                "actor": actor("concurrent-progress"),
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [pool.submit(append_checkpoint), pool.submit(append_progress)]
        responses = [future.result(timeout=10) for future in responses]
    assert [response.status_code for response in responses] == [201, 201]

    page = event_page(api, project, work_item)
    assert page["total"] == 3
    assert sorted(item["event_type"] for item in page["items"][1:]) == [
        "checkpoint_added",
        "progress",
    ]
    order_keys = [
        (datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")), item["id"])
        for item in page["items"]
    ]
    assert order_keys == sorted(order_keys)
    assert len({item["id"] for item in page["items"]}) == 3
    assert api.get(endpoint).json()["work_item"]["version"] == 1
    beyond_end = event_page(
        api,
        project,
        work_item,
        event_type="progress",
        offset=100,
    )
    assert beyond_end["items"] == []
    assert beyond_end["total"] == 1
