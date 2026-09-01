"""Phase 2 atomic work-lease behavior against real PostgreSQL."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import mnemonic_api.application as application_module
from mnemonic_api.models import WorkLease
from mnemonic_api.services.leases import claim_receipt

pytestmark = pytest.mark.postgres


def collection(project):
    return f"/api/v1/projects/{project['id']}/work-items"


def create_work(api, project, payload, **changes):
    response = api.post(collection(project), json={**payload, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def item_path(project, work_item):
    return f"{collection(project)}/{work_item['id']}"


def claim_payload(request_id, *, client="claude-code", session="lease-session"):
    return {
        "holder_client": client,
        "holder_session_id": session,
        "claim_request_id": request_id,
    }


def completion_payload(expected_version, lease_token=None):
    payload = {
        "expected_version": expected_version,
        "checkpoint": {
            "prompt": "Lease holder completed and verified the work.",
            "source_client": "claude-code",
            "source_session_id": "lease-completion",
        },
    }
    if lease_token is not None:
        payload["lease_token"] = lease_token
    return payload


def expire_lease(postgres_engine, work_item_id):
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE work_leases
                SET acquired_at = clock_timestamp() - interval '3 seconds',
                    renewed_at = clock_timestamp() - interval '2 seconds',
                    expires_at = clock_timestamp() - interval '1 second'
                WHERE work_item_id = :work_item_id
                """
            ),
            {"work_item_id": work_item_id},
        )


def test_claim_replay_context_readiness_renew_release_and_no_work_activity(
    api, project, work_payload, postgres_engine
):
    created = create_work(api, project, work_payload)
    work_item = created["work_item"]
    endpoint = item_path(project, work_item)
    before = api.get(endpoint).json()
    payload = claim_payload("recoverable-request")

    claimed = api.post(f"{endpoint}/claim-and-recall", json=payload)
    assert claimed.status_code == 200, claimed.text
    result = claimed.json()
    receipt = result["lease"]
    assert receipt["work_item_id"] == work_item["id"]
    assert receipt["holder_client"] == payload["holder_client"]
    assert receipt["holder_session_id"] == payload["holder_session_id"]
    assert receipt["claim_request_id"] == payload["claim_request_id"]
    assert len(receipt["lease_token"]) >= 43
    assert receipt["acquired_at"] == receipt["renewed_at"]
    assert result["context"]["work_item"] == before
    readiness = result["context"]["readiness"]
    assert readiness["is_ready"] is False
    assert readiness["has_active_lease"] is True
    assert readiness["display_state"] == "active"
    assert readiness["active_lease"] == {
        key: receipt[key]
        for key in [
            "holder_client",
            "holder_session_id",
            "acquired_at",
            "renewed_at",
            "expires_at",
        ]
    }
    assert "lease_token" not in json.dumps(result["context"])
    assert "claim_request_id" not in json.dumps(result["context"])

    replay = api.post(f"{endpoint}/claim", json=payload)
    assert replay.status_code == 200
    assert replay.json() == receipt
    different_request = api.post(
        f"{endpoint}/claim",
        json={**payload, "claim_request_id": "different-active-request"},
    )
    assert different_request.status_code == 409
    assert different_request.json()["detail"]["code"] == "lease_held"

    summary = api.get(collection(project), params={"status": "active"}).json()["items"][0]
    assert summary["readiness"] == readiness
    assert "lease_token" not in json.dumps(summary)

    renewed = api.post(
        f"{endpoint}/renew-claim", json={"lease_token": receipt["lease_token"]}
    )
    assert renewed.status_code == 200, renewed.text
    renewed_receipt = renewed.json()
    assert renewed_receipt["lease_token"] == receipt["lease_token"]
    assert renewed_receipt["claim_request_id"] == receipt["claim_request_id"]
    assert renewed_receipt["acquired_at"] == receipt["acquired_at"]
    assert renewed_receipt["expires_at"] >= receipt["expires_at"]
    assert api.post(f"{endpoint}/renew-claim", json={}).status_code == 422
    assert api.post(f"{endpoint}/release-claim", json={}).status_code == 422

    assert api.get(endpoint).json() == before
    with Session(postgres_engine) as database:
        retained = database.get(WorkLease, UUID(work_item["id"]))
        assert retained is not None
        assert receipt["lease_token"] not in repr(retained)
        receipt_model = claim_receipt(retained)
        assert receipt["lease_token"] not in repr(receipt_model)
        assert receipt_model.model_dump()["lease_token"] == receipt["lease_token"]

    released = api.post(
        f"{endpoint}/release-claim", json={"lease_token": receipt["lease_token"]}
    )
    assert released.status_code == 200
    assert released.json() == {"work_item_id": work_item["id"], "released": True}
    repeated = api.post(
        f"{endpoint}/release-claim", json={"lease_token": receipt["lease_token"]}
    )
    assert repeated.status_code == 200
    assert repeated.json() == {"work_item_id": work_item["id"], "released": False}
    ready = api.get(f"{endpoint}/context").json()["readiness"]
    assert ready["is_ready"] is True
    assert ready["has_active_lease"] is False
    assert ready["active_lease"] is None
    assert api.get(endpoint).json() == before


