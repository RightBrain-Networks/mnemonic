"""Starter labels are independent editable copies of the requested Fish Food snapshot."""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from .conftest import BACKEND_DIR, reset_disposable_schema
from .test_project_tags import browser

pytestmark = pytest.mark.postgres
DIR_DESCRIPTION = "Design Impact Review: investigate the impact a revised decision document"
STARTERS = [
    ("bug-suspected", "A suspected bug that warrants further investigation", "#790000"),
    ("bug-proven", "A bug that has been proven to exist and needs to be fixed", "#ff8080"),
    ("infra", "Fix, implement, or improve the CI pipeline or other "
     "infrastructure-related subsystem",
     "#80ff80"),
    ("tests", "Implement, research, or fix regression tests, test workflow, or test documentation",
     "#804040"),
    ("ux-ui", "UI or UX-related work", "#d19df0"),
    ("security", "Fixing, researching, documenting, or implementing security-related functionality",
     "#ffff00"),
]


def test_new_project_gets_exact_starter_catalog_and_can_customize_it(api, project):
    path = f"/api/v1/projects/{project['id']}/tags"
    rows = api.get(path).json()["items"]
    assert [(row["name"], row["description"], row["color"]) for row in rows[3:]] == STARTERS
    assert [row["menu_order"] for row in rows] == list(range(9))
    assert rows[2]["description"] == DIR_DESCRIPTION
    assert all(row["system"] for row in rows[:3])
    assert not any(row["system"] for row in rows[3:])
    edited = browser(api, project, "PATCH", "/" + rows[3]["id"],
                     name="investigate", description="Local usage", color="#aabbcc")
    assert edited.status_code == 200, edited.text
    deleted = browser(api, project, "DELETE", "/" + rows[4]["id"])
    assert deleted.status_code == 200, deleted.text
    other = api.post("/api/v1/projects", json={"name": "Independent starter tags"}).json()
    other_rows = api.get(f"/api/v1/projects/{other['id']}/tags").json()["items"]
    assert [(row["name"], row["description"], row["color"])
            for row in other_rows[3:]] == STARTERS
    assert {row["id"] for row in rows}.isdisjoint(row["id"] for row in other_rows)


def test_migration_preserves_existing_catalog_and_changes_only_system_dir(postgres_engine):
    reset_disposable_schema(postgres_engine)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    project_id, tag_id = uuid4(), uuid4()
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0067_project_tags")
        connection.execute(text("INSERT INTO projects(id,name,slug) VALUES (:id,'Existing','old')"),
                           {"id": project_id})
        connection.execute(text("""
            INSERT INTO project_tags(id,project_id,name,description,color,menu_order,system)
            VALUES (:id,:project,'infra','Keep my wording','#123456',12,false)
        """), {"id": tag_id, "project": project_id})
        before = connection.execute(text("SELECT * FROM project_tags WHERE name <> 'dir' "
                                         "ORDER BY id")).all()
        command.upgrade(config, "head")
        after = connection.execute(text("SELECT * FROM project_tags WHERE name <> 'dir' "
                                        "ORDER BY id")).all()
        assert before == after
        assert connection.scalar(text("SELECT count(*) FROM project_tags")) == 4
        assert connection.scalar(text("SELECT description FROM project_tags WHERE name='dir'")) \
            == DIR_DESCRIPTION
    # The one-time migration must leave the immutability guard enabled.
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError, match="system tags are immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(text("UPDATE project_tags SET description='changed' "
                                    "WHERE name='dir'"))
