"""Fresh summaries use deployment policy; durable history never uses today's limit."""

from contextlib import ExitStack
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from mnemonic_api.config import Settings
from mnemonic_api.main import create_app

from .conftest import TEST_API_KEY
from .test_artifact_extraction_migration_postgres import migrate


def test_summary_limit_defaults_and_environment(monkeypatch):
    monkeypatch.delenv("MNEMONIC_WORK_SUMMARY_MAX_CHARS", raising=False)
    fields = {"database_url": "postgresql://example.invalid/test", "api_key": TEST_API_KEY}
    assert Settings(**fields).work_summary_max_chars == 2048
    monkeypatch.setenv("MNEMONIC_WORK_SUMMARY_MAX_CHARS", "4096")
    assert Settings(**fields).work_summary_max_chars == 4096
    for invalid in ("0", "-1", "text", "2.5", ""):
        monkeypatch.setenv("MNEMONIC_WORK_SUMMARY_MAX_CHARS", invalid)
        with pytest.raises(ValidationError):
            Settings(**fields)


@pytest.fixture
def configured_api(api, postgres_engine):
    with ExitStack() as stack:
        def client(maximum):
            config = api.app.state.settings.model_copy(update={
                "work_summary_max_chars": maximum, "artifact_max_bytes": 0,
            })
            result = stack.enter_context(TestClient(create_app(config, engine=postgres_engine)))
            result.headers["Authorization"] = f"Bearer {TEST_API_KEY}"
            return result
        yield client


def work_path(project):
    return f"/api/v1/projects/{project['id']}/work-items"


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("maximum", "character"), [(2048, "a"), (2048, "🧠"), (4096, "x"), (32, "é")]
)
def test_creation_limit_and_exact_error(configured_api, project, work_payload, maximum, character):
    api = configured_api(maximum)
    path = work_path(project)
    body = {**work_payload, "summary": character * maximum, "client_operation_id": str(uuid4())}
    created = api.post(path, json=body)
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    assert work["summary"] == body["summary"]
    assert api.get(f"{path}/{work['id']}/context").status_code == 200
    events = api.get(f"{path}/{work['id']}/events")
    assert events.status_code == 200, events.text
    assert events.json()["items"][0]["metadata"]["initial"]["summary"] == body["summary"]
    assert api.get(path, params={"view": "full"}).status_code == 200
    refused = api.post(path, json={**body, "client_operation_id": str(uuid4()),
                                  "summary": character * (maximum + 1)})
    assert refused.status_code == 422, refused.text
    assert refused.json() == {"detail": {
        "code": "work_summary_too_long",
        "message": f"Work summary exceeds the configured maximum of {maximum} characters.",
        "context": {"max_chars": maximum},
    }}
    assert api.get(path).json()["total"] == 1


@pytest.mark.postgres
def test_lower_limit_preserves_reads_replays_and_unrelated_edits(
    configured_api, project, work_payload,
):
    high = configured_api(4096)
    low = configured_api(32)
    path = work_path(project)
    body = {**work_payload, "summary": "a" * 4096, "client_operation_id": str(uuid4())}
    created = high.post(path, json=body)
    assert created.status_code == 201, created.text
    assert low.post(path, json=body).json() == created.json()
    work = created.json()["work_item"]
    item = f"{path}/{work['id']}"
    assert low.get(item).json()["work_item"]["summary"] == body["summary"]
    changed_body = {"expected_version": 1, "summary": "b" * 4096,
                    "actor": {"actor_client": "pytest", "actor_session_id": "summary-limit"},
                    "client_operation_id": str(uuid4())}
    changed = high.patch(item, json=changed_body)
    assert changed.status_code == 200, changed.text
    assert low.patch(item, json=changed_body).json() == changed.json()
    assert low.get(f"{item}/events").status_code == 200
    unrelated = low.patch(item, json={"expected_version": 2, "title": "Edited title",
                                     "summary": changed_body["summary"]})
    assert unrelated.status_code == 200, unrelated.text
    refused = low.patch(item, json={"expected_version": 3, "summary": "c" * 33})
    assert refused.status_code == 422
    assert refused.json()["detail"]["context"] == {"max_chars": 32}
    shortened = low.patch(item, json={"expected_version": 3, "summary": "c" * 32})
    assert shortened.status_code == 200, shortened.text
    assert low.get(f"{item}/events").status_code == 200


