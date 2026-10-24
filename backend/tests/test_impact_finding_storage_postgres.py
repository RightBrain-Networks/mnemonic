"""Finding metadata survives archives and cannot rewrite existing evidence."""

import io
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from mnemonic_api.impact_integrity import IMPACT_FINDINGS
from mnemonic_backup.archive import BackupError, restore_project
from tests.test_impact_findings_postgres import append, review
from tests.test_knowledge_postgres import path
from tests.test_product_decision_migration_postgres import migrate
from tests.test_project_backup_archive import _export, _rewrite, _snapshot

pytestmark = pytest.mark.postgres


def _findings(engine):
    with engine.connect() as connection:
        return {name: count for name, sql in IMPACT_FINDINGS.items()
                if (count := connection.scalar(text(sql)))}


def test_nullable_migration_preserves_legacy_receipts_and_allows_empty_metadata_downgrade(
    api, project, work_payload, postgres_engine,
):
    _, episodes, remediation = review(api, project, work_payload, revisions=1)
    result, intent = append(api, project, episodes[0], remediation)
    assert "finding_id" not in result.json() and "remediation_target" not in result.json()
    before = _snapshot(postgres_engine, project)
    receipt = next(row for row in before["client_operations"]
                   if row["client_operation_id"] == intent["client_operation_id"])
    assert "finding_id" not in receipt["response_body"]
    assert _findings(postgres_engine) == {}
    migrate(postgres_engine, "0062_review_scope_notices", downgrade=True)
    migrate(postgres_engine, "head")
    assert _snapshot(postgres_engine, project) == before
    replay = api.post(path(project) + f"/assessments/{episodes[0]['id']}/dispositions", json=intent)
    assert replay.status_code == 201 and replay.json() == result.json()
    assert _findings(postgres_engine) == {}


@pytest.mark.parametrize("metadata", [
    {"finding_id": "root-cause"}, {"remediation_target": "decision-record"},
])
def test_downgrade_refuses_to_discard_either_kind_of_authored_metadata(
    api, project, work_payload, postgres_engine, metadata,
):
    _, episodes, remediation = review(api, project, work_payload, revisions=1)
    append(api, project, episodes[0], remediation, **metadata)
    before = _snapshot(postgres_engine, project)
    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        migrate(postgres_engine, "0062_review_scope_notices", downgrade=True)
    assert _snapshot(postgres_engine, project) == before


def test_finding_metadata_is_append_only_and_roundtrips_with_exact_receipts(
    api, project, work_payload, postgres_engine,
):
    _, episodes, remediation = review(api, project, work_payload, revisions=1)
    labelled, intent = append(api, project, episodes[0], remediation,
                              finding_id="transport-rule", remediation_target="both")
    for field, value in (("finding_id", "rewritten"), ("remediation_target", "code")):
        with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
            connection.execute(text(f"UPDATE decision_impact_dispositions SET {field}=:value"),
                               {"value": value})
    before = _snapshot(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]),
                    io.BytesIO(_export(postgres_engine, project)))
    after = _snapshot(postgres_engine, project)
    for name in ("decision_impact_dispositions", "client_operations",
                 "decision_impact_assessments"):
        assert after[name] == before[name]
    replay = api.post(path(project) + f"/assessments/{episodes[0]['id']}/dispositions", json=intent)
    assert replay.status_code == 201 and replay.json() == labelled.json()
    assert _findings(postgres_engine) == {}


@pytest.mark.parametrize("damage", [
    "row-finding", "receipt-finding", "row-target", "receipt-target", "receipt-missing",
    "receipt-invented-legacy-finding", "invalid-finding", "invalid-target",
    "target-on-resolved", "missing-legacy-actor",
])
def test_restore_rejects_forged_finding_metadata_and_preserves_original_project(
    api, project, work_payload, postgres_engine, damage,
):
    _, episodes, remediation = review(api, project, work_payload, revisions=1)
    labelled, _ = append(api, project, episodes[0], remediation,
                         finding_id="one-cause", remediation_target="decision-record")
    legacy, _ = append(api, project, episodes[0], remediation, disposition="resolved")
    before = _snapshot(postgres_engine, project)
    backup = _export(postgres_engine, project)

    def corrupt(_header, rows):
        records = rows["decision_impact_dispositions"]
        receipts = rows["client_operations"]
        row = next(item for item in records if item["id"] == labelled.json()["id"])
        receipt = next(item["response_body"] for item in receipts
                       if item["response_body"].get("id") == row["id"])
        legacy_receipt = next(item["response_body"] for item in receipts
                              if item["response_body"].get("id") == legacy.json()["id"])
        mutations = {
            "row-finding": lambda: row.update(finding_id="forged-cause"),
            "receipt-finding": lambda: receipt.update(finding_id="forged-cause"),
            "row-target": lambda: row.update(remediation_target="code"),
            "receipt-target": lambda: receipt.update(remediation_target="code"),
            "receipt-missing": lambda: receipt.pop("finding_id"),
            "receipt-invented-legacy-finding": lambda: legacy_receipt.update(finding_id="invented"),
            "invalid-finding": lambda: row.update(finding_id="invalid space"),
            "invalid-target": lambda: row.update(remediation_target="unknown"),
            "target-on-resolved": lambda: next(
                item for item in records if item["id"] == legacy.json()["id"]
            ).update(remediation_target="code"),
            "missing-legacy-actor": lambda: legacy_receipt.pop("actor"),
        }
        mutations[damage]()

    with pytest.raises(BackupError):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(backup, corrupt)))
    assert _snapshot(postgres_engine, project) == before
