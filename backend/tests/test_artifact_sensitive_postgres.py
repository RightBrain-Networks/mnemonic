"""Sensitive content stays behind fresh, request-bound, single-use human approval."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from mnemonic_api.models import ArtifactAccessApproval

from .test_artifact_extraction_postgres import Parser, run_job
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import collection, upload

pytestmark = pytest.mark.postgres
ACTOR = {"agent_session_id": "sensitive-test-session", "actor_client": "pytest-agent"}


def _headers(**approval):
    return {"X-Artifact-Metadata": json.dumps({**ACTOR, **approval})}


def _artifact(api, project, storage):
    artifact = upload(api, project, filename="private.txt", body=b"confidentialneedle contents",
                      metadata={"sensitive": True})
    assert run_job(api, storage, Parser())
    return artifact, collection(project) + "/" + artifact["id"]


def _token(response):
    assert response.status_code == 428, response.text
    assert response.headers["cache-control"] == "no-store"
    detail = response.json()["detail"]
    assert detail["code"] == "artifact_human_approval_required"
    assert "EXPLICIT HUMAN APPROVAL REQUIRED" in detail["message"]
    assert "Do not retry automatically" in detail["message"]
    assert "confidentialneedle" not in response.text
    assert detail["context"]["human_approval_required"] is True
    return detail["context"]["approval_token"]


def test_sensitive_download_requires_new_human_approval_for_each_use(
    api, project, artifact_storage,
):
    _, path = _artifact(api, project, artifact_storage)
    token = _token(api.get(path + "/content", headers=_headers()))
    _token(api.get(path + "/content", headers=_headers(approval_token=token)))
    approved = _headers(approval_token=token, human_approved=True)
    result = api.get(path + "/content", headers=approved)
    assert result.status_code == 200, result.text
    assert result.content == b"confidentialneedle contents"
    assert _token(api.get(path + "/content", headers=approved)) != token
    history = api.get(path + "/history").json()
    actions = [event["action"] for event in history["audit"]["items"]]
    assert actions.count("approval_granted") == 1
    assert actions.count("sensitive_downloaded") == 1
    assert actions.count("approval_rejected") == 2
    assert token not in json.dumps(history)
    with api.app.state.session_factory() as database:
        stored = list(database.scalars(select(ArtifactAccessApproval)))
        assert all(row.token_hash != token for row in stored)
        assert sum(row.consumed_at is not None for row in stored) == 1


@pytest.mark.parametrize("changed", ["caller", "action", "artifact", "revision", "expired"])
def test_approval_cannot_cross_request_boundaries(api, project, artifact_storage, changed):
    artifact, path = _artifact(api, project, artifact_storage)
    token = _token(api.get(path + "/content", headers=_headers()))
    request_headers = _headers(approval_token=token, human_approved=True)
    target = path + "/content"
    params = {}
    if changed == "caller":
        request_headers = {"X-Artifact-Metadata": json.dumps({
            **ACTOR, "actor_client": "another-agent", "approval_token": token,
            "human_approved": True,
        })}
    elif changed == "action":
        target, params = path + "/text", {"expected_revision": 1}
    elif changed == "artifact":
        _, other = _artifact(api, project, artifact_storage)
        target = other + "/content"
    elif changed == "revision":
        updated = api.patch(path, json={
            "client_operation_id": str(uuid4()), "expected_revision": artifact["revision"],
            "description": "Updated description", **ACTOR,
        })
        assert updated.status_code == 200, updated.text
    else:
        with api.app.state.session_factory() as database:
            original = database.scalar(select(ArtifactAccessApproval))
            token = uuid4().hex
            database.add(ArtifactAccessApproval(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                artifact_id=original.artifact_id, revision=original.revision,
                action=original.action, request_hash=original.request_hash, **ACTOR,
                created_at=datetime.now(UTC) - timedelta(minutes=10),
                expires_at=datetime.now(UTC) - timedelta(minutes=5),
            ))
            database.commit()
        request_headers = _headers(approval_token=token, human_approved=True)
    assert _token(api.get(target, params=params, headers=request_headers)) != token


def test_concurrent_token_consumption_allows_exactly_one_read(api, project, artifact_storage):
    _, path = _artifact(api, project, artifact_storage)
    token = _token(api.get(path + "/content", headers=_headers()))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: api.get(
            path + "/content", headers=_headers(approval_token=token, human_approved=True),
        ), range(2)))
    assert sorted(result.status_code for result in results) == [200, 428]


def test_each_text_page_needs_a_request_bound_approval(api, project, artifact_storage):
    _, path = _artifact(api, project, artifact_storage)
    params = {"expected_revision": 1, "limit": 5}
    token = _token(api.get(path + "/text", params=params, headers=_headers()))
    approved = _headers(approval_token=token, human_approved=True)
    _token(api.get(path + "/text", params={**params, "offset": 5}, headers=approved))
    first = api.get(path + "/text", params=params, headers=approved)
    assert first.status_code == 200, first.text
    assert first.json()["text"] == "confi"
    _token(api.get(path + "/text", params=params, headers=approved))


def test_search_withholds_sensitive_body_and_properties_and_cache_cannot_bypass_approval(
    api, project, artifact_storage,
):
    artifact, path = _artifact(api, project, artifact_storage)
    search = collection(project) + "/search-content"
    payload = {"q": "confidentialneedle", "fulltext": True, **ACTOR}
    broad = api.post(search, json=payload)
    assert broad.status_code == 200, broad.text
    assert broad.json()["items"] == []
    assert broad.json()["sensitive_content_withheld"] == 1
    targeted = {**payload, "artifact_id": artifact["id"]}
    token = _token(api.post(search, json=targeted))
    approved = {**targeted, "approval_token": token, "human_approved": True}
    _token(api.post(search, json={**approved, "q": "contents"}))
    result = api.post(search, json=approved)
    assert result.status_code == 200, result.text
    assert result.json()["items"][0]["artifact"]["id"] == artifact["id"]
    assert result.json()["items"][0]["snippet"] is not None
    assert result.json()["sensitive_content_withheld"] == 0
    _token(api.post(search, json=approved))
    assert api.post(search, json=payload).json()["items"] == []
    assert api.get(path).json()["extraction"]["metadata"] == {}
    revision = api.get(path + "/history").json()["revisions"]["items"][0]
    assert revision["extraction"]["metadata"] == {}


def test_human_dashboard_access_is_explicit_and_audited(api, project, artifact_storage):
    artifact, path = _artifact(api, project, artifact_storage)
    dashboard = {"X-Artifact-Access": "human-dashboard"}
    result = api.get(path + "/content", headers=dashboard)
    assert result.status_code == 200, result.text
    search = api.post(collection(project) + "/search-content", headers=dashboard, json={
        "q": "confidentialneedle", "fulltext": True,
    })
    assert search.json()["items"][0]["artifact"]["id"] == artifact["id"]
    events = api.get(path + "/history").json()["audit"]["items"]
    assert any(event["action"] == "sensitive_downloaded" and
               event["details"]["access_mode"] == "human_dashboard" for event in events)
    _token(api.get(path + "/content", headers=_headers()))


def test_approval_requires_strict_boolean_and_never_echoes_token_into_metadata(
    api, project, artifact_storage,
):
    artifact, path = _artifact(api, project, artifact_storage)
    token = _token(api.get(path + "/content", headers=_headers()))
    for value in ("true", 1, "yes"):
        result = api.get(path + "/content", headers=_headers(
            approval_token=token, human_approved=value,
        ))
        assert result.status_code == 422
        assert token not in result.text
    with api.app.state.session_factory() as database:
        assert database.scalar(select(ArtifactAccessApproval).where(
            ArtifactAccessApproval.artifact_id == UUID(artifact["id"]),
        )).consumed_at is None


@pytest.mark.parametrize("field", ["actor_client", "agent_session_id"])
def test_content_routes_reject_credential_and_token_echo_in_audit_fields(
    api, project, artifact_storage, field,
):
    artifact, path = _artifact(api, project, artifact_storage)
    token = _token(api.get(path + "/content", headers=_headers()))
    for secret in (token, api.app.state.settings.api_key.get_secret_value()):
        actor = {**ACTOR, field: secret, "approval_token": token, "human_approved": True}
        for suffix, params in (("content", {}), ("text", {"expected_revision": 1})):
            response = api.get(path + "/" + suffix, params=params, headers={
                "X-Artifact-Metadata": json.dumps(actor),
            })
            assert response.status_code == 422
            assert secret not in response.text
        response = api.post(collection(project) + "/search-content", json={
            "q": "confidentialneedle", "fulltext": True, "artifact_id": artifact["id"],
            **actor,
        })
        assert response.status_code == 422
        assert secret not in response.text
    history = api.get(path + "/history").json()
    assert history["audit"]["total"] == 2


def test_historically_sensitive_properties_stay_hidden_after_flag_is_cleared(
    api, project, artifact_storage,
):
    _, path = _artifact(api, project, artifact_storage)
    updated = api.patch(path, json={
        "client_operation_id": str(uuid4()), "expected_revision": 1,
        "sensitive": False, **ACTOR,
    })
    assert updated.status_code == 200, updated.text
    history = api.get(path + "/history").json()
    old = history["revisions"]["items"][-1]
    assert old["sensitive"] is True
    assert old["extraction"]["metadata"] == {}
    filtered = api.get(path + "/history", params={"q": "Synthetic Author"}).json()
    assert filtered["revisions"]["total"] == 0
    assert api.get(collection(project), params={"q": "Synthetic Author"}).json()["total"] == 0