@pytest.mark.postgres
def test_suggestion_uses_same_summary_limit(configured_api, project):
    api = configured_api(32)
    result = api.post(f"/api/v1/projects/{project['id']}/duplicate-suggestions", json={
        "title": "Draft", "summary": "x" * 33, "initial_prompt": "Complete instructions",
    })
    assert result.status_code == 422
    assert result.json()["detail"]["context"] == {"max_chars": 32}


@pytest.mark.postgres
def test_migration_preserves_summary_receipt_and_search(
    api, project, work_payload, postgres_engine,
):
    migrate(postgres_engine, "0027_artifact_fulltext", downgrade=True)
    path = work_path(project)
    body = {**work_payload, "summary": "migration searchable summary " + "x" * 970,
            "client_operation_id": str(uuid4())}
    created = api.post(path, json=body)
    assert created.status_code == 201, created.text
    migrate(postgres_engine, "head")
    assert api.post(path, json=body).json() == created.json()
    found = api.get(path, params={"q": "searchable", "view": "full"})
    assert found.status_code == 200 and found.json()["total"] == 1
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("""
            SELECT data_type FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='work_items'
              AND column_name='summary'
        """)) == "text"
    migrate(postgres_engine, "0027_artifact_fulltext", downgrade=True)
    migrate(postgres_engine, "head")


@pytest.mark.postgres
def test_downgrade_refuses_long_history_even_after_shortening(
    api, project, work_payload, postgres_engine,
):
    path = work_path(project)
    created = api.post(path, json={**work_payload, "summary": "x" * 2048})
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    assert api.patch(f"{path}/{work['id']}", json={
        "expected_version": 1, "summary": "Short now",
    }).status_code == 200
    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        migrate(postgres_engine, "0027_artifact_fulltext", downgrade=True)



@pytest.mark.postgres
def test_follow_up_limit_and_replay(configured_api, project, work_payload, checkpoint_fields):
    from .test_project_reports_postgres import actor, close, create

    high = configured_api(4096)
    work = create(high, project, work_payload)
    closed, _ = close(high, project, work, checkpoint_fields, status="wont-do")
    assert closed.status_code == 200, closed.text
    report = closed.json()["job_completion_report"]
    path = f"/api/v1/projects/{project['id']}/job-completion-reports/{report['id']}/follow-ups"
    payload = {"title": "Follow up", "summary": "x" * 4096,
               "initial_checkpoint": checkpoint_fields, "actor": actor(checkpoint_fields),
               "client_operation_id": str(uuid4())}
    created = high.post(path, json=payload)
    assert created.status_code == 201, created.text
    low = configured_api(32)
    assert low.post(path, json=payload).json() == created.json()
    refused = low.post(path, json={**payload, "client_operation_id": str(uuid4())})
    assert refused.status_code == 422
    assert refused.json()["detail"]["context"]["max_chars"] == 32


@pytest.mark.postgres
def test_generated_remediation_fits_small_configured_limit(
    configured_api, project, work_payload, checkpoint_fields,
):
    from .code_review_database_fixtures import close_work, create_work, finish_review, policy

    api = configured_api(32)
    policy(api, project)
    work = create_work(api, project, {**work_payload, "summary": "Short work objective"})
    closed = close_work(api, project, work, checkpoint_fields)
    result = finish_review(api, project, closed["work_item"], closed["code_review_request"])
    assert len(result["remediation_work"]["work_item"]["summary"]) <= 32
