"""Phase 5 immutable work-event behavior against real PostgreSQL."""

from copy import deepcopy

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import TEST_API_KEY

pytestmark = pytest.mark.postgres


def collection(project):
    return f"/api/v1/projects/{project['id']}/work-items"


def create_work(api, project, payload, *, title="Event work"):
    body = deepcopy(payload)
    body["title"] = title
    body["initial_checkpoint"]["source_session_id"] = title.lower().replace(" ", "-")
    response = api.post(collection(project), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def item_path(project, work_item):
    return f"{collection(project)}/{work_item['id']}"


def actor(session="event-session"):
    return {
        "actor_client": "pytest",
        "actor_session_id": session,
        "actor_model": "test-model",
    }


def events(api, project, work_item, **params):
    response = api.get(f"{item_path(project, work_item)}/events", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_event_emission_replay_progress_reads_and_bounded_recall(api, project, work_payload):
    created = create_work(api, project, work_payload)
    work_item = created["work_item"]
    endpoint = item_path(project, work_item)

    page = events(api, project, work_item)
    assert page["total"] == 1
    assert page["pre_phase5_history_may_be_incomplete"] is False
    creation = page["items"][0]
    assert creation["event_type"] == "work_created"
    assert creation["origin"] == "live"
    assert creation["checkpoint_id"] == created["initial_checkpoint"]["id"]
    assert creation["metadata"]["initial"] == {
        "title": work_item["title"],
        "summary": work_item["summary"],
        "status": "pending",
        "priority": work_item["priority"],
        "version": 1,
    }
    assert creation["actor_client"] == work_payload["initial_checkpoint"]["source_client"]
    assert creation["body"] is None

    before = api.get(endpoint).json()
    progress = api.post(
        f"{endpoint}/events",
        json={
            "event_type": "progress",
            "body": "  Exact progress text.\r\n  ",
            "metadata": {"percent": 20, "nested": ["safe", True]},
            "actor": actor("progress-actor"),
        },
    )
    assert progress.status_code == 201, progress.text
    assert progress.json()["body"] == "  Exact progress text.\r\n  "
    assert progress.json()["event_type"] == "progress"
    assert api.get(endpoint).json()["version"] == before["version"]

    checkpoint = api.post(
        f"{endpoint}/checkpoints",
        json={
            "kind": "progress",
            "prompt": "Checkpoint progress remains resume context.",
            "source_client": "pytest",
            "source_session_id": "checkpoint-event",
        },
    )
    assert checkpoint.status_code == 201, checkpoint.text
    changed = api.patch(
        endpoint,
        json={
            "expected_version": 1,
            "priority": 55,
            "actor": actor("patch-event"),
        },
    )
    assert changed.status_code == 200, changed.text

    claim_payload = {
        "holder_client": "pytest",
        "holder_session_id": "claim-event",
        "claim_request_id": "claim-event-request",
    }
    claim = api.post(f"{endpoint}/claim", json=claim_payload)
    assert claim.status_code == 200, claim.text
    replay = api.post(f"{endpoint}/claim", json=claim_payload)
    assert replay.status_code == 200
    assert replay.json() == claim.json()
    count_after_replay = events(api, project, work_item)["total"]

    release = api.post(
        f"{endpoint}/release-claim",
        json={
            "lease_token": claim.json()["lease_token"],
            "actor": actor("release-event"),
        },
    )
    assert release.status_code == 200, release.text
    assert release.json()["released"] is True

    page = events(api, project, work_item)
    assert page["total"] == count_after_replay + 1
    assert [item["event_type"] for item in page["items"]] == [
        "work_created",
        "progress",
        "checkpoint_added",
        "work_updated",
        "work_claimed",
        "work_released",
    ]
    assert page["items"][-1]["metadata"]["lease_holder_kind"] == "client"
    assert page["items"][-1]["actor_session_id"] == "release-event"
    assert "lease_token" not in str(page)
    assert "claim_request_id" not in str(page)

    newest_progress = events(
        api,
        project,
        work_item,
        order="newest",
        event_type="progress",
        limit=1,
    )
    assert newest_progress["total"] == 1
    assert newest_progress["items"][0]["body"] == "  Exact progress text.\r\n  "

    context = api.get(
        f"{endpoint}/context",
        params={"recent_event_limit": 3},
    )
    assert context.status_code == 200, context.text
    context = context.json()
    assert context["event_total"] == page["total"]
    assert len(context["recent_events"]) == 3
    assert context["omitted_event_count"] == page["total"] - 3
    assert [item["event_type"] for item in context["recent_events"]] == [
        "work_updated",
        "work_claimed",
        "work_released",
    ]


def test_progress_rejects_reserved_types_keys_and_request_known_secret_echo(
    api, project, work_payload
):
    work_item = create_work(api, project, work_payload, title="Secret boundary")["work_item"]
    endpoint = item_path(project, work_item)
    before = events(api, project, work_item)["total"]

    reserved = api.post(
        f"{endpoint}/events",
        json={
            "event_type": "work_completed",
            "body": "forged",
            "actor": actor(),
        },
    )
    assert reserved.status_code == 422

    secret_key = api.post(
        f"{endpoint}/events",
        json={
            "event_type": "progress",
            "body": "Safe body",
            "metadata": {"Authorization": "not logged"},
            "actor": actor(),
        },
    )
    assert secret_key.status_code == 422
    assert "not logged" not in secret_key.text

    echo = api.post(
        f"{endpoint}/events",
        json={
            "event_type": "progress",
            "body": f"accidental credential: {TEST_API_KEY}",
            "actor": actor(),
        },
    )
    assert echo.status_code == 422
    assert echo.json()["detail"]["code"] == "event_secret_echo"
    assert TEST_API_KEY not in echo.text
    assert echo.json()["detail"]["context"]["fields"] == ["body"]
    assert events(api, project, work_item)["total"] == before


def test_relationship_endpoint_events_and_mutation_actor_contracts(api, project, work_payload):
    source = create_work(api, project, work_payload, title="Event source")["work_item"]
    target = create_work(api, project, work_payload, title="Event target")["work_item"]
    addition = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "related",
            "source_work_item_id": source["id"],
            "target_work_item_id": target["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "relationship-add",
        },
    )
    assert addition.status_code == 200, addition.text
    relationship = addition.json()["relationship"]
    for work_item, counterpart in ((source, target), (target, source)):
        event = events(
            api,
            project,
            work_item,
            event_type="relationship_added",
        )["items"][0]
        assert event["relationship_id"] == relationship["id"]
        assert event["relationship_direction"] == "undirected"
        assert event["counterpart_work_item_id"] == counterpart["id"]

    replay = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "related",
            "source_work_item_id": source["id"],
            "target_work_item_id": target["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "relationship-add",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert events(api, project, source, event_type="relationship_added")["total"] == 1

    removal = api.request(
        "DELETE",
        f"/api/v1/projects/{project['id']}/relationships/{relationship['id']}",
        json={"actor": actor("relationship-remove")},
    )
    assert removal.status_code == 200, removal.text
    for work_item in (source, target):
        event = events(
            api,
            project,
            work_item,
            event_type="relationship_removed",
        )["items"][0]
        assert event["actor_session_id"] == "relationship-remove"

    actor_only = api.patch(
        item_path(project, source),
        json={"expected_version": 1, "actor": actor("actor-only")},
    )
    assert actor_only.status_code == 422

    unattributed = api.patch(
        item_path(project, source),
        json={"expected_version": 1, "summary": "Direct REST actor omitted."},
    )
    assert unattributed.status_code == 200, unattributed.text
    update_event = events(api, project, source, event_type="work_updated")["items"][0]
    assert update_event["actor_kind"] == "unattributed"


def test_work_events_are_database_immutable(api, project, work_payload, postgres_engine):
    work_item = create_work(api, project, work_payload, title="Immutable event")["work_item"]
    event_id = events(api, project, work_item)["items"][0]["id"]
    with postgres_engine.begin() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text("UPDATE work_events SET body = 'changed' WHERE id = :event_id"),
                {"event_id": event_id},
            )
    with postgres_engine.begin() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text("DELETE FROM work_events WHERE id = :event_id"),
                {"event_id": event_id},
            )


