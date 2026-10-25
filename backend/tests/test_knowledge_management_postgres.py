"""Human file management preserves concurrent agent edits and deletion provenance."""

import io
import json
import tarfile
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from mnemonic_api.artifact_storage import ArtifactStorage
from mnemonic_api.impact_integrity import IMPACT_FINDINGS
from mnemonic_api.knowledge_deletion_integrity import DELETION_FINDINGS, historical_graph_checks
from mnemonic_api.knowledge_integrity import KNOWLEDGE_FINDINGS
from mnemonic_api.models import DecisionCommitMapping, DecisionScope
from mnemonic_backup.archive import restore_project

from .test_knowledge_postgres import domain, path, put, put_body
from .test_project_backup_archive import _export, _snapshot
from .test_work_items_postgres import create_work

pytestmark = pytest.mark.postgres
HEADERS = {"Host": "localhost:3000", "Origin": "http://localhost:3000"}


def browser_path(project):
    return path(project).replace("/api/v1/", "/api/mnemonic/")


def preview(api, project, suffix):
    response = api.get(browser_path(project) + suffix + "/deletion-preview", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def deletion_body(snapshot, *, acknowledge=True):
    key = "expected_revision" if snapshot["decision_id"] else "expected_version"
    return {key: snapshot[key], "expected_preview": snapshot["preview_token"],
            "acknowledge_relationships": acknowledge, "client_operation_id": str(uuid4())}


@pytest.fixture
def fractional_edge_timestamps():
    def assign_timestamp(_mapper, _connection, target):
        # PostgreSQL's JSON timestamp trims the final zero; Python isoformat does not.
        target.created_at = datetime.now(UTC).replace(microsecond=123450)

    models = (DecisionScope, DecisionCommitMapping)
    for model in models:
        event.listen(model, "before_insert", assign_timestamp)
    try:
        yield
    finally:
        for model in models:
            event.remove(model, "before_insert", assign_timestamp)


def test_human_upload_edit_replace_yields_to_agent_revision(api, project):
    target = domain(api, project)
    body = put_body(target, "Human upload", author_kind="human", agent_session_id=None,
                    model_id=None)
    route = browser_path(project) + "/decisions"
    uploaded = api.put(route, headers=HEADERS, json=body)
    assert uploaded.status_code == 200, uploaded.text
    document = uploaded.json()["decision"]
    fresh = {**body, "decision_id": document["decision_id"], "expected_revision": 0,
             "content": "Human replacement", "client_operation_id": str(uuid4())}
    replaced = api.put(route, headers=HEADERS, json=fresh)
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["decision"]["revision"] == 1
    identical = {**fresh, "expected_revision": 1, "client_operation_id": str(uuid4())}
    repeated = api.put(route, headers=HEADERS, json=identical)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["decision"]["revision"] == 2
    assert api.put(route, headers=HEADERS, json=identical).json() == repeated.json()
    agent = put(api, project, put_body(target, "Agent current", decision_id=document["decision_id"],
                                     expected_revision=2))
    stale = api.put(route, headers=HEADERS,
                    json={**fresh, "content": "Agent current", "client_operation_id": str(uuid4())})
    assert stale.status_code == 409, stale.text
    assert api.get(path(project) + "/decisions/" + document["decision_id"]).json()["reference"] == (
        agent["decision"]["reference"]
    )
    assert api.put(route, headers=HEADERS, json=put_body(target, "Impersonate")).status_code == 400


def test_export_and_download_include_exact_content_and_metadata(api, project):
    target = domain(api, project)
    content = "# café\r\n\nKeep whitespace.  \n"
    put(api, project, put_body(target, content))
    put(api, project, put_body(target, "Plain text", media_type="text/plain", title="Plain"))
    response = api.get(browser_path(project) + f"/domains/{target['id']}/export", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].endswith('"ADR-decisions.tar.gz"')
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        assert set(archive.getnames()) == {
            "domain.json", "ADR-00001.md", "ADR-00001.json", "ADR-00002.md", "ADR-00002.json",
        }
        assert archive.extractfile("ADR-00001.md").read() == content.encode()
        metadata = json.load(archive.extractfile("ADR-00002.json"))
        assert metadata["media_type"] == "text/plain"
        assert metadata["reference"] == "ADR-00002-0"
    downloaded = api.get(browser_path(project) + "/decisions/ADR-00001/download", headers=HEADERS)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == content.encode()
    put(api, project, put_body(target, "A newer revision", decision_id="ADR-00001",
                               expected_revision=0))
    pinned = api.get(browser_path(project) + "/decisions/ADR-00001/download",
                     headers=HEADERS, params={"revision": 0})
    assert pinned.content == content.encode()


def test_document_deletion_removes_edges_archives_proof_and_replays(
    api, project, postgres_engine, fractional_edge_timestamps,
):
    target = domain(api, project)
    first = put(api, project, put_body(target, "delete-me marker"))["decision"]
    second = put(api, project, put_body(target, "Dependent", related_decision_ids=[
        first["decision_id"],
    ]))["decision"]
    scope_intent = {
        "revision": 0, "commit_hash": "a" * 40, "repository": "owner/repo",
        "client_operation_id": str(uuid4()),
    }
    scope = api.post(path(project) + "/decisions/ADR-00001/scope", json=scope_intent)
    assert scope.status_code == 200, scope.text
    mapped = api.post(path(project) + "/commits/reconcile", json={
        "repository": scope.json()["scope"]["repository"], "original_commit_hash": "a" * 40,
        "merged_commit_hash": "b" * 40, "client_operation_id": str(uuid4()),
    })
    assert mapped.status_code == 200, mapped.text
    suffix = "/decisions/ADR-00001"
    snapshot = preview(api, project, suffix)
    assert snapshot["relationship_count"] == 3
    denied = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS,
                      json=deletion_body(snapshot, acknowledge=False))
    assert denied.status_code == 409, denied.text
    body = deletion_body(snapshot)
    removed = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS, json=body)
    assert removed.status_code == 200, removed.text
    assert api.post(browser_path(project) + suffix + "/delete", headers=HEADERS,
                    json=body).json() == removed.json()
    replayed_scope = api.post(path(project) + "/decisions/ADR-00001/scope", json=scope_intent)
    assert replayed_scope.status_code == 200, replayed_scope.text
    assert replayed_scope.json() == scope.json()
    fresh_scope = api.post(path(project) + "/decisions/ADR-00001/scope", json={
        **scope_intent, "client_operation_id": str(uuid4()),
    })
    assert fresh_scope.status_code == 410, fresh_scope.text
    deleted = api.get(path(project) + suffix)
    assert deleted.status_code == 410
    assert removed.json()["deleted_at"][:19] in deleted.json()["detail"]["message"]
    remaining = api.get(path(project) + "/decisions/" + second["decision_id"]).json()
    assert remaining["related_decision_ids"] == []
    assert api.get(path(project) + "/decisions").json()["total"] == 1
    lookup = api.get(path(project) + "/commits", params={
        "repository": "owner/repo", "commit_hash": "a" * 40,
    })
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["total"] == 0
    search = api.post(f"/api/v1/projects/{project['id']}/search", json={
        "q": "delete-me marker", "fulltext": True, "facets": ["decisions"],
    })
    assert search.status_code == 200, search.text
    assert search.json()["total"] == 0
    with postgres_engine.connect() as connection:
        for name, query in {**historical_graph_checks(KNOWLEDGE_FINDINGS),
                            **DELETION_FINDINGS}.items():
            assert connection.scalar(text(query)) == 0, name
    snapshot = _snapshot(postgres_engine, project)
    backup = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(backup))
    restored = _snapshot(postgres_engine, project)
    assert restored["knowledge_deleted_edges"] == snapshot["knowledge_deleted_edges"]
    assert restored["decision_documents"] == snapshot["decision_documents"]


