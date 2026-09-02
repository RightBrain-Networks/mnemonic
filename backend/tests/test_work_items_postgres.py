"""Phase 1 canonical work/checkpoint API and database invariants."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from mnemonic_api.models import Checkpoint, WorkItem

pytestmark = pytest.mark.postgres


def collection(project):
    return f"/api/v1/projects/{project['id']}/work-items"


def item_path(project, work_item):
    return f"{collection(project)}/{work_item['id']}"


def create_work(api, project, payload, **changes):
    response = api.post(collection(project), json={**payload, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def checkpoint_payload(prompt, session_id, *, kind="context", tags=None):
    return {
        "kind": kind,
        "prompt": prompt,
        "source_client": "claude-code",
        "source_session_id": session_id,
        "source_model": "test-model",
        "repository_branch": "feature/work-graph",
        "verified_against": "abcdef1",
        "tags": tags or [],
        "source_metadata": {"session": session_id},
    }


def test_successful_mutation_publishes_live_sync_invalidation(api, project, work_payload):
    with api.websocket_connect(
        "/api/v1/sync", headers={"origin": "http://localhost:3000"}
    ) as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "ready"
        revision = ready["revision"]
        created = create_work(api, project, work_payload)
        work_item = created["work_item"]
        assert websocket.receive_json() == {
            "type": "invalidate",
            "revision": revision + 1,
            "scope": "work-items",
        }
        api.patch(
            item_path(project, work_item),
            json={"expected_version": 1, "title": "Changed through another client"},
        )
        assert websocket.receive_json() == {
            "type": "invalidate",
            "revision": revision + 2,
            "scope": "work-items",
        }
        conflict = api.patch(
            item_path(project, work_item),
            json={"expected_version": 1, "title": "Stale overwrite"},
        )
        assert conflict.status_code == 409

    with api.websocket_connect(
        "/api/v1/sync", headers={"origin": "http://localhost:3000"}
    ) as websocket:
        assert websocket.receive_json() == {
            "type": "ready", "revision": revision + 2
        }


def test_create_search_get_and_bounded_context_contract(api, project, work_payload):
    created = create_work(api, project, work_payload)
    work_item = created["work_item"]
    initial = created["initial_checkpoint"]
    assert created["initial_relationships"] == []
    assert work_item["priority"] == 30
    assert work_item["status"] == "pending"
    assert work_item["version"] == 1
    assert work_item["initial_checkpoint_id"] == initial["id"]
    assert initial["work_item_id"] == work_item["id"]
    assert initial["kind"] == "context"
    assert initial["prompt"] == work_payload["initial_checkpoint"]["prompt"]
    assert initial["migration_origin"] is None
    assert initial["legacy_record_id"] is None
    assert initial["created_at"].endswith("Z")

    assert api.get(item_path(project, work_item)).json() == work_item
    result = api.get(collection(project)).json()
    assert result["total"] == 1
    summary = result["items"][0]
    assert summary["work_item"] == work_item
    assert summary["checkpoint_count"] == 1
    assert summary["ancestor_path"] == []
    assert summary["ancestor_path_truncated"] is False
    assert "prompt" not in summary["current_context"]
    assert "source_metadata" not in summary["current_context"]
    assert summary["current_context"]["id"] == initial["id"]
    assert summary["readiness"] == {
        "lifecycle_status": "pending",
        "is_terminal": False,
        "has_active_lease": False,
        "has_dropped_lease": False,
        "active_lease": None,
        "unresolved_blocker_count": 0,
        "is_blocked": False,
        "unresolved_gate_count": 0,
        "is_gated": False,
        "is_ready": True,
        "display_state": "pending",
    }

    context = api.get(f"{item_path(project, work_item)}/context").json()
    assert context["work_item"] == work_item
    assert context["initial_checkpoint"] == initial
    # A single-checkpoint item serializes that body once: current_context is null
    # and the client reads initial_checkpoint.
    assert context["current_context"] is None
    assert context["current_context_is_initial"] is True
    assert context["recent_checkpoints"] == []
    assert context["checkpoint_total"] == 1
    assert context["omitted_checkpoint_count"] == 0
    assert context["incoming_relationships"] == []
    assert context["outgoing_relationships"] == []
    assert context["undirected_relationships"] == []
    assert context["relationship_counts"] == {
        "incoming": 0,
        "outgoing": 0,
        "undirected": 0,
        "total": 0,
    }


def test_minimal_view_returns_only_choosing_fields(api, project, work_payload):
    created = api.post(collection(project), json=work_payload).json()
    work_item = created["work_item"]

    minimal = api.get(collection(project), params={"view": "minimal"})
    assert minimal.status_code == 200, minimal.text
    item = minimal.json()["items"][0]
    assert item == {
        "work_item": {
            "id": work_item["id"],
            "title": work_item["title"],
            "status": work_item["status"],
            "priority": work_item["priority"],
            "version": work_item["version"],
            "updated_at": work_item["updated_at"],
        },
        "checkpoint_count": 1,
        "display_state": "pending",
    }

    # The dashboard shape is unchanged and remains the REST default.
    default = api.get(collection(project)).json()["items"][0]
    assert default == api.get(collection(project), params={"view": "full"}).json()["items"][0]
    assert set(default) == {
        "work_item",
        "checkpoint_count",
        "ancestor_path",
        "ancestor_path_truncated",
        "current_context",
        "readiness",
    }
    # Minimal is strictly cheaper than the shape it replaces for agent callers.
    assert len(json.dumps(item)) < len(json.dumps(default))


def test_append_history_current_context_and_terminal_clarification(
    api, project, work_payload
):
    created = create_work(api, project, work_payload)
    work_item = created["work_item"]
    endpoint = item_path(project, work_item)
    progress = api.post(
        f"{endpoint}/checkpoints",
        json=checkpoint_payload(
            "  Progress remains exact.\r\n  ", "session-progress", kind="progress"
        ),
    )
    assert progress.status_code == 201, progress.text
    later_progress = api.post(
        f"{endpoint}/checkpoints",
        json=checkpoint_payload(
            "A second progress record.", "session-progress-two", kind="progress"
        ),
    )
    assert later_progress.status_code == 201, later_progress.text
    correction = api.post(
        f"{endpoint}/checkpoints",
        json=checkpoint_payload("Corrected context for the next session.", "session-correction"),
    )
    assert correction.status_code == 201, correction.text
    assert progress.json()["prompt"] == "  Progress remains exact.\r\n  "
    assert api.get(endpoint).json()["version"] == 1

    oldest = api.get(f"{endpoint}/checkpoints", params={"limit": 2}).json()
    newest = api.get(
        f"{endpoint}/checkpoints", params={"order": "newest", "limit": 2}
    ).json()
    assert oldest["total"] == 4
    assert [row["id"] for row in oldest["items"]] == [
        created["initial_checkpoint"]["id"],
        progress.json()["id"],
    ]
    assert [row["id"] for row in newest["items"]] == [
        correction.json()["id"],
        later_progress.json()["id"],
    ]

    context = api.get(f"{endpoint}/context", params={"recent_limit": 1}).json()
    assert context["current_context"]["id"] == correction.json()["id"]
    assert context["current_context_is_initial"] is False
    # No checkpoint body is serialized twice.
    returned = [context["initial_checkpoint"]["id"], context["current_context"]["id"]]
    returned += [row["id"] for row in context["recent_checkpoints"]]
    assert len(returned) == len(set(returned))
    assert [row["id"] for row in context["recent_checkpoints"]] == [
        later_progress.json()["id"]
    ]
    assert created["initial_checkpoint"]["id"] not in {
        row["id"] for row in context["recent_checkpoints"]
    }
    assert correction.json()["id"] not in {
        row["id"] for row in context["recent_checkpoints"]
    }
    assert context["checkpoint_total"] == 4
    assert context["omitted_checkpoint_count"] == 1

    completed = api.post(
        f"{endpoint}/complete",
        json={
            "expected_version": 1,
            "checkpoint": checkpoint_payload(
                "Implemented and verified the work.", "session-complete"
            )
            | {"kind": "context"},
        },
    )
    # Completion payloads deliberately reject caller-selected kinds.
    assert completed.status_code == 422
    completed = api.post(
        f"{endpoint}/complete",
        json={
            "expected_version": 1,
            "checkpoint": {
                key: value
                for key, value in checkpoint_payload(
                    "Implemented and verified the work.", "session-complete"
                ).items()
                if key != "kind"
            },
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["work_item"]["status"] == "done"
    assert completed.json()["checkpoint"]["kind"] == "completion"

    clarification = api.post(
        f"{endpoint}/checkpoints",
        json=checkpoint_payload("Audit clarification after completion.", "session-audit"),
    )
    assert clarification.status_code == 201
    assert api.get(endpoint).json()["status"] == "done"
    terminal_context = api.get(f"{endpoint}/context").json()
    assert terminal_context["current_context"]["id"] == clarification.json()["id"]
    assert terminal_context["current_context_is_initial"] is False
    assert terminal_context["readiness"]["display_state"] == "done"


def test_lifecycle_versions_typed_errors_and_soft_delete(api, project, work_payload):
    created = create_work(api, project, work_payload)
    work_item = created["work_item"]
    endpoint = item_path(project, work_item)
    assert api.patch(endpoint, json={"expected_version": 1, "status": "done"}).status_code == 422

    retired = api.patch(endpoint, json={"expected_version": 1, "status": "wont-do"})
    assert retired.status_code == 200
    assert retired.json()["version"] == 2
    completed = api.post(
        f"{endpoint}/complete",
        json={
            "expected_version": 2,
            "checkpoint": {
                "prompt": "Cannot complete retired work.",
                "source_client": "claude-code",
                "source_session_id": "retired-completion",
            },
        },
    )
    assert completed.status_code == 409
    assert completed.json()["detail"]["code"] == "work_not_pending"

    reopened = api.patch(endpoint, json={"expected_version": 2, "status": "pending"})
    assert reopened.status_code == 200
    assert reopened.json()["version"] == 3
    stale = api.patch(endpoint, json={"expected_version": 2, "title": "Stale"})
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "version_conflict",
        "message": "This work item changed. Recall it again before editing or deleting.",
        "context": {},
    }

    completed = api.post(
        f"{endpoint}/complete",
        json={
            "expected_version": 3,
            "checkpoint": {
                "prompt": "Completed after reopening.",
                "source_client": "claude-code",
                "source_session_id": "completion",
            },
        },
    )
    assert completed.status_code == 200
    repeated = api.post(
        f"{endpoint}/complete",
        json={
            "expected_version": 4,
            "checkpoint": {
                "prompt": "Duplicate completion.",
                "source_client": "claude-code",
                "source_session_id": "completion",
            },
        },
    )
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "work_not_pending"
    assert api.patch(endpoint, json={"expected_version": 4, "status": "wont-do"}).status_code == 409

    reopened = api.patch(endpoint, json={"expected_version": 4, "status": "pending"})
    assert reopened.status_code == 200
    history = api.get(f"{endpoint}/checkpoints").json()
    assert any(row["kind"] == "completion" for row in history["items"])
    deletion = api.post(f"{endpoint}/delete", json={"expected_version": 5})
    assert deletion.status_code == 200
    assert deletion.json() == {
        "deleted": True,
        "project_id": project["id"],
        "work_item_id": work_item["id"],
        "version": 6,
    }
    assert api.get(endpoint).status_code == 404
    assert api.get(collection(project), params={"status": "all"}).json()["total"] == 0


def test_deferral_is_dedicated_nonterminal_and_excluded_from_agent_claims(
    api, project, work_payload
):
    work_item = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work_item)
    actor = {
        "actor_client": "dashboard",
        "actor_session_id": "human-deferral",
    }

    ordinary_patch = api.patch(
        endpoint,
        json={"expected_version": 1, "status": "deferred", "actor": actor},
    )
    assert ordinary_patch.status_code == 422
    stale = api.post(
        f"{endpoint}/defer",
        json={"expected_version": 2, "actor": actor},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "version_conflict"

    deferred = api.post(
        f"{endpoint}/defer",
        json={"expected_version": 1, "actor": actor},
    )
    assert deferred.status_code == 200, deferred.text
    assert deferred.json()["status"] == "deferred"
    assert deferred.json()["version"] == 2
    context = api.get(f"{endpoint}/context").json()
    assert context["readiness"]["lifecycle_status"] == "deferred"
    assert context["readiness"]["is_terminal"] is False
    assert context["readiness"]["is_ready"] is False
    assert context["readiness"]["display_state"] == "deferred"
    assert api.get(collection(project)).json()["total"] == 0
    assert api.get(collection(project), params={"status": "deferred"}).json()["total"] == 1
    assert api.get(f"/api/v1/projects/{project['id']}/ready-work").json()["total"] == 0

    claim = api.post(
        f"{endpoint}/claim",
        json={
            "holder_client": "claude-code",
            "holder_session_id": "autonomous-session",
            "claim_request_id": "deferred-autonomous-claim",
        },
    )
    assert claim.status_code == 409
    assert claim.json()["detail"]["code"] == "work_not_pending"
    completion = api.post(
        f"{endpoint}/complete",
        json={
            "expected_version": 2,
            "checkpoint": {
                "prompt": "Deferred work cannot be completed.",
                "source_client": "claude-code",
                "source_session_id": "deferred-completion",
            },
        },
    )
    assert completion.status_code == 409
    assert completion.json()["detail"]["code"] == "work_not_pending"

    pending = api.patch(
        endpoint,
        json={"expected_version": 2, "status": "pending", "actor": actor},
    )
    assert pending.status_code == 200, pending.text
    assert pending.json()["version"] == 3
    assert api.get(collection(project)).json()["total"] == 1

    claimed = api.post(
        f"{endpoint}/claim",
        json={
            "holder_client": "claude-code",
            "holder_session_id": "directed-session",
            "claim_request_id": "directed-claim",
        },
    )
    assert claimed.status_code == 200, claimed.text
    active_defer = api.post(
        f"{endpoint}/defer",
        json={"expected_version": 3, "actor": actor},
    )
    assert active_defer.status_code == 409
    assert active_defer.json()["detail"]["code"] == "lease_held"


def test_checkpoint_contract_is_append_only_and_validates_lease_fields(
    api, project, work_payload, postgres_engine
):
    created = create_work(api, project, work_payload)
    work_item = created["work_item"]
    endpoint = item_path(project, work_item)
    invalid_kind = api.post(
        f"{endpoint}/checkpoints",
        json=checkpoint_payload("Not a completion route.", "invalid-kind", kind="completion"),
    )
    assert invalid_kind.status_code == 422
    patch = api.patch(
        endpoint, json={"expected_version": 1, "title": "No token", "lease_token": "secret"}
    )
    assert patch.status_code == 409
    assert patch.json()["detail"]["code"] == "lease_token_mismatch"
    deletion = api.post(
        f"{endpoint}/delete", json={"expected_version": 1, "lease_token": "secret"}
    )
    assert deletion.status_code == 409
    assert deletion.json()["detail"]["code"] == "lease_token_mismatch"

    checkpoint_id = created["initial_checkpoint"]["id"]
    with pytest.raises(DBAPIError, match="checkpoints are immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE checkpoints SET prompt = 'rewritten' WHERE id = :id"),
                {"id": checkpoint_id},
            )
    with pytest.raises(DBAPIError, match="checkpoints are immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM checkpoints WHERE id = :id"), {"id": checkpoint_id}
            )
    assert api.get(f"{endpoint}/checkpoints").json()["items"][0]["prompt"] == work_payload[
        "initial_checkpoint"
    ]["prompt"]


def test_search_aggregates_checkpoint_hits_and_filter_contract(api, project, work_payload):
    first = create_work(api, project, work_payload)
    second = create_work(
        api,
        project,
        {
            **work_payload,
            "title": "Separate work",
            "summary": "A different durable objective.",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "prompt": "Ordinary initial context.",
                "tags": ["initial-only"],
                "source_session_id": "second-initial",
            },
        },
    )
    endpoint = item_path(project, first["work_item"])
    for session_id in ["shared-hit-a", "shared-hit-b"]:
        response = api.post(
            f"{endpoint}/checkpoints",
            json=checkpoint_payload(
                f"The frobnicated state appears in {session_id}.",
                session_id,
                kind="progress",
                tags=["later-tag"],
            ),
        )
        assert response.status_code == 201

    found = api.get(collection(project), params={"q": "frobnicate"}).json()
    assert found["total"] == 1
    assert [row["work_item"]["id"] for row in found["items"]] == [first["work_item"]["id"]]
    assert api.get(collection(project), params={"tag": "later-tag"}).json()["total"] == 1
    assert api.get(
        collection(project), params={"source_session_id": "shared-hit-b"}
    ).json()["total"] == 1
    initial_only = api.get(collection(project), params={"tag": "initial-only"}).json()
    assert initial_only["total"] == 1
    assert initial_only["items"][0]["work_item"]["id"] == second["work_item"]["id"]


def test_cross_project_isolation_and_two_appenders_plus_editor_succeed(
    api, project, work_payload, postgres_engine
):
    created = create_work(api, project, work_payload)
    work_item = created["work_item"]
    endpoint = item_path(project, work_item)
    other = api.post("/api/v1/projects", json={"name": "Other work project"}).json()
    wrong = f"/api/v1/projects/{other['id']}/work-items/{work_item['id']}"
    assert api.get(wrong).status_code == 404
    assert api.get(f"{wrong}/checkpoints").status_code == 404
    assert api.post(
        f"{wrong}/checkpoints", json=checkpoint_payload("Wrong project", "wrong-project")
    ).status_code == 404

    barrier = Barrier(3)

    def append(session_id):
        barrier.wait(timeout=5)
        return api.post(
            f"{endpoint}/checkpoints",
            json=checkpoint_payload(f"Progress from {session_id}.", session_id, kind="progress"),
        )

    def edit():
        barrier.wait(timeout=5)
        return api.patch(endpoint, json={"expected_version": 1, "title": "Edited identity"})

    with ThreadPoolExecutor(max_workers=3) as pool:
        append_a = pool.submit(append, "append-a")
        append_b = pool.submit(append, "append-b")
        edited = pool.submit(edit)
        responses = [append_a.result(), append_b.result(), edited.result()]
    assert sorted(response.status_code for response in responses) == [200, 201, 201]
    final = api.get(endpoint).json()
    assert final["title"] == "Edited identity"
    assert final["version"] == 2
    assert api.get(f"{endpoint}/checkpoints").json()["total"] == 3
    with Session(postgres_engine) as database:
        row = database.get(WorkItem, UUID(work_item["id"]))
        assert row is not None and row.deleted_at is None
        assert database.query(Checkpoint).filter_by(work_item_id=row.id).count() == 3


def test_missing_project_and_work_return_typed_not_found(api, project, work_payload):
    missing_project = uuid4()
    response = api.get(f"/api/v1/projects/{missing_project}/work-items")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "project_not_found"
    response = api.get(f"{collection(project)}/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "work_item_not_found"

    # A wrong project and a wrong work item have different recoveries, so the
    # two must stay distinguishable even on routes addressing both.
    created = api.post(collection(project), json=work_payload).json()["work_item"]
    for suffix in ("", "/context", "/checkpoints"):
        stray = api.get(f"/api/v1/projects/{missing_project}/work-items/{created['id']}{suffix}")
        assert stray.status_code == 404, suffix
        assert stray.json()["detail"]["code"] == "project_not_found", suffix
        absent = api.get(f"{collection(project)}/{uuid4()}{suffix}")
        assert absent.status_code == 404, suffix
        assert absent.json()["detail"]["code"] == "work_item_not_found", suffix
