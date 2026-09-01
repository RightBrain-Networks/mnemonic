"""Phase 3 typed relationship, graph concurrency, readiness, and hierarchy tests."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.postgres


def work_collection(project):
    return f"/api/v1/projects/{project['id']}/work-items"


def relationship_collection(project):
    return f"/api/v1/projects/{project['id']}/relationships"


def work_path(project, work_item):
    return f"{work_collection(project)}/{work_item['id']}"


def create_work(api, project, base_payload, name, *, initial_relationships=None):
    payload = {
        **base_payload,
        "title": name,
        "summary": f"Durable objective for {name}.",
        "initial_checkpoint": {
            **base_payload["initial_checkpoint"],
            "prompt": f"Initial context for {name}.",
            "source_session_id": f"session-{name.lower().replace(' ', '-')}",
        },
    }
    if initial_relationships is not None:
        payload["initial_relationships"] = initial_relationships
    response = api.post(work_collection(project), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def relationship_payload(
    source,
    target,
    relationship_type,
    *,
    context_checkpoint_id=None,
    session="graph-session",
):
    payload = {
        "relationship_type": relationship_type,
        "source_work_item_id": source["id"],
        "target_work_item_id": target["id"],
        "created_by_client": "claude-code",
        "created_by_session_id": session,
        "created_by_model": "graph-model",
    }
    if context_checkpoint_id is not None:
        payload["context_checkpoint_id"] = context_checkpoint_id
    return payload


def add_relationship(api, project, payload):
    response = api.post(relationship_collection(project), json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def claim_payload(request_id):
    return {
        "holder_client": "claude-code",
        "holder_session_id": "lease-holder",
        "claim_request_id": request_id,
    }


def completion_payload(version=1, lease_token=None):
    result = {
        "expected_version": version,
        "checkpoint": {
            "prompt": "Completed with all prerequisites satisfied.",
            "source_client": "claude-code",
            "source_session_id": "completion-session",
        },
    }
    if lease_token is not None:
        result["lease_token"] = lease_token
    return result


def test_all_relationship_types_round_trip_normalize_and_delete_guard(
    api, project, work_payload
):
    source_created = create_work(api, project, work_payload, "Source")
    target_created = create_work(api, project, work_payload, "Target")
    other_created = create_work(api, project, work_payload, "Other parent")
    source = source_created["work_item"]
    target = target_created["work_item"]
    other = other_created["work_item"]

    created_edges = {}
    for relationship_type in [
        "blocks",
        "parent-child",
        "duplicate-of",
        "related",
    ]:
        result = add_relationship(
            api,
            project,
            relationship_payload(source, target, relationship_type),
        )
        assert result["created"] is True
        edge = result["relationship"]
        assert edge["relationship_type"] == relationship_type
        assert edge["created_by_client"] == "claude-code"
        assert edge["created_by_session_id"] == "graph-session"
        assert edge["created_by_model"] == "graph-model"
        assert edge["created_at"].endswith("Z")
        created_edges[relationship_type] = edge

    discovery = add_relationship(
        api,
        project,
        relationship_payload(
            source,
            target,
            "discovered-from",
            context_checkpoint_id=target_created["initial_checkpoint"]["id"],
        ),
    )["relationship"]
    assert discovery["context_checkpoint_id"] == target_created["initial_checkpoint"]["id"]
    assert discovery["context_checkpoint_work_item_id"] == target["id"]
    created_edges["discovered-from"] = discovery

    duplicate = add_relationship(
        api,
        project,
        relationship_payload(source, target, "blocks", session="retry-session"),
    )
    assert duplicate["created"] is False
    assert duplicate["relationship"]["id"] == created_edges["blocks"]["id"]

    reverse_related = add_relationship(
        api,
        project,
        relationship_payload(target, source, "related"),
    )
    assert reverse_related["created"] is False
    assert reverse_related["relationship"]["id"] == created_edges["related"]["id"]
    assert UUID(reverse_related["relationship"]["source_work_item_id"]) < UUID(
        reverse_related["relationship"]["target_work_item_id"]
    )

    incoming = api.get(
        f"{work_path(project, target)}/relationships",
        params={"direction": "incoming", "limit": 100},
    ).json()
    assert incoming["total"] == 4
    assert {item["relationship"]["relationship_type"] for item in incoming["items"]} == {
        "blocks",
        "parent-child",
        "discovered-from",
        "duplicate-of",
    }
    assert all(item["direction"] == "incoming" for item in incoming["items"])
    assert all("prompt" not in item["counterpart"] for item in incoming["items"])

    undirected = api.get(
        f"{work_path(project, target)}/relationships",
        params={"direction": "undirected"},
    ).json()
    assert undirected["total"] == 1
    assert undirected["items"][0]["direction"] == "undirected"

    second_parent = api.post(
        relationship_collection(project),
        json=relationship_payload(other, target, "parent-child"),
    )
    assert second_parent.status_code == 409
    assert second_parent.json()["detail"]["code"] == "parent_already_set"

    guarded = api.post(f"{work_path(project, target)}/delete", json={"expected_version": 1})
    assert guarded.status_code == 409
    assert guarded.json()["detail"]["code"] == "active_relationships"

    for edge in created_edges.values():
        fetched = api.get(f"{relationship_collection(project)}/{edge['id']}")
        assert fetched.status_code == 200
        removed = api.delete(f"{relationship_collection(project)}/{edge['id']}")
        assert removed.json()["removed"] is True
        repeated = api.delete(f"{relationship_collection(project)}/{edge['id']}")
        assert repeated.json()["removed"] is False
    assert api.post(
        f"{work_path(project, target)}/delete", json={"expected_version": 1}
    ).status_code == 200


def test_relationship_validation_project_locality_and_database_constraints(
    api, project, work_payload, postgres_engine
):
    first = create_work(api, project, work_payload, "First endpoint")
    second = create_work(api, project, work_payload, "Second endpoint")
    unrelated = create_work(api, project, work_payload, "Unrelated context")
    first_work = first["work_item"]
    second_work = second["work_item"]

    self_edge = api.post(
        relationship_collection(project),
        json=relationship_payload(first_work, first_work, "blocks"),
    )
    assert self_edge.status_code == 422

    missing_context = api.post(
        relationship_collection(project),
        json=relationship_payload(first_work, second_work, "discovered-from"),
    )
    assert missing_context.status_code == 422
    wrong_context = api.post(
        relationship_collection(project),
        json=relationship_payload(
            first_work,
            second_work,
            "discovered-from",
            context_checkpoint_id=unrelated["initial_checkpoint"]["id"],
        ),
    )
    assert wrong_context.status_code == 404

    other_project = api.post("/api/v1/projects", json={"name": "Other graph project"}).json()
    cross = create_work(api, other_project, work_payload, "Cross project")["work_item"]
    cross_project = api.post(
        relationship_collection(project),
        json=relationship_payload(first_work, cross, "blocks"),
    )
    assert cross_project.status_code == 404

    base_values = {
        "id": uuid4(),
        "project_id": project["id"],
        "type": "related",
        "source": first_work["id"],
        "target": second_work["id"],
        "client": "raw-sql",
        "session": "raw-session",
    }
    ordered_endpoint_ids = sorted(
        [UUID(first_work["id"]), UUID(second_work["id"])]
    )
    with pytest.raises(DBAPIError):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO work_relationships (
                        id, project_id, relationship_type,
                        source_work_item_id, target_work_item_id,
                        created_by_client, created_by_session_id
                    ) VALUES (
                        :id, :project_id, :type, :source, :target, :client, :session
                    )
                    """
                ),
                {
                    **base_values,
                    "source": ordered_endpoint_ids[1],
                    "target": ordered_endpoint_ids[0],
                },
            )

    with pytest.raises(DBAPIError):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO work_relationships (
                        id, project_id, relationship_type,
                        source_work_item_id, target_work_item_id,
                        context_checkpoint_work_item_id, context_checkpoint_id,
                        created_by_client, created_by_session_id
                    ) VALUES (
                        :id, :project_id, 'discovered-from', :source, :target,
                        :context_work, :context_id, :client, :session
                    )
                    """
                ),
                {
                    **base_values,
                    "id": uuid4(),
                    "context_work": unrelated["work_item"]["id"],
                    "context_id": unrelated["initial_checkpoint"]["id"],
                },
            )

    with pytest.raises(DBAPIError):
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO work_relationships (
                        id, project_id, relationship_type,
                        source_work_item_id, target_work_item_id,
                        created_by_client, created_by_session_id
                    ) VALUES (
                        :id, :project_id, 'blocks', :source, :target, :client, :session
                    )
                    """
                ),
                {
                    **base_values,
                    "id": uuid4(),
                    "target": cross["id"],
                },
            )


