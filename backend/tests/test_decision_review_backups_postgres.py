"""Automatic review origins survive restore and cannot be changed through backups."""

import io
from uuid import UUID, uuid4

import pytest

from mnemonic_backup.archive import BackupError, restore_project

from .test_decision_impact_postgres import changed, decision
from .test_decision_reviews_postgres import context, reviews, settings
from .test_knowledge_postgres import domain
from .test_project_backup_archive import _export, _rewrite, _snapshot
from .test_work_item_moves_postgres import _move_payload
from .test_work_items_postgres import create_work

pytestmark = pytest.mark.postgres


def review(api, project):
    settings(api, project, automatic_decision_reviews=True, decision_review_priority=41)
    target = domain(api, project)
    before = decision(api, project, target, "Original backup policy")
    changed(api, project, target, before, "Updated backup policy")
    item, = reviews(api, project)
    return context(api, project, item["work_item_id"])


def test_roundtrip_retains_decision_review_origin_policy_and_pinned_prompt(
    api, project, postgres_engine,
):
    initial = review(api, project)
    backup = _export(postgres_engine, project)
    settings(api, project, automatic_decision_reviews=False, decision_review_priority=67)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(backup))
    restored = context(api, project, initial["work_item"]["id"])
    assert restored["work_item"] == initial["work_item"]
    assert restored["initial_checkpoint"] == initial["initial_checkpoint"]
    policy = api.get(f"/api/v1/projects/{project['id']}/settings").json()
    assert policy["automatic_decision_reviews"] is True
    assert policy["decision_review_priority"] == 41


def test_backup_roundtrip_retains_domain_review_override(api, project, postgres_engine):
    target = domain(api, project)
    path = f"/api/v1/projects/{project['id']}/knowledge/domains/{target['id']}"
    changed_domain = api.patch(path, json={
        "expected_version": target["version"], "automatic_decision_reviews": "off",
        "client_operation_id": str(uuid4()),
    })
    assert changed_domain.status_code == 200, changed_domain.text
    backup = _export(postgres_engine, project)
    changed_again = api.patch(path, json={
        "expected_version": changed_domain.json()["version"], "automatic_decision_reviews": "on",
        "client_operation_id": str(uuid4()),
    })
    assert changed_again.status_code == 200, changed_again.text
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(backup))
    domains = api.get(f"/api/v1/projects/{project['id']}/knowledge/domains").json()["items"]
    restored = next(item for item in domains if item["id"] == target["id"])
    assert restored == changed_domain.json()


def test_backup_rejects_invalid_domain_review_override(api, project, postgres_engine):
    target = domain(api, project)
    before = _snapshot(postgres_engine, project)
    backup = _export(postgres_engine, project)

    def corrupt(_header, rows):
        next(row for row in rows["knowledge_domains"] if row["id"] == target["id"])[
            "automatic_decision_reviews"
        ] = "unknown"

    with pytest.raises(BackupError):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(backup, corrupt)))
    assert _snapshot(postgres_engine, project) == before


@pytest.mark.parametrize("kind", ["create_knowledge_domain", "rename_knowledge_domain"])
@pytest.mark.parametrize("value", ["invalid", None, True, 0, {}])
def test_backup_rejects_invalid_domain_policy_in_receipt(
    api, project, postgres_engine, kind, value,
):
    target = domain(api, project)
    path = f"/api/v1/projects/{project['id']}/knowledge/domains/{target['id']}"
    updated = api.patch(path, json={
        "expected_version": target["version"], "automatic_decision_reviews": "off",
        "client_operation_id": str(uuid4()),
    })
    assert updated.status_code == 200, updated.text
    before = _snapshot(postgres_engine, project)
    backup = _export(postgres_engine, project)

    def corrupt(_header, rows):
        receipt = next(row for row in rows["client_operations"] if row["operation_kind"] == kind)
        receipt["response_body"]["automatic_decision_reviews"] = value

    with pytest.raises(BackupError, match="integrity"):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(backup, corrupt)))
    assert _snapshot(postgres_engine, project) == before


@pytest.mark.parametrize("damage", [
    "marker", "source-session", "source-project", "decision-reference", "missing-assessments",
    "foreign-assessment", "malformed-assessments", "prompt-hash", "priority",
])
def test_backup_cannot_forge_review_origin_or_invalid_policy(
    api, project, work_payload, postgres_engine, damage,
):
    initial = review(api, project)
    ordinary = create_work(api, project, work_payload)["work_item"]
    before = _snapshot(postgres_engine, project)
    backup = _export(postgres_engine, project)

    def corrupt(_header, rows):
        work = next(row for row in rows["work_items"] if row["id"] == initial["work_item"]["id"])
        checkpoint = next(
            row for row in rows["checkpoints"] if row["id"] == work["initial_checkpoint_id"]
        )
        metadata = checkpoint["source_metadata"]
        if damage == "marker":
            # Move the unique marker to a valid ordinary item while every FK still resolves.
            next(row for row in rows["work_items"] if row["id"] == ordinary["id"])[
                "decision_review_revision_id"
            ] = work["decision_review_revision_id"]
            work["decision_review_revision_id"] = None
        elif damage == "source-session":
            checkpoint["source_session_id"] = "forged-native-session"
        elif damage == "priority":
            rows["project_settings"][0]["decision_review_priority"] = 101
        else:
            changes = {
                "source-project": {"decision_project_id": str(uuid4())},
                "decision-reference": {"decision_reference": "ADR-00001-0"},
                "missing-assessments": {"assessment_ids": []},
                "foreign-assessment": {"assessment_ids": [str(uuid4())]},
                "malformed-assessments": {"assessment_ids": "invalid"},
                "prompt-hash": {"prompt_revision": "not-a-sha256"},
            }
            metadata.update(changes[damage])

    damaged = _rewrite(backup, corrupt)
    with pytest.raises(BackupError) as error:
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(damaged))
    assert error.value.code == "backup_integrity_conflict"
    assert _snapshot(postgres_engine, project) == before


def test_moved_decision_review_retains_source_scope_and_restores_in_destination(
    api, project, postgres_engine,
):
    initial = review(api, project)
    work = initial["work_item"]
    target = api.post("/api/v1/projects", json={"name": "Review move destination"}).json()
    moved = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move",
        json=_move_payload(target, work["version"]),
    )
    assert moved.status_code == 200, moved.text
    current = context(api, target, work["id"])
    marker = work["decision_review_revision_id"]
    assert current["work_item"]["decision_review_revision_id"] == marker
    assert current["initial_checkpoint"] == initial["initial_checkpoint"]
    assert f"Source project: `{project['id']}`" in current["initial_checkpoint"]["prompt"]
    before = _snapshot(postgres_engine, project)
    backup = _export(postgres_engine, target)
    restore_project(postgres_engine, UUID(target["id"]), io.BytesIO(backup))
    assert context(api, target, work["id"])["initial_checkpoint"] == initial["initial_checkpoint"]
    assert _snapshot(postgres_engine, project) == before