def test_deferring_dropped_work_clears_expired_lease_before_pending_resume(
    api, project, work_payload, postgres_engine
):
    work_item = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work_item)
    claimed = api.post(f"{endpoint}/claim", json=claim_payload("dropped-before-defer"))
    assert claimed.status_code == 200, claimed.text
    expire_lease(postgres_engine, work_item["id"])

    dropped = api.get(f"{endpoint}/context").json()["readiness"]
    assert dropped["has_dropped_lease"] is True
    assert dropped["display_state"] == "dropped"
    deferred = api.post(f"{endpoint}/defer", json={"expected_version": 1})
    assert deferred.status_code == 200, deferred.text
    with Session(postgres_engine) as database:
        assert database.get(WorkLease, UUID(work_item["id"])) is None

    resumed = api.patch(endpoint, json={"expected_version": 2, "status": "pending"})
    assert resumed.status_code == 200, resumed.text
    pending = api.get(f"{endpoint}/context").json()["readiness"]
    assert pending["has_dropped_lease"] is False
    assert pending["display_state"] == "pending"


def test_simultaneous_distinct_and_identical_claims_are_serialized(
    api, project, work_payload, postgres_engine
):
    distinct_work = create_work(api, project, work_payload)["work_item"]
    distinct_endpoint = item_path(project, distinct_work)
    barrier = Barrier(2)

    def distinct_claim(payload):
        barrier.wait(timeout=5)
        return api.post(f"{distinct_endpoint}/claim", json=payload)

    payloads = [
        claim_payload("request-a", session="session-a"),
        claim_payload("request-b", session="session-b"),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(distinct_claim, payloads))
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["detail"]["code"] == "lease_held"
    assert set(conflict.json()["detail"]["context"]) == {
        "holder_client",
        "expires_at",
    }

    identical_work = create_work(
        api,
        project,
        {
            **work_payload,
            "title": "Identical replay concurrency",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "source_session_id": "identical-work",
            },
        },
    )["work_item"]
    identical_endpoint = item_path(project, identical_work)
    identical_barrier = Barrier(2)
    identical_payload = claim_payload("same-request", session="same-session")

    def identical_claim():
        identical_barrier.wait(timeout=5)
        return api.post(f"{identical_endpoint}/claim", json=identical_payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [pool.submit(identical_claim) for _ in range(2)]
        responses = [future.result() for future in responses]
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()

    with Session(postgres_engine) as database:
        assert database.scalar(select(func.count()).select_from(WorkLease)) == 2


def test_expiry_requires_new_request_and_old_token_cannot_delete_replacement(
    api, project, work_payload, postgres_engine
):
    work_item = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work_item)
    original_payload = claim_payload("original-request")
    original = api.post(f"{endpoint}/claim", json=original_payload).json()
    expire_lease(postgres_engine, work_item["id"])

    expired_renewal = api.post(
        f"{endpoint}/renew-claim", json={"lease_token": original["lease_token"]}
    )
    assert expired_renewal.status_code == 409
    assert expired_renewal.json()["detail"]["code"] == "lease_expired"

    replay = api.post(f"{endpoint}/claim", json=original_payload)
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "claim_request_expired"
    changed_holder_reuse = api.post(
        f"{endpoint}/claim",
        json=claim_payload(
            "original-request", client="different-client", session="different-session"
        ),
    )
    assert changed_holder_reuse.status_code == 409
    assert changed_holder_reuse.json()["detail"]["code"] == "claim_request_expired"

    wrong_expired_release = api.post(
        f"{endpoint}/release-claim", json={"lease_token": "different-expired-token"}
    )
    assert wrong_expired_release.status_code == 200
    assert wrong_expired_release.json()["released"] is False

    releasable_work = create_work(
        api,
        project,
        {
            **work_payload,
            "title": "Release an expired retained lease",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "source_session_id": "expired-release-work",
            },
        },
    )["work_item"]
    releasable_endpoint = item_path(project, releasable_work)
    releasable = api.post(
        f"{releasable_endpoint}/claim", json=claim_payload("expired-release-request")
    ).json()
    expire_lease(postgres_engine, releasable_work["id"])
    released_expired = api.post(
        f"{releasable_endpoint}/release-claim",
        json={"lease_token": releasable["lease_token"]},
    )
    assert released_expired.status_code == 200
    assert released_expired.json() == {
        "work_item_id": releasable_work["id"],
        "released": True,
    }

    replacement = api.post(
        f"{endpoint}/claim",
        json=claim_payload("replacement-request", session="replacement-session"),
    )
    assert replacement.status_code == 200, replacement.text
    replacement = replacement.json()
    assert replacement["lease_token"] != original["lease_token"]

    for action in ["renew-claim", "release-claim"]:
        stale = api.post(
            f"{endpoint}/{action}", json={"lease_token": original["lease_token"]}
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "lease_token_mismatch"
        assert original["lease_token"] not in stale.text

    assert api.post(
        f"{endpoint}/claim",
        json=claim_payload("replacement-request", session="replacement-session"),
    ).json() == replacement


def test_terminal_mutations_require_active_token_and_consume_the_lease(
    api, project, work_payload, postgres_engine
):
    completed_work = create_work(api, project, work_payload)["work_item"]
    completed_endpoint = item_path(project, completed_work)
    completed_claim = api.post(
        f"{completed_endpoint}/claim", json=claim_payload("complete-request")
    ).json()
    missing = api.post(
        f"{completed_endpoint}/complete", json=completion_payload(expected_version=1)
    )
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "lease_token_mismatch"
    wrong = api.post(
        f"{completed_endpoint}/complete",
        json=completion_payload(expected_version=1, lease_token="wrong-token"),
    )
    assert wrong.status_code == 409
    completed = api.post(
        f"{completed_endpoint}/complete",
        json=completion_payload(1, completed_claim["lease_token"]),
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["work_item"]["status"] == "done"

    retired_work = create_work(
        api,
        project,
        {
            **work_payload,
            "title": "Retire under lease",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "source_session_id": "retire-work",
            },
        },
    )["work_item"]
    retired_endpoint = item_path(project, retired_work)
    retired_claim = api.post(
        f"{retired_endpoint}/claim", json=claim_payload("retire-request")
    ).json()
    assert api.patch(
        retired_endpoint, json={"expected_version": 1, "status": "wont-do"}
    ).status_code == 409
    wrong_retirement = api.patch(
        retired_endpoint,
        json={
            "expected_version": 1,
            "status": "wont-do",
            "lease_token": "wrong-retirement-token",
        },
    )
    assert wrong_retirement.status_code == 409
    assert wrong_retirement.json()["detail"]["code"] == "lease_token_mismatch"
    retired = api.patch(
        retired_endpoint,
        json={
            "expected_version": 1,
            "status": "wont-do",
            "lease_token": retired_claim["lease_token"],
        },
    )
    assert retired.status_code == 200
    assert retired.json()["status"] == "wont-do"

    deleted_work = create_work(
        api,
        project,
        {
            **work_payload,
            "title": "Delete under lease",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "source_session_id": "delete-work",
            },
        },
    )["work_item"]
    deleted_endpoint = item_path(project, deleted_work)
    deleted_claim = api.post(
        f"{deleted_endpoint}/claim", json=claim_payload("delete-request")
    ).json()
    assert api.post(
        f"{deleted_endpoint}/delete", json={"expected_version": 1}
    ).status_code == 409
    wrong_deletion = api.post(
        f"{deleted_endpoint}/delete",
        json={"expected_version": 1, "lease_token": "wrong-deletion-token"},
    )
    assert wrong_deletion.status_code == 409
    assert wrong_deletion.json()["detail"]["code"] == "lease_token_mismatch"
    deleted = api.post(
        f"{deleted_endpoint}/delete",
        json={"expected_version": 1, "lease_token": deleted_claim["lease_token"]},
    )
    assert deleted.status_code == 200
    assert api.get(deleted_endpoint).status_code == 404

    with Session(postgres_engine) as database:
        retained_ids = set(database.scalars(select(WorkLease.work_item_id)))
    assert UUID(completed_work["id"]) not in retained_ids
    assert UUID(retired_work["id"]) not in retained_ids
    assert UUID(deleted_work["id"]) not in retained_ids


