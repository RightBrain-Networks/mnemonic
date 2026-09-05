"""Phase 8 hierarchy presentation correctness matrix on real PostgreSQL."""

from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from .report_fixtures import reported

pytestmark = pytest.mark.postgres


def work_collection(project: dict) -> str:
    return f"/api/v1/projects/{project['id']}/work-items"


def work_path(project: dict, work: dict) -> str:
    return f"{work_collection(project)}/{work['work_item']['id']}"


def relationship_collection(project: dict) -> str:
    return f"/api/v1/projects/{project['id']}/relationships"


def create_work(
    api,
    project: dict,
    base_payload: dict,
    *,
    title: str,
    priority: int = 30,
    status: str = "pending",
    source_client: str = "hierarchy-matrix",
    source_session_id: str | None = None,
    tags: list[str] | None = None,
    parent: dict | None = None,
) -> dict:
    checkpoint = {
        **base_payload["initial_checkpoint"],
        "prompt": f"Durable hierarchy context for {title}.",
        "source_client": source_client,
        "source_session_id": source_session_id or f"matrix-{title.lower().replace(' ', '-')}",
        "tags": tags or ["hierarchy-matrix"],
    }
    payload = {
        **base_payload,
        "title": title,
        "summary": f"Hierarchy matrix objective for {title}.",
        "priority": priority,
        "status": "pending",
        "initial_checkpoint": checkpoint,
    }
    if parent is not None:
        payload["initial_relationships"] = [
            {
                "type": "parent-child",
                "direction": "incoming",
                "other_work_item_id": parent["work_item"]["id"],
            }
        ]
    response = api.post(work_collection(project), json=payload)
    assert response.status_code == 201, response.text
    created = response.json()
    if status != "pending":
        endpoint = work_path(project, created)
        closed = api.patch(endpoint, json=reported(
            {"expected_version": 1, "status": status}, retirement=True,
        ))
        assert closed.status_code == 200, closed.text
        created["work_item"] = {key: value for key, value in closed.json().items()
                                if key != "job_completion_report"}
    return created


def add_relationship(
    api,
    project: dict,
    source: dict,
    target: dict,
    relationship_type: str,
    *,
    context_checkpoint_id: str | None = None,
) -> dict:
    payload = {
        "relationship_type": relationship_type,
        "source_work_item_id": source["work_item"]["id"],
        "target_work_item_id": target["work_item"]["id"],
        "created_by_client": "hierarchy-matrix",
        "created_by_session_id": "hierarchy-matrix",
    }
    if context_checkpoint_id is not None:
        payload["context_checkpoint_id"] = context_checkpoint_id
    response = api.post(relationship_collection(project), json=payload)
    assert response.status_code == 200, response.text
    return response.json()["relationship"]


def complete_work(api, project: dict, created: dict) -> None:
    response = api.post(
        f"{work_path(project, created)}/complete",
        json=reported({
            "expected_version": created["work_item"]["version"],
            "checkpoint": {
                "prompt": f"Completed {created['work_item']['title']} for the hierarchy matrix.",
                "source_client": "hierarchy-matrix",
                "source_session_id": "hierarchy-completion",
            },
        }),
    )
    assert response.status_code == 200, response.text


def defer_work(api, project: dict, created: dict) -> None:
    response = api.post(
        f"{work_path(project, created)}/defer",
        json={"expected_version": created["work_item"]["version"]},
    )
    assert response.status_code == 200, response.text


