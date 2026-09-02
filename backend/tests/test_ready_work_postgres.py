"""Phase 4 ready-work discovery against real PostgreSQL."""

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import false, text

from mnemonic_api.services import readiness as readiness_service

pytestmark = pytest.mark.postgres


def collection(project):
    return f"/api/v1/projects/{project['id']}/work-items"


def create_work(api, project, payload, *, title, priority=0, tags=None):
    body = deepcopy(payload)
    body["title"] = title
    body["priority"] = priority
    body["initial_checkpoint"]["source_session_id"] = title.lower().replace(" ", "-")
    if tags is not None:
        body["initial_checkpoint"]["tags"] = tags
    response = api.post(collection(project), json=body)
    assert response.status_code == 201, response.text
    return response.json()["work_item"]


def ready_path(project):
    return f"/api/v1/projects/{project['id']}/ready-work"


def test_ready_work_predicate_order_filters_and_pointer_boundary(
    api, project, work_payload, postgres_engine
):
    older = create_work(
        api,
        project,
        work_payload,
        title="Older high priority",
        priority=90,
        tags=["QueueTag"],
    )
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO checkpoints (
                    id, work_item_id, kind, prompt, source_client,
                    source_session_id, tags
                ) VALUES (
                    :checkpoint_id, :work_item_id, 'context',
                    'Retained Unicode-tag checkpoint.', 'legacy-client',
                    'legacy-session', ARRAY[CAST(:unicode_tag AS varchar),
                                            CAST(:ascii_tag AS varchar)]
                )
                """
            ),
            {
                "checkpoint_id": uuid4(),
                "unicode_tag": "İ",
                "ascii_tag": "QueueTag",
                "work_item_id": older["id"],
            },
        )
    newer = create_work(
        api,
        project,
        work_payload,
        title="Newer high priority",
        priority=90,
        tags=["other"],
    )
    blocked = create_work(
        api,
        project,
        work_payload,
        title="Blocked target",
        priority=100,
    )
    blocker = create_work(
        api,
        project,
        work_payload,
        title="Unresolved blocker",
        priority=1,
    )
    terminal = create_work(
        api,
        project,
        work_payload,
        title="Terminal work",
        priority=100,
    )
    retired = api.patch(
        f"{collection(project)}/{terminal['id']}",
        json={"expected_version": 1, "status": "wont-do"},
    )
    assert retired.status_code == 200, retired.text

    edge = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "blocks",
            "source_work_item_id": blocker["id"],
            "target_work_item_id": blocked["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "ready-matrix",
        },
    )
    assert edge.status_code == 200, edge.text

    leased = create_work(
        api,
        project,
        work_payload,
        title="Leased work",
        priority=100,
    )
    claim = api.post(
        f"{collection(project)}/{leased['id']}/claim",
        json={
            "holder_client": "pytest",
            "holder_session_id": "ready-lease",
            "claim_request_id": "ready-lease-request",
        },
    )
    assert claim.status_code == 200, claim.text

    response = api.get(ready_path(project))
    assert response.status_code == 200, response.text
    page = response.json()
    returned_ids = [item["work_item"]["id"] for item in page["items"]]
    assert returned_ids[:2] == [older["id"], newer["id"]]
    assert blocked["id"] not in returned_ids
    assert terminal["id"] not in returned_ids
    assert leased["id"] not in returned_ids
    for item in page["items"]:
        assert set(item) == {"work_item", "checkpoint_count", "display_state"}
        assert item["display_state"] == "pending"
        assert "summary" not in item["work_item"]
        assert "prompt" not in item
        assert "lease_token" not in str(item)

    tagged = api.get(ready_path(project), params={"tag": "queuetag"}).json()
    assert [item["work_item"]["id"] for item in tagged["items"]] == [older["id"]]
    assert tagged["total"] == 1

    mixed_case = api.get(
        ready_path(project), params={"tag": " QuEuEtAg "}
    ).json()
    assert [item["work_item"]["id"] for item in mixed_case["items"]] == [older["id"]]

    unicode_tag = api.get(ready_path(project), params={"tag": "İ"}).json()
    assert [item["work_item"]["id"] for item in unicode_tag["items"]] == [older["id"]]
    python_lowered = "İ".lower()
    assert python_lowered == "i\u0307"
    assert api.get(
        ready_path(project), params={"tag": python_lowered}
    ).json()["items"] == []

    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = connection.execute(
            text(
                """
                EXPLAIN (FORMAT TEXT)
                SELECT id
                FROM checkpoints
                WHERE mnemonic_normalized_tags(tags)
                    @> mnemonic_normalized_tags(
                        ARRAY[CAST(:tag AS varchar)]::varchar[]
                    )
                """
            ),
            {"tag": "İ"},
        ).scalars().all()
    assert "ix_checkpoints_normalized_tags_gin" in "\n".join(plan)

    priority = api.get(
        ready_path(project),
        params={"min_priority": 90, "limit": 1, "offset": 1},
    ).json()
    assert priority["total"] == 2
    assert [item["work_item"]["id"] for item in priority["items"]] == [newer["id"]]
    beyond = api.get(
        ready_path(project),
        params={"min_priority": 90, "offset": 20},
    ).json()
    assert beyond["items"] == []
    assert beyond["total"] == 2


def test_ready_direct_parent_filter_and_claim_time_revalidation(api, project, work_payload):
    parent = create_work(api, project, work_payload, title="Ready parent", priority=1)
    child = create_work(api, project, work_payload, title="Ready child", priority=5)
    other = create_work(api, project, work_payload, title="Ready unrelated", priority=10)
    edge = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "parent-child",
            "source_work_item_id": parent["id"],
            "target_work_item_id": child["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "parent-filter",
        },
    )
    assert edge.status_code == 200, edge.text

    page = api.get(
        ready_path(project),
        params={"parent_work_item_id": parent["id"]},
    ).json()
    assert page["total"] == 1
    assert [item["work_item"]["id"] for item in page["items"]] == [child["id"]]
    assert other["id"] not in {item["work_item"]["id"] for item in page["items"]}

    blocker = create_work(api, project, work_payload, title="Late blocker", priority=1)
    late = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "blocks",
            "source_work_item_id": blocker["id"],
            "target_work_item_id": child["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "late-blocker",
        },
    )
    assert late.status_code == 200, late.text
    claim = api.post(
        f"{collection(project)}/{child['id']}/claim",
        json={
            "holder_client": "pytest",
            "holder_session_id": "claim-after-ready",
            "claim_request_id": "claim-after-ready-request",
        },
    )
    assert claim.status_code == 409
    assert claim.json()["detail"]["code"] == "work_blocked"

    unknown_parent = api.get(
        ready_path(project),
        params={"parent_work_item_id": str(UUID(int=0))},
    )
    assert unknown_parent.status_code == 404
    assert unknown_parent.json()["detail"]["code"] == "work_item_not_found"
    assert api.get(ready_path(project), params={"unknown": "value"}).status_code == 422


def test_future_gate_seam_composes_with_ready_list_and_fresh_claim(
    api,
    project,
    work_payload,
    monkeypatch,
):
    work = create_work(api, project, work_payload, title="Future gated work", priority=10)
    replay_work = create_work(api, project, work_payload, title="Gate replay work", priority=1)
    replay_payload = {
        "holder_client": "pytest",
        "holder_session_id": "gate-replay",
        "claim_request_id": "gate-replay-request",
    }
    first_claim = api.post(
        f"{collection(project)}/{replay_work['id']}/claim",
        json=replay_payload,
    )
    assert first_claim.status_code == 200, first_claim.text
    assert [
        item["work_item"]["id"] for item in api.get(ready_path(project)).json()["items"]
    ] == [work["id"]]

    monkeypatch.setattr(
        readiness_service,
        "gate_eligibility_clause",
        lambda work_item_id: false(),
    )


    replay = api.post(
        f"{collection(project)}/{replay_work['id']}/claim",
        json=replay_payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["lease_token"] == first_claim.json()["lease_token"]
    gated_page = api.get(ready_path(project))
    assert gated_page.status_code == 200, gated_page.text
    assert gated_page.json()["items"] == []
    assert gated_page.json()["total"] == 0

    claim = api.post(
        f"{collection(project)}/{work['id']}/claim",
        json={
            "holder_client": "pytest",
            "holder_session_id": "future-gate",
            "claim_request_id": "future-gate-request",
        },
    )
    assert claim.status_code == 409
    assert claim.json()["detail"]["code"] == "work_gated"
