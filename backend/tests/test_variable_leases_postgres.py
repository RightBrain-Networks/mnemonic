"""Project lease policies govern fresh claims and renewals without changing prior receipts."""

import io
from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from mnemonic_backup.archive import restore_project

from .test_artifact_extraction_migration_postgres import migrate
from .test_leases_postgres import claim_payload, create_work, expire_lease, item_path
from .test_project_backup_archive import _export

pytestmark = pytest.mark.postgres
DEFAULTS = {"default_minutes": 15, "minimum_minutes": 10, "maximum_minutes": 120}


def settings_path(project):
    return f"/api/v1/projects/{project['id']}/settings"


def change_policy(api, project, **changes):
    path = settings_path(project)
    revision = api.get(path).json()["revision"]
    return api.patch(path, json={"expected_revision": revision, **changes})


def duration(receipt):
    return (datetime.fromisoformat(receipt["expires_at"]) -
            datetime.fromisoformat(receipt["renewed_at"])).total_seconds() / 60


def test_initial_policies_and_status_only_never_load_context(
    api, project, work_payload, monkeypatch,
):
    created = create_work(api, project, work_payload)
    endpoint = item_path(project, created["work_item"])
    detail = api.get(endpoint).json()
    assert detail["lease_settings"] == DEFAULTS
    assert detail["readiness"]["display_state"] == "pending"
    assert api.get(f"{endpoint}/context").json()["lease_settings"] == DEFAULTS
    settings = api.get(settings_path(project)).json()
    for name, value in DEFAULTS.items():
        assert settings[f"lease_{name}"] == value

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Status-only reads must not load implementation/review prose")

    monkeypatch.setattr("mnemonic_api.application.routes.work_items.work_item_detail", forbidden)
    monkeypatch.setattr("mnemonic_api.services.code_review_reads.review_context", forbidden)
    status = api.get(endpoint, params={"status_only": "true"})
    assert status.status_code == 200, status.text
    assert set(status.json()) == {
        "work_item_id", "project_id", "status", "version", "readiness", "lease_settings",
    }
    assert status.json()["readiness"] == detail["readiness"]
    assert status.json()["lease_settings"] == DEFAULTS
    claim = api.post(f"{endpoint}/claim", json=claim_payload("startup"))
    assert claim.status_code == 200, claim.text
    assert duration(claim.json()) == 15
    active = api.get(endpoint, params={"status_only": True}).json()
    assert active["readiness"]["display_state"] == "active"
    assert claim.json()["lease_token"] not in str(active)


@pytest.mark.parametrize("minutes", [10, 11, 45, 120])
@pytest.mark.parametrize("operation", ["claim", "claim-and-recall"])
def test_initial_duration_can_be_any_value_within_policy(
    api, project, work_payload, minutes, operation,
):
    work = create_work(api, project, work_payload)["work_item"]
    result = api.post(f"{item_path(project, work)}/{operation}",
                      json={**claim_payload("custom-start"), "lease_minutes": minutes})
    assert result.status_code == 200, result.text
    receipt = result.json()["lease"] if operation == "claim-and-recall" else result.json()
    assert duration(receipt) == minutes


@pytest.mark.parametrize("minutes", [9, 121])
@pytest.mark.parametrize("operation", ["claim", "claim-and-recall", "renew-claim"])
def test_current_project_bounds_reject_without_mutation(
    api, project, work_payload, minutes, operation,
):
    work = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work)
    payload = claim_payload("bounds")
    before = None
    if operation == "renew-claim":
        before = api.post(f"{endpoint}/claim", json=payload).json()
        payload = {"lease_token": before["lease_token"]}
    result = api.post(f"{endpoint}/{operation}", json={**payload, "lease_minutes": minutes})
    assert result.status_code == 422, result.text
    assert result.json()["detail"]["code"] == "lease_minutes_out_of_range"
    state = api.get(endpoint, params={"status_only": True}).json()
    assert state["readiness"]["has_active_lease"] is (before is not None)
    if before:
        assert state["readiness"]["active_lease"]["expires_at"] == before["expires_at"]