def test_delete_preview_changes_when_edges_change_and_empty_domain_needs_no_checkbox(api, project):
    target = domain(api, project)
    suffix = f"/domains/{target['id']}"
    empty = preview(api, project, suffix)
    assert empty["document_count"] == empty["relationship_count"] == 0
    first = put(api, project, put_body(target))["decision"]
    stale = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS,
                     json=deletion_body(empty))
    assert stale.status_code == 409, stale.text
    current = preview(api, project, suffix)
    assert current["document_count"] == 1
    body = deletion_body(current)
    response = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    assert api.get(path(project) + "/decisions/" + first["decision_id"]).status_code == 410
    assert api.post(path(project) + suffix + "/delete", json=body).status_code == 404
    assert api.patch(path(project) + suffix, json={"name": "Revive", "expected_version": 2,
                     "client_operation_id": str(uuid4())}).status_code == 410
    orphan = domain(api, project, "NEW", "Empty")
    orphan_suffix = f"/domains/{orphan['id']}"
    response = api.post(browser_path(project) + orphan_suffix + "/delete", headers=HEADERS,
                        json=deletion_body(preview(api, project, orphan_suffix), acknowledge=False))
    assert response.status_code == 200, response.text


def test_product_decision_domain_deletion_is_explicitly_protected(api, project):
    domains = api.get(path(project) + "/domains").json()["items"]
    pdr = next(item for item in domains if item["is_product_decision_domain"])
    suffix = f"/domains/{pdr['id']}"
    snapshot = preview(api, project, suffix)
    assert snapshot["blocked_reason"]
    response = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS,
                        json=deletion_body(snapshot))
    assert response.status_code == 409, response.text


