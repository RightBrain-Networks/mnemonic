"""Cross-surface human-gate readiness and lifecycle contracts on PostgreSQL."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.postgres


BlockerState = Literal["none", "resolved", "unresolved"]
LeaseState = Literal["none", "active", "expired"]
LifecycleState = Literal["pending", "deferred", "wont-do"]


@dataclass(frozen=True)
class ReadinessCase:
    name: str
    lifecycle: LifecycleState
    blocker: BlockerState
    lease: LeaseState
    unresolved_gates: int
    resolved_gates: int
    expected_terminal: bool
    expected_active_lease: bool
    expected_dropped_lease: bool
    expected_blocker_count: int
    expected_gate_count: int
    expected_ready: bool
    expected_display_state: str


CASES = (
    ReadinessCase(
        name="pending-resolved-gate-resolved-blocker-no-lease",
        lifecycle="pending",
        blocker="resolved",
        lease="none",
        unresolved_gates=0,
        resolved_gates=1,
        expected_terminal=False,
        expected_active_lease=False,
        expected_dropped_lease=False,
        expected_blocker_count=0,
        expected_gate_count=0,
        expected_ready=True,
        expected_display_state="pending",
    ),
    ReadinessCase(
        name="pending-unresolved-gate-no-blocker-active-lease",
        lifecycle="pending",
        blocker="none",
        lease="active",
        unresolved_gates=1,
        resolved_gates=0,
        expected_terminal=False,
        expected_active_lease=True,
        expected_dropped_lease=False,
        expected_blocker_count=0,
        expected_gate_count=1,
        expected_ready=False,
        expected_display_state="waiting",
    ),
    ReadinessCase(
        name="pending-two-gates-unresolved-blocker-expired-lease",
        lifecycle="pending",
        blocker="unresolved",
        lease="expired",
        unresolved_gates=2,
        resolved_gates=0,
        expected_terminal=False,
        expected_active_lease=False,
        expected_dropped_lease=True,
        expected_blocker_count=1,
        expected_gate_count=2,
        expected_ready=False,
        expected_display_state="waiting",
    ),
    ReadinessCase(
        name="pending-resolved-gate-unresolved-blocker-active-lease",
        lifecycle="pending",
        blocker="unresolved",
        lease="active",
        unresolved_gates=0,
        resolved_gates=1,
        expected_terminal=False,
        expected_active_lease=True,
        expected_dropped_lease=False,
        expected_blocker_count=1,
        expected_gate_count=0,
        expected_ready=False,
        expected_display_state="blocked",
    ),
    ReadinessCase(
        name="pending-resolved-gate-no-blocker-expired-lease",
        lifecycle="pending",
        blocker="none",
        lease="expired",
        unresolved_gates=0,
        resolved_gates=1,
        expected_terminal=False,
        expected_active_lease=False,
        expected_dropped_lease=True,
        expected_blocker_count=0,
        expected_gate_count=0,
        expected_ready=True,
        expected_display_state="dropped",
    ),
    ReadinessCase(
        name="deferred-unresolved-gate-unresolved-blocker-no-lease",
        lifecycle="deferred",
        blocker="unresolved",
        lease="none",
        unresolved_gates=1,
        resolved_gates=0,
        expected_terminal=False,
        expected_active_lease=False,
        expected_dropped_lease=False,
        expected_blocker_count=1,
        expected_gate_count=1,
        expected_ready=False,
        expected_display_state="deferred",
    ),
    ReadinessCase(
        name="deferred-resolved-gate-resolved-blocker-no-lease",
        lifecycle="deferred",
        blocker="resolved",
        lease="none",
        unresolved_gates=0,
        resolved_gates=1,
        expected_terminal=False,
        expected_active_lease=False,
        expected_dropped_lease=False,
        expected_blocker_count=0,
        expected_gate_count=0,
        expected_ready=False,
        expected_display_state="deferred",
    ),
    ReadinessCase(
        name="terminal-resolved-gate-unresolved-blocker-no-lease",
        lifecycle="wont-do",
        blocker="unresolved",
        lease="none",
        unresolved_gates=0,
        resolved_gates=1,
        expected_terminal=True,
        expected_active_lease=False,
        expected_dropped_lease=False,
        expected_blocker_count=1,
        expected_gate_count=0,
        expected_ready=False,
        expected_display_state="wont-do",
    ),
)


def collection(project: dict) -> str:
    return f"/api/v1/projects/{project['id']}/work-items"


def item_path(project: dict, work_item: dict) -> str:
    return f"{collection(project)}/{work_item['id']}"


def create_work(api, project: dict, payload: dict, *, title: str, tag: str) -> dict:
    body = deepcopy(payload)
    body["title"] = title
    body["initial_checkpoint"]["source_session_id"] = f"matrix-{uuid4().hex}"
    body["initial_checkpoint"]["tags"] = [tag]
    response = api.post(collection(project), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def add_relationship(
    api,
    project: dict,
    *,
    relationship_type: str,
    source_work_item_id: str,
    target_work_item_id: str,
) -> dict:
    response = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": relationship_type,
            "source_work_item_id": source_work_item_id,
            "target_work_item_id": target_work_item_id,
            "created_by_client": "pytest",
            "created_by_session_id": "readiness-matrix",
            "created_by_model": "test-model",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["relationship"]


def claim_payload(request_id: str) -> dict:
    return {
        "holder_client": "pytest-agent",
        "holder_session_id": "readiness-matrix",
        "claim_request_id": request_id,
    }


def gate_request(question: str) -> dict:
    return {
        "question": question,
        "requested_by_client": "pytest-agent",
        "requested_by_session_id": "readiness-matrix",
        "requested_by_model": "test-model",
    }


def gate_resolution(answer: str) -> dict:
    return {
        "resolution": answer,
        "resolved_by_client": "dashboard",
        "resolved_by_session_id": "readiness-matrix-human",
        "resolved_by_model": None,
        "acknowledge_context_change": False,
    }


def completion_payload(work_payload: dict, prompt: str) -> dict:
    checkpoint = deepcopy(work_payload["initial_checkpoint"])
    checkpoint["prompt"] = prompt
    checkpoint["source_session_id"] = f"matrix-completion-{uuid4().hex}"
    return {"expected_version": 1, "checkpoint": checkpoint}


def expire_lease(postgres_engine, work_item_id: str) -> None:
    with postgres_engine.begin() as connection:
        result = connection.execute(
            text(
                """
                UPDATE work_leases
                SET acquired_at = clock_timestamp() - interval '3 seconds',
                    renewed_at = clock_timestamp() - interval '2 seconds',
                    expires_at = clock_timestamp() - interval '1 second'
                WHERE work_item_id = CAST(:work_item_id AS uuid)
                """
            ),
            {"work_item_id": work_item_id},
        )
        assert result.rowcount == 1


def public_lease(receipt: dict) -> dict:
    return {
        key: receipt[key]
        for key in (
            "holder_client",
            "holder_session_id",
            "acquired_at",
            "renewed_at",
            "expires_at",
        )
    }


def expected_readiness(case: ReadinessCase, receipt: dict | None) -> dict:
    if case.expected_active_lease:
        assert receipt is not None
        active_lease = public_lease(receipt)
    else:
        active_lease = None
    return {
        "lifecycle_status": case.lifecycle,
        "is_terminal": case.expected_terminal,
        "has_active_lease": case.expected_active_lease,
        "has_dropped_lease": case.expected_dropped_lease,
        "active_lease": active_lease,
        "unresolved_blocker_count": case.expected_blocker_count,
        "is_blocked": case.expected_blocker_count > 0,
        "unresolved_gate_count": case.expected_gate_count,
        "is_gated": case.expected_gate_count > 0,
        "is_ready": case.expected_ready,
        "display_state": case.expected_display_state,
    }


def minimal_summary(work_item: dict, *, display_state: str) -> dict:
    return {
        "work_item": {
            key: work_item[key]
            for key in ("id", "title", "status", "priority", "version", "updated_at")
        },
        "checkpoint_count": 1,
        "display_state": display_state,
    }


def counterpart_from_context(context: dict, work_item_id: str) -> dict:
    adjacent = (
        context["incoming_relationships"]
        + context["outgoing_relationships"]
        + context["undirected_relationships"]
    )
    matches = [
        relationship["counterpart"]
        for relationship in adjacent
        if relationship["counterpart"]["id"] == work_item_id
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_readiness_lifecycle_matrix_agrees_across_every_public_projection(
    api,
    project,
    work_payload,
    postgres_engine,
    case: ReadinessCase,
):
    api.app.state.settings.human_gate_requests_enabled = True
    unique = uuid4().hex[:10]
    tag = f"readiness-{unique}"
    target_created = create_work(
        api,
        project,
        work_payload,
        title=f"Readiness target {case.name} {unique}",
        tag=tag,
    )
    target = target_created["work_item"]
    target_path = item_path(project, target)

    watcher = create_work(
        api,
        project,
        work_payload,
        title=f"Projection watcher {unique}",
        tag=f"watcher-{unique}",
    )["work_item"]
    add_relationship(
        api,
        project,
        relationship_type="related",
        source_work_item_id=watcher["id"],
        target_work_item_id=target["id"],
    )

    # Acquire first: an active/retained lease may legitimately overlap with a
    # blocker added later, while a fresh claim after that blocker must fail.
    receipt = None
    if case.lease != "none":
        claimed = api.post(
            f"{target_path}/claim",
            json=claim_payload(f"matrix-{case.name}-{unique}"),
        )
        assert claimed.status_code == 200, claimed.text
        receipt = claimed.json()

    if case.blocker != "none":
        blocker = create_work(
            api,
            project,
            work_payload,
            title=f"Blocker {case.name} {unique}",
            tag=f"blocker-{unique}",
        )["work_item"]
        if case.blocker == "resolved":
            completed = api.post(
                f"{item_path(project, blocker)}/complete",
                json=completion_payload(work_payload, f"Resolved blocker {unique}."),
            )
            assert completed.status_code == 200, completed.text
            assert completed.json()["work_item"]["status"] == "done"
        add_relationship(
            api,
            project,
            relationship_type="blocks",
            source_work_item_id=blocker["id"],
            target_work_item_id=target["id"],
        )

    if case.lease == "expired":
        # Establish retained expiry before the gate exists. The database backstop
        # intentionally rejects any later lease-row mutation while gated.
        expire_lease(postgres_engine, target["id"])

    gates_path = f"{target_path}/gates"
    resolved_gate_ids = []
    for index in range(case.resolved_gates):
        requested = api.post(
            gates_path,
            json=gate_request(f"Resolved matrix question {index} for {unique}?"),
        )
        assert requested.status_code == 201, requested.text
        gate = requested.json()
        resolved = api.post(
            f"{gates_path}/{gate['id']}/resolve",
            json=gate_resolution(f"Resolved matrix answer {index} for {unique}."),
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["status"] == "resolved"
        resolved_gate_ids.append(gate["id"])

    unresolved_gate_ids = []
    for index in range(case.unresolved_gates):
        requested = api.post(
            gates_path,
            json=gate_request(f"Unresolved matrix question {index} for {unique}?"),
        )
        assert requested.status_code == 201, requested.text
        assert requested.json()["status"] == "unresolved"
        unresolved_gate_ids.append(requested.json()["id"])

    if case.lifecycle == "deferred":
        deferred = api.post(f"{target_path}/defer", json={"expected_version": 1})
        assert deferred.status_code == 200, deferred.text
        assert deferred.json()["status"] == "deferred"
    elif case.lifecycle == "wont-do":
        retired = api.patch(
            target_path,
            json={"expected_version": 1, "status": "wont-do"},
        )
        assert retired.status_code == 200, retired.text
        assert retired.json()["status"] == "wont-do"

    expected = expected_readiness(case, receipt)
    context_response = api.get(f"{target_path}/context")
    assert context_response.status_code == 200, context_response.text
    context = context_response.json()
    assert context["readiness"] == expected
    assert context["unresolved_gate_total"] == case.unresolved_gates
    assert context["omitted_unresolved_gate_count"] == 0
    assert {gate["id"] for gate in context["unresolved_gates"]} == set(
        unresolved_gate_ids
    )
    assert context["resolved_gate_total"] == case.resolved_gates
    assert context["omitted_resolved_gate_count"] == 0
    assert {gate["id"] for gate in context["recent_resolved_gates"]} == set(
        resolved_gate_ids
    )

    minimal = api.get(
        collection(project),
        params={"status": "all", "tag": tag, "view": "minimal"},
    )
    assert minimal.status_code == 200, minimal.text
    expected_minimal = minimal_summary(
        context["work_item"], display_state=case.expected_display_state
    )
    assert minimal.json() == {
        "items": [expected_minimal],
        "total": 1,
        "limit": 30,
        "offset": 0,
    }

    full = api.get(
        collection(project),
        params={"status": "all", "tag": tag, "view": "full"},
    )
    assert full.status_code == 200, full.text
    full_page = full.json()
    assert full_page["total"] == 1
    assert full_page["limit"] == 30
    assert full_page["offset"] == 0
    assert len(full_page["items"]) == 1
    full_summary = full_page["items"][0]
    assert set(full_summary) == {
        "work_item",
        "checkpoint_count",
        "ancestor_path",
        "ancestor_path_truncated",
        "current_context",
        "readiness",
    }
    assert full_summary["work_item"] == context["work_item"]
    assert full_summary["checkpoint_count"] == 1
    assert full_summary["readiness"] == expected

    ready = api.get(
        f"/api/v1/projects/{project['id']}/ready-work",
        params={"tag": tag},
    )
    assert ready.status_code == 200, ready.text
    assert ready.json() == {
        "items": [expected_minimal] if case.expected_ready else [],
        "total": 1 if case.expected_ready else 0,
        "limit": 30,
        "offset": 0,
    }

    watcher_context = api.get(f"{item_path(project, watcher)}/context")
    assert watcher_context.status_code == 200, watcher_context.text
    assert counterpart_from_context(watcher_context.json(), target["id"]) == {
        "id": target["id"],
        "title": context["work_item"]["title"],
        "status": case.lifecycle,
        "readiness": expected,
    }

    if case.lifecycle == "deferred":
        forbidden_terminal = api.patch(
            target_path,
            json={"expected_version": 2, "status": "wont-do"},
        )
        assert forbidden_terminal.status_code == 409
        assert forbidden_terminal.json() == {
            "detail": {
                "code": "invalid_status_transition",
                "message": "That lifecycle transition is not allowed.",
                "context": {},
            }
        }
        restored = api.patch(
            target_path,
            json={"expected_version": 2, "status": "pending"},
        )
        assert restored.status_code == 200, restored.text
        restored_readiness = api.get(f"{target_path}/context").json()["readiness"]
        assert restored_readiness["is_gated"] is (case.unresolved_gates > 0)
        assert restored_readiness["is_blocked"] is (case.expected_blocker_count > 0)
        assert restored_readiness["display_state"] == (
            "waiting"
            if case.unresolved_gates
            else "blocked"
            if case.expected_blocker_count
            else "pending"
        )
    elif case.lifecycle == "wont-do":
        terminal_request = api.post(
            gates_path,
            json=gate_request(f"Terminal work cannot ask a new question {unique}?"),
        )
        assert terminal_request.status_code == 409
        assert terminal_request.json() == {
            "detail": {
                "code": "work_not_pending",
                "message": "Only pending work can request human input.",
                "context": {},
            }
        }


def assert_conflict(response, *, code: str, message: str, context: dict | None = None) -> None:
    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": {
            "code": code,
            "message": message,
            "context": context or {},
        }
    }


def test_overlapping_gate_blocker_and_active_lease_errors_have_stable_precedence(
    api,
    project,
    work_payload,
):
    api.app.state.settings.human_gate_requests_enabled = True
    unique = uuid4().hex[:10]
    target = create_work(
        api,
        project,
        work_payload,
        title=f"Overlapping conflicts {unique}",
        tag=f"overlap-{unique}",
    )["work_item"]
    target_path = item_path(project, target)
    original_claim = claim_payload(f"overlap-original-{unique}")
    claimed = api.post(f"{target_path}/claim", json=original_claim)
    assert claimed.status_code == 200, claimed.text
    receipt = claimed.json()

    blocker = create_work(
        api,
        project,
        work_payload,
        title=f"Overlapping blocker {unique}",
        tag=f"overlap-blocker-{unique}",
    )["work_item"]
    add_relationship(
        api,
        project,
        relationship_type="blocks",
        source_work_item_id=blocker["id"],
        target_work_item_id=target["id"],
    )
    requested = api.post(
        f"{target_path}/gates",
        json=gate_request(f"Which overlapping conflict wins for {unique}?"),
    )
    assert requested.status_code == 201, requested.text
    gate = requested.json()

    replayed = api.post(f"{target_path}/claim", json=original_claim)
    assert replayed.status_code == 200
    assert replayed.json() == receipt

    different_claim = api.post(
        f"{target_path}/claim",
        json=claim_payload(f"overlap-different-{unique}"),
    )
    assert_conflict(
        different_claim,
        code="work_blocked",
        message="This work item has an unresolved blocker.",
    )
    completion = api.post(
        f"{target_path}/complete",
        json=completion_payload(work_payload, f"Cannot complete overlap {unique}."),
    )
    assert_conflict(
        completion,
        code="work_blocked",
        message="This work item has an unresolved blocker.",
    )
    terminal = api.patch(
        target_path,
        json={"expected_version": 1, "status": "wont-do"},
    )
    assert_conflict(
        terminal,
        code="work_gated",
        message=(
            "This work item has unresolved human input. "
            "Review and resolve every gate first."
        ),
    )
    deletion = api.post(f"{target_path}/delete", json={"expected_version": 1})
    assert_conflict(
        deletion,
        code="active_relationships",
        message="Remove this work item's relationships before deleting it.",
    )
    deferral = api.post(f"{target_path}/defer", json={"expected_version": 1})
    assert_conflict(
        deferral,
        code="lease_held",
        message="Active work cannot be deferred until its lease is released or expires.",
        context={
            "holder_client": receipt["holder_client"],
            "expires_at": receipt["expires_at"],
        },
    )

    renewed = api.post(
        f"{target_path}/renew-claim",
        json={"lease_token": receipt["lease_token"]},
    )
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["lease_token"] == receipt["lease_token"]
    released = api.post(
        f"{target_path}/release-claim",
        json={"lease_token": receipt["lease_token"]},
    )
    assert released.status_code == 200, released.text
    assert released.json() == {"work_item_id": target["id"], "released": True}

    still_blocked = api.post(
        f"{target_path}/claim",
        json=claim_payload(f"overlap-after-release-{unique}"),
    )
    assert_conflict(
        still_blocked,
        code="work_blocked",
        message="This work item has an unresolved blocker.",
    )

    resolved = api.post(
        f"{target_path}/gates/{gate['id']}/resolve",
        json=gate_resolution(f"Reviewed overlapping conflict {unique}."),
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"
    blocked_after_resolution = api.post(
        f"{target_path}/claim",
        json=claim_payload(f"overlap-after-resolution-{unique}"),
    )
    assert_conflict(
        blocked_after_resolution,
        code="work_blocked",
        message="This work item has an unresolved blocker.",
    )
