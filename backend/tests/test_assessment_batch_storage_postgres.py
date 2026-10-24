"""Batch receipts retain every immutable row through backup, restore and downgrade guards."""

import io
from copy import deepcopy
from uuid import UUID

import pytest

from mnemonic_backup.archive import BackupError, restore_project

from .test_assessment_batch_postgres import audit_dispositions, batch_body, endpoint, episode
from .test_product_decision_migration_postgres import migrate
from .test_project_backup_archive import _export, _rewrite, _snapshot

pytestmark = pytest.mark.postgres


def saved_batch(api, project):
    assessment, candidates = episode(api, project)
    body = batch_body(candidates)
    url = endpoint(project, assessment) + "/dispositions/batch"
    response = api.post(url, json=body)
    assert response.status_code == 201, response.text
    return url, body, response.json()


def test_batch_backup_roundtrip_retains_exact_replay_and_blocks_lossy_downgrade(
    api, project, postgres_engine,
):
    url, body, response = saved_batch(api, project)
    archive = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    audit_dispositions(postgres_engine)
    assert api.post(url, json=body).json() == response
    with pytest.raises(RuntimeError, match="Batch disposition receipts"):
        migrate(postgres_engine, "0060_supersession_reuse", downgrade=True)


@pytest.mark.parametrize("forgery", ["missing-row", "missing-item", "extra-item", "duplicate-item",
                                     "order", "version", "actor", "reason", "assessment"])
def test_restore_rejects_incomplete_or_forged_batch_atomically(
    api, project, postgres_engine, forgery,
):
    saved_batch(api, project)
    archive = _export(postgres_engine, project)
    before = _snapshot(postgres_engine, project)

    def corrupt(_header, rows):
        receipt, = [item for item in rows["client_operations"]
                    if item["operation_kind"] == "assess_decision_impact_batch"]
        response = receipt["response_body"]
        if forgery == "missing-row":
            rows["decision_impact_dispositions"].pop()
        elif forgery == "missing-item":
            response["items"].pop()
        elif forgery == "extra-item":
            response["items"].append(deepcopy(response["items"][0]))
        elif forgery == "duplicate-item":
            response["items"][1] = deepcopy(response["items"][0])
        elif forgery == "order":
            response["items"].reverse()
        elif forgery == "version":
            response["version"] += 1
        elif forgery == "assessment":
            response["assessment_id"] = project["id"]
        elif forgery == "actor":
            response["items"][1]["actor"]["actor_session_id"] = "Invented actor"
        else:
            response["items"][1]["reason"] = "Invented reason"

    with pytest.raises(BackupError):
        restore_project(
            postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(archive, corrupt)),
        )
    assert _snapshot(postgres_engine, project) == before
