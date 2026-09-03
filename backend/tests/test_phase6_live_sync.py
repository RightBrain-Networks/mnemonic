"""Outcome-aware live invalidation for Phase 6-covered writes."""

import logging
from collections.abc import Callable
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import mnemonic_api.application.middleware as middleware_module
import mnemonic_api.application.mutations as mutations_module
from mnemonic_api.application.mutations import MutationTrace, record_mutation_trace
from mnemonic_api.config import Settings
from mnemonic_api.errors import client_operation_unavailable
from mnemonic_api.main import create_app

API_KEY = "phase6-live-sync-key-with-at-least-32-characters"
DATABASE_URL = "postgresql://localhost:1/unavailable"
PROJECT_ID = "00000000-0000-4000-8000-000000000001"
WORK_ITEM_ID = "00000000-0000-4000-8000-000000000002"


def settings() -> Settings:
    return Settings(database_url=DATABASE_URL, api_key=API_KEY)


@pytest.mark.parametrize(
    ("decision", "expected_publications"),
    [(True, 1), (False, 0), ("missing", 1)],
)
def test_successful_mutation_publication_honors_explicit_domain_outcome(
    decision: bool | str,
    expected_publications: int,
) -> None:
    app = create_app(settings())
    path = f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ITEM_ID}/phase6-test"

    @app.post(path)
    def phase6_test_route(request: Request) -> dict[str, bool]:
        if isinstance(decision, bool):
            trace = MutationTrace("create_work", mutation_applied=decision)
            record_mutation_trace(request, trace)
        return {"ok": True}

    publications = []

    async def capture_publication(event) -> None:
        publications.append(event)

    publish: Callable = capture_publication
    app.state.live_sync_hub.publish = publish
    with TestClient(app) as client:
        response = client.post(
            path, headers={"Authorization": f"Bearer {API_KEY}"}
        )

    assert response.status_code == 200
    assert len(publications) == expected_publications
    if publications:
        assert publications[0].scope == "work-items"
        assert publications[0].message(1) == {
            "type": "invalidate",
            "revision": 1,
            "scope": "work-items",
        }


@pytest.mark.postgres
def test_completion_invariant_failure_logs_one_bounded_unavailable_outcome(
    api,
    project,
    work_payload,
    monkeypatch,
    caplog,
) -> None:
    operation_id = str(uuid4())

    def fail_completion(*args, **kwargs):
        del args, kwargs
        raise client_operation_unavailable()

    monkeypatch.setattr(
        mutations_module, "complete_client_operation", fail_completion
    )
    middleware_module.logger.disabled = False
    caplog.set_level(logging.INFO, logger="mnemonic_api.application.middleware")

    response = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "client_operation_id": operation_id},
    )

    assert response.status_code == 503
    outcomes = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Client operation outcome")
    ]
    assert outcomes == [
        "Client operation outcome kind=create_work outcome=unavailable"
    ]
    assert operation_id not in caplog.text


@pytest.mark.postgres
def test_commit_failure_logs_one_bounded_unavailable_outcome(
    api,
    project,
    work_payload,
    monkeypatch,
    caplog,
) -> None:
    operation_id = str(uuid4())

    def fail_commit(self):
        del self
        raise SQLAlchemyError("private commit failure")

    monkeypatch.setattr(Session, "commit", fail_commit)
    middleware_module.logger.disabled = False
    caplog.set_level(logging.INFO, logger="mnemonic_api.application.middleware")

    response = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "client_operation_id": operation_id},
    )

    assert response.status_code == 503
    outcomes = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Client operation outcome")
    ]
    assert outcomes == [
        "Client operation outcome kind=create_work outcome=unavailable"
    ]
    assert operation_id not in caplog.text
    assert "private commit failure" not in caplog.text


@pytest.mark.postgres
def test_publish_failure_after_commit_heals_without_republishing_on_exact_replay(
    api,
    project,
    work_payload,
    caplog,
) -> None:
    operation_id = str(uuid4())
    publications = []

    async def fail_once(event) -> None:
        publications.append(event)
        if len(publications) == 1:
            raise RuntimeError("synthetic post-commit publication failure")

    api.app.state.live_sync_hub.publish = fail_once
    middleware_module.logger.disabled = False
    caplog.set_level(logging.INFO, logger="mnemonic_api.application.middleware")
    path = f"/api/v1/projects/{project['id']}/work-items"
    payload = {**work_payload, "client_operation_id": operation_id}

    with pytest.raises(
        RuntimeError, match="synthetic post-commit publication failure"
    ):
        api.post(path, json=payload)

    replay = api.post(path, json=payload)
    assert replay.status_code == 201
    listed = api.get(path)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert len(publications) == 1
    outcomes = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Client operation outcome")
    ]
    assert outcomes == [
        "Client operation outcome kind=create_work outcome=executed",
        "Client operation outcome kind=create_work outcome=replayed",
    ]
    assert operation_id not in caplog.text