def test_custom_policy_is_project_owned_and_renewal_uses_current_policy(
    api, project, work_payload,
):
    changed = change_policy(api, project, lease_minimum_minutes=2,
                            lease_default_minutes=180, lease_maximum_minutes=240)
    assert changed.status_code == 200, changed.text
    other = api.post("/api/v1/projects", json={"name": "Independent policy"}).json()
    assert api.get(settings_path(other)).json()["lease_default_minutes"] == 15
    work = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work)
    payload = claim_payload("policy-default")
    receipt = api.post(f"{endpoint}/claim", json=payload).json()
    assert duration(receipt) == 180
    updated = change_policy(api, project, lease_default_minutes=5, lease_maximum_minutes=20)
    assert updated.status_code == 200, updated.text
    active = api.get(endpoint).json()["readiness"]["active_lease"]
    assert active["expires_at"] == receipt["expires_at"]
    assert api.post(f"{endpoint}/claim", json=payload).json() == receipt
    assert api.get(f"{endpoint}/context").json()["lease_settings"] == {
        "default_minutes": 5, "minimum_minutes": 2, "maximum_minutes": 20,
    }
    renewed = api.post(f"{endpoint}/renew-claim", json={"lease_token": receipt["lease_token"],
                                                        "lease_minutes": 2})
    assert renewed.status_code == 200, renewed.text
    assert duration(renewed.json()) == 2
    assert renewed.json()["acquired_at"] == receipt["acquired_at"]
    assert api.post(f"{endpoint}/claim", json=payload).json() == renewed.json()
    default_renew = api.post(f"{endpoint}/renew-claim",
                             json={"lease_token": receipt["lease_token"]})
    assert duration(default_renew.json()) == 5


@pytest.mark.parametrize("initial_minutes", [None, 15, 60])
def test_claim_replay_preserves_exact_duration_request_after_policy_change(
    api, project, work_payload, initial_minutes, postgres_engine,
):
    work = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work)
    payload = claim_payload("exact-retry")
    if initial_minutes is not None:
        payload["lease_minutes"] = initial_minutes
    receipt = api.post(f"{endpoint}/claim", json=payload).json()
    assert change_policy(api, project, lease_minimum_minutes=20,
                         lease_default_minutes=20, lease_maximum_minutes=20).status_code == 200
    assert api.post(f"{endpoint}/claim", json=payload).json() == receipt
    changed = {**payload, "lease_minutes": 20}
    rejected = api.post(f"{endpoint}/claim", json=changed)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "claim_request_mismatch"
    if initial_minutes is not None:
        omitted = api.post(f"{endpoint}/claim", json=claim_payload("exact-retry"))
        assert omitted.status_code == 409
    expire_lease(postgres_engine, work["id"])
    expired = api.post(f"{endpoint}/claim", json=payload)
    assert expired.status_code == 409
    assert expired.json()["detail"]["code"] == "claim_request_expired"
    fresh = api.post(f"{endpoint}/claim", json={**claim_payload("new-generation"),
                                                "lease_minutes": 20})
    assert fresh.status_code == 200, fresh.text
    assert duration(fresh.json()) == 20


def test_settings_partial_validation_revision_and_noop(api, project):
    original = api.get(settings_path(project)).json()
    for changes in ({"lease_minimum_minutes": 16}, {"lease_maximum_minutes": 14},
                    {"lease_default_minutes": 121}, {"lease_default_minutes": 9}):
        result = change_policy(api, project, **changes)
        assert result.status_code == 422, result.text
        assert result.json()["detail"]["code"] == "invalid_lease_settings"
        assert api.get(settings_path(project)).json() == original
    noop = change_policy(api, project, lease_default_minutes=15)
    assert noop.json() == original
    updated = change_policy(api, project, lease_minimum_minutes=30,
                            lease_default_minutes=30, lease_maximum_minutes=30)
    assert updated.status_code == 200, updated.text
    assert int(updated.json()["revision"]) == int(original["revision"]) + 1
    stale = api.patch(settings_path(project), json={"expected_revision": original["revision"],
                                                   "lease_default_minutes": 30})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "project_settings_changed"