@pytest.mark.parametrize("relationship_type", ["blocks", "parent-child"])
def test_direct_transitive_and_concurrent_cycles_are_rejected(
    api, project, work_payload, relationship_type
):
    first = create_work(api, project, work_payload, f"{relationship_type} A")["work_item"]
    second = create_work(api, project, work_payload, f"{relationship_type} B")["work_item"]
    third = create_work(api, project, work_payload, f"{relationship_type} C")["work_item"]
    add_relationship(api, project, relationship_payload(first, second, relationship_type))
    reciprocal = api.post(
        relationship_collection(project),
        json=relationship_payload(second, first, relationship_type),
    )
    assert reciprocal.status_code == 409
    assert reciprocal.json()["detail"]["code"] == "relationship_cycle"
    add_relationship(api, project, relationship_payload(second, third, relationship_type))
    transitive = api.post(
        relationship_collection(project),
        json=relationship_payload(third, first, relationship_type),
    )
    assert transitive.status_code == 409
    assert transitive.json()["detail"]["code"] == "relationship_cycle"

    left = create_work(api, project, work_payload, f"Concurrent {relationship_type} left")[
        "work_item"
    ]
    right = create_work(api, project, work_payload, f"Concurrent {relationship_type} right")[
        "work_item"
    ]
    barrier = Barrier(2)

    def insert(payload):
        barrier.wait(timeout=5)
        return api.post(relationship_collection(project), json=payload)

    payloads = [
        relationship_payload(left, right, relationship_type, session="concurrent-left"),
        relationship_payload(right, left, relationship_type, session="concurrent-right"),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(insert, payloads))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert next(response for response in responses if response.status_code == 409).json()[
        "detail"
    ]["code"] == "relationship_cycle"


