"""Browser reads, revisions and durable controls use the transcript domain unchanged."""

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select

from mnemonic_api.transcript_normalized_storage import SEGMENTS

from .test_browser_transcripts import HEADERS
from .test_leases_postgres import expire_lease
from .test_transcript_imports_postgres import codex_source
from .test_transcript_indexing_postgres import collection, read, register, run

pytestmark = pytest.mark.postgres


@pytest.fixture
def browser(api):
    # Exercise the actual create_app registration alongside machine REST.
    api.headers.update(HEADERS)
    return api


def _browser_path(path):
    return path.replace("/api/v1/", "/api/transcripts/", 1)


def _settings(project):
    return f"/api/v1/projects/{project['id']}/transcript-settings"


def _ready(api, project, work_payload, tmp_path, postgres_engine):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    return work, read(api, project, record)


def test_metadata_settings_and_health_share_project_scope(
    api, browser, project, work_payload, tmp_path,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    path = collection(project)
    for route, params in [
        (path, {"detail": "full", "sort_by": "name", "sort_direction": "asc", "limit": 1}),
        (path, {"work_item_id": work["id"], "query": "synthetic-session"}),
        (path + "/" + record["id"], {}), (_settings(project), {}),
    ]:
        expected = api.get(route, params=params)
        actual = browser.get(_browser_path(route), params=params)
        assert actual.status_code == expected.status_code == 200, actual.text
        assert actual.json() == expected.json()
    health = browser.get(_browser_path(path) + "/health")
    machine_health = api.get(path + "/health")
    assert health.status_code == machine_health.status_code == 200
    for key in ("project_id", "warnings", "warnings_omitted", "affected_transcripts",
                "recheck_seconds", "worker_checked_at"):
        assert health.json()[key] == machine_health.json()[key]
    other = api.post("/api/v1/projects", json={"name": "Other"}).json()
    assert browser.get(_browser_path(collection(other)) + "/" + record["id"]).status_code == 404
    assert browser.get(_browser_path(path) + "/" + record["id"] + "/text").status_code == 409


def test_indexed_text_segments_download_and_stale_pins_match_machine_reads(
    api, browser, project, work_payload, tmp_path, postgres_engine,
):
    work, ready = _ready(api, project, work_payload, tmp_path, postgres_engine)
    path = collection(project) + "/" + ready["id"]
    with api.app.state.session_factory() as database:
        segment = database.execute(select(SEGMENTS).order_by(SEGMENTS.c.ordinal)).mappings().first()
    assert segment is not None
    segment_params = {"segment_id": segment["segment_id"],
                      "expected_normalized_revision": ready["normalized_revision"],
                      "expected_sha256": ready["text_sha256"], "limit": 8}
    for params in [
        {"expected_sha256": ready["text_sha256"], "offset": 1, "limit": 12}, segment_params,
    ]:
        expected = api.get(path + "/text", params=params)
        actual = browser.get(_browser_path(path) + "/text", params=params)
        assert actual.status_code == expected.status_code == 200, actual.text
        assert actual.json() == expected.json()
    page = browser.get(_browser_path(collection(project)), params={
        "query": "needle", "fulltext": "true", "content_kinds": "human_text",
        "work_item_id": work["id"], "detail": "full",
    })
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["id"] == ready["id"]
    download = browser.get(_browser_path(path) + "/content",
                           params={"expected_sha256": ready["text_sha256"]})
    assert download.status_code == 200
    assert download.content == api.get(path + "/content").content
    assert hashlib.sha256(download.content).hexdigest() == ready["text_sha256"]
    assert int(download.headers["content-length"]) == len(download.content)
    assert download.headers["content-disposition"] == f'attachment; filename="{ready["id"]}.txt"'
    assert download.headers["content-type"] == "application/octet-stream"
    for suffix, params in [
        ("/text", {"expected_sha256": "0" * 64}),
        ("/content", {"expected_sha256": "0" * 64}),
        ("/text", segment_params | {"expected_normalized_revision": "0" * 64}),
    ]:
        expected = api.get(path + suffix, params=params)
        actual = browser.get(_browser_path(path) + suffix, params=params)
        assert actual.status_code == expected.status_code == 409
        assert actual.json() == expected.json()


def test_settings_keep_optimistic_revision_and_operator_limits(api, browser, project):
    path = _settings(project)
    settings = browser.get(_browser_path(path)).json()
    payload = {"enabled": False, "max_file_size_bytes": 1024,
               "expected_revision": settings["revision"]}
    saved = browser.patch(_browser_path(path), json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == settings["revision"] + 1
    assert saved.json() == api.get(path).json()
    stale = browser.patch(_browser_path(path), json=payload)
    assert stale.status_code == 409
    assert stale.json() == api.patch(path, json=payload).json()
    api.app.state.settings.transcript_max_bytes = 2048
    oversized = payload | {"max_file_size_bytes": 4096,
                           "expected_revision": saved.json()["revision"]}
    rejected = browser.patch(_browser_path(path), json=oversized)
    expected = api.patch(path, json=oversized)
    assert rejected.status_code == expected.status_code == 422
    assert rejected.json() == expected.json()


def test_import_receipt_replays_after_source_disappears(api, browser, project, tmp_path):
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    source = codex_source(tmp_path)
    payload = {"client_operation_id": str(uuid4()), "directory": str(tmp_path)}
    path = collection(project) + "/import"
    first = browser.post(_browser_path(path), json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["imported"] == 1
    source.unlink()
    assert browser.post(_browser_path(path), json=payload).json() == first.json()
    assert api.post(path, json=payload).json() == first.json()
    assert api.get(collection(project)).json()["total"] == 1
    changed_payload = payload | {"directory": str(tmp_path / "gone")}
    changed = browser.post(_browser_path(path), json=changed_payload)
    assert changed.status_code == 409
    assert changed.json() == api.post(path, json=changed_payload).json()


def test_rebuild_receipt_survives_uncertain_response_and_reuses_operation(
    api, browser, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    from mnemonic_api.application import browser_transcripts as transport

    _, ready = _ready(api, project, work_payload, tmp_path, postgres_engine)
    payload = {"client_operation_id": str(uuid4())}
    path = collection(project) + "/rebuild"
    original = transport._response_policy

    def too_small(action, route):
        policy = original(action, route)
        policy.json_maximum = 1
        return policy

    with monkeypatch.context() as patched:
        patched.setattr(transport, "_response_policy", too_small)
        lost = browser.post(_browser_path(path), json=payload)
    assert lost.status_code == 502
    assert isinstance(lost.json()["detail"], str)
    retried = browser.post(_browser_path(path), json=payload)
    assert retried.status_code == 200, retried.text
    assert retried.json()["queued"] == 1
    assert retried.json()["client_operation_id"] == payload["client_operation_id"]
    assert retried.json() == api.post(path, json=payload).json()
    assert run(api)
    assert not run(api)
    assert read(api, project, ready)["status"] == "ready"