def test_progress_validation_does_not_reflect_attacker_controlled_field_names(
    api, project, work_payload
):
    work_item = create_work(api, project, work_payload, title="Sanitized validation")["work_item"]
    endpoint = item_path(project, work_item)
    before = events(api, project, work_item)["total"]
    root_key = "SENSITIVE_CALLER_KEY_123"
    root_value = "root-private-content-123"
    nested_key = "NESTED_PRIVATE_KEY_456"
    nested_value = "nested-private-content-456"
    base_payload = {
        "event_type": "progress",
        "body": "Safe progress body.",
        "metadata": {},
        "actor": actor("sanitized-validation"),
    }
    cases = [
        (
            {**base_payload, root_key: root_value},
            ["body", "field"],
            (root_key, root_value),
        ),
        (
            {
                **base_payload,
                "actor": {**base_payload["actor"], nested_key: nested_value},
            },
            ["body", "actor", "field"],
            (nested_key, nested_value),
        ),
    ]

    for payload, expected_location, private_content in cases:
        response = api.post(f"{endpoint}/events", json=payload)
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == [
            {
                "type": "extra_forbidden",
                "loc": expected_location,
                "msg": "Extra inputs are not permitted.",
            }
        ]
        for value in private_content:
            assert value not in response.text

    assert events(api, project, work_item)["total"] == before
