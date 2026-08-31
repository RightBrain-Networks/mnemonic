from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from mnemonic_api.models import Checkpoint, WorkItem

from .conftest import BACKEND_DIR

pytestmark = pytest.mark.postgres


def save(api, project, payload, **changes):
    response = api.post(f"/api/v1/projects/{project['id']}/handoffs", json={**payload, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def path(project, handoff=None):
    base = f"/api/v1/projects/{project['id']}/handoffs"
    return f"{base}/{handoff['id']}" if handoff else base


def comment_path(project, handoff):
    return f"{path(project, handoff)}/comments"


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


def test_round_trip_compact_search_and_project_isolation(api, project, handoff_payload):
    handoff_payload["prompt"] += "\nContext in multiple languages: 日本語 café 🧠.\n"
    handoff = save(api, project, handoff_payload)
    assert handoff["version"] == 1
    assert handoff["prompt"] == handoff_payload["prompt"]
    assert handoff["source_metadata"] == handoff_payload["source_metadata"]
    assert handoff["created_at"].endswith("Z")
    assert api.get(path(project, handoff)).json() == handoff
    summaries = api.get(path(project)).json()
    assert summaries["total"] == 1
    assert "prompt" not in summaries["items"][0]
    assert "source_metadata" not in summaries["items"][0]

    other = api.post("/api/v1/projects", json={"name": "Second project"}).json()
    wrong_path = path(other, handoff)
    assert api.get(wrong_path).status_code == 404
    assert api.patch(wrong_path, json={"expected_version": 1, "title": "Wrong"}).status_code == 404
    assert api.delete(wrong_path, params={"expected_version": 1}).status_code == 404
    assert api.get(path(other), params={"q": "cache", "status": "all"}).json()["total"] == 0
    assert api.get(path(project, handoff)).json()["version"] == 1


def test_missing_projects_return_404_instead_of_empty_results(api, handoff_payload):
    missing = {"id": str(uuid4())}
    assert api.get(path(missing)).status_code == 404
    assert api.post(path(missing), json=handoff_payload).status_code == 404


def test_versions_provenance_lifecycle_and_soft_deletion(
    api, project, handoff_payload, postgres_engine
):
    handoff = save(api, project, handoff_payload)
    endpoint = path(project, handoff)
    assert api.patch(endpoint, json={"title": "No version"}).status_code == 422
    assert api.delete(endpoint).status_code == 422
    immutable = {"expected_version": 1, "source_session_id": "replacement"}
    assert api.patch(endpoint, json=immutable).status_code == 422
    assert (
        api.patch(
            endpoint,
            json={"expected_version": 1, "prompt": " New exact prompt.\n"},
        ).status_code
        == 422
    )
    updated = api.patch(endpoint, json={"expected_version": 1, "status": "wont-do"})
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["source_session_id"] == handoff["source_session_id"]
    assert updated.json()["prompt"] == handoff_payload["prompt"]
    assert api.get(path(project)).json()["total"] == 0
    assert api.get(path(project), params={"status": "wont-do"}).json()["total"] == 1
    assert api.get(path(project), params={"status": "all"}).json()["total"] == 1
    assert api.patch(endpoint, json={"expected_version": 1, "title": "Stale"}).status_code == 409
    assert api.delete(endpoint, params={"expected_version": 1}).status_code == 409
    deleted = api.delete(endpoint, params={"expected_version": 2})
    assert deleted.status_code == 204 and deleted.content == b""
    assert api.get(endpoint).status_code == 404
    assert api.patch(endpoint, json={"expected_version": 3, "title": "No"}).status_code == 404
    assert api.delete(endpoint, params={"expected_version": 3}).status_code == 404
    assert api.get(path(project), params={"status": "all"}).json()["total"] == 0
    with Session(postgres_engine) as database:
        row = database.get(WorkItem, UUID(handoff["id"]))
        assert row is not None and row.deleted_at is not None
        assert row.version == 3
        checkpoint = database.get(Checkpoint, row.initial_checkpoint_id)
        assert checkpoint is not None and checkpoint.prompt == handoff_payload["prompt"]


def test_comments_are_append_only_project_scoped_and_searchable(api, project, handoff_payload):
    handoff = save(api, project, handoff_payload)
    body = "  Found the frobnicated lock race.\n\npytest tests/test_cache.py passed.  "
    created = api.post(
        comment_path(project, handoff),
        json={
            "body": body,
            "source_client": "claude-code",
            "source_session_id": "session-progress-123",
            "source_model": "test-model",
        },
    )
    assert created.status_code == 201, created.text
    comment = created.json()
    assert comment["body"] == body
    assert comment["kind"] == "comment"
    assert comment["handoff_id"] == handoff["id"]
    assert comment["source_session_id"] == "session-progress-123"
    assert comment["created_at"].endswith("Z")
    assert api.get(path(project, handoff)).json()["version"] == 1

    timeline = api.get(comment_path(project, handoff), params={"limit": 1}).json()
    assert timeline == {
        "items": [comment],
        "total": 1,
        "limit": 1,
        "offset": 0,
    }
    assert (
        api.get(path(project), params={"q": "frobnicates"}).json()["items"][0]["id"]
        == handoff["id"]
    )
    assert api.get(path(project), params={"q": "session-progress-123"}).json()["total"] == 1

    other = api.post("/api/v1/projects", json={"name": "Comment isolation"}).json()
    assert api.get(comment_path(other, handoff)).status_code == 404
    assert (
        api.post(
            comment_path(other, handoff),
            json={
                "body": "Wrong project",
                "source_client": "dashboard",
                "source_session_id": "dashboard-session",
            },
        ).status_code
        == 404
    )


def test_completion_requires_summary_and_is_atomic(api, project, handoff_payload):
    handoff = save(api, project, handoff_payload)
    endpoint = path(project, handoff)
    assert api.patch(endpoint, json={"expected_version": 1, "status": "done"}).status_code == 422

    completed = api.post(
        f"{endpoint}/complete",
        json={
            "expected_version": 1,
            "summary": (
                "Implemented cache invalidation. Added a regression test. "
                "Observed the focused and full suites pass; no known follow-up remains."
            ),
            "source_client": "claude-code",
            "source_session_id": "completing-session",
            "source_model": "test-model",
        },
    )
    assert completed.status_code == 200, completed.text
    result = completed.json()
    assert result["handoff"]["status"] == "done"
    assert result["handoff"]["version"] == 2
    assert result["comment"]["kind"] == "work-summary"
    assert result["comment"]["source_session_id"] == "completing-session"

    stale = api.post(
        f"{endpoint}/complete",
        json={
            "expected_version": 1,
            "summary": "Duplicate summary must not be written.",
            "source_client": "claude-code",
            "source_session_id": "completing-session",
        },
    )
    assert stale.status_code == 409
    timeline = api.get(comment_path(project, handoff)).json()
    assert timeline["total"] == 1
    assert timeline["items"] == [result["comment"]]
    assert api.get(path(project)).json()["total"] == 0
    assert api.get(path(project), params={"status": "done"}).json()["total"] == 1


def test_postgres_full_text_stemming_and_weighted_ranking(api, project, handoff_payload):
    body_match = save(
        api,
        project,
        handoff_payload,
        title="Background",
        summary="A secondary lead",
        prompt="Investigate migrating a service.",
    )
    summary_match = save(
        api,
        project,
        handoff_payload,
        title="Preparation",
        summary="Investigate migrating state",
        prompt="Check the database.",
    )
    title_match = save(
        api,
        project,
        handoff_payload,
        title="Migrating the service",
        summary="Check existing state",
        prompt="Check the database.",
    )
    # "migrates" does not occur literally in any record; only PostgreSQL's
    # English-language stemming can satisfy this query.
    result = api.get(path(project), params={"q": "migrates"})
    assert result.status_code == 200, result.text
    items = result.json()["items"]
    assert [item["id"] for item in items] == [
        title_match["id"],
        summary_match["id"],
        body_match["id"],
    ]


def test_literal_identifiers_paths_and_wildcards_are_safe(api, project, handoff_payload):
    target = save(
        api,
        project,
        handoff_payload,
        title="Escape 100% of wildcard_patterns",
        prompt="Find C:\\work\\project\\cache.py and src/nested/cache.py exactly.",
        tags=["special-tag", "  CACHE ", "cache"],
        source_session_id="session:opaque_7251",
    )
    save(
        api,
        project,
        handoff_payload,
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
        assert [item["id"] for item in result.json()["items"]] == [target["id"]], query
    attack = api.get(path(project), params={"q": "'; DROP TABLE handoffs;--"})
    assert attack.status_code == 200
    assert api.get(path(project)).json()["total"] == 2
    assert api.get(path(project, target)).json()["tags"] == ["special-tag", "cache"]


def test_pagination_and_combined_filters(api, project, handoff_payload):
    first = save(api, project, handoff_payload, source_session_id="alpha")
    second = save(api, project, handoff_payload, source_session_id="beta")
    save(api, project, handoff_payload, source_client="opencode", source_session_id="alpha")
    save(api, project, handoff_payload, status="promoted", tags=["cache"])
    result = api.get(
        path(project),
        params={"q": "cache", "tag": " CACHE ", "source_client": "claude-code", "limit": 1},
    ).json()
    assert result["total"] == 2
    assert result["items"][0]["id"] == second["id"]
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
    assert next_page["items"][0]["id"] == first["id"]
    scoped = api.get(
        path(project), params={"source_client": "claude-code", "source_session_id": "alpha"}
    ).json()
    assert scoped["total"] == 1 and scoped["items"][0]["id"] == first["id"]
    assert api.get(path(project), params={"q": " \n "}).json()["total"] == 3
    assert api.get(path(project), params={"status": "all", "offset": 200}).json()["total"] == 4
    assert api.get(path(project), params={"status": "all", "offset": 200}).json()["items"] == []


def test_edit_refreshes_search_vector(api, project, handoff_payload):
    handoff = save(api, project, handoff_payload, title="Orchestrating state")
    assert api.get(path(project), params={"q": "orchestrates"}).json()["total"] == 1
    update = api.patch(
        path(project, handoff), json={"expected_version": 1, "title": "Brand new heading"}
    )
    assert update.status_code == 200
    assert api.get(path(project), params={"q": "orchestrates"}).json()["total"] == 0
    assert api.get(path(project), params={"q": "heading"}).json()["total"] == 1


def test_two_simultaneous_writers_cannot_overwrite_each_other(api, project, handoff_payload):
    handoff = save(api, project, handoff_payload)
    barrier = Barrier(2)

    def writer(title):
        barrier.wait(timeout=5)
        return api.patch(path(project, handoff), json={"expected_version": 1, "title": title})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(writer, ["Writer A", "Writer B"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    final = api.get(path(project, handoff)).json()
    assert final["version"] == 2
    assert final["title"] == winner["title"]


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
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
                "WHERE attrelid = 'handoffs'::regclass AND attname = 'search_vector'"
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