def test_tombstones_and_archived_edges_cannot_be_forged_or_mutated(api, project, postgres_engine):
    target = domain(api, project)
    first = put(api, project, put_body(target))["decision"]
    put(api, project, put_body(target, "Dependent", related_decision_ids=[first["decision_id"]]))
    with pytest.raises(DBAPIError):
        with postgres_engine.begin() as connection:
            connection.execute(text("UPDATE decision_documents SET deleted_at=clock_timestamp() "
                                    "WHERE id=:id"), {"id": first["id"]})
    with pytest.raises(DBAPIError):
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM decision_relationships"))
    suffix = "/decisions/ADR-00001"
    response = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS,
                        json=deletion_body(preview(api, project, suffix)))
    assert response.status_code == 200, response.text
    with pytest.raises(DBAPIError):
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM knowledge_deleted_edges"))
    with pytest.raises(DBAPIError):
        with postgres_engine.begin() as connection:
            connection.execute(text("UPDATE decision_documents SET deleted_at=NULL WHERE id=:id"),
                                {"id": first["id"]})


def test_domain_deletion_removes_resource_dependencies_and_supersession_edges(
    api, project, work_payload, tmp_path, postgres_engine,
):
    from .test_artifacts_postgres import upload

    api.app.state.artifact_storage = ArtifactStorage(tmp_path / "artifacts", max_bytes=1024)
    artifact = upload(api, project)
    work = create_work(api, project, work_payload)["work_item"]
    target = domain(api, project)
    other = domain(api, project, "OUT", "Outside")
    first = put(api, project, put_body(target, "Deleted source", related_work_item_ids=[work["id"]],
                                      related_artifact_ids=[artifact["id"]]))["decision"]
    put(api, project, put_body(target, "Superseding source", supersession_details=[{
        "decision_id": first["decision_id"], "revision": 0, "extent": "partial", "reason": "Update",
    }]))
    outside = put(api, project, put_body(other, "Outside dependent", depends_on=[{
        "decision_id": first["decision_id"], "revision": 0, "rationale": "Requires source",
    }]))["decision"]
    suffix = f"/domains/{target['id']}"
    snapshot = preview(api, project, suffix)
    assert snapshot["relationships"]["work_items"] == 1
    assert snapshot["relationships"]["artifacts"] == 1
    assert snapshot["relationships"]["dependencies"] == 1
    response = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS,
                        json=deletion_body(snapshot))
    assert response.status_code == 200, response.text
    surviving = api.get(path(project) + "/decisions/" + outside["decision_id"]).json()
    assert surviving.get("depends_on", []) == []
    with postgres_engine.connect() as connection:
        for name, query in {**historical_graph_checks(KNOWLEDGE_FINDINGS),
                            **historical_graph_checks(IMPACT_FINDINGS),
                            **DELETION_FINDINGS}.items():
            assert connection.scalar(text(query)) == 0, name


