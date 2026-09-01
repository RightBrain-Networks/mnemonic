from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from .conftest import BACKEND_DIR

pytestmark = pytest.mark.postgres

# Flat provenance fields live on the checkpoint in the canonical shape; the
# helpers below fold them back into `initial_checkpoint` so each test reads as
# one record with the one or two fields it actually varies.
CHECKPOINT_OVERRIDES = ("prompt", "source_client", "source_session_id", "tags")


def path(project, work_item=None):
    base = f"/api/v1/projects/{project['id']}/work-items"
    return f"{base}/{work_item['id']}" if work_item else base


def save(api, project, payload, **changes):
    checkpoint_changes = {
        field: changes.pop(field) for field in CHECKPOINT_OVERRIDES if field in changes
    }
    body = {
        **payload,
        **changes,
        "initial_checkpoint": {**payload["initial_checkpoint"], **checkpoint_changes},
    }
    response = api.post(path(project), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_project_crud_counts_and_conflict(api, project):
    duplicate = api.post("/api/v1/projects", json={"name": "First Project"})
    assert duplicate.status_code == 409
    second = api.post(
        "/api/v1/projects", json={"name": "Another", "repository_url": "https://example.com/repo"}
    )
    assert second.status_code == 201
    result = api.get("/api/v1/projects", params={"limit": 1, "offset": 1}).json()
    assert result["total"] == 2
    assert result["limit"] == 1 and result["offset"] == 1
    assert result["items"][0]["id"] == project["id"]
    updated = api.patch(
        f"/api/v1/projects/{project['id']}",
        json={"name": "Renamed", "description": "A project description", "repository_url": None},
    )
    assert updated.status_code == 200
    assert updated.json()["slug"] == project["slug"]
    assert updated.json()["description"] == "A project description"
    assert api.get(f"/api/v1/projects/{project['id']}").json() == updated.json()
    assert api.get(f"/api/v1/projects/{uuid4()}").status_code == 404


def test_project_settings_are_exact_nullable_and_project_local(api, project):
    endpoint = f"/api/v1/projects/{project['id']}/settings"
    unset = {"project_id": project["id"], "recall_pointer_template": None}
    assert api.get(endpoint).json() == unset

    template = "  Recall $WORK_ITEM_TITLE\r\nfor $PROJECT_ID.\t "
    saved = api.patch(endpoint, json={"recall_pointer_template": template})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {**unset, "recall_pointer_template": template}
    assert api.get(endpoint).json() == saved.json()

    other = api.post("/api/v1/projects", json={"name": "Settings isolation"}).json()
    assert api.get(f"/api/v1/projects/{other['id']}/settings").json() == {
        "project_id": other["id"],
        "recall_pointer_template": None,
    }

    cleared = api.patch(endpoint, json={"recall_pointer_template": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == unset
    assert api.get(endpoint).json() == unset


def test_two_simultaneous_first_project_settings_saves_both_succeed(api, project):
    endpoint = f"/api/v1/projects/{project['id']}/settings"
    templates = [
        "Writer A: $PROJECT_ID / $WORK_ITEM_ID",
        "Writer B: $WORK_ITEM_TITLE / $WORK_ITEM_SUMMARY",
    ]
    barrier = Barrier(2)

    def writer(template):
        barrier.wait(timeout=5)
        return api.patch(endpoint, json={"recall_pointer_template": template})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(writer, templates))

    assert [response.status_code for response in responses] == [200, 200]
    assert {
        response.json()["recall_pointer_template"] for response in responses
    } == set(templates)
    assert api.get(endpoint).json()["recall_pointer_template"] in templates


def test_project_settings_validate_payload_and_project(api, project):
    endpoint = f"/api/v1/projects/{project['id']}/settings"
    for payload in [
        {},
        {"recall_pointer_template": " \r\n\t"},
        {"recall_pointer_template": "x" * 100001},
        {"recall_pointer_template": "valid", "unknown": "field"},
    ]:
        response = api.patch(endpoint, json=payload)
        assert response.status_code == 422, response.text

    missing_endpoint = f"/api/v1/projects/{uuid4()}/settings"
    assert api.get(missing_endpoint).status_code == 404
    assert api.patch(
        missing_endpoint, json={"recall_pointer_template": "valid"}
    ).status_code == 404


def test_postgres_full_text_stemming_and_weighted_ranking(api, project, work_payload):
    body_match = save(
        api,
        project,
        work_payload,
        title="Background",
        summary="A secondary lead",
        prompt="Investigate migrating a service.",
    )["work_item"]
    summary_match = save(
        api,
        project,
        work_payload,
        title="Preparation",
        summary="Investigate migrating state",
        prompt="Check the database.",
    )["work_item"]
    title_match = save(
        api,
        project,
        work_payload,
        title="Migrating the service",
        summary="Check existing state",
        prompt="Check the database.",
    )["work_item"]
    # "migrates" does not occur literally in any record; only PostgreSQL's
    # English-language stemming can satisfy this query. Title (weight A) must
    # outrank summary (weight B), which must outrank checkpoint prompt (weight C).
    result = api.get(path(project), params={"q": "migrates"})
    assert result.status_code == 200, result.text
    items = result.json()["items"]
    assert [item["work_item"]["id"] for item in items] == [
        title_match["id"],
        summary_match["id"],
        body_match["id"],
    ]


def test_literal_identifiers_paths_and_wildcards_are_safe(api, project, work_payload):
    created = save(
        api,
        project,
        work_payload,
        title="Escape 100% of wildcard_patterns",
        prompt="Find C:\\work\\project\\cache.py and src/nested/cache.py exactly.",
        tags=["special-tag", "  CACHE ", "cache"],
        source_session_id="session:opaque_7251",
    )
    target = created["work_item"]
    save(
        api,
        project,
        work_payload,
        title="Unrelated",
        summary="Other work",
        prompt="Nothing relevant.",
        tags=[],
        source_session_id="different-session",
    )
    for query in [
        "%",
        "_",
        "C:\\work\\project\\cache.py",
        target["id"],
        "session:opaque_7251",
        "special-tag",
    ]:
        result = api.get(path(project), params={"q": query})
        assert result.status_code == 200, result.text
        assert [item["work_item"]["id"] for item in result.json()["items"]] == [
            target["id"]
        ], query
    attack = api.get(path(project), params={"q": "'; DROP TABLE work_items;--"})
    assert attack.status_code == 200
    assert api.get(path(project)).json()["total"] == 2
    # Tags are normalized and deduplicated on the checkpoint that carries them.
    assert created["initial_checkpoint"]["tags"] == ["special-tag", "cache"]


def test_pagination_and_combined_filters(api, project, work_payload):
    first = save(api, project, work_payload, source_session_id="alpha")["work_item"]
    second = save(api, project, work_payload, source_session_id="beta")["work_item"]
    save(api, project, work_payload, source_client="opencode", source_session_id="alpha")
    save(api, project, work_payload, status="promoted", tags=["cache"])
    result = api.get(
        path(project),
        params={"q": "cache", "tag": " CACHE ", "source_client": "claude-code", "limit": 1},
    ).json()
    assert result["total"] == 2
    assert result["items"][0]["work_item"]["id"] == second["id"]
    next_page = api.get(
        path(project),
        params={
            "q": "cache",
            "tag": "cache",
            "source_client": "claude-code",
            "limit": 1,
            "offset": 1,
        },
    ).json()
    assert next_page["total"] == 2
    assert next_page["items"][0]["work_item"]["id"] == first["id"]
    scoped = api.get(
        path(project), params={"source_client": "claude-code", "source_session_id": "alpha"}
    ).json()
    assert scoped["total"] == 1 and scoped["items"][0]["work_item"]["id"] == first["id"]
    assert api.get(path(project), params={"q": " \n "}).json()["total"] == 3
    assert api.get(path(project), params={"status": "all", "offset": 200}).json()["total"] == 4
    assert api.get(path(project), params={"status": "all", "offset": 200}).json()["items"] == []


def test_work_list_sort_orders_flat_and_hierarchy_pages(api, project, work_payload):
    first = save(
        api,
        project,
        work_payload,
        title="Created first",
        priority=10,
    )["work_item"]
    second = save(
        api,
        project,
        work_payload,
        title="Created second",
        priority=90,
    )["work_item"]
    updated = api.patch(
        path(project, first),
        json={"expected_version": 1, "title": "Updated most recently"},
    )
    assert updated.status_code == 200, updated.text

    def ordered_ids(view, sort=None):
        params = {"status": "all", "view": view}
        if sort is not None:
            params["sort"] = sort
        response = api.get(path(project), params=params)
        assert response.status_code == 200, response.text
        return [
            (item["summary"] if view == "roots" else item)["work_item"]["id"]
            for item in response.json()["items"]
        ]

    for view in ("full", "roots"):
        assert ordered_ids(view) == [first["id"], second["id"]]
        assert ordered_ids(view, "updated") == [first["id"], second["id"]]
        assert ordered_ids(view, "created") == [second["id"], first["id"]]
        assert ordered_ids(view, "priority") == [second["id"], first["id"]]


def test_active_and_dropped_filters_derive_pending_lease_state(
    api, project, work_payload, postgres_engine
):
    unleased = save(api, project, work_payload)["work_item"]
    active = save(api, project, work_payload)["work_item"]
    dropped = save(api, project, work_payload)["work_item"]
    deferred = save(api, project, work_payload)["work_item"]
    promoted = save(api, project, work_payload, status="promoted")["work_item"]
    deferred_response = api.post(
        f"{path(project, deferred)}/defer",
        json={"expected_version": 1},
    )
    assert deferred_response.status_code == 200, deferred_response.text

    for item, request_id in ((active, "active-claim"), (dropped, "dropped-claim")):
        response = api.post(
            f"{path(project, item)}/claim",
            json={
                "holder_client": "claude-code",
                "holder_session_id": f"session-{request_id}",
                "claim_request_id": request_id,
            },
        )
        assert response.status_code == 200, response.text

    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE work_leases "
                "SET acquired_at = clock_timestamp() - interval '3 seconds', "
                "renewed_at = clock_timestamp() - interval '2 seconds', "
                "expires_at = clock_timestamp() - interval '1 second' "
                "WHERE work_item_id = CAST(:work_item_id AS uuid)"
            ),
            {"work_item_id": dropped["id"]},
        )

    def filtered_ids(status):
        response = api.get(path(project), params={"status": status})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == len(body["items"])
        return {item["work_item"]["id"] for item in body["items"]}

    assert filtered_ids("active") == {active["id"]}
    assert filtered_ids("dropped") == {dropped["id"]}
    assert filtered_ids("pending") == {unleased["id"]}
    assert filtered_ids("deferred") == {deferred["id"]}
    assert filtered_ids("all") == {
        unleased["id"],
        active["id"],
        dropped["id"],
        deferred["id"],
        promoted["id"],
    }
    dropped_result = api.get(path(project), params={"status": "dropped"}).json()["items"][0]
    assert dropped_result["readiness"]["has_dropped_lease"] is True
    assert dropped_result["readiness"]["display_state"] == "dropped"


