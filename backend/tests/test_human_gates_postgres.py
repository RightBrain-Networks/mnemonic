"""Phase 7–8 human-gate and hierarchy integration contracts."""

import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session

import mnemonic_api.application.middleware as middleware_module
from mnemonic_api.services.relationships import _work_pointers
from mnemonic_api.services.work_context import _summary_inputs

from .report_fixtures import reported


def collection(project: dict) -> str:
    return f"/api/v1/projects/{project['id']}/work-items"


def create_work(api, project: dict, payload: dict, *, title: str) -> dict:
    response = api.post(collection(project), json={**payload, "title": title})
    assert response.status_code == 201, response.text
    return response.json()


def gate_path(project: dict, work: dict) -> str:
    return f"{collection(project)}/{work['work_item']['id']}/gates"


def gate_request(*, operation_id=None, question="Which boundary should this use?") -> dict:
    payload = {
        "question": question,
        "requested_by_client": "pytest-agent",
        "requested_by_session_id": "phase-7-agent",
        "requested_by_model": "test-model",
    }
    if operation_id is not None:
        payload["client_operation_id"] = str(operation_id)
    return payload


def resolution_payload(*, revision, operation_id=None) -> dict:
    payload = {
        "resolution": "Use the reviewed durable boundary.",
        "resolved_by_client": "dashboard",
        "resolved_by_session_id": "phase-7-human",
        "resolved_by_model": "human-ui",
        "reviewed_context_revision": revision,
    }
    if operation_id is not None:
        payload["client_operation_id"] = str(operation_id)
    return payload


@pytest.mark.postgres
def test_gate_request_capability_overlap_attention_resolution_and_replay(
    api,
    project,
    work_payload,
):
    created = create_work(api, project, work_payload, title="Needs one human choice")
    work = created["work_item"]
    path = gate_path(project, created)

    claim_body = {
        "holder_client": "pytest-agent",
        "holder_session_id": "phase-7-agent",
        "claim_request_id": "gate-overlap-claim",
    }
    claim = api.post(f"{collection(project)}/{work['id']}/claim", json=claim_body)
    assert claim.status_code == 200, claim.text

    request_operation_id = uuid4()
    requested = api.post(
        path,
        json=gate_request(operation_id=request_operation_id),
    )
    assert requested.status_code == 201, requested.text
    gate = requested.json()
    assert gate["status"] == "unresolved"
    assert gate["current_context_revision"] == {
        "work_version": 1,
        "context_checkpoint_id": created["initial_checkpoint"]["id"],
        "relationship_event_count": 0,
    }
    assert gate["context_changed_since_request"] is False

    context = api.get(f"{collection(project)}/{work['id']}/context")
    assert context.status_code == 200, context.text
    context_body = context.json()
    assert context_body["readiness"]["is_gated"] is True
    assert context_body["readiness"]["has_active_lease"] is True
    assert context_body["readiness"]["display_state"] == "waiting"
    assert context_body["unresolved_gate_total"] == 1
    assert context_body["unresolved_gates"][0]["id"] == gate["id"]

    ready = api.get(f"/api/v1/projects/{project['id']}/ready-work")
    assert ready.status_code == 200
    assert ready.json()["items"] == []

    replayed_claim = api.post(
        f"{collection(project)}/{work['id']}/claim",
        json=claim_body,
    )
    assert replayed_claim.status_code == 200
    assert replayed_claim.json() == claim.json()
    fresh_claim = api.post(
        f"{collection(project)}/{work['id']}/claim",
        json={
            "holder_client": "other-agent",
            "holder_session_id": "other-session",
            "claim_request_id": "other-claim",
        },
    )
    assert fresh_claim.status_code == 409
    assert fresh_claim.json()["detail"]["code"] == "work_gated"

    renewed = api.post(
        f"{collection(project)}/{work['id']}/renew-claim",
        json={"lease_token": claim.json()["lease_token"]},
    )
    assert renewed.status_code == 200, renewed.text
    released = api.post(
        f"{collection(project)}/{work['id']}/release-claim",
        json={"lease_token": claim.json()["lease_token"]},
    )
    assert released.status_code == 200, released.text
    assert released.json()["released"] is True

    blocked_completion = api.post(
        f"{collection(project)}/{work['id']}/complete",
        json=reported({"expected_version": 1, "checkpoint": work_payload["initial_checkpoint"]}),
    )
    assert blocked_completion.status_code == 409
    assert blocked_completion.json()["detail"]["code"] == "work_gated"
    blocked_terminal = api.patch(
        f"{collection(project)}/{work['id']}",
        json=reported({"expected_version": 1, "status": "wont-do"}, retirement=True),
    )
    assert blocked_terminal.status_code == 409
    assert blocked_terminal.json()["detail"]["code"] == "work_gated"
    blocked_delete = api.post(
        f"{collection(project)}/{work['id']}/delete",
        json={"expected_version": 1},
    )
    assert blocked_delete.status_code == 409
    assert blocked_delete.json()["detail"]["code"] == "work_gated"

    attention = api.get(f"/api/v1/projects/{project['id']}/human-attention")
    assert attention.status_code == 200, attention.text
    attention_body = attention.json()
    assert attention_body["total"] == 1
    assert attention_body["items"][0]["gate"]["id"] == gate["id"]
    assert attention_body["items"][0]["summary"]["readiness"]["is_gated"] is True
    count_only = api.get(
        f"/api/v1/projects/{project['id']}/human-attention",
        params={"limit": 0},
    )
    assert count_only.json() == {
        "items": [],
        "total": 1,
        "limit": 0,
        "next_cursor": None,
    }

    activity_before_resolution = api.get(
        f"{collection(project)}/{work['id']}"
    ).json()["work_item"]["updated_at"]
    resolve_operation_id = uuid4()
    resolved = api.post(
        f"{path}/{gate['id']}/resolve",
        json=resolution_payload(
            operation_id=resolve_operation_id,
            revision=gate["current_context_revision"],
        ),
    )
    assert resolved.status_code == 200, resolved.text
    resolved_gate = resolved.json()
    assert resolved_gate["status"] == "resolved"
    assert resolved_gate["context_changed_at_resolution"] is False
    activity_after_resolution = api.get(
        f"{collection(project)}/{work['id']}"
    ).json()["work_item"]["updated_at"]
    assert activity_after_resolution != activity_before_resolution
    immediate_resolution_replay = api.post(
        f"{path}/{gate['id']}/resolve",
        json=resolution_payload(
            operation_id=resolve_operation_id,
            revision=gate["current_context_revision"],
        ),
    )
    assert immediate_resolution_replay.status_code == 200
    assert immediate_resolution_replay.json() == resolved_gate
    assert api.get(f"{collection(project)}/{work['id']}").json()["work_item"][
        "updated_at"
    ] == activity_after_resolution

    request_replay = api.post(
        path,
        json=gate_request(operation_id=request_operation_id),
    )
    assert request_replay.status_code == 201
    assert request_replay.json() == gate

    current = api.get(f"{collection(project)}/{work['id']}/context").json()
    assert current["readiness"]["is_gated"] is False
    assert current["resolved_gate_total"] == 1
    assert current["recent_resolved_gates"][0]["id"] == gate["id"]
    assert api.get(f"/api/v1/projects/{project['id']}/human-attention").json()[
        "total"
    ] == 0

    events = api.get(f"{collection(project)}/{work['id']}/events").json()["items"]
    gate_events = [
        event for event in events if event["event_type"].startswith("human_attention_")
    ]
    assert [event["event_type"] for event in gate_events] == [
        "human_attention_requested",
        "human_attention_resolved",
    ]
    assert {event["metadata"]["gate_id"] for event in gate_events} == {gate["id"]}
    assert all("gate_id" not in event for event in gate_events)
    requested_events = api.get(
        f"{collection(project)}/{work['id']}/events",
        params={"event_type": "human_attention_requested"},
    )
    assert requested_events.status_code == 200, requested_events.text
    assert [event["event_type"] for event in requested_events.json()["items"]] == [
        "human_attention_requested"
    ]
    resolved_events = api.get(
        f"{collection(project)}/{work['id']}/events",
        params={"event_type": "human_attention_resolved"},
    )
    assert resolved_events.status_code == 200, resolved_events.text
    assert [event["event_type"] for event in resolved_events.json()["items"]] == [
        "human_attention_resolved"
    ]

    deleted = api.post(
        f"{collection(project)}/{work['id']}/delete",
        json={"expected_version": 1},
    )
    assert deleted.status_code == 200, deleted.text
    resolution_replay = api.post(
        f"{path}/{gate['id']}/resolve",
        json=resolution_payload(
            operation_id=resolve_operation_id,
            revision=gate["current_context_revision"],
        ),
    )
    assert resolution_replay.status_code == 200
    assert resolution_replay.json() == resolved_gate
    history = api.get(path)
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["status"] == "resolved"


