"""Closing work captures exact decision use, and merge mappings never rewrite history."""

import io
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from mnemonic_api.knowledge_integrity import KNOWLEDGE_FINDINGS
from mnemonic_backup.archive import BackupError, restore_project

from .test_knowledge_postgres import domain, path, put, put_body
from .test_project_backup_archive import _export, _rewrite, _snapshot
from .test_project_reports_postgres import close, create

pytestmark = pytest.mark.postgres
REPOSITORY = "github.com/example/application"
CANONICAL_REPOSITORY = "https://github.com/example/application"
ORIGINAL = "a" * 40
MERGED = "b" * 40


@pytest.fixture
def database(postgres_engine, api):
    with postgres_engine.connect() as connection:
        yield connection


def declaration(decision):
    return {
        "disposition": "cited", "reason": None,
        "citations": [{"decision_id": decision["decision_id"], "revision": decision["revision"]}],
        "commits": [{"repository": REPOSITORY, "commit_hash": ORIGINAL}],
    }


def report(provenance):
    return {"summary": "Implemented the recorded decision.", "fyi_items": [],
            "prompt_revision": "1", "decision_provenance": provenance}


def test_completion_requires_explicit_declaration_and_rolls_back(
    api, project, work_payload, checkpoint_fields, database,
):
    domain(api, project)
    work = create(api, project, work_payload)
    failed, _ = close(api, project, work, checkpoint_fields, job_completion_report={
        "summary": "Missing declaration.", "fyi_items": [], "prompt_revision": "1",
    })
    assert failed.status_code == 422, failed.text
    assert failed.json()["detail"]["code"] == "decision_provenance_required"
    endpoint = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    assert api.get(endpoint).json()["work_item"]["version"] == work["version"]
    assert database.scalar(text("SELECT count(*) FROM job_completion_reports")) == 0
    successful, _ = close(api, project, work, checkpoint_fields, job_completion_report=report({
        "disposition": "not-code", "reason": "Only documentation was reviewed.",
    }))
    assert successful.status_code == 200, successful.text
    assert database.scalar(text("SELECT count(*) FROM decision_scopes")) == 0


def test_completion_citations_pin_old_revision_and_replay_atomically(
    api, project, work_payload, checkpoint_fields, database,
):
    owner = domain(api, project)
    decision = put(api, project, put_body(owner))["decision"]
    put(api, project, put_body(owner, "New wording.", decision_id=decision["decision_id"],
                               expected_revision=0))
    work = create(api, project, work_payload)
    response, intent = close(api, project, work, checkpoint_fields,
                             job_completion_report=report(declaration(decision)))
    assert response.status_code == 200, response.text
    endpoint = f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete"
    assert api.post(endpoint, json=intent).json() == response.json()
    scope = api.get(path(project) + f"/decisions/{decision['decision_id']}/scope").json()
    assert scope["total"] == 1
    item = scope["items"][0]
    assert item["revision"] == 0
    assert item["repository"] == CANONICAL_REPOSITORY
    assert item["work_item_id"] == work["id"]
    assert item["agent_session_id"] == checkpoint_fields["source_session_id"]
    assert item["completion_report_id"] == response.json()["job_completion_report"]["id"]
    for name, query in KNOWLEDGE_FINDINGS.items():
        assert database.scalar(text(query)) == 0, name


def test_bad_citation_cannot_close_work_or_leave_partial_scope(
    api, project, work_payload, checkpoint_fields, database,
):
    owner = domain(api, project)
    decision = put(api, project, put_body(owner))["decision"]
    work = create(api, project, work_payload)
    provenance = declaration(decision)
    provenance["citations"].append({"decision_id": "ADR-99999", "revision": 0})
    response, _ = close(api, project, work, checkpoint_fields,
                         job_completion_report=report(provenance))
    assert response.status_code == 404, response.text
    assert database.scalar(text("SELECT count(*) FROM decision_scopes")) == 0
    assert database.scalar(text("SELECT count(*) FROM job_completion_reports")) == 0


def test_merge_reconciliation_reverse_lookup_and_immutability(api, project, database):
    owner = domain(api, project)
    decision = put(api, project, put_body(owner))["decision"]
    endpoint = path(project) + f"/decisions/{decision['decision_id']}/scope"
    scope = api.post(endpoint, json={
        "revision": 0, "commit_hash": ORIGINAL, "repository": REPOSITORY,
        "client_operation_id": str(uuid4()),
    })
    assert scope.status_code == 200, scope.text
    intent = {"repository": scope.json()["scope"]["repository"], "original_commit_hash": ORIGINAL,
              "merged_commit_hash": MERGED, "client_operation_id": str(uuid4())}
    response = api.post(path(project) + "/commits/reconcile", json=intent)
    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    assert api.post(path(project) + "/commits/reconcile", json=intent).json() == response.json()
    for commit in (ORIGINAL, MERGED):
        found = api.get(path(project) + "/commits",
                        params={"repository": REPOSITORY, "commit_hash": commit})
        assert found.status_code == 200, found.text
        assert found.json()["total"] == 1
        item = found.json()["items"][0]
        assert item["commit_hash"] == ORIGINAL
        assert item["merged_commit_hashes"] == [MERGED]
    other = api.get(path(project) + "/commits",
                    params={"repository": "other/repo", "commit_hash": MERGED})
    assert other.json()["total"] == 0
    for name, query in KNOWLEDGE_FINDINGS.items():
        assert database.scalar(text(query)) == 0, name
    with pytest.raises(DBAPIError), database.begin_nested():
        database.execute(text("DELETE FROM decision_commit_mappings"))


def test_old_receipt_remains_replayable_after_domain_creation(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    from alembic import command
    from alembic.config import Config

    from .conftest import BACKEND_DIR
    from .test_product_decision_migration_postgres import _historical_automatic_review_columns

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    work = create(api, project, work_payload)
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0055_decision_provenance")
    with _historical_automatic_review_columns():
        result, intent = close(api, project, work, checkpoint_fields, job_completion_report={
            "summary": "Historical domainless completion.", "fyi_items": [], "prompt_revision": "1",
        })
    assert result.status_code == 200, result.text
    assert "decision_provenance" not in result.json()["job_completion_report"]
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    domain(api, project)
    endpoint = f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete"
    assert api.post(endpoint, json=intent).json() == result.json()


def test_backup_retains_completion_citations_and_refuses_forged_declaration(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    decision = put(api, project, put_body(domain(api, project)))["decision"]
    work = create(api, project, work_payload)
    result, _ = close(api, project, work, checkpoint_fields,
                       job_completion_report=report(declaration(decision)))
    assert result.status_code == 200, result.text
    mapping = api.post(path(project) + "/commits/reconcile", json={
        "repository": CANONICAL_REPOSITORY, "original_commit_hash": ORIGINAL,
        "merged_commit_hash": MERGED, "client_operation_id": str(uuid4()),
    })
    assert mapping.status_code == 200, mapping.text
    original = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    restored = _snapshot(postgres_engine, project)
    assert restored["project_activity_heads"][0]["stream_id"] != \
        original["project_activity_heads"][0]["stream_id"]
    original["project_activity_heads"] = restored["project_activity_heads"]
    assert restored == original

    def forge(_header, rows):
        rows["job_completion_reports"][0]["decision_provenance"] = {
            "disposition": "not-code", "reason": "Forged declaration.",
            "citations": [], "commits": [],
        }

    with pytest.raises(BackupError, match="integrity"):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(archive, forge)))
    assert _snapshot(postgres_engine, project) == original