def test_blocker_readiness_claim_completion_resolution_and_active_overlap(
    api, project, work_payload
):
    blocker = create_work(api, project, work_payload, "Prerequisite")["work_item"]
    target = create_work(api, project, work_payload, "Dependent")["work_item"]
    target_endpoint = work_path(project, target)
    edge = add_relationship(
        api, project, relationship_payload(blocker, target, "blocks")
    )["relationship"]

    blocked = api.get(f"{target_endpoint}/context").json()
    assert blocked["readiness"]["is_blocked"] is True
    assert blocked["readiness"]["unresolved_blocker_count"] == 1
    assert blocked["readiness"]["is_ready"] is False
    assert blocked["readiness"]["display_state"] == "blocked"
    assert blocked["relationship_counts"]["incoming"] == 1
    assert blocked["incoming_relationships"][0]["counterpart"]["id"] == blocker["id"]
    denied_claim = api.post(f"{target_endpoint}/claim", json=claim_payload("blocked-claim"))
    assert denied_claim.status_code == 409
    assert denied_claim.json()["detail"]["code"] == "work_blocked"
    denied_completion = api.post(
        f"{target_endpoint}/complete", json=completion_payload()
    )
    assert denied_completion.status_code == 409
    assert denied_completion.json()["detail"]["code"] == "work_blocked"

    retired = api.patch(
        work_path(project, blocker), json={"expected_version": 1, "status": "wont-do"}
    )
    assert retired.status_code == 200
    assert api.get(f"{target_endpoint}/context").json()["readiness"]["is_blocked"] is True
    reopened_from_retired = api.patch(
        work_path(project, blocker), json={"expected_version": 2, "status": "open"}
    )
    assert reopened_from_retired.status_code == 200
    promoted = api.patch(
        work_path(project, blocker), json={"expected_version": 3, "status": "promoted"}
    )
    assert promoted.status_code == 200
    assert api.get(f"{target_endpoint}/context").json()["readiness"]["is_blocked"] is True
    reopened = api.patch(
        work_path(project, blocker), json={"expected_version": 4, "status": "open"}
    )
    assert reopened.status_code == 200
    completed_blocker = api.post(
        f"{work_path(project, blocker)}/complete", json=completion_payload(version=5)
    )
    assert completed_blocker.status_code == 200
    assert api.get(f"{target_endpoint}/context").json()["readiness"]["is_ready"] is True

    api.delete(f"{relationship_collection(project)}/{edge['id']}")
    nonblocking = add_relationship(
        api, project, relationship_payload(blocker, target, "related")
    )["relationship"]
    assert api.get(f"{target_endpoint}/context").json()["readiness"]["is_ready"] is True
    api.delete(f"{relationship_collection(project)}/{nonblocking['id']}")

    active_target = create_work(api, project, work_payload, "Active dependent")["work_item"]
    active_endpoint = work_path(project, active_target)
    claim_request = claim_payload("active-before-blocker")
    receipt = api.post(f"{active_endpoint}/claim", json=claim_request).json()
    active_blocker = create_work(api, project, work_payload, "Active prerequisite")["work_item"]
    active_edge = add_relationship(
        api, project, relationship_payload(active_blocker, active_target, "blocks")
    )["relationship"]
    overlap = api.get(f"{active_endpoint}/context").json()["readiness"]
    assert overlap["has_active_lease"] is True
    assert overlap["is_blocked"] is True
    assert overlap["is_ready"] is False
    assert overlap["display_state"] == "blocked"
    replay = api.post(f"{active_endpoint}/claim", json=claim_request)
    assert replay.status_code == 200
    assert replay.json()["lease_token"] == receipt["lease_token"]
    new_claim = api.post(f"{active_endpoint}/claim", json=claim_payload("new-claim"))
    assert new_claim.status_code == 409
    assert new_claim.json()["detail"]["code"] == "work_blocked"
    assert api.post(
        f"{active_endpoint}/release-claim", json={"lease_token": receipt["lease_token"]}
    ).json()["released"] is True
    api.delete(f"{relationship_collection(project)}/{active_edge['id']}")
    assert api.get(f"{active_endpoint}/context").json()["readiness"]["is_ready"] is True
    restored_claim = api.post(
        f"{active_endpoint}/claim", json=claim_payload("after-blocker-removal")
    )
    assert restored_claim.status_code == 200
    assert api.post(
        f"{active_endpoint}/release-claim",
        json={"lease_token": restored_claim.json()["lease_token"]},
    ).json()["released"] is True