def claim_work(api, project: dict, created: dict, request_id: str) -> dict:
    response = api.post(
        f"{work_path(project, created)}/claim",
        json={
            "holder_client": "hierarchy-matrix",
            "holder_session_id": request_id,
            "claim_request_id": request_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def request_gate(api, project: dict, created: dict, question: str) -> dict:
    response = api.post(
        f"{work_path(project, created)}/gates",
        json={
            "question": question,
            "requested_by_client": "hierarchy-matrix",
            "requested_by_session_id": "hierarchy-matrix",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def resolve_gate(api, project: dict, created: dict, gate: dict) -> None:
    response = api.post(
        f"{work_path(project, created)}/gates/{gate['id']}/resolve",
        json={
            "resolution": "Use the reviewed hierarchy boundary.",
            "resolved_by_client": "dashboard",
            "resolved_by_session_id": "hierarchy-human",
            "reviewed_context_revision": gate["current_context_revision"],
        },
    )
    assert response.status_code == 200, response.text


def hierarchy_items(api, path: str, **params) -> dict:
    response = api.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def hierarchy_entry(page: dict, work_id: str) -> dict:
    return next(
        item
        for item in page["items"]
        if item["summary"]["work_item"]["id"] == work_id
    )


def empty_presentation(*, discovered: bool = False) -> dict:
    return {
        "direct_child_count": 0,
        "descendant_count": 0,
        "blocked_descendant_count": 0,
        "active_descendant_count": 0,
        "completed_descendant_count": 0,
        "discovered_descendant_count": 0,
        "branch_unresolved_human_gate_count": 0,
        "branch_merged_duplicate_count": 0,
        "is_discovered_work": discovered,
        "discovered_from_parent": False,
        "next_active_descendant_lease_expires_at": None,
    }


@pytest.mark.postgres
def test_hierarchy_pages_use_one_coherent_aggregate_statement_after_preflight(
    api,
    project,
    work_payload,
    postgres_engine,
):
    root = create_work(
        api,
        project,
        work_payload,
        title="Counted hierarchy root",
        tags=["query-count-root"],
    )
    create_work(
        api,
        project,
        work_payload,
        title="Counted hierarchy child",
        tags=["query-count-child"],
        parent=root,
    )
    cases = (
        (
            work_collection(project),
            {"view": "roots", "status": "all"},
        ),
        (
            f"{work_path(project, root)}/children",
            {"status": "all"},
        ),
        (
            work_collection(project),
            {
                "view": "roots",
                "status": "all",
                "tag": "query-count-child",
            },
        ),
    )

    for path, params in cases:
        statements: list[str] = []

        def observe_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
            captured=statements,
        ):
            captured.append(statement)

        event.listen(postgres_engine, "before_cursor_execute", observe_statement)
        try:
            response = api.get(path, params=params)
        finally:
            event.remove(
                postgres_engine,
                "before_cursor_execute",
                observe_statement,
            )

        assert response.status_code == 200, response.text
        normalized = [" ".join(statement.split()) for statement in statements]
        assert normalized[0] == (
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )
        assert normalized[-3:-1] == [
            "SET LOCAL jit = off",
            "SET LOCAL statement_timeout = '5000ms'",
        ]
        assert sum(statement.startswith("WITH RECURSIVE") for statement in normalized) == 1
        assert normalized[-1].startswith("WITH RECURSIVE")
        assert "candidate_branches AS MATERIALIZED" in normalized[-1]


@pytest.mark.postgres
def test_hierarchy_query_cancellation_returns_typed_timeout_and_recovers(
    api,
    project,
    work_payload,
    postgres_engine,
):
    create_work(api, project, work_payload, title="Forced hierarchy timeout")

    class ForcedQueryCanceled(Exception):
        sqlstate = "57014"

    def cancel_hierarchy_query(
        _connection,
        _cursor,
        statement,
        parameters,
        _context,
        _executemany,
    ):
        if "candidate_branches AS MATERIALIZED" in statement:
            raise DBAPIError(
                statement,
                parameters,
                ForcedQueryCanceled("forced query cancellation"),
                connection_invalidated=False,
            )

    event.listen(postgres_engine, "before_cursor_execute", cancel_hierarchy_query)
    try:
        timed_out = api.get(
            work_collection(project),
            params={"view": "roots", "status": "all"},
        )
    finally:
        event.remove(
            postgres_engine,
            "before_cursor_execute",
            cancel_hierarchy_query,
        )

    assert timed_out.status_code == 503
    assert timed_out.json() == {
        "detail": {
            "code": "hierarchy_timeout",
            "message": (
                "Hierarchy traversal exceeded its safety limit; narrow the view or "
                "investigate the graph."
            ),
            "context": {},
        }
    }
    recovered = api.get(
        work_collection(project),
        params={"view": "roots", "status": "all"},
    )
    assert recovered.status_code == 200, recovered.text


@pytest.mark.postgres
def test_rollups_preserve_lifecycle_and_nonexclusive_operational_facts_without_multiplication(
    api,
    project,
    work_payload,
    postgres_engine,
):
    root = create_work(api, project, work_payload, title="Matrix root")
    deferred = create_work(
        api, project, work_payload, title="Deferred descendant", parent=root
    )
    completed = create_work(
        api, project, work_payload, title="Done descendant", parent=root
    )
    wont_do = create_work(
        api,
        project,
        work_payload,
        title="Wont do descendant",
        status="wont-do",
        parent=root,
    )
    promoted = create_work(
        api,
        project,
        work_payload,
        title="Promoted descendant",
        status="promoted",
        parent=root,
    )
    overlap = create_work(
        api, project, work_payload, title="Blocked active gated descendant", parent=root
    )
    expired = create_work(
        api, project, work_payload, title="Expired gated descendant", parent=root
    )
    resolved_target = create_work(
        api, project, work_payload, title="Resolved blocker target", parent=root
    )

    defer_work(api, project, deferred)
    complete_work(api, project, completed)
    active_claim = claim_work(api, project, overlap, "active-overlap")

    unresolved_blockers = [
        create_work(api, project, work_payload, title=f"Open blocker {index}")
        for index in range(2)
    ]
    resolved_blocker = create_work(api, project, work_payload, title="Resolved blocker")
    complete_work(api, project, resolved_blocker)
    for blocker in unresolved_blockers:
        add_relationship(api, project, blocker, overlap, "blocks")
    add_relationship(api, project, resolved_blocker, overlap, "blocks")
    add_relationship(api, project, resolved_blocker, resolved_target, "blocks")

    discovery_origins = [
        create_work(api, project, work_payload, title=f"Discovery origin {index}")
        for index in range(2)
    ]
    for origin in discovery_origins:
        add_relationship(
            api,
            project,
            overlap,
            origin,
            "discovered-from",
            context_checkpoint_id=origin["initial_checkpoint"]["id"],
        )

    claim_work(api, project, expired, "expired-overlap")
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE work_leases "
                "SET acquired_at = clock_timestamp() - interval '3 seconds', "
                "renewed_at = clock_timestamp() - interval '2 seconds', "
                "expires_at = clock_timestamp() - interval '1 second' "
                "WHERE work_item_id = CAST(:work_item_id AS uuid)"
            ),
            {"work_item_id": expired["work_item"]["id"]},
        )

    request_gate(api, project, root, "Which root boundary should a human approve?")
    request_gate(api, project, overlap, "Which blocked-active boundary is correct?")
    request_gate(api, project, overlap, "Which second independent choice is required?")
    resolved_gate = request_gate(api, project, overlap, "Which already-reviewed choice was used?")
    resolve_gate(api, project, overlap, resolved_gate)
    request_gate(api, project, expired, "What should happen after the retained lease expired?")

    roots = hierarchy_items(
        api,
        work_collection(project),
        view="roots",
        status="all",
        limit=100,
    )
    root_entry = hierarchy_entry(roots, root["work_item"]["id"])
    expected_presentation = {
        "direct_child_count": 7,
        "descendant_count": 7,
        "blocked_descendant_count": 1,
        "active_descendant_count": 1,
        "completed_descendant_count": 1,
        "discovered_descendant_count": 1,
        "branch_unresolved_human_gate_count": 4,
        "branch_merged_duplicate_count": 0,
        "is_discovered_work": False,
        "discovered_from_parent": False,
        "next_active_descendant_lease_expires_at": active_claim["expires_at"],
    }
    assert root_entry["presentation"] == expected_presentation
    assert root_entry["summary"]["readiness"]["display_state"] == "waiting"

    children = hierarchy_items(
        api,
        f"{work_path(project, root)}/children",
        status="all",
        limit=100,
    )
    assert children["total"] == 7
    by_id = {
        item["summary"]["work_item"]["id"]: item for item in children["items"]
    }
    overlap_entry = by_id[overlap["work_item"]["id"]]
    overlap_readiness = overlap_entry["summary"]["readiness"]
    assert overlap_readiness["unresolved_blocker_count"] == 2
    assert overlap_readiness["unresolved_gate_count"] == 2
    assert overlap_readiness["is_blocked"] is True
    assert overlap_readiness["is_gated"] is True
    assert overlap_readiness["has_active_lease"] is True
    assert overlap_readiness["display_state"] == "waiting"
    assert overlap_entry["presentation"] == {
        **empty_presentation(discovered=True),
        "branch_unresolved_human_gate_count": 2,
    }

    expired_readiness = by_id[expired["work_item"]["id"]]["summary"]["readiness"]
    assert expired_readiness["has_active_lease"] is False
    assert expired_readiness["has_dropped_lease"] is True
    assert expired_readiness["unresolved_gate_count"] == 1
    assert expired_readiness["display_state"] == "waiting"
    resolved_readiness = by_id[resolved_target["work_item"]["id"]]["summary"][
        "readiness"
    ]
    assert resolved_readiness["unresolved_blocker_count"] == 0
    assert resolved_readiness["is_blocked"] is False
    assert resolved_readiness["is_ready"] is True

    assert by_id[deferred["work_item"]["id"]]["summary"]["readiness"][
        "display_state"
    ] == "deferred"
    assert by_id[completed["work_item"]["id"]]["summary"]["readiness"][
        "display_state"
    ] == "done"
    assert by_id[wont_do["work_item"]["id"]]["summary"]["readiness"][
        "display_state"
    ] == "wont-do"
    assert by_id[promoted["work_item"]["id"]]["summary"]["readiness"][
        "display_state"
    ] == "promoted"

    expected_root_sets = {
        "deferred": {root["work_item"]["id"]},
        "done": {root["work_item"]["id"], resolved_blocker["work_item"]["id"]},
        "wont-do": {root["work_item"]["id"]},
        "promoted": {root["work_item"]["id"]},
        "active": {root["work_item"]["id"]},
        "dropped": {root["work_item"]["id"]},
    }
    for status, expected_ids in expected_root_sets.items():
        filtered = hierarchy_items(
            api,
            work_collection(project),
            view="roots",
            status=status,
            limit=100,
        )
        actual_ids = {
            item["summary"]["work_item"]["id"] for item in filtered["items"]
        }
        assert filtered["total"] == len(expected_ids)
        assert actual_ids == expected_ids
        filtered_root = hierarchy_entry(filtered, root["work_item"]["id"])
        assert filtered_root["self_matches_filter"] is False
        assert filtered_root["has_matching_descendants"] is True
        assert filtered_root["presentation"] == expected_presentation


@pytest.mark.postgres
def test_discovery_labels_require_explicit_edges_and_never_infer_parentage(
    api,
    project,
    work_payload,
):
    parent = create_work(api, project, work_payload, title="Discovery parent")
    origin = create_work(api, project, work_payload, title="Other discovery branch")
    planned = create_work(
        api, project, work_payload, title="Planned child", parent=parent
    )
    from_parent = create_work(
        api, project, work_payload, title="Discovered from parent", parent=parent
    )
    from_other = create_work(
        api, project, work_payload, title="Discovered elsewhere", parent=parent
    )
    ungrouped = create_work(api, project, work_payload, title="Ungrouped discovery")

    add_relationship(
        api,
        project,
        from_parent,
        parent,
        "discovered-from",
        context_checkpoint_id=parent["initial_checkpoint"]["id"],
    )
    for discovered in (from_other, ungrouped):
        add_relationship(
            api,
            project,
            discovered,
            origin,
            "discovered-from",
            context_checkpoint_id=origin["initial_checkpoint"]["id"],
        )

    roots = hierarchy_items(
        api,
        work_collection(project),
        view="roots",
        status="all",
        limit=100,
    )
    assert roots["total"] == 3
    assert {
        item["summary"]["work_item"]["id"] for item in roots["items"]
    } == {
        parent["work_item"]["id"],
        origin["work_item"]["id"],
        ungrouped["work_item"]["id"],
    }
    parent_entry = hierarchy_entry(roots, parent["work_item"]["id"])
    assert parent_entry["presentation"] == {
        **empty_presentation(),
        "direct_child_count": 3,
        "descendant_count": 3,
        "discovered_descendant_count": 2,
    }
    ungrouped_entry = hierarchy_entry(roots, ungrouped["work_item"]["id"])
    assert ungrouped_entry["presentation"] == empty_presentation(discovered=True)
    assert ungrouped_entry["summary"]["ancestor_path"] == []
    assert ungrouped_entry["summary"]["ancestor_path_truncated"] is False

    children = hierarchy_items(
        api,
        f"{work_path(project, parent)}/children",
        status="all",
        limit=100,
    )
    assert children["total"] == 3
    child_entries = {
        item["summary"]["work_item"]["id"]: item for item in children["items"]
    }
    planned_presentation = child_entries[planned["work_item"]["id"]]["presentation"]
    parent_presentation = child_entries[from_parent["work_item"]["id"]][
        "presentation"
    ]
    other_presentation = child_entries[from_other["work_item"]["id"]]["presentation"]
    assert planned_presentation == empty_presentation()
    assert parent_presentation == {
        **empty_presentation(discovered=True),
        "discovered_from_parent": True,
    }
    assert other_presentation == empty_presentation(discovered=True)


@pytest.mark.postgres
def test_lifecycle_source_and_tag_filters_qualify_branches_through_deep_matches(
    api,
    project,
    work_payload,
):
    root = create_work(api, project, work_payload, title="Filter root")
    middle = create_work(
        api, project, work_payload, title="Filter middle", parent=root
    )
    deep = create_work(
        api, project, work_payload, title="Filter deep", parent=middle
    )
    leaf = create_work(
        api,
        project,
        work_payload,
        title="Deep only match",
        source_client="deep-filter-client",
        source_session_id="deep-filter-session",
        tags=["deep-only-tag"],
        parent=deep,
    )
    defer_work(api, project, leaf)

    expected_root_presentation = {
        **empty_presentation(),
        "direct_child_count": 1,
        "descendant_count": 3,
    }
    filters = (
        {"status": "deferred"},
        {"status": "all", "tag": "DEEP-ONLY-TAG"},
        {
            "status": "all",
            "source_client": "deep-filter-client",
            "source_session_id": "deep-filter-session",
        },
    )
    for filter_params in filters:
        roots = hierarchy_items(
            api,
            work_collection(project),
            view="roots",
            limit=100,
            **filter_params,
        )
        assert roots["total"] == 1
        root_entry = hierarchy_entry(roots, root["work_item"]["id"])
        assert root_entry["self_matches_filter"] is False
        assert root_entry["has_matching_descendants"] is True
        assert root_entry["presentation"] == expected_root_presentation

        root_children = hierarchy_items(
            api,
            f"{work_path(project, root)}/children",
            limit=100,
            **filter_params,
        )
        assert root_children["total"] == 1
        middle_entry = hierarchy_entry(root_children, middle["work_item"]["id"])
        assert middle_entry["self_matches_filter"] is False
        assert middle_entry["has_matching_descendants"] is True
        assert middle_entry["presentation"]["descendant_count"] == 2

    deep_children = hierarchy_items(
        api,
        f"{work_path(project, deep)}/children",
        status="deferred",
        limit=100,
    )
    leaf_entry = hierarchy_entry(deep_children, leaf["work_item"]["id"])
    assert deep_children["total"] == 1
    assert leaf_entry["self_matches_filter"] is True
    assert leaf_entry["has_matching_descendants"] is False
    assert leaf_entry["presentation"] == empty_presentation()

    all_children = hierarchy_items(
        api,
        f"{work_path(project, root)}/children",
        status="all",
        limit=100,
    )
    all_middle = hierarchy_entry(all_children, middle["work_item"]["id"])
    assert all_children["total"] == 1
    assert all_middle["self_matches_filter"] is True
    assert all_middle["has_matching_descendants"] is True
    assert all_middle["presentation"]["descendant_count"] == 2


@pytest.mark.postgres
def test_server_rollups_traverse_a_valid_tree_beyond_the_browser_depth_guard(
    api,
    project,
    work_payload,
):
    root = create_work(api, project, work_payload, title="Depth zero root")
    parent = root
    nodes = []
    for depth in range(1, 53):
        node = create_work(
            api,
            project,
            work_payload,
            title=f"Depth {depth} node",
            parent=parent,
        )
        nodes.append(node)
        parent = node
    leaf = nodes[-1]
    add_relationship(
        api,
        project,
        leaf,
        root,
        "discovered-from",
        context_checkpoint_id=root["initial_checkpoint"]["id"],
    )
    complete_work(api, project, leaf)

    roots = hierarchy_items(
        api,
        work_collection(project),
        view="roots",
        status="all",
        limit=100,
    )
    assert roots["total"] == 1
    root_entry = hierarchy_entry(roots, root["work_item"]["id"])
    assert root_entry["presentation"] == {
        **empty_presentation(),
        "direct_child_count": 1,
        "descendant_count": 52,
        "completed_descendant_count": 1,
        "discovered_descendant_count": 1,
    }
    assert root_entry["summary"]["ancestor_path_truncated"] is False

    done_roots = hierarchy_items(
        api,
        work_collection(project),
        view="roots",
        status="done",
        limit=100,
    )
    done_root = hierarchy_entry(done_roots, root["work_item"]["id"])
    assert done_roots["total"] == 1
    assert done_root["self_matches_filter"] is False
    assert done_root["has_matching_descendants"] is True
    assert done_root["presentation"] == root_entry["presentation"]

    first_level = hierarchy_items(
        api,
        f"{work_path(project, root)}/children",
        status="done",
        limit=100,
    )
    first_entry = hierarchy_entry(first_level, nodes[0]["work_item"]["id"])
    assert first_level["total"] == 1
    assert first_entry["self_matches_filter"] is False
    assert first_entry["has_matching_descendants"] is True
    assert first_entry["presentation"] == {
        **empty_presentation(),
        "direct_child_count": 1,
        "descendant_count": 51,
        "completed_descendant_count": 1,
        "discovered_descendant_count": 1,
    }


def sort_key(work: dict, sort: str) -> tuple:
    updated = datetime.fromisoformat(work["updated_at"].replace("Z", "+00:00"))
    identifier = UUID(work["id"]).int
    if sort == "created":
        created = datetime.fromisoformat(work["created_at"].replace("Z", "+00:00"))
        return created, identifier
    if sort == "priority":
        return work["priority"], updated, identifier
    return updated, identifier


def assert_two_page_order(api, path: str, works: list[dict], *, roots: bool) -> list[dict]:
    observed: dict[str, dict] = {}
    for sort in ("updated", "created", "priority"):
        params = {"status": "all", "sort": sort, "limit": 2}
        if roots:
            params["view"] = "roots"
        first = hierarchy_items(api, path, offset=0, **params)
        later = hierarchy_items(api, path, offset=2, **params)
        expected = sorted(works, key=lambda work: sort_key(work, sort), reverse=True)
        entries = first["items"] + later["items"]
        ids = [item["summary"]["work_item"]["id"] for item in entries]
        assert first["total"] == later["total"] == 4
        assert first["limit"] == later["limit"] == 2
        assert first["offset"] == 0
        assert later["offset"] == 2
        assert ids == [work["id"] for work in expected]
        assert len(ids) == len(set(ids)) == 4
        for item in entries:
            observed[item["summary"]["work_item"]["id"]] = item
    return list(observed.values())


@pytest.mark.postgres
def test_root_and_child_first_and_later_pages_preserve_every_sort(
    api,
    project,
    work_payload,
):
    roots = [
        create_work(
            api,
            project,
            work_payload,
            title=f"Paged root {index}",
            priority=priority,
        )
        for index, priority in enumerate((10, 80, 50, 80))
    ]
    children = [
        create_work(
            api,
            project,
            work_payload,
            title=f"Paged child {index}",
            priority=priority,
            parent=roots[0],
        )
        for index, priority in enumerate((40, 40, 90, 10))
    ]

    for index in (2, 0, 3, 1):
        current = api.get(work_path(project, roots[index])).json()["work_item"]
        response = api.patch(
            work_path(project, roots[index]),
            json={
                "expected_version": current["version"],
                "title": f"Updated paged root {index}",
            },
        )
        assert response.status_code == 200, response.text
    for index in (1, 3, 0, 2):
        current = api.get(work_path(project, children[index])).json()["work_item"]
        response = api.patch(
            work_path(project, children[index]),
            json={
                "expected_version": current["version"],
                "title": f"Updated paged child {index}",
            },
        )
        assert response.status_code == 200, response.text

    root_records = [
        api.get(work_path(project, item)).json()["work_item"] for item in roots
    ]
    child_records = [
        api.get(work_path(project, item)).json()["work_item"] for item in children
    ]
    root_entries = assert_two_page_order(
        api,
        work_collection(project),
        root_records,
        roots=True,
    )
    child_entries = assert_two_page_order(
        api,
        f"{work_path(project, roots[0])}/children",
        child_records,
        roots=False,
    )

    root_by_id = {
        item["summary"]["work_item"]["id"]: item for item in root_entries
    }
    assert root_by_id[roots[0]["work_item"]["id"]]["presentation"] == {
        **empty_presentation(),
        "direct_child_count": 4,
        "descendant_count": 4,
    }
    for item in roots[1:]:
        assert root_by_id[item["work_item"]["id"]]["presentation"] == empty_presentation()
    assert all(entry["self_matches_filter"] is True for entry in root_entries)
    assert all(
        entry["presentation"] == empty_presentation() for entry in child_entries
    )
    assert all(entry["self_matches_filter"] is True for entry in child_entries)
    assert all(entry["has_matching_descendants"] is False for entry in child_entries)