@pytest.mark.postgres
def test_gate_mutations_publish_only_identifier_free_frames_and_value_free_outcomes(
    api,
    project,
    work_payload,
    caplog,
):
    question = "Private WebSocket audit question marker."
    answer = "Private WebSocket audit answer marker."
    request_operation_id = uuid4()
    resolve_operation_id = uuid4()
    middleware_module.logger.disabled = False
    caplog.set_level(logging.INFO, logger="mnemonic_api.application.middleware")

    with api.websocket_connect(
        "/api/v1/sync", headers={"origin": "http://localhost:3000"}
    ) as websocket:
        ready = websocket.receive_json()
        revision = ready["revision"]
        created = create_work(api, project, work_payload, title="Gate sync privacy")
        assert websocket.receive_json() == {
            "type": "invalidate",
            "revision": revision + 1,
            "scope": "work-items",
        }
        path = gate_path(project, created)
        requested = api.post(
            path,
            json=gate_request(operation_id=request_operation_id, question=question),
        )
        assert requested.status_code == 201, requested.text
        assert websocket.receive_json() == {
            "type": "invalidate",
            "revision": revision + 2,
            "scope": "work-items",
        }
        resolved = api.post(
            f"{path}/{requested.json()['id']}/resolve",
            json={
                **resolution_payload(
                    operation_id=resolve_operation_id,
                    revision=requested.json()["current_context_revision"],
                ),
                "resolution": answer,
            },
        )
        assert resolved.status_code == 200, resolved.text
        assert websocket.receive_json() == {
            "type": "invalidate",
            "revision": revision + 3,
            "scope": "work-items",
        }

    outcomes = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Client operation outcome")
    ]
    assert outcomes == [
        "Client operation outcome kind=create_work outcome=executed",
        "Client operation outcome kind=request_human_input outcome=executed",
        "Client operation outcome kind=resolve_human_input outcome=executed",
    ]
    for secret in (
        question,
        answer,
        str(request_operation_id),
        str(resolve_operation_id),
        api.headers["Authorization"].removeprefix("Bearer "),
    ):
        assert secret not in caplog.text