def test_checkpoint_tokens_and_expired_terminal_behavior(
    api, project, work_payload, postgres_engine
):
    work_item = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work_item)
    claim = api.post(f"{endpoint}/claim", json=claim_payload("checkpoint-request")).json()
    checkpoint = {
        "kind": "progress",
        "prompt": "A durable observation while another session holds the lease.",
        "source_client": "claude-code",
        "source_session_id": "observer-session",
    }
    assert api.post(f"{endpoint}/checkpoints", json=checkpoint).status_code == 201
    invalid = api.post(
        f"{endpoint}/checkpoints", json={**checkpoint, "lease_token": "wrong-token"}
    )
    assert invalid.status_code == 409
    assert invalid.json()["detail"]["code"] == "lease_token_mismatch"
    assert api.post(
        f"{endpoint}/checkpoints",
        json={**checkpoint, "lease_token": claim["lease_token"]},
    ).status_code == 201
    assert api.get(f"{endpoint}/checkpoints").json()["total"] == 3

    expire_lease(postgres_engine, work_item["id"])
    stale = api.patch(
        endpoint,
        json={
            "expected_version": 1,
            "status": "promoted",
            "lease_token": claim["lease_token"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "lease_expired"
    promoted = api.patch(endpoint, json={"expected_version": 1, "status": "promoted"})
    assert promoted.status_code == 200


def test_terminal_deleted_and_cross_project_claims_are_rejected_without_token_leakage(
    api, project, work_payload, caplog
):
    terminal = create_work(api, project, work_payload, status="wont-do")["work_item"]
    terminal_claim = api.post(
        f"{item_path(project, terminal)}/claim", json=claim_payload("terminal-request")
    )
    assert terminal_claim.status_code == 409
    assert terminal_claim.json()["detail"]["code"] == "work_not_pending"

    deleted = create_work(
        api,
        project,
        {
            **work_payload,
            "title": "Deleted work",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "source_session_id": "deleted-work",
            },
        },
    )["work_item"]
    deleted_endpoint = item_path(project, deleted)
    assert api.post(f"{deleted_endpoint}/delete", json={"expected_version": 1}).status_code == 200
    assert api.post(
        f"{deleted_endpoint}/claim", json=claim_payload("deleted-request")
    ).status_code == 404

    visible = create_work(
        api,
        project,
        {
            **work_payload,
            "title": "Project isolation",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "source_session_id": "isolated-work",
            },
        },
    )["work_item"]
    endpoint = item_path(project, visible)
    receipt = api.post(f"{endpoint}/claim", json=claim_payload("isolation-request")).json()
    other = api.post("/api/v1/projects", json={"name": "Lease isolation"}).json()
    wrong = f"{collection(other)}/{visible['id']}"
    for action, body in [
        ("claim", claim_payload("wrong-project")),
        ("renew-claim", {"lease_token": receipt["lease_token"]}),
        ("release-claim", {"lease_token": receipt["lease_token"]}),
    ]:
        response = api.post(f"{wrong}/{action}", json=body)
        assert response.status_code == 404
        assert receipt["lease_token"] not in response.text

    sentinel = "not-the-secret-token-" + uuid4().hex
    mismatch = api.post(f"{endpoint}/renew-claim", json={"lease_token": sentinel})
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "lease_token_mismatch"
    assert sentinel not in mismatch.text
    assert sentinel not in caplog.text
    assert receipt["lease_token"] not in caplog.text


def test_capabilities_are_body_only_and_lease_routes_reject_every_query_parameter(
    api, project, work_payload, postgres_engine, caplog
):
    work_item = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work_item)
    query_token = "raw-url-capability-" + uuid4().hex
    query_value = "raw-url-body-field-" + uuid4().hex
    payload = claim_payload("body-only-request")

    rejected = [
        api.post(f"{endpoint}/claim", params={"lease_token": query_token}, json=payload),
        api.post(f"{endpoint}/claim", params={"holder_client": query_value}, json=payload),
        api.post(
            f"{endpoint}/claim-and-recall",
            params={"claim_request_id": query_value},
            json=payload,
        ),
    ]
    for response in rejected:
        assert response.status_code == 422
        assert query_token not in response.text
        assert query_value not in response.text

    with Session(postgres_engine) as database:
        assert database.get(WorkLease, UUID(work_item["id"])) is None

    receipt = api.post(f"{endpoint}/claim", json=payload).json()
    rejected_renew = api.post(
        f"{endpoint}/renew-claim",
        params={"lease_token": query_token},
        json={"lease_token": receipt["lease_token"]},
    )
    rejected_release = api.post(
        f"{endpoint}/release-claim",
        params={"holder_session_id": query_value},
        json={"lease_token": receipt["lease_token"]},
    )
    rejected_terminal = api.patch(
        endpoint,
        params={"lease_token": query_token},
        json={
            "expected_version": 1,
            "status": "wont-do",
            "lease_token": receipt["lease_token"],
        },
    )
    rejected_deletion = api.post(
        f"{endpoint}/delete",
        params={"lease_token": query_token},
        json={"expected_version": 1, "lease_token": receipt["lease_token"]},
    )
    for response in [rejected_renew, rejected_release, rejected_terminal, rejected_deletion]:
        assert response.status_code == 422
        assert query_token not in response.text
        assert query_value not in response.text
        assert receipt["lease_token"] not in response.text

    # The rejected release, terminal transition, and deletion had no side effects.
    replay = api.post(f"{endpoint}/claim", json=payload)
    assert replay.status_code == 200
    assert replay.json() == receipt
    assert api.get(endpoint).json()["status"] == "pending"

    # Ordinary retrieval queries remain valid, and the same capability is accepted
    # once it is carried in the request body instead of the URL.
    search = api.get(collection(project), params={"q": "cache", "status": "all"})
    assert search.status_code == 200
    assert search.json()["total"] >= 1
    accepted_deletion = api.post(
        f"{endpoint}/delete",
        json={"expected_version": 1, "lease_token": receipt["lease_token"]},
    )
    assert accepted_deletion.status_code == 200
    assert api.get(endpoint).status_code == 404

    assert query_token not in caplog.text
    assert query_value not in caplog.text
    assert receipt["lease_token"] not in caplog.text


def test_claim_and_recall_rolls_back_lease_when_context_assembly_fails(
    api, project, work_payload, postgres_engine, monkeypatch
):
    work_item = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work_item)

    def fail_context(*_args, **_kwargs):
        raise RuntimeError("synthetic context failure")

    monkeypatch.setattr(application_module, "assemble_work_context", fail_context)
    with pytest.raises(RuntimeError, match="synthetic context failure"):
        api.post(
            f"{endpoint}/claim-and-recall",
            json=claim_payload("rolled-back-request"),
        )
    with Session(postgres_engine) as database:
        assert database.get(WorkLease, UUID(work_item["id"])) is None
