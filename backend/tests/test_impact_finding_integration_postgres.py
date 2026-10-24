"""Finding metadata survives batching, projections and permanent receipt boundaries."""

import io
from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from mnemonic_backup.archive import BackupError, restore_project

from .test_assessment_batch_postgres import audit_dispositions, batch_body, endpoint
from .test_assessment_progress_postgres import review_claim
from .test_decision_changes_postgres import group
from .test_impact_findings_postgres import detail, review, summary
from .test_knowledge_postgres import path
from .test_product_decision_migration_postgres import migrate
from .test_progress_lease_postgres import renewals, retained
from .test_project_backup_archive import _export, _rewrite, _snapshot

pytestmark = pytest.mark.postgres


def labelled_batch(api, project, work_payload):
    change_id, episodes, remediation = review(
        api, project, work_payload, citations=1, revisions=1,
    )
    episode, = episodes
    body = batch_body(episode["candidates"])
    for item, target in zip(body["items"], ("code", "decision-record"), strict=True):
        item.update(disposition="change-required", finding_id="shared-cause",
                    remediation_target=target, remediation_work_item_id=remediation["id"])
    return change_id, episode, body


def save_batch(api, project, episode, body):
    url = endpoint(project, episode) + "/dispositions/batch"
    saved = api.post(url, json=body)
    assert saved.status_code == 201, saved.text
    return url, saved.json()


def test_batch_finding_identity_preserves_targets_versions_replay_and_read_projections(
    api, project, work_payload, postgres_engine,
):
    change_id, episode, body = labelled_batch(api, project, work_payload)
    url, saved = save_batch(api, project, episode, body)
    assert [row["version"] for row in saved["items"]] == [1, 2]
    assert saved["version"] == 2
    assert [row["remediation_target"] for row in saved["items"]] == ["code", "decision-record"]
    assert {row["finding_id"] for row in saved["items"]} == {"shared-cause"}
    assert api.post(url, json=body).json() == saved
    altered = deepcopy(body)
    altered["items"][1]["remediation_target"] = "both"
    assert api.post(url, json=altered).status_code == 409
    assert detail(api, project, episode["id"])["finding_summary"] == summary(1, 0, 2)
    assert group(api, project, change_id)["finding_summary"] == summary(1, 0, 2)

    compact = api.get(endpoint(project, episode), params={
        "detail": "compact", "include_diff": False, "include_scopes": "none",
    }).json()
    assert compact["finding_summary"] == summary(1, 0, 2)
    expected = {row["candidate_reference"]: (row["finding_id"], row["remediation_target"])
                for row in saved["items"]}
    assert {row["candidate_reference"]: (row["finding_id"], row["remediation_target"])
            for row in compact["dispositions"]} == expected
    assert all("reason" not in row and "actor" not in row for row in compact["dispositions"])
    grouped = api.get(path(project) + f"/changes/{change_id}/candidates").json()
    occurrences = [row for item in grouped["items"] for row in item["occurrences"]]
    assert grouped["occurrence_count"] == grouped["change_required_count"] == 2
    assert {row["candidate_reference"]: (row["latest_disposition"]["finding_id"],
                                         row["latest_disposition"]["remediation_target"])
            for row in occurrences} == expected
    assert all("reason" not in row["latest_disposition"] for row in occurrences)
    audit_dispositions(postgres_engine)
    restore_project(postgres_engine, UUID(project["id"]),
                    io.BytesIO(_export(postgres_engine, project)))
    assert api.post(url, json=body).json() == saved
    assert detail(api, project, episode["id"])["finding_summary"] == summary(1, 0, 2)
    audit_dispositions(postgres_engine)


@pytest.mark.parametrize("invalid", ["candidate", "target", "finding"])
def test_later_invalid_finding_batch_item_rolls_back_rows_receipt_and_summary(
    api, project, work_payload, postgres_engine, invalid,
):
    change_id, episode, body = labelled_batch(api, project, work_payload)
    field, value = {"candidate": ("candidate_reference", "ADR-99999-0"),
                    "target": ("disposition", "resolved"),
                    "finding": ("finding_id", "invalid label")}[invalid]
    body["items"][1][field] = value
    before = _snapshot(postgres_engine, project)
    response = api.post(endpoint(project, episode) + "/dispositions/batch", json=body)
    assert response.status_code == 422, response.text
    assert _snapshot(postgres_engine, project) == before
    assert group(api, project, change_id)["finding_summary"] == summary(0, 0, 0)
    assert detail(api, project, episode["id"])["version"] == 0


