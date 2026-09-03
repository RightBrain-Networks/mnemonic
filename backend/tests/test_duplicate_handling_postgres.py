"""Phase 9 duplicate merge API, canonical-read, and alias-freeze contracts."""

from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.postgres


def collection(project: dict) -> str:
    return f"/api/v1/projects/{project['id']}/work-items"


def work_path(project: dict, work_item: dict) -> str:
    return f"{collection(project)}/{work_item['id']}"


def create_work(
    api,
    project: dict,
    work_payload: dict,
    title: str,
    *,
    summary: str | None = None,
    tags: list[str] | None = None,
    initial_relationships: list[dict] | None = None,
) -> dict:
    response = api.post(
        collection(project),
        json={
            **work_payload,
            "title": title,
            "summary": summary or f"Durable context for {title}.",
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "source_session_id": f"phase9-{uuid4()}",
                "tags": tags or [],
            },
            "initial_relationships": initial_relationships or [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def context(api, project: dict, work_item: dict) -> dict:
    response = api.get(f"{work_path(project, work_item)}/context")
    assert response.status_code == 200, response.text
    return response.json()


def merge_payload(
    source_context: dict,
    destination_context: dict,
    *,
    operation_id: str | None = None,
) -> dict:
    return {
        "destination_work_item_id": destination_context["work_item"]["id"],
        "reviewed_source_revision": source_context["merge_review_revision"],
        "reviewed_destination_revision": destination_context["merge_review_revision"],
        "rationale": "Both records describe the same durable implementation objective.",
        "merged_by_client": "pytest",
        "merged_by_session_id": "phase9-duplicate-merge",
        "merged_by_model": "test-model",
        "client_operation_id": operation_id or str(uuid4()),
    }


def merge_work(
    api,
    project: dict,
    source: dict,
    destination: dict,
    *,
    operation_id: str | None = None,
) -> tuple[dict, dict]:
    payload = merge_payload(
        context(api, project, source),
        context(api, project, destination),
        operation_id=operation_id,
    )
    response = api.post(f"{work_path(project, source)}/merge", json=payload)
    assert response.status_code == 201, response.text
    return response.json(), payload


def authoritative_counts(postgres_engine) -> dict[str, int]:
    with postgres_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT
                    (SELECT count(*) FROM work_items) AS work_items,
                    (SELECT count(*) FROM checkpoints) AS checkpoints,
                    (SELECT count(*) FROM work_events) AS work_events,
                    (SELECT count(*) FROM work_gates) AS work_gates,
                    (SELECT count(*) FROM work_relationships) AS work_relationships,
                    (SELECT count(*) FROM work_duplicate_merges) AS work_duplicate_merges,
                    (SELECT count(*) FROM work_leases) AS work_leases,
                    (SELECT count(*) FROM client_operations) AS client_operations
                """
            )
        ).one()
    return {key: int(value) for key, value in row._mapping.items()}


def test_merge_has_exact_effects_frozen_replay_and_source_owned_history(
    api,
    project,
    work_payload,
    postgres_engine,
):
    source_scope = ["alias/source/**"]
    destination_scope = ["canonical/root/**"]
    source_created = create_work(
        api,
        project,
        {
            **work_payload,
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "verified_against": "abcdef1",
                "affected_paths": source_scope,
            },
        },
        "Retired duplicate with unique marmalade evidence",
    )
    destination_created = create_work(
        api,
        project,
        {
            **work_payload,
            "initial_checkpoint": {
                **work_payload["initial_checkpoint"],
                "verified_against": "abcdef2",
                "affected_paths": destination_scope,
            },
        },
        "Canonical durable objective",
    )
    source = source_created["work_item"]
    destination = destination_created["work_item"]

    # Freeze a pre-merge mutation receipt. Its exact replay must remain usable
    # after the addressed work item becomes an alias.
    old_update_payload = {
        "expected_version": 1,
        "priority": 41,
        "actor": {
            "actor_client": "pytest",
            "actor_session_id": "pre-merge-receipt",
        },
        "client_operation_id": str(uuid4()),
    }
    old_update = api.patch(work_path(project, source), json=old_update_payload)
    assert old_update.status_code == 200, old_update.text

    source_review = context(api, project, source)
    destination_review = context(api, project, destination)
    operation_id = str(uuid4())
    payload = merge_payload(
        source_review,
        destination_review,
        operation_id=operation_id,
    )
    response = api.post(f"{work_path(project, source)}/merge", json=payload)
    assert response.status_code == 201, response.text
    result = response.json()

    assert list(result) == [
        "merge",
        "source_work_item",
        "destination_work_item",
        "direct_destination",
        "canonical_work_item",
        "supporting_relationship_created",
        "supporting_relationship",
        "relationship_events",
        "merge_events",
    ]
    assert list(result["merge"]) == [
        "id",
        "merge_sequence",
        "project_id",
        "source_work_item_id",
        "destination_work_item_id",
        "duplicate_relationship_id",
        "reviewed_source_revision",
        "reviewed_destination_revision",
        "resulting_source_work_version",
        "resulting_destination_work_version",
        "rationale",
        "merged_by_client",
        "merged_by_session_id",
        "merged_by_model",
        "created_at",
    ]
    merge = result["merge"]
    assert merge["reviewed_source_revision"] == source_review["merge_review_revision"]
    assert merge["reviewed_destination_revision"] == (
        destination_review["merge_review_revision"]
    )
    assert merge["resulting_source_work_version"] == source_review["work_item"]["version"] + 1
    assert merge["resulting_destination_work_version"] == (
        destination_review["work_item"]["version"] + 1
    )
    assert result["source_work_item"]["version"] == merge["resulting_source_work_version"]
    assert result["destination_work_item"]["version"] == (
        merge["resulting_destination_work_version"]
    )
    assert result["source_work_item"]["title"] == source["title"]
    assert result["destination_work_item"]["title"] == destination["title"]
    assert result["source_work_item"]["status"] == source["status"]
    assert result["destination_work_item"]["status"] == destination["status"]
    assert result["source_work_item"]["updated_at"] == merge["created_at"]
    assert result["destination_work_item"]["updated_at"] == merge["created_at"]
    assert result["direct_destination"]["id"] == destination["id"]
    assert result["canonical_work_item"] == result["direct_destination"]
    assert result["supporting_relationship_created"] is True

    relationship = result["supporting_relationship"]
    assert relationship["id"] == merge["duplicate_relationship_id"]
    assert relationship["relationship_type"] == "duplicate-of"
    assert relationship["source_work_item_id"] == source["id"]
    assert relationship["target_work_item_id"] == destination["id"]
    assert relationship["created_at"] == merge["created_at"]
    assert [event["work_item_id"] for event in result["relationship_events"]] == [
        source["id"],
        destination["id"],
    ]
    assert [event["work_item_id"] for event in result["merge_events"]] == [
        source["id"],
        destination["id"],
    ]
    assert [event["metadata"]["role"] for event in result["merge_events"]] == [
        "source",
        "destination",
    ]
    for event in [*result["relationship_events"], *result["merge_events"]]:
        assert event["created_at"] == merge["created_at"]
        assert event["actor_client"] == payload["merged_by_client"]
        assert event["actor_session_id"] == payload["merged_by_session_id"]
    for event in result["merge_events"]:
        assert event["body"] == payload["rationale"]
        assert event["metadata"] == {
            "merge_id": merge["id"],
            "source_work_item_id": source["id"],
            "destination_work_item_id": destination["id"],
            "role": event["metadata"]["role"],
            "source_work_version": result["source_work_item"]["version"],
            "destination_work_version": result["destination_work_item"]["version"],
        }
    assert "created_for_duplicate_merge_id" not in response.text
    assert "work_duplicate_merge_id" not in response.text
    assert operation_id not in response.text

    before_replay = authoritative_counts(postgres_engine)
    replay = api.post(f"{work_path(project, source)}/merge", json=payload)
    assert replay.status_code == 201, replay.text
    assert replay.json() == result
    assert authoritative_counts(postgres_engine) == before_replay

    old_update_replay = api.patch(work_path(project, source), json=old_update_payload)
    assert old_update_replay.status_code == 200, old_update_replay.text
    assert old_update_replay.json() == old_update.json()
    assert authoritative_counts(postgres_engine) == before_replay

    source_detail = api.get(work_path(project, source))
    assert source_detail.status_code == 200, source_detail.text
    assert source_detail.json()["work_item"]["version"] == result["source_work_item"]["version"]
    assert source_detail.json()["canonical"] == {
        "is_duplicate": True,
        "direct_destination": result["direct_destination"],
        "canonical_work_item": result["canonical_work_item"],
        "path": [result["direct_destination"]],
        "duplicate_member_count": 1,
    }
    destination_detail = api.get(work_path(project, destination)).json()
    assert destination_detail["canonical"]["is_duplicate"] is False
    assert destination_detail["canonical"]["path"] == []
    assert destination_detail["canonical"]["duplicate_member_count"] == 1

    source_context = context(api, project, source)
    destination_context = context(api, project, destination)
    assert source_context["initial_checkpoint"]["affected_paths"] == source_scope
    assert destination_context["initial_checkpoint"]["affected_paths"] == destination_scope
    assert source_context["canonical"] == source_detail.json()["canonical"]
    assert source_context["duplicate_members"][0]["id"] == source["id"]
    assert source_context["duplicate_member_total"] == 1
    assert source_context["omitted_duplicate_member_count"] == 0
    assert source_context["readiness"]["is_duplicate"] is True
    assert source_context["readiness"]["canonical_work_item_id"] == destination["id"]
    assert source_context["readiness"]["is_ready"] is False
    assert source_context["readiness"]["display_state"] == "duplicate"

    events = api.get(f"{work_path(project, source)}/events", params={"limit": 100})
    assert events.status_code == 200, events.text
    assert events.json()["total"] == source_context["event_total"]
    assert any(event["event_type"] == "work_merged" for event in events.json()["items"])
    assert "created_for_duplicate_merge_id" not in events.text
    assert "work_duplicate_merge_id" not in events.text
    checkpoints = api.get(f"{work_path(project, source)}/checkpoints")
    relationships = api.get(f"{work_path(project, source)}/relationships")
    gates = api.get(f"{work_path(project, source)}/gates")
    assert checkpoints.status_code == relationships.status_code == gates.status_code == 200
    assert checkpoints.json()["items"][0]["affected_paths"] == source_scope


def test_chains_group_search_before_paging_and_stay_out_of_hierarchy(
    api,
    project,
    work_payload,
    postgres_engine,
):
    alias_a = create_work(
        api,
        project,
        work_payload,
        "Xylophoneevidence first duplicate",
        tags=["alias-only"],
    )["work_item"]
    alias_b = create_work(
        api,
        project,
        work_payload,
        "Intermediate duplicate",
    )["work_item"]
    root_c = create_work(
        api,
        project,
        work_payload,
        "First canonical root",
        tags=["root-only"],
    )["work_item"]
    alias_d = create_work(
        api,
        project,
        work_payload,
        "Xylophoneevidence second duplicate",
    )["work_item"]
    root_e = create_work(
        api,
        project,
        work_payload,
        "Second canonical root",
    )["work_item"]
    parent = create_work(
        api,
        project,
        work_payload,
        "Structural parent",
    )["work_item"]

    parent_edge = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "parent-child",
            "source_work_item_id": parent["id"],
            "target_work_item_id": root_e["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "phase9-hierarchy",
        },
    )
    assert parent_edge.status_code == 200, parent_edge.text

    first_result, first_payload = merge_work(api, project, alias_a, alias_b)
    merge_work(api, project, alias_b, root_c)
    merge_work(api, project, alias_d, root_e)

    counts_after_chain = authoritative_counts(postgres_engine)
    historical_replay = api.post(
        f"{work_path(project, alias_a)}/merge",
        json=first_payload,
    )
    assert historical_replay.status_code == 201, historical_replay.text
    assert historical_replay.json() == first_result
    assert authoritative_counts(postgres_engine) == counts_after_chain

    alias_detail = api.get(work_path(project, alias_a)).json()
    assert [member["id"] for member in alias_detail["canonical"]["path"]] == [
        alias_b["id"],
        root_c["id"],
    ]
    assert alias_detail["canonical"]["canonical_work_item"]["id"] == root_c["id"]
    assert alias_detail["canonical"]["duplicate_member_count"] == 2
    alias_context = context(api, project, alias_a)
    assert [member["id"] for member in alias_context["duplicate_members"]] == [
        alias_a["id"],
        alias_b["id"],
    ]

    query = {"q": "xylophoneevidence", "status": "all", "limit": 1}
    first_page = api.get(collection(project), params=query)
    second_page = api.get(collection(project), params={**query, "offset": 1})
    assert first_page.status_code == second_page.status_code == 200
    assert first_page.json()["total"] == second_page.json()["total"] == 2
    paged_hits = [first_page.json()["items"][0], second_page.json()["items"][0]]
    assert {hit["summary"]["work_item"]["id"] for hit in paged_hits} == {
        root_c["id"],
        root_e["id"],
    }
    assert {hit["matched_member"]["id"] for hit in paged_hits} == {
        alias_a["id"],
        alias_d["id"],
    }

    alias_hits = api.get(
        collection(project),
        params={
            "q": "xylophoneevidence",
            "status": "all",
            "duplicate_scope": "aliases",
        },
    ).json()
    assert alias_hits["total"] == 2
    assert {hit["summary"]["work_item"]["id"] for hit in alias_hits["items"]} == {
        alias_a["id"],
        alias_d["id"],
    }
    assert all(
        hit["matched_member"]["id"] == hit["summary"]["work_item"]["id"]
        for hit in alias_hits["items"]
    )

    group_aliases = api.get(
        collection(project),
        params={
            "status": "all",
            "duplicate_scope": "aliases",
            "canonical_work_item_id": root_c["id"],
        },
    )
    assert group_aliases.status_code == 200, group_aliases.text
    assert group_aliases.json()["total"] == 2
    assert {
        hit["summary"]["work_item"]["id"] for hit in group_aliases.json()["items"]
    } == {alias_a["id"], alias_b["id"]}
    whole_group = api.get(
        collection(project),
        params={
            "status": "all",
            "duplicate_scope": "all",
            "canonical_work_item_id": root_c["id"],
        },
    ).json()
    assert whole_group["total"] == 3
    assert {hit["summary"]["work_item"]["id"] for hit in whole_group["items"]} == {
        alias_a["id"],
        alias_b["id"],
        root_c["id"],
    }

    # Non-text filters qualify the returned root, not an alias that nominated it.
    alias_only_filter = api.get(
        collection(project),
        params={"q": "xylophoneevidence", "tag": "alias-only", "status": "all"},
    )
    assert alias_only_filter.status_code == 200
    assert alias_only_filter.json()["total"] == 0
    root_filter = api.get(
        collection(project),
        params={"q": "xylophoneevidence", "tag": "root-only", "status": "all"},
    )
    assert root_filter.status_code == 200
    assert root_filter.json()["total"] == 1
    assert root_filter.json()["items"][0]["matched_member"]["id"] == alias_a["id"]

    invalid_default_group = api.get(
        collection(project), params={"canonical_work_item_id": root_c["id"]}
    )
    assert invalid_default_group.status_code == 422
    alias_as_group = api.get(
        collection(project),
        params={
            "status": "all",
            "duplicate_scope": "all",
            "canonical_work_item_id": alias_b["id"],
        },
    )
    assert alias_as_group.status_code == 409
    assert alias_as_group.json()["detail"] == {
        "code": "work_duplicate",
        "message": "This work item is a retained duplicate alias and cannot be mutated or claimed.",
        "context": {"canonical_work_item_id": root_c["id"]},
    }
    other_project = api.post("/api/v1/projects", json={"name": "Filter isolation"}).json()
    foreign = create_work(api, other_project, work_payload, "Foreign root")["work_item"]
    for invisible_id in (foreign["id"], str(uuid4())):
        missing = api.get(
            collection(project),
            params={
                "status": "all",
                "duplicate_scope": "all",
                "canonical_work_item_id": invisible_id,
            },
        )
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "work_item_not_found"

    roots = api.get(
        collection(project), params={"view": "roots", "status": "all", "limit": 100}
    )
    assert roots.status_code == 200, roots.text
    root_entries = {
        entry["summary"]["work_item"]["id"]: entry for entry in roots.json()["items"]
    }
    assert alias_a["id"] not in root_entries
    assert alias_b["id"] not in root_entries
    assert alias_d["id"] not in root_entries
    assert root_entries[root_c["id"]]["presentation"][
        "branch_merged_duplicate_count"
    ] == 2
    assert root_entries[parent["id"]]["presentation"][
        "branch_merged_duplicate_count"
    ] == 1
    alias_children = api.get(f"{work_path(project, alias_a)}/children", params={"status": "all"})
    assert alias_children.status_code == 409
    assert alias_children.json()["detail"]["code"] == "work_duplicate"

    ready_ids = {
        item["work_item"]["id"]
        for item in api.get(
            f"/api/v1/projects/{project['id']}/ready-work", params={"limit": 100}
        ).json()["items"]
    }
    assert ready_ids.isdisjoint({alias_a["id"], alias_b["id"], alias_d["id"]})
    alias_parent = api.get(
        f"/api/v1/projects/{project['id']}/ready-work",
        params={"parent_work_item_id": alias_a["id"]},
    )
    assert alias_parent.status_code == 409
    assert alias_parent.json()["detail"] == {
        "code": "work_duplicate",
        "message": "This work item is a retained duplicate alias and cannot be mutated or claimed.",
        "context": {"canonical_work_item_id": root_c["id"]},
    }


def test_every_fresh_alias_mutation_fails_without_side_effects(
    api,
    project,
    work_payload,
    postgres_engine,
):
    source = create_work(api, project, work_payload, "Frozen duplicate source")["work_item"]
    destination = create_work(api, project, work_payload, "Canonical mutation target")[
        "work_item"
    ]
    other = create_work(api, project, work_payload, "Unrelated endpoint")["work_item"]
    merged, _payload = merge_work(api, project, source, destination)
    alias_version = merged["source_work_item"]["version"]
    alias_context = context(api, project, source)
    actor = {
        "actor_client": "pytest",
        "actor_session_id": "fresh-alias-write",
    }

    responses = {
        "update": api.patch(
            work_path(project, source),
            json={"expected_version": alias_version, "title": "Forbidden alias edit"},
        ),
        "defer": api.post(
            f"{work_path(project, source)}/defer",
            json={"expected_version": alias_version},
        ),
        "complete": api.post(
            f"{work_path(project, source)}/complete",
            json={
                "expected_version": alias_version,
                "checkpoint": {
                    "prompt": "Forbidden alias completion.",
                    "source_client": "pytest",
                    "source_session_id": "fresh-alias-complete",
                },
            },
        ),
        "delete": api.post(
            f"{work_path(project, source)}/delete",
            json={"expected_version": alias_version},
        ),
        "checkpoint": api.post(
            f"{work_path(project, source)}/checkpoints",
            json={
                "kind": "progress",
                "prompt": "Forbidden alias checkpoint.",
                "source_client": "pytest",
                "source_session_id": "fresh-alias-checkpoint",
            },
        ),
        "event": api.post(
            f"{work_path(project, source)}/events",
            json={
                "event_type": "progress",
                "body": "Forbidden alias progress.",
                "metadata": {},
                "actor": actor,
            },
        ),
        "gate": api.post(
            f"{work_path(project, source)}/gates",
            json={
                "question": "Forbidden alias question?",
                "requested_by_client": "pytest",
                "requested_by_session_id": "fresh-alias-gate",
            },
        ),
        "claim": api.post(
            f"{work_path(project, source)}/claim",
            json={
                "holder_client": "pytest",
                "holder_session_id": "fresh-alias-claim",
                "claim_request_id": "fresh-alias-claim",
            },
        ),
        "claim-and-recall": api.post(
            f"{work_path(project, source)}/claim-and-recall",
            json={
                "holder_client": "pytest",
                "holder_session_id": "fresh-alias-recall",
                "claim_request_id": "fresh-alias-recall",
            },
        ),
        "renew": api.post(
            f"{work_path(project, source)}/renew-claim",
            json={"lease_token": "not-a-real-token"},
        ),
        "release": api.post(
            f"{work_path(project, source)}/release-claim",
            json={"lease_token": "not-a-real-token"},
        ),
        "add-source": api.post(
            f"/api/v1/projects/{project['id']}/relationships",
            json={
                "relationship_type": "related",
                "source_work_item_id": source["id"],
                "target_work_item_id": other["id"],
                "created_by_client": "pytest",
                "created_by_session_id": "fresh-alias-related-source",
            },
        ),
        "add-target": api.post(
            f"/api/v1/projects/{project['id']}/relationships",
            json={
                "relationship_type": "related",
                "source_work_item_id": other["id"],
                "target_work_item_id": source["id"],
                "created_by_client": "pytest",
                "created_by_session_id": "fresh-alias-related-target",
            },
        ),
    }
    for operation, response in responses.items():
        assert response.status_code == 409, (operation, response.text)
        assert response.json()["detail"] == {
            "code": "work_duplicate",
            "message": (
                "This work item is a retained duplicate alias and cannot be mutated or claimed."
            ),
            "context": {"canonical_work_item_id": destination["id"]},
        }

    remove = api.delete(
        f"/api/v1/projects/{project['id']}/relationships/"
        f"{merged['supporting_relationship']['id']}"
    )
    assert remove.status_code == 409
    assert remove.json()["detail"]["code"] == "duplicate_relationship_frozen"

    merge_again_payload = merge_payload(
        alias_context,
        context(api, project, other),
    )
    merge_again = api.post(
        f"{work_path(project, source)}/merge",
        json=merge_again_payload,
    )
    assert merge_again.status_code == 409
    assert merge_again.json()["detail"]["code"] == "work_already_duplicate"

    generic_duplicate = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "duplicate-of",
            "source_work_item_id": destination["id"],
            "target_work_item_id": other["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "generic-duplicate-forbidden",
        },
    )
    assert generic_duplicate.status_code == 409
    assert generic_duplicate.json()["detail"]["code"] == "duplicate_merge_required"

    initial_duplicate_payload = {
        **work_payload,
        "title": "Creation with forbidden duplicate mark",
        "initial_checkpoint": {
            **work_payload["initial_checkpoint"],
            "source_session_id": "initial-duplicate-forbidden",
        },
        "initial_relationships": [
            {
                "type": "duplicate-of",
                "direction": "outgoing",
                "other_work_item_id": destination["id"],
            }
        ],
    }
    initial_duplicate = api.post(collection(project), json=initial_duplicate_payload)
    assert initial_duplicate.status_code == 409
    assert initial_duplicate.json()["detail"]["code"] == "duplicate_merge_required"

    # Every failed request above is transactionally clean.
    expected_counts = {
        "work_items": 3,
        "checkpoints": 3,
        "work_events": 7,
        "work_gates": 0,
        "work_relationships": 1,
        "work_duplicate_merges": 1,
        "work_leases": 0,
        "client_operations": 1,
    }
    assert authoritative_counts(postgres_engine) == expected_counts
    current = api.get(work_path(project, source)).json()
    assert current["work_item"] == merged["source_work_item"]
    assert current["canonical"]["canonical_work_item"]["id"] == destination["id"]