def test_atomic_linked_creation_hierarchy_filters_and_search_ancestry(
    api, project, work_payload
):
    root_created = create_work(api, project, work_payload, "Terminal root")
    root = root_created["work_item"]
    root_context_id = root_created["initial_checkpoint"]["id"]
    assert api.patch(
        work_path(project, root), json={"expected_version": 1, "status": "promoted"}
    ).status_code == 200

    child_created = create_work(
        api,
        project,
        work_payload,
        "Open child",
        initial_relationships=[
            {
                "type": "parent-child",
                "direction": "incoming",
                "other_work_item_id": root["id"],
            },
            {
                "type": "discovered-from",
                "direction": "outgoing",
                "other_work_item_id": root["id"],
                "context_checkpoint_id": root_context_id,
            },
        ],
    )
    child = child_created["work_item"]
    assert len(child_created["initial_relationships"]) == 2
    for edge in child_created["initial_relationships"]:
        assert edge["created_by_client"] == work_payload["initial_checkpoint"]["source_client"]
        assert edge["created_by_session_id"] == child_created["initial_checkpoint"][
            "source_session_id"
        ]

    grandchild_created = create_work(
        api,
        project,
        work_payload,
        "Deep searchable descendant",
        initial_relationships=[
            {
                "type": "parent-child",
                "direction": "incoming",
                "other_work_item_id": child["id"],
            }
        ],
    )
    grandchild = grandchild_created["work_item"]

    roots = api.get(
        work_collection(project), params={"view": "roots", "status": "open"}
    )
    assert roots.status_code == 200, roots.text
    root_page = roots.json()
    assert root_page["total"] == 1
    root_entry = root_page["items"][0]
    assert root_entry["summary"]["work_item"]["id"] == root["id"]
    assert root_entry["self_matches_filter"] is False
    assert root_entry["has_matching_descendants"] is True

    children = api.get(
        f"{work_path(project, root)}/children", params={"status": "open"}
    ).json()
    assert children["total"] == 1
    assert children["items"][0]["summary"]["work_item"]["id"] == child["id"]
    assert children["items"][0]["self_matches_filter"] is True
    assert children["items"][0]["has_matching_descendants"] is True

    search = api.get(
        work_collection(project),
        params={"q": "Deep searchable descendant", "status": "all", "view": "full"},
    )
    assert search.status_code == 200, search.text
    hit = next(
        item for item in search.json()["items"] if item["work_item"]["id"] == grandchild["id"]
    )
    assert [item["id"] for item in hit["ancestor_path"]] == [root["id"], child["id"]]
    assert hit["ancestor_path_truncated"] is False
    rejected = api.get(
        work_collection(project), params={"q": "descendant", "view": "roots"}
    )
    assert rejected.status_code == 422

    before_total = api.get(
        work_collection(project), params={"status": "all", "q": "Atomic rollback marker"}
    ).json()["total"]
    failed_payload = {
        **work_payload,
        "title": "Atomic rollback marker",
        "initial_checkpoint": {
            **work_payload["initial_checkpoint"],
            "source_session_id": "atomic-failure",
        },
        "initial_relationships": [
            {
                "type": "parent-child",
                "direction": "incoming",
                "other_work_item_id": root["id"],
            },
            {
                "type": "parent-child",
                "direction": "incoming",
                "other_work_item_id": child["id"],
            }
        ],
    }
    failed = api.post(work_collection(project), json=failed_payload)
    assert failed.status_code == 409
    assert failed.json()["detail"]["code"] == "parent_already_set"
    after_total = api.get(
        work_collection(project), params={"status": "all", "q": "Atomic rollback marker"}
    ).json()["total"]
    assert before_total == after_total == 0