@pytest.mark.parametrize("column,value", [("lease_minimum_minutes", 0),
                                           ("lease_default_minutes", 121),
                                           ("lease_maximum_minutes", 14)])
def test_database_settings_constraints(api, project, postgres_engine, column, value):
    with pytest.raises(IntegrityError) as error, postgres_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE project_settings SET {column}=:value, revision=revision+1 "
                 "WHERE project_id=:id"),
            {"value": value, "id": project["id"]},
        )
    assert error.value.orig.diag.constraint_name == "ck_project_settings_lease_minutes_order"


def test_migration_backfills_projects_and_preserves_active_lease_replay(
    api, project, work_payload, postgres_engine,
):
    work = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work)
    payload = claim_payload("historical-omitted")
    before = api.post(f"{endpoint}/claim", json=payload).json()
    migrate(postgres_engine, "0033_transcript_imports", downgrade=True)
    migrate(postgres_engine, "head")
    assert api.post(f"{endpoint}/claim", json=payload).json() == before
    assert api.get(endpoint).json()["lease_settings"] == DEFAULTS
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT claim_lease_minutes FROM work_leases")) is None
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == \
            "0035_prompt_library"


def test_backup_restores_policy_and_exact_claim_duration(
    api, project, work_payload, postgres_engine,
):
    assert change_policy(api, project, lease_default_minutes=180,
                         lease_maximum_minutes=240).status_code == 200
    work = create_work(api, project, work_payload)["work_item"]
    endpoint = item_path(project, work)
    payload = {**claim_payload("backup-duration"), "lease_minutes": 200}
    before = api.post(f"{endpoint}/claim", json=payload).json()
    content = _export(postgres_engine, project)
    assert change_policy(api, project, lease_default_minutes=15,
                         lease_maximum_minutes=120).status_code == 200
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content))
    assert api.get(settings_path(project)).json()["lease_default_minutes"] == 180
    assert api.post(f"{endpoint}/claim", json=payload).json() == before
    mismatch = api.post(f"{endpoint}/claim", json={**payload, "lease_minutes": 180})
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "claim_request_mismatch"


@pytest.mark.parametrize("custom", ["policy", "claim"])
def test_downgrade_cannot_discard_custom_policy_or_claim_identity(
    api, project, work_payload, postgres_engine, custom,
):
    if custom == "policy":
        assert change_policy(api, project, lease_default_minutes=20).status_code == 200
    else:
        work = create_work(api, project, work_payload)["work_item"]
        result = api.post(f"{item_path(project, work)}/claim", json={
            **claim_payload("explicit-duration"), "lease_minutes": 15,
        })
        assert result.status_code == 200, result.text
    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        migrate(postgres_engine, "0033_transcript_imports", downgrade=True)
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == \
            "0035_prompt_library"


def test_cold_review_status_read_and_custom_lease(api, project, work_payload, checkpoint_fields,
                                                monkeypatch):
    from .code_review_fixtures import mandatory

    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    review = completion["code_review_request"]
    endpoint = item_path(project, completion["work_item"])

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Cold status reads must not reveal the review handoff")

    monkeypatch.setattr("mnemonic_api.services.code_review_reads.review_context", forbidden)
    status = api.get(endpoint, params={"status_only": True})
    assert status.status_code == 200, status.text
    assert status.json()["readiness"]["display_state"] == "to-review"
    assert status.json()["lease_settings"] == DEFAULTS
    payload = {**claim_payload("cold-custom"), "lease_minutes": 45, "purpose": "code_review",
               "code_review_id": review["id"], "mode": "cold"}
    result = api.post(f"{endpoint}/claim", json=payload)
    assert result.status_code == 200, result.text
    assert duration(result.json()) == 45
    renewed = api.post(f"{endpoint}/renew-claim", json={
        "lease_token": result.json()["lease_token"], "lease_minutes": 10,
    })
    assert renewed.status_code == 200, renewed.text
    assert duration(renewed.json()) == 10