def test_edit_refreshes_search_vector(api, project, work_payload):
    work_item = save(api, project, work_payload, title="Orchestrating state")["work_item"]
    assert api.get(path(project), params={"q": "orchestrates"}).json()["total"] == 1
    update = api.patch(
        path(project, work_item), json={"expected_version": 1, "title": "Brand new heading"}
    )
    assert update.status_code == 200
    assert api.get(path(project), params={"q": "orchestrates"}).json()["total"] == 0
    assert api.get(path(project), params={"q": "heading"}).json()["total"] == 1


def test_two_simultaneous_writers_cannot_overwrite_each_other(api, project, work_payload):
    work_item = save(api, project, work_payload)["work_item"]
    barrier = Barrier(2)

    def writer(title):
        barrier.wait(timeout=5)
        return api.patch(path(project, work_item), json={"expected_version": 1, "title": title})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(writer, ["Writer A", "Writer B"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    final = api.get(path(project, work_item)).json()
    assert final["version"] == 2
    assert final["title"] == winner["title"]


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"sort": "oldest"},
        {"status": "deleted"},
        {"q": "x" * 501},
        {"tag": " "},
        {"semantic": "sometimes"},
        {"q": "bad\x00query"},
        {"unknown_field": "not-allowed"},
    ],
)
def test_invalid_query_returns_422(api, project, query):
    assert api.get(path(project), params=query).status_code == 422


def test_database_readiness_is_public(api):
    response = api.get("/readyz", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_migration_matches_models_and_has_stored_gin_search(postgres_engine):
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.check(config)
        columns = connection.execute(
            text(
                "SELECT attgenerated FROM pg_attribute "
                "WHERE attrelid = 'work_items'::regclass AND attname = 'search_vector'"
            )
        ).scalar_one()
        assert columns == "s"
        index_defs = (
            connection.execute(
                text("SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema()")
            )
            .scalars()
            .all()
        )
        assert any("USING gin (search_vector)" in definition for definition in index_defs)
        assert any("USING gin (tags)" in definition for definition in index_defs)
    assert inspect(postgres_engine).has_table("alembic_version")