def test_retained_decision_review_can_be_assessed_and_completed_after_source_deletion(
    api, project, checkpoint_fields,
):
    from .test_decision_reviews_postgres import assess, context, finish, reviews

    target = domain(api, project)
    configured = api.patch(path(project) + f"/domains/{target['id']}", json={
        "automatic_decision_reviews": "on", "expected_version": target["version"],
        "client_operation_id": str(uuid4()),
    })
    assert configured.status_code == 200, configured.text
    first = put(api, project, put_body(target, "Original policy"))["decision"]
    put(api, project, put_body(target, "Changed policy", decision_id=first["decision_id"],
                               expected_revision=0))
    review, = reviews(api, project)
    work = context(api, project, review["work_item_id"])["work_item"]
    change_id = work["decision_review_change_id"]
    destination = api.post("/api/v1/projects", json={"name": "Moved impact reviews"}).json()
    moved = api.post(f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move", json={
        "target_project_id": destination["id"], "expected_version": work["version"],
        "actor": {"actor_client": "dashboard", "actor_session_id": "move-review"},
        "client_operation_id": str(uuid4()),
    })
    assert moved.status_code == 200, moved.text
    work = moved.json()["work_item"]
    suffix = "/decisions/" + first["decision_id"]
    snapshot = preview(api, project, suffix)
    assert snapshot["removed_relationship_count"] == 0
    assert snapshot["affected_review_count"] == snapshot["relationship_count"] == 1
    rejected = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS,
                        json=deletion_body(snapshot, acknowledge=False))
    assert rejected.status_code == 409, rejected.text
    deleted = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS,
                       json=deletion_body(snapshot))
    assert deleted.status_code == 200, deleted.text
    assert api.get(path(project) + suffix).status_code == 410
    change = api.get(path(project) + "/changes/" + change_id)
    assert change.status_code == 200, change.text
    episodes = api.get(path(project) + "/assessments", params={"change_id": change_id})
    assert episodes.status_code == 200, episodes.text
    for episode in episodes.json()["items"]:
        detail = api.get(path(project) + "/assessments/" + episode["id"])
        assert detail.status_code == 200, detail.text
        for candidate in detail.json()["candidates"]:
            assess(api, project, episode["id"], candidate["reference"], "not-affected")
    completed = finish(api, destination, work, checkpoint_fields)
    assert completed.status_code == 200, completed.text


def test_deletion_preview_counts_reviews_that_capture_a_dependency_candidate(api, project):
    from .test_decision_reviews_postgres import settings

    origin_domain = domain(api, project)
    other_domain = domain(api, project, "DEP", "Dependent decisions")
    settings(api, project, automatic_decision_reviews=True)
    origin = put(api, project, put_body(origin_domain, "Origin policy"))["decision"]
    dependent = put(api, project, put_body(other_domain, "Dependent policy", depends_on=[{
        "decision_id": origin["decision_id"], "revision": 0, "rationale": "Requires origin",
    }]))["decision"]
    put(api, project, put_body(origin_domain, "Origin changed",
                               decision_id=origin["decision_id"], expected_revision=0))
    snapshot = preview(api, project, "/decisions/" + dependent["decision_id"])
    assert snapshot["removed_relationship_count"] == 1
    assert snapshot["affected_review_count"] == 1
    assert snapshot["relationship_count"] == 2