def test_batch_legacy_omitted_finding_fields_survive_migration_restore_and_exact_replay(
    api, project, work_payload, postgres_engine,
):
    _, episode, body = labelled_batch(api, project, work_payload)
    for item in body["items"]:
        item.pop("finding_id")
        item.pop("remediation_target")
    url, saved = save_batch(api, project, episode, body)
    assert all("finding_id" not in row and "remediation_target" not in row
               for row in saved["items"])
    original = _snapshot(postgres_engine, project)
    migrate(postgres_engine, "0062_review_scope_notices", downgrade=True)
    migrate(postgres_engine, "head")
    assert _snapshot(postgres_engine, project) == original
    restore_project(postgres_engine, UUID(project["id"]),
                    io.BytesIO(_export(postgres_engine, project)))
    restored = _snapshot(postgres_engine, project)
    for name in ("client_operations", "decision_impact_dispositions"):
        assert restored[name] == original[name]
    assert api.post(url, json=body).json() == saved
    assert detail(api, project, episode["id"])["finding_summary"] == summary(0, 2, 2)
    audit_dispositions(postgres_engine)


@pytest.mark.parametrize("damage", [
    "row-finding", "receipt-finding", "row-target", "receipt-target",
    "receipt-missing-finding", "receipt-missing-target", "legacy-invented-finding",
    "legacy-invented-target", "legacy-missing-actor",
])
def test_forged_batch_finding_receipts_fail_restore_atomically(
    api, project, work_payload, postgres_engine, damage,
):
    _, episode, body = labelled_batch(api, project, work_payload)
    _, saved = save_batch(api, project, episode, body)
    legacy = deepcopy(body)
    legacy.update(expected_version=2, client_operation_id=str(uuid4()))
    for item in legacy["items"]:
        item.pop("finding_id")
        item.pop("remediation_target")
    _, legacy_saved = save_batch(api, project, episode, legacy)
    before = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)

    def corrupt(_header, rows):
        identifier = (legacy_saved if damage.startswith("legacy-") else saved)["items"][1]["id"]
        row = next(item for item in rows["decision_impact_dispositions"]
                   if item["id"] == identifier)
        receipt = next(item for operation in rows["client_operations"]
                       if operation["operation_kind"] == "assess_decision_impact_batch"
                       for item in operation["response_body"]["items"] if item["id"] == identifier)
        mutations = {
            "row-finding": lambda: row.update(finding_id="forged"),
            "receipt-finding": lambda: receipt.update(finding_id="forged"),
            "row-target": lambda: row.update(remediation_target="both"),
            "receipt-target": lambda: receipt.update(remediation_target="both"),
            "receipt-missing-finding": lambda: receipt.pop("finding_id"),
            "receipt-missing-target": lambda: receipt.pop("remediation_target"),
            "legacy-invented-finding": lambda: receipt.update(finding_id="invented"),
            "legacy-invented-target": lambda: receipt.update(remediation_target="code"),
            "legacy-missing-actor": lambda: receipt.pop("actor"),
        }
        mutations[damage]()

    with pytest.raises(BackupError):
        restore_project(postgres_engine, UUID(project["id"]),
                        io.BytesIO(_rewrite(archive, corrupt)))
    assert _snapshot(postgres_engine, project) == before


def test_batch_finding_id_cannot_echo_private_lease_or_renew_it(
    api, project, postgres_engine,
):
    episode, candidates, _, work, claim = review_claim(api, project)
    body = batch_body(candidates, lease_token=claim["lease_token"])
    body["items"][0]["finding_id"] = "finding-" + claim["lease_token"]
    before = retained(postgres_engine, work["id"])
    response = api.post(endpoint(project, episode) + "/dispositions/batch", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "client_operation_secret_echo"
    assert claim["lease_token"] not in response.text
    assert retained(postgres_engine, work["id"]) == before
    assert renewals(postgres_engine, work["id"]) == 0
    assert detail(api, project, episode["id"])["version"] == 0
    assert not _snapshot(postgres_engine, project)["decision_impact_dispositions"]