@pytest.mark.postgres
def test_gate_request_replay_precedes_conflict_detection(
    api,
    project,
    work_payload,
):
    created = create_work(api, project, work_payload, title="Replay gate request")
    path = gate_path(project, created)
    operation_id = uuid4()
    payload = gate_request(operation_id=operation_id)

    created_gate = api.post(path, json=payload)
    assert created_gate.status_code == 201, created_gate.text
    replay = api.post(path, json=payload)
    assert replay.status_code == 201
    assert replay.json() == created_gate.json()
    conflict = api.post(
        path,
        json={**payload, "question": "A genuinely different question."},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "client_operation_conflict"
    distinct_intent = api.post(
        path,
        json=gate_request(operation_id=uuid4()),
    )
    assert distinct_intent.status_code == 201, distinct_intent.text
    assert distinct_intent.json()["id"] != created_gate.json()["id"]


@pytest.mark.postgres
def test_resolution_is_bound_to_exact_reviewed_context_revision(
    api,
    project,
    work_payload,
    checkpoint_fields,
):
    created = create_work(api, project, work_payload, title="Review changing context")
    work = created["work_item"]
    path = gate_path(project, created)
    gate = api.post(path, json=gate_request()).json()

    changed = api.patch(
        f"{collection(project)}/{work['id']}",
        json={"expected_version": 1, "summary": "The work facts changed."},
    )
    assert changed.status_code == 200, changed.text

    stale = api.post(
        f"{path}/{gate['id']}/resolve",
        json=resolution_payload(
            operation_id=uuid4(),
            revision=gate["current_context_revision"],
        ),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "gate_context_changed"

    reviewed_b = api.get(f"{path}/{gate['id']}/context")
    assert reviewed_b.status_code == 200, reviewed_b.text
    gate_b = next(
        item
        for item in reviewed_b.json()["unresolved_gates"]
        if item["id"] == gate["id"]
    )
    revision_b = gate_b["current_context_revision"]
    assert revision_b["work_version"] == 2

    checkpoint_c = api.post(
        f"{collection(project)}/{work['id']}/checkpoints",
        json={**checkpoint_fields, "kind": "context"},
    )
    assert checkpoint_c.status_code == 201, checkpoint_c.text
    b_to_c = api.post(
        f"{path}/{gate['id']}/resolve",
        json=resolution_payload(operation_id=uuid4(), revision=revision_b),
    )
    assert b_to_c.status_code == 409
    assert b_to_c.json()["detail"]["code"] == "gate_context_changed"

    reviewed_c = api.get(f"{path}/{gate['id']}/context")
    gate_c = next(
        item
        for item in reviewed_c.json()["unresolved_gates"]
        if item["id"] == gate["id"]
    )
    revision_c = gate_c["current_context_revision"]
    assert revision_c["context_checkpoint_id"] == checkpoint_c.json()["id"]
    resolved = api.post(
        f"{path}/{gate['id']}/resolve",
        json=resolution_payload(operation_id=uuid4(), revision=revision_c),
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["resolved_context_revision"] == revision_c
    assert body["context_changed_at_resolution"] is True


@pytest.mark.postgres
def test_deferred_resolution_requires_fresh_review_and_scope_is_project_local(
    api,
    project,
    work_payload,
):
    created = create_work(api, project, work_payload, title="Defer a pending decision")
    work = created["work_item"]
    path = gate_path(project, created)
    gate = api.post(path, json=gate_request()).json()

    deferred = api.post(
        f"{collection(project)}/{work['id']}/defer",
        json={"expected_version": 1},
    )
    assert deferred.status_code == 200, deferred.text
    assert deferred.json()["status"] == "deferred"

    request_while_deferred = api.post(
        path,
        json=gate_request(question="Can deferred work ask another question?"),
    )
    assert request_while_deferred.status_code == 409
    assert request_while_deferred.json()["detail"]["code"] == "work_not_pending"

    stale = api.post(
        f"{path}/{gate['id']}/resolve",
        json=resolution_payload(revision=gate["current_context_revision"]),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "gate_context_changed"

    extra_field = api.post(
        f"{path}/{gate['id']}/resolve",
        json={
            **resolution_payload(revision=gate["current_context_revision"]),
            "unexpected": True,
        },
    )
    assert extra_field.status_code == 422

    other_project_response = api.post(
        "/api/v1/projects",
        json={"name": "Other gate scope"},
    )
    assert other_project_response.status_code == 201
    other_project = other_project_response.json()
    wrong_project = api.post(
        f"{collection(other_project)}/{work['id']}/gates/{gate['id']}/resolve",
        json=resolution_payload(revision=gate["current_context_revision"]),
    )
    assert wrong_project.status_code == 404
    assert wrong_project.json()["detail"]["code"] == "work_item_not_found"
    sibling = create_work(api, project, work_payload, title="Wrong gate work scope")
    wrong_work = api.post(
        f"{gate_path(project, sibling)}/{gate['id']}/resolve",
        json=resolution_payload(revision=gate["current_context_revision"]),
    )
    assert wrong_work.status_code == 404
    assert wrong_work.json()["detail"]["code"] == "gate_not_found"
    unknown_gate = api.post(
        f"{path}/{uuid4()}/resolve",
        json=resolution_payload(revision=gate["current_context_revision"]),
    )
    assert unknown_gate.status_code == 404
    assert unknown_gate.json()["detail"]["code"] == "gate_not_found"

    review = api.get(f"{path}/{gate['id']}/context")
    assert review.status_code == 200, review.text
    reviewed_gate = next(
        item
        for item in review.json()["unresolved_gates"]
        if item["id"] == gate["id"]
    )
    reviewed_revision = reviewed_gate["current_context_revision"]
    assert reviewed_revision["work_version"] == 2
    resolved = api.post(
        f"{path}/{gate['id']}/resolve",
        json=resolution_payload(revision=reviewed_revision),
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["context_changed_at_resolution"] is True


@pytest.mark.postgres
def test_gate_history_cursor_traverses_to_exhaustion(
    api,
    project,
    work_payload,
):
    created = create_work(api, project, work_payload, title="Traverse gate history")
    path = gate_path(project, created)
    gates = []
    for index in range(3):
        requested = api.post(
            path,
            json=gate_request(question=f"History decision {index + 1}?"),
        )
        assert requested.status_code == 201, requested.text
        gates.append(requested.json())

    cursor = None
    seen_ids = []
    for index in range(3):
        params = {"limit": 1}
        if cursor is not None:
            params["cursor"] = cursor
        response = api.get(path, params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == 3
        assert len(page["items"]) == 1
        seen_ids.append(page["items"][0]["id"])
        cursor = page["next_cursor"]
        assert (cursor is None) is (index == 2)

    assert seen_ids == [gate["id"] for gate in reversed(gates)]


@pytest.mark.postgres
def test_attention_filter_project_isolation_and_ancestor_path(
    api,
    project,
    work_payload,
):
    root = create_work(api, project, work_payload, title="Attention ancestor")
    child = create_work(api, project, work_payload, title="Nested attention target")
    standalone = create_work(api, project, work_payload, title="Second local gate")
    relationship = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "parent-child",
            "source_work_item_id": root["work_item"]["id"],
            "target_work_item_id": child["work_item"]["id"],
            "created_by_client": "pytest-agent",
            "created_by_session_id": "attention-ancestry",
            "created_by_model": "test-model",
        },
    )
    assert relationship.status_code == 200, relationship.text
    child_gate = api.post(gate_path(project, child), json=gate_request()).json()
    standalone_gate = api.post(
        gate_path(project, standalone),
        json=gate_request(question="Second local decision?"),
    ).json()

    other_project_response = api.post(
        "/api/v1/projects",
        json={"name": "Attention isolation project"},
    )
    assert other_project_response.status_code == 201
    other_project = other_project_response.json()
    foreign = create_work(api, other_project, work_payload, title="Foreign gate")
    foreign_gate = api.post(
        gate_path(other_project, foreign),
        json=gate_request(question="Foreign decision?"),
    ).json()

    local_attention = api.get(f"/api/v1/projects/{project['id']}/human-attention")
    assert local_attention.status_code == 200, local_attention.text
    assert local_attention.json()["total"] == 2
    assert {item["gate"]["id"] for item in local_attention.json()["items"]} == {
        child_gate["id"],
        standalone_gate["id"],
    }
    assert foreign_gate["id"] not in {
        item["gate"]["id"] for item in local_attention.json()["items"]
    }

    filtered = api.get(
        f"/api/v1/projects/{project['id']}/human-attention",
        params={"work_item_id": child["work_item"]["id"]},
    )
    assert filtered.status_code == 200, filtered.text
    filtered_page = filtered.json()
    assert filtered_page["total"] == 1
    assert filtered_page["items"][0]["gate"]["id"] == child_gate["id"]
    assert [
        item["id"] for item in filtered_page["items"][0]["summary"]["ancestor_path"]
    ] == [root["work_item"]["id"]]
    assert filtered_page["items"][0]["summary"]["ancestor_path_truncated"] is False

    blank_full_view = api.get(
        collection(project),
        params={"q": " \n ", "status": "all", "view": "full"},
    )
    assert blank_full_view.status_code == 200, blank_full_view.text
    child_summary = next(
        item["summary"]
        for item in blank_full_view.json()["items"]
        if item["summary"]["work_item"]["id"] == child["work_item"]["id"]
    )
    assert [item["id"] for item in child_summary["ancestor_path"]] == [
        root["work_item"]["id"]
    ]

    foreign_filter = api.get(
        f"/api/v1/projects/{project['id']}/human-attention",
        params={"work_item_id": foreign["work_item"]["id"]},
    )
    assert foreign_filter.status_code == 404
    assert foreign_filter.json()["detail"]["code"] == "work_item_not_found"
    unknown_filter = api.get(
        f"/api/v1/projects/{project['id']}/human-attention",
        params={"work_item_id": uuid4()},
    )
    assert unknown_filter.status_code == 404
    assert unknown_filter.json()["detail"]["code"] == "work_item_not_found"


@pytest.mark.postgres
def test_gate_slices_focus_and_immutable_attention_cursor(
    api,
    project,
    work_payload,
):
    created = create_work(api, project, work_payload, title="Many explicit decisions")
    path = gate_path(project, created)
    gates = []
    for index in range(21):
        response = api.post(
            path,
            json=gate_request(question=f"Decision {index + 1}?"),
        )
        assert response.status_code == 201, response.text
        gates.append(response.json())

    context = api.get(f"{collection(project)}/{created['work_item']['id']}/context")
    assert context.status_code == 200, context.text
    body = context.json()
    assert body["unresolved_gate_total"] == 21
    assert len(body["unresolved_gates"]) == 20
    assert body["omitted_unresolved_gate_count"] == 1
    assert gates[-1]["id"] not in {
        item["id"] for item in body["unresolved_gates"]
    }

    query_focus = api.get(
        f"{collection(project)}/{created['work_item']['id']}/context",
        params={"focus_gate_id": gates[-1]["id"]},
    )
    assert query_focus.status_code == 422

    focused = api.get(f"{path}/{gates[-1]['id']}/context")
    assert focused.status_code == 200, focused.text
    assert gates[-1]["id"] in {
        item["id"] for item in focused.json()["unresolved_gates"]
    }
    assert len(focused.json()["unresolved_gates"]) == 20

    first_page = api.get(
        f"/api/v1/projects/{project['id']}/human-attention",
        params={"limit": 1},
    )
    assert first_page.status_code == 200, first_page.text
    first_body = first_page.json()
    assert first_body["items"][0]["gate"]["id"] == gates[0]["id"]
    assert first_body["next_cursor"] is not None

    encoded_cursor = first_body["next_cursor"]
    raw_cursor = base64.urlsafe_b64decode(
        encoded_cursor + "=" * (-len(encoded_cursor) % 4)
    )
    cursor_payload = json.loads(raw_cursor.decode("ascii"))
    for override in ({"v": True}, {"last_sequence": 2**63}):
        malformed_payload = {**cursor_payload, **override}
        malformed_cursor = base64.urlsafe_b64encode(
            json.dumps(
                malformed_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).rstrip(b"=").decode("ascii")
        rejected = api.get(
            f"/api/v1/projects/{project['id']}/human-attention",
            params={"limit": 1, "cursor": malformed_cursor},
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "invalid_cursor"

    resolved = api.post(
        f"{path}/{gates[0]['id']}/resolve",
        json=resolution_payload(revision=gates[0]["current_context_revision"]),
    )
    assert resolved.status_code == 200, resolved.text
    second_page = api.get(
        f"/api/v1/projects/{project['id']}/human-attention",
        params={"limit": 1, "cursor": first_body["next_cursor"]},
    )
    assert second_page.status_code == 200, second_page.text
    assert second_page.json()["total"] == 20
    assert second_page.json()["items"][0]["gate"]["id"] == gates[1]["id"]

    wrong_endpoint_cursor = api.get(
        path,
        params={"cursor": first_body["next_cursor"]},
    )
    assert wrong_endpoint_cursor.status_code == 422
    assert wrong_endpoint_cursor.json()["detail"]["code"] == "invalid_cursor"
    resolved_history = api.get(path, params={"status": "resolved"})
    assert resolved_history.status_code == 200
    assert resolved_history.json()["total"] == 1
    assert resolved_history.json()["items"][0]["id"] == gates[0]["id"]


@pytest.mark.postgres
def test_paired_gate_history_is_independent_of_bounded_ordinary_event_recall(
    api,
    project,
    work_payload,
):
    created = create_work(api, project, work_payload, title="Keep paired decisions separate")
    work = created["work_item"]
    path = gate_path(project, created)
    answers = {}

    for index in range(21):
        requested = api.post(
            path,
            json=gate_request(question=f"Durable decision {index + 1}?"),
        )
        assert requested.status_code == 201, requested.text
        gate = requested.json()
        answer = f"Durable answer {index + 1}."
        resolved = api.post(
            f"{path}/{gate['id']}/resolve",
            json={
                **resolution_payload(revision=gate["current_context_revision"]),
                "resolution": answer,
            },
        )
        assert resolved.status_code == 200, resolved.text
        answers[gate["id"]] = answer

    event_path = f"{collection(project)}/{work['id']}/events"
    for index in range(25):
        appended = api.post(
            event_path,
            json={
                "event_type": "progress",
                "body": f"Unrelated progress {index + 1}.",
                "metadata": {"ordinal": index + 1},
                "actor": {
                    "actor_client": "pytest-agent",
                    "actor_session_id": "paired-recall",
                    "actor_model": "test-model",
                },
            },
        )
        assert appended.status_code == 201, appended.text

    context_response = api.get(
        f"{collection(project)}/{work['id']}/context",
        params={"recent_event_limit": 20},
    )
    assert context_response.status_code == 200, context_response.text
    context = context_response.json()
    assert len(context["recent_events"]) == 20
    assert {event["event_type"] for event in context["recent_events"]} == {"progress"}
    assert context["resolved_gate_total"] == 21
    assert len(context["recent_resolved_gates"]) == 20
    assert context["omitted_resolved_gate_count"] == 1
    for gate in context["recent_resolved_gates"]:
        assert gate["resolution"] == answers[gate["id"]]

    history_response = api.get(path, params={"status": "resolved", "limit": 100})
    assert history_response.status_code == 200, history_response.text
    history = history_response.json()
    assert history["total"] == 21
    assert len(history["items"]) == 21
    assert {gate["id"]: gate["resolution"] for gate in history["items"]} == answers


@pytest.mark.postgres
def test_gate_request_and_completion_race_has_one_valid_linearized_outcome(
    api,
    project,
    work_payload,
    postgres_engine,
):
    created = create_work(api, project, work_payload, title="Request or complete atomically")
    work = created["work_item"]
    path = gate_path(project, created)
    authorization = api.headers["Authorization"]
    start = Barrier(3)

    def request_gate():
        with TestClient(api.app) as client:
            start.wait(timeout=5)
            return client.post(
                path,
                json=gate_request(),
                headers={"Authorization": authorization},
            )

    def complete():
        with TestClient(api.app) as client:
            start.wait(timeout=5)
            return client.post(
                f"{collection(project)}/{work['id']}/complete",
                json=reported({
                    "expected_version": 1,
                    "checkpoint": work_payload["initial_checkpoint"],
                }),
                headers={"Authorization": authorization},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        request_future = pool.submit(request_gate)
        completion_future = pool.submit(complete)
        start.wait(timeout=5)
        requested = request_future.result(timeout=10)
        completed = completion_future.result(timeout=10)

    assert (requested.status_code, completed.status_code) in {
        (201, 409),
        (409, 200),
    }
    with postgres_engine.connect() as connection:
        status = connection.execute(
            text("SELECT status FROM work_items WHERE id = CAST(:id AS uuid)"),
            {"id": work["id"]},
        ).scalar_one()
        gate_count = connection.execute(
            text("SELECT count(*) FROM work_gates WHERE work_item_id = CAST(:id AS uuid)"),
            {"id": work["id"]},
        ).scalar_one()
    if requested.status_code == 201:
        assert completed.status_code == 409
        assert completed.json()["detail"]["code"] == "work_gated"
        assert (status, gate_count) == ("pending", 1)
    else:
        assert completed.status_code == 200
        assert requested.status_code == 409
        assert requested.json()["detail"]["code"] == "work_not_pending"
        assert (status, gate_count) == ("done", 0)


@pytest.mark.postgres
def test_summary_and_pointer_lease_state_share_one_database_timestamp(
    api,
    project,
    work_payload,
    postgres_engine,
):
    created = create_work(api, project, work_payload, title="One lease snapshot")
    work_id = created["work_item"]["id"]
    claimed = api.post(
        f"{collection(project)}/{work_id}/claim",
        json={
            "holder_client": "pytest-agent",
            "holder_session_id": "lease-snapshot",
            "claim_request_id": "lease-snapshot",
        },
    )
    assert claimed.status_code == 200, claimed.text

    lease_statements: list[tuple[str, dict]] = []

    def observe_lease_statement(
        connection, cursor, statement, parameters, context, executemany
    ):
        del connection, cursor, context, executemany
        if "FROM work_leases" in statement:
            lease_statements.append((statement, parameters))

    event.listen(postgres_engine, "before_cursor_execute", observe_lease_statement)
    try:
        with Session(postgres_engine) as database:
            as_of = database.scalar(text("SELECT transaction_timestamp()"))
            assert as_of is not None
            *_, active_leases, dropped_lease_ids = _summary_inputs(
                database, [UUID(work_id)], as_of=as_of
            )
            summary_lease_statements = list(lease_statements)
            lease_statements.clear()
            pointers = _work_pointers(database, [UUID(work_id)], as_of=as_of)
            pointer_lease_statements = list(lease_statements)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", observe_lease_statement)

    assert len(summary_lease_statements) == 1
    assert len(pointer_lease_statements) == 1
    assert as_of in summary_lease_statements[0][1].values()
    assert as_of in pointer_lease_statements[0][1].values()
    assert (UUID(work_id) in active_leases) != (UUID(work_id) in dropped_lease_ids)
    assert pointers[UUID(work_id)].readiness.has_active_lease is True
    assert pointers[UUID(work_id)].readiness.has_dropped_lease is False


@pytest.mark.postgres
def test_gate_secret_echo_precedes_receipts_and_allows_retained_public_ids(
    api,
    project,
    work_payload,
    postgres_engine,
):
    created = create_work(api, project, work_payload, title="Keep gate controls out of text")
    path = gate_path(project, created)
    operation_id = uuid4()
    echoed_operation = api.post(
        path,
        json=gate_request(
            operation_id=operation_id,
            question=f"Do not retain control {operation_id}.",
        ),
    )
    assert echoed_operation.status_code == 422
    assert echoed_operation.json()["detail"] == {
        "code": "gate_secret_echo",
        "message": "Credential or control data cannot appear in durable human-gate fields.",
        "context": {},
    }
    assert str(operation_id) not in echoed_operation.text

    bearer = api.headers["Authorization"].removeprefix("Bearer ")
    echoed_bearer = api.post(
        path,
        json=gate_request(question=f"Do not retain credential {bearer}."),
    )
    assert echoed_bearer.status_code == 422
    assert echoed_bearer.json()["detail"]["code"] == "gate_secret_echo"
    assert bearer not in echoed_bearer.text

    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM work_gates")).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 0
    retained_operation_id = uuid4()
    gate_response = api.post(
        path,
        json=gate_request(operation_id=retained_operation_id),
    )
    assert gate_response.status_code == 201, gate_response.text
    gate = gate_response.json()

    cross_gate_request_operation_id = uuid4()
    cross_reference_request = api.post(
        path,
        json=gate_request(
            operation_id=cross_gate_request_operation_id,
            question=(
                f"Review prior gate {gate['id']} and operation "
                f"{retained_operation_id}."
            ),
        ),
    )
    assert cross_reference_request.status_code == 201, cross_reference_request.text
    second_gate = cross_reference_request.json()

    resolution_operation_id = uuid4()
    echoed_resolution = api.post(
        f"{path}/{gate['id']}/resolve",
        json={
            **resolution_payload(
                operation_id=resolution_operation_id,
                revision=gate["current_context_revision"],
            ),
            "resolution": f"Do not retain control {resolution_operation_id}.",
        },
    )
    assert echoed_resolution.status_code == 422
    assert echoed_resolution.json()["detail"]["code"] == "gate_secret_echo"

    cross_reference_resolution = api.post(
        f"{path}/{gate['id']}/resolve",
        json={
            **resolution_payload(
                operation_id=uuid4(),
                revision=gate["current_context_revision"],
            ),
            "resolution": (
                f"This answer references gate {gate['id']}, gate "
                f"{second_gate['id']}, and operation {retained_operation_id}."
            ),
        },
    )
    assert cross_reference_resolution.status_code == 200, cross_reference_resolution.text

    current = api.get(path, params={"status": "unresolved"})
    assert current.status_code == 200
    assert [item["id"] for item in current.json()["items"]] == [second_gate["id"]]
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM work_gates")).scalar_one() == 2
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 3
        assert connection.execute(
            text(
                "SELECT count(*) FROM work_events "
                "WHERE event_type = 'human_attention_resolved'"
            )
        ).scalar_one() == 1


@pytest.mark.postgres
def test_gate_text_rejects_nul_before_any_durable_side_effect(
    api,
    project,
    work_payload,
    postgres_engine,
):
    created = create_work(api, project, work_payload, title="Reject invalid gate text")
    path = gate_path(project, created)

    invalid_request = api.post(
        path,
        json=gate_request(
            operation_id=uuid4(),
            question="Never store this NUL:\x00",
        ),
    )
    assert invalid_request.status_code == 422, invalid_request.text

    gate = api.post(path, json=gate_request()).json()
    invalid_resolution = api.post(
        f"{path}/{gate['id']}/resolve",
        json={
            **resolution_payload(
                operation_id=uuid4(),
                revision=gate["current_context_revision"],
            ),
            "resolution": "Never store this NUL:\x00",
        },
    )
    assert invalid_resolution.status_code == 422, invalid_resolution.text

    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM work_gates")).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM client_operations")
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM work_events "
                "WHERE event_type = 'human_attention_resolved'"
            )
        ).scalar_one() == 0


@pytest.mark.postgres
def test_gate_receipt_replay_precedes_later_retained_control_collisions(
    api,
    project,
    work_payload,
    postgres_engine,
):
    created = create_work(api, project, work_payload, title="Replay before later controls")
    path = gate_path(project, created)

    later_request_control = uuid4()
    request_operation_id = uuid4()
    original_request = gate_request(
        operation_id=request_operation_id,
        question=f"Choose the ordinary external UUID {later_request_control}.",
    )
    requested = api.post(path, json=original_request)
    assert requested.status_code == 201, requested.text
    requested_gate = requested.json()

    retained_request_control = api.post(
        path,
        json=gate_request(
            operation_id=later_request_control,
            question="Bind the later request-control collision.",
        ),
    )
    assert retained_request_control.status_code == 201, retained_request_control.text

    replayed_request = api.post(path, json=original_request)
    assert replayed_request.status_code == 201, replayed_request.text
    assert replayed_request.json() == requested_gate

    later_resolution_control = uuid4()
    resolution_operation_id = uuid4()
    original_resolution = {
        **resolution_payload(
            operation_id=resolution_operation_id,
            revision=requested_gate["current_context_revision"],
        ),
        "resolution": f"Record the ordinary external UUID {later_resolution_control}.",
    }
    resolved = api.post(
        f"{path}/{requested_gate['id']}/resolve",
        json=original_resolution,
    )
    assert resolved.status_code == 200, resolved.text
    resolved_gate = resolved.json()

    retained_resolution_control = api.post(
        path,
        json=gate_request(
            operation_id=later_resolution_control,
            question="Bind the later resolution-control collision.",
        ),
    )
    assert retained_resolution_control.status_code == 201, retained_resolution_control.text

    replayed_resolution = api.post(
        f"{path}/{requested_gate['id']}/resolve",
        json=original_resolution,
    )
    assert replayed_resolution.status_code == 200, replayed_resolution.text
    assert replayed_resolution.json() == resolved_gate

    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM work_gates")).scalar_one() == 3
        assert connection.execute(
            text(
                "SELECT count(*) FROM client_operations "
                "WHERE client_operation_id IN ("
                ":request_operation_id, :later_request_control, "
                ":resolution_operation_id, :later_resolution_control)"
            ),
            {
                "request_operation_id": request_operation_id,
                "later_request_control": later_request_control,
                "resolution_operation_id": resolution_operation_id,
                "later_resolution_control": later_resolution_control,
            },
        ).scalar_one() == 4
        assert connection.execute(
            text(
                "SELECT count(*) FROM work_events "
                "WHERE event_type = 'human_attention_resolved'"
            )
        ).scalar_one() == 1


@pytest.mark.postgres
def test_same_and_different_key_resolution_races_are_linearizable(
    api,
    project,
    work_payload,
    postgres_engine,
):
    created = create_work(api, project, work_payload, title="Resolve exactly once")
    path = gate_path(project, created)
    authorization = api.headers["Authorization"]

    def race(gate_id, bodies):
        start = Barrier(len(bodies) + 1)

        def resolve(body):
            with TestClient(api.app) as client:
                start.wait(timeout=5)
                return client.post(
                    f"{path}/{gate_id}/resolve",
                    json=body,
                    headers={"Authorization": authorization},
                )

        with ThreadPoolExecutor(max_workers=len(bodies)) as pool:
            futures = [pool.submit(resolve, body) for body in bodies]
            start.wait(timeout=5)
            return [future.result(timeout=10) for future in futures]

    first_gate = api.post(path, json=gate_request()).json()
    same_body = resolution_payload(
        operation_id=uuid4(),
        revision=first_gate["current_context_revision"],
    )
    same_key = race(first_gate["id"], [same_body, same_body])
    assert [response.status_code for response in same_key] == [200, 200]
    assert same_key[0].json() == same_key[1].json()

    second_gate = api.post(
        path,
        json=gate_request(question="Which independent winner should resolve this?"),
    ).json()
    different_bodies = [
        {
            **resolution_payload(
                operation_id=uuid4(),
                revision=second_gate["current_context_revision"],
            ),
            "resolution": f"Answer from contender {index}.",
        }
        for index in range(2)
    ]
    different_keys = race(second_gate["id"], different_bodies)
    assert sorted(response.status_code for response in different_keys) == [200, 409]
    winner_index = next(
        index for index, response in enumerate(different_keys)
        if response.status_code == 200
    )
    loser_index = 1 - winner_index
    assert different_keys[loser_index].json()["detail"]["code"] == (
        "gate_already_resolved"
    )
    loser_retry = api.post(
        f"{path}/{second_gate['id']}/resolve",
        json=different_bodies[loser_index],
    )
    assert loser_retry.status_code == 409
    assert loser_retry.json()["detail"]["code"] == "gate_already_resolved"

    with postgres_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM client_operations "
                "WHERE operation_kind = 'resolve_human_input'"
            )
        ).scalar_one() == 2
        for gate_id in (first_gate["id"], second_gate["id"]):
            assert connection.execute(
                text(
                    "SELECT count(*) FROM work_events "
                    "WHERE event_type = 'human_attention_resolved' "
                    "AND gate_id = CAST(:gate_id AS uuid)"
                ),
                {"gate_id": gate_id},
            ).scalar_one() == 1


@pytest.mark.postgres
def test_hierarchy_presentation_uses_structural_descendants_and_explicit_discovery(
    api,
    project,
    work_payload,
    checkpoint_fields,
):
    root = create_work(api, project, work_payload, title="Root workstream")
    child = create_work(api, project, work_payload, title="Active discovered child")
    grandchild = create_work(api, project, work_payload, title="Completed grandchild")

    def relationship(kind, source, target, checkpoint_id=None):
        payload = {
            "relationship_type": kind,
            "source_work_item_id": source["work_item"]["id"],
            "target_work_item_id": target["work_item"]["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "phase-8-hierarchy",
        }
        if checkpoint_id is not None:
            payload["context_checkpoint_id"] = checkpoint_id
        response = api.post(
            f"/api/v1/projects/{project['id']}/relationships",
            json=payload,
        )
        assert response.status_code == 200, response.text

    relationship("parent-child", root, child)
    relationship("parent-child", child, grandchild)
    relationship(
        "discovered-from",
        child,
        root,
        root["initial_checkpoint"]["id"],
    )

    claim = api.post(
        f"{collection(project)}/{child['work_item']['id']}/claim",
        json={
            "holder_client": "pytest",
            "holder_session_id": "phase-8-active",
            "claim_request_id": "phase-8-active",
        },
    )
    assert claim.status_code == 200, claim.text
    completed = api.post(
        f"{collection(project)}/{grandchild['work_item']['id']}/complete",
        json=reported({
            "expected_version": 1,
            "checkpoint": checkpoint_fields,
        }),
    )
    assert completed.status_code == 200, completed.text

    for index in range(2):
        response = api.post(
            gate_path(project, root),
            json=gate_request(question=f"Root decision {index + 1}?"),
        )
        assert response.status_code == 201, response.text

    roots = api.get(
        collection(project),
        params={"view": "roots", "status": "all"},
    )
    assert roots.status_code == 200, roots.text
    root_entry = next(
        item
        for item in roots.json()["items"]
        if item["summary"]["work_item"]["id"] == root["work_item"]["id"]
    )
    presentation = root_entry["presentation"]
    assert presentation["direct_child_count"] == 1
    assert presentation["descendant_count"] == 2
    assert presentation["active_descendant_count"] == 1
    assert presentation["completed_descendant_count"] == 1
    assert presentation["discovered_descendant_count"] == 1
    assert presentation["branch_unresolved_human_gate_count"] == 2
    assert presentation["is_discovered_work"] is False
    assert presentation["discovered_from_parent"] is False
    assert presentation["next_active_descendant_lease_expires_at"] == claim.json()[
        "expires_at"
    ]
    assert root_entry["summary"]["readiness"]["display_state"] == "waiting"

    children = api.get(
        f"{collection(project)}/{root['work_item']['id']}/children",
        params={"status": "all"},
    )
    assert children.status_code == 200, children.text
    child_entry = children.json()["items"][0]
    assert child_entry["presentation"]["descendant_count"] == 1
    assert child_entry["presentation"]["completed_descendant_count"] == 1
    assert child_entry["presentation"]["is_discovered_work"] is True
    assert child_entry["presentation"]["discovered_from_parent"] is True


@pytest.mark.postgres
def test_hierarchy_recursive_rollup_is_bounded_for_corrupt_cycles(
    api,
    project,
    work_payload,
    postgres_engine,
):
    self_node = create_work(api, project, work_payload, title="Corrupt self cycle")[
        "work_item"
    ]
    left = create_work(api, project, work_payload, title="Corrupt cycle left")[
        "work_item"
    ]
    right = create_work(api, project, work_payload, title="Corrupt cycle right")[
        "work_item"
    ]
    relationship_ids = [uuid4(), uuid4(), uuid4()]
    insert = text(
        "INSERT INTO work_relationships "
        "(id, project_id, relationship_type, source_work_item_id, "
        "target_work_item_id, created_by_client, created_by_session_id) "
        "VALUES "
        "(CAST(:self_id AS uuid), CAST(:project_id AS uuid), 'parent-child', "
        " CAST(:self_node AS uuid), CAST(:self_node AS uuid), 'corruption-test', 'self'), "
        "(CAST(:left_id AS uuid), CAST(:project_id AS uuid), 'parent-child', "
        " CAST(:left AS uuid), CAST(:right AS uuid), 'corruption-test', 'left'), "
        "(CAST(:right_id AS uuid), CAST(:project_id AS uuid), 'parent-child', "
        " CAST(:right AS uuid), CAST(:left AS uuid), 'corruption-test', 'right')"
    )
    parameters = {
        "self_id": relationship_ids[0],
        "left_id": relationship_ids[1],
        "right_id": relationship_ids[2],
        "project_id": project["id"],
        "self_node": self_node["id"],
        "left": left["id"],
        "right": right["id"],
    }
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE work_relationships "
                    "DROP CONSTRAINT ck_work_relationships_endpoints_differ"
                )
            )
            connection.execute(
                text("ALTER TABLE work_relationships DISABLE TRIGGER USER")
            )
        with postgres_engine.begin() as connection:
            connection.execute(insert, parameters)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE work_relationships ENABLE TRIGGER USER")
            )

        self_children = api.get(
            f"{collection(project)}/{self_node['id']}/children"
        )
        cycle_children = api.get(f"{collection(project)}/{left['id']}/children")
        assert self_children.status_code == 200, self_children.text
        assert cycle_children.status_code == 200, cycle_children.text
        assert [
            item["summary"]["work_item"]["id"]
            for item in self_children.json()["items"]
        ] == [self_node["id"]]
        assert [
            item["summary"]["work_item"]["id"]
            for item in cycle_children.json()["items"]
        ] == [right["id"]]
        assert cycle_children.json()["items"][0]["presentation"][
            "descendant_count"
        ] == 1
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE work_relationships DISABLE TRIGGER USER")
            )
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM work_relationships "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": relationship_ids},
            )
        with postgres_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE work_relationships ENABLE TRIGGER USER")
            )
            connection.execute(
                text(
                    "ALTER TABLE work_relationships ADD CONSTRAINT "
                    "ck_work_relationships_endpoints_differ "
                    "CHECK (source_work_item_id <> target_work_item_id)"
                )
            )