def test_nonblocking_relationship_types_preserve_readiness(api, project, work_payload):
    origin_created = create_work(api, project, work_payload, "Nonblocking origin")
    dependent_created = create_work(api, project, work_payload, "Nonblocking dependent")
    origin = origin_created["work_item"]
    dependent = dependent_created["work_item"]

    payloads = [
        relationship_payload(origin, dependent, "parent-child"),
        relationship_payload(
            dependent,
            origin,
            "discovered-from",
            context_checkpoint_id=origin_created["initial_checkpoint"]["id"],
        ),
        relationship_payload(dependent, origin, "duplicate-of"),
        relationship_payload(dependent, origin, "related"),
    ]
    for payload in payloads:
        add_relationship(api, project, payload)

    context = api.get(f"{work_path(project, dependent)}/context").json()
    assert context["readiness"]["is_ready"] is True
    assert context["readiness"]["is_blocked"] is False
    assert context["readiness"]["unresolved_blocker_count"] == 0
    assert context["relationship_counts"] == {
        "incoming": 1,
        "outgoing": 2,
        "undirected": 1,
        "total": 4,
    }


def test_context_relationship_projection_is_bounded_with_exact_counts(
    api, project, work_payload
):
    anchor = create_work(api, project, work_payload, "High degree anchor")["work_item"]
    for index in range(51):
        counterpart = create_work(
            api,
            project,
            work_payload,
            f"High degree counterpart {index}",
        )["work_item"]
        add_relationship(
            api,
            project,
            relationship_payload(anchor, counterpart, "related"),
        )

    context = api.get(f"{work_path(project, anchor)}/context")
    assert context.status_code == 200, context.text
    body = context.json()
    assert body["relationship_counts"] == {
        "incoming": 0,
        "outgoing": 0,
        "undirected": 51,
        "total": 51,
    }
    assert len(body["undirected_relationships"]) == 50
    assert all(
        "prompt" not in relationship["counterpart"]
        for relationship in body["undirected_relationships"]
    )

    full_page = api.get(
        f"{work_path(project, anchor)}/relationships",
        params={"direction": "undirected", "limit": 100},
    )
    assert full_page.status_code == 200
    assert full_page.json()["total"] == 51
    assert len(full_page.json()["items"]) == 51


def test_relationship_model_parity_and_indexes(postgres_engine):
    with Session(postgres_engine) as database:
        constraints = set(
            database.scalars(
                text(
                    """
                    SELECT conname
                    FROM pg_constraint
                    WHERE conrelid = 'work_relationships'::regclass
                    """
                )
            )
        )
        assert {
            "pk_work_relationships",
            "uq_work_relationships_identity",
            "fk_work_relationships_source_work_item",
            "fk_work_relationships_target_work_item",
            "fk_work_relationships_context_checkpoint",
            "ck_work_relationships_discovery_context",
            "ck_work_relationships_related_normalized",
        } <= constraints
        indexes = set(
            database.scalars(
                text(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename = 'work_relationships'
                    """
                )
            )
        )
        assert {
            "uq_work_relationships_one_parent",
            "ix_work_relationships_source",
            "ix_work_relationships_target",
        } <= indexes
