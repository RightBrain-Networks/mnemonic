"""Project rubric persistence, explicit reads, and safe settings edits."""

import io
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from mnemonic_api.priority_rubric import DEFAULT_PRIORITY_RUBRIC
from mnemonic_backup.archive import restore_project

from .test_artifact_extraction_migration_postgres import migrate
from .test_leases_postgres import create_work, item_path
from .test_project_backup_archive import _export

pytestmark = pytest.mark.postgres


def path(project):
    return f"/api/v1/projects/{project['id']}/priority-rubric"


def save(api, project, content, revision=None):
    if revision is None:
        revision = api.get(path(project)).json()["revision"]
    return api.patch(path(project), json={"content": content, "expected_revision": revision})


def test_rubric_defaults_persistence_isolation_and_explicit_reads(api, project, work_payload):
    initial = api.get(path(project))
    assert initial.status_code == 200
    assert initial.json() == {
        "project_id": project["id"], "content": DEFAULT_PRIORITY_RUBRIC, "revision": "1",
    }
    content = "  # Priorités\r\n\n**Customer impact** first. 📄\n"
    saved = save(api, project, content)
    assert saved.status_code == 200, saved.text
    assert saved.json()["content"] == content
    assert api.get(path(project)).json() == saved.json()
    other = api.post("/api/v1/projects", json={"name": "Separate priorities"}).json()
    assert api.get(path(other)).json()["content"] == DEFAULT_PRIORITY_RUBRIC
    work = create_work(api, project, work_payload)["work_item"]
    for endpoint in (
        "/api/v1/projects", f"/api/v1/projects/{project['id']}",
        f"/api/v1/projects/{project['id']}/settings", item_path(project, work),
        f"{item_path(project, work)}/context", f"{item_path(project, work)}?status_only=true",
    ):
        response = api.get(endpoint)
        assert response.status_code == 200, response.text
        assert "priority_rubric" not in response.text
        assert "Customer impact" not in response.text


def test_stale_and_noop_edits_preserve_content_and_revision(api, project):
    original = api.get(path(project)).json()
    assert save(api, project, original["content"], original["revision"]).json() == original
    saved = save(api, project, "# Latest policy", original["revision"]).json()
    assert int(saved["revision"]) == int(original["revision"]) + 1
    conflict = save(api, project, "# Stale draft", original["revision"])
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "priority_rubric_changed"
    assert api.get(path(project)).json() == saved
    activity = api.get(f"/api/v1/projects/{project['id']}/activity").json()["items"]
    assert [item["settings_revision"] for item in activity
            if item["kind"] == "project_settings_updated"] == [saved["revision"]]


@pytest.mark.parametrize("content", [
    "", " \n\t", "\u2003", "a\x00b", "\ud800", "x" * 100001, 4, None,
], ids=["empty", "blank", "unicode-blank", "nul", "surrogate", "oversize", "number", "null"])
def test_invalid_content_does_not_change_saved_rubric(api, project, content):
    before = api.get(path(project)).json()
    # Serialize explicitly so lone surrogates reach the wire as JSON escapes.
    import json

    response = api.patch(path(project), content=json.dumps({
        "content": content, "expected_revision": before["revision"],
    }), headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert api.get(path(project)).json() == before


def test_unknown_project_and_read_arguments_are_rejected(api):
    endpoint = path({"id": uuid4()})
    assert api.get(endpoint).status_code == 404
    assert api.patch(endpoint, json={"content": "x", "expected_revision": "1"}).status_code == 404


def test_reads_are_explicit_and_settings_writes_cannot_edit_rubric(api, project):
    assert api.get(path(project), params={"extra": "true"}).status_code == 422
    assert api.request("GET", path(project), json={}).status_code == 422
    endpoint = f"/api/v1/projects/{project['id']}/settings"
    assert api.patch(endpoint, json={
        "expected_revision": "1", "priority_rubric": "Unexpected route",
    }).status_code == 422


@pytest.mark.parametrize(
    "content", ["", "\u2003", "x" * 100001], ids=["empty", "blank", "oversize"],
)
def test_database_content_constraints(postgres_engine, project, content):
    with pytest.raises(IntegrityError) as error, postgres_engine.begin() as connection:
        connection.execute(text(
            "UPDATE project_settings SET priority_rubric=:content, revision=revision+1 "
            "WHERE project_id=:id"
        ), {"content": content, "id": project["id"]})
    assert error.value.orig.diag.constraint_name == "ck_project_settings_priority_rubric_valid"


def test_database_rubric_edits_require_a_revision_increment(postgres_engine, project):
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        connection.execute(text(
            "UPDATE project_settings SET priority_rubric='A valid but unversioned edit' "
            "WHERE project_id=:id"
        ), {"id": project["id"]})


def test_migration_seeds_existing_projects_and_keeps_customizations_on_upgrade(
    api, project, postgres_engine,
):
    migrate(postgres_engine, "0050_manual_review_modes", downgrade=True)
    migrate(postgres_engine, "head")
    assert api.get(path(project)).json()["content"] == DEFAULT_PRIORITY_RUBRIC
    saved = save(api, project, "# Customized rubric").json()
    migrate(postgres_engine, "head")
    assert api.get(path(project)).json() == saved
    with pytest.raises(RuntimeError, match="Customized priority rubrics"):
        migrate(postgres_engine, "0050_manual_review_modes", downgrade=True)
    assert api.get(path(project)).json() == saved


def test_backup_restores_exact_custom_rubric(api, project, postgres_engine):
    saved = save(api, project, "# Backed up rubric\n\nKeep **this**.\n").json()
    backup = _export(postgres_engine, project)
    assert save(api, project, "# Later edit").status_code == 200
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(backup))
    assert api.get(path(project)).json() == saved
