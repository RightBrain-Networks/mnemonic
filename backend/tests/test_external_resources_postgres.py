"""Allocation, lease generations, exact retries, and human-only controls."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mnemonic_api.services.external_resources import expire_due_leases

from .test_project_tags import HEADERS

pytestmark = pytest.mark.postgres
DEFINITION = {
    "name": "Concurrent pytest workers",
    "capacity": 16,
    "minimum_minutes": 1,
    "default_minutes": 4,
    "maximum_minutes": 60,
}


def operation(**values):
    return {"client_operation_id": str(uuid4()), **values}


def reservation(quantity=8, **values):
    return {
        "holder_client": "codex",
        "holder_session_id": "session-a",
        "claim_request_id": str(uuid4()),
        "quantity": quantity,
        **values,
    }


@pytest.fixture
def paths(api, project):
    path = f"/projects/{project['id']}/external-resources"
    body = operation(**DEFINITION)
    created = api.post("/api/mnemonic" + path, headers=HEADERS, json=body)
    assert created.status_code == 201, created.text
    assert api.post("/api/mnemonic" + path, headers=HEADERS, json=body).json() == created.json()
    path += "/" + created.json()["id"]
    return "/api/v1" + path, "/api/mnemonic" + path


def test_shared_capacity_retries_release_and_revoke(api, paths):
    agent, human = paths
    claim = reservation()
    held = api.post(agent + "/claim", json=claim)
    assert held.status_code == 200, held.text
    lease = held.json()
    assert api.post(agent + "/claim", json=claim).json() == lease
    assert api.post(agent + "/claim", json={**claim, "quantity": 1}).status_code == 409
    assert api.post(agent + "/claim", json=reservation(1)).status_code == 409
    assert (
        api.post(agent + "/claim", json=reservation(9, holder_session_id="other")).status_code
        == 409
    )
    assert (
        api.post(
            agent + "/claim", json=reservation(1, holder_session_id="other", lease_minutes=61)
        ).status_code
        == 422
    )
    other = api.post(agent + "/claim", json=reservation(8, holder_session_id="other")).json()
    status = api.get(human, headers=HEADERS)
    assert status.json()["available"] == 0
    assert len(status.json()["leases"]) == 2
    for private in (
        "lease_token",
        "claim_request_id",
        lease["lease_token"],
        claim["claim_request_id"],
    ):
        assert private not in status.text
    lease_path = f"/leases/{lease['id']}"
    wrong = operation(lease_token=other["lease_token"])
    for action in ("renew", "release"):
        assert api.post(agent + lease_path + "/" + action, json=wrong).status_code == 409
    renew_path = agent + lease_path + "/renew"
    renew = operation(lease_token=lease["lease_token"], quantity=4, lease_minutes=10)
    changed = api.post(renew_path, json=renew)
    assert changed.status_code == 200, changed.text
    assert changed.json()["quantity"] == 4
    assert api.post(renew_path, json=renew).json() == changed.json()
    assert api.post(renew_path, json={**renew, "quantity": 6}).status_code == 409
    assert (
        api.post(
            renew_path, json=operation(lease_token=lease["lease_token"], quantity=9)
        ).status_code
        == 409
    )
    assert api.get(agent).json()["available"] == 4
    current = api.post(agent + "/claim", json=claim).json()
    assert current["claim_quantity"] == 8 and current["quantity"] == 4
    assert current["lease_token"] == lease["lease_token"]
    invalid = operation(**{**DEFINITION, "capacity": 11})
    assert api.put(human, headers=HEADERS, json=invalid).status_code == 409
    assert api.put(agent, json=invalid).status_code == 405
    grown = api.put(human, headers=HEADERS, json=operation(**{**DEFINITION, "capacity": 20}))
    assert grown.status_code == 200, grown.text
    assert (
        api.post(renew_path, json=operation(lease_token=lease["lease_token"], quantity=9)).json()[
            "quantity"
        ]
        == 9
    )
    revoke = operation()
    assert (
        api.post(
            human + lease_path + "/revoke", headers=HEADERS, json={**revoke, "quantity": 1}
        ).status_code
        >= 400
    )
    revoked = api.post(human + lease_path + "/revoke", headers=HEADERS, json=revoke)
    assert revoked.status_code == 200, revoked.text
    assert api.post(agent + "/claim", json=claim).status_code == 409
    replacement = api.post(agent + "/claim", json=reservation()).json()
    assert replacement["id"] != lease["id"]
    assert api.post(human + lease_path + "/revoke", headers=HEADERS, json=revoke).json() == (
        revoked.json()
    )
    assert api.get(agent).json()["available"] == 4
    release = operation(lease_token=other["lease_token"])
    uri = agent + f"/leases/{other['id']}/release"
    assert api.post(uri, json=release).json()["released"]
    assert api.post(uri, json=release).json()["released"]
    assert api.get(agent).json()["available"] == 12


def test_competing_claims_never_overbook_and_expiry_is_observed_once(api, paths, postgres_engine):
    agent, _ = paths
    requests = [reservation(16, holder_session_id=str(i)) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda body: api.post(agent + "/claim", json=body), requests))
    assert sorted(r.status_code for r in replies) == [200, 409]
    lease = next(r.json() for r in replies if r.status_code == 200)
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE external_resource_leases SET "
                "acquired_at=now()-interval '20 minutes', renewed_at=now()-interval '20 minutes', "
                "expires_at=now()-interval '1 minute' WHERE id=:id"
            ),
            {"id": lease["id"]},
        )
    assert api.get(agent).json()["available"] == 16
    with Session(postgres_engine) as database:
        expire_due_leases(database)
        expire_due_leases(database)
        assert (
            database.scalar(
                text(
                    "SELECT count(*) FROM project_changes WHERE operation='external_lease_expired'"
                )
            )
            == 1
        )
    uri = agent + f"/leases/{lease['id']}/renew"
    assert api.post(uri, json=operation(lease_token=lease["lease_token"])).status_code == 409
    assert api.post(agent + "/claim", json=reservation(16)).status_code == 200


def test_uint32_duration_bounds_and_project_scoping(api, paths):
    agent, human = paths
    for field, value in (
        ("capacity", 0),
        ("capacity", 4294967296),
        ("capacity", True),
        ("default_minutes", 61),
        ("minimum_minutes", 0),
    ):
        response = api.put(human, headers=HEADERS, json=operation(**{**DEFINITION, field: value}))
        assert response.status_code in (400, 422), response.text
    maximum = 4294967295
    assert (
        api.put(
            human, headers=HEADERS, json=operation(**{**DEFINITION, "capacity": maximum})
        ).status_code
        == 200
    )
    held = api.post(agent + "/claim", json=reservation(maximum))
    assert held.status_code == 200, held.text
    assert api.get(agent).json()["available"] == 0
    other = api.post("/api/v1/projects", json={"name": "Unrelated"}).json()
    base = f"/api/v1/projects/{other['id']}/external-resources"
    assert api.get(base).json()["items"] == []
    assert api.get(base + "/" + held.json()["resource_id"]).status_code == 404


def test_independent_resources_and_current_renewal_policy(api, paths):
    agent, human = paths
    first = api.post(agent + "/claim", json=reservation(2, lease_minutes=2)).json()
    collection = human.rsplit("/", 1)[0]
    created = api.post(
        collection, headers=HEADERS, json=operation(**{**DEFINITION, "name": "GPU", "capacity": 1})
    )
    assert created.status_code == 201, created.text
    second_resource = agent.rsplit("/", 1)[0] + "/" + created.json()["id"]
    assert api.post(second_resource + "/claim", json=reservation(1)).status_code == 200
    assert api.get(agent).json()["available"] == 14
    revised = {**DEFINITION, "minimum_minutes": 10, "default_minutes": 20}
    response = api.put(human, headers=HEADERS, json=operation(**revised))
    assert (
        response.status_code == 200
        and response.json()["leases"][0]["expires_at"] == (first["expires_at"])
    )
    renew = agent + "/leases/" + first["id"] + "/renew"
    assert (
        api.post(
            renew, json=operation(lease_token=first["lease_token"], quantity=4, lease_minutes=2)
        ).status_code
        == 422
    )
    renewed = api.post(renew, json=operation(lease_token=first["lease_token"]))
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["id"] == first["id"] and renewed.json()["quantity"] == 2
    assert renewed.json()["expires_at"] > first["expires_at"]


def test_empty_downgrade_restores_exact_previous_catalog(pristine_postgres_engine):
    import runpy

    from alembic import command
    from alembic.config import Config

    from .conftest import BACKEND_DIR

    audit = runpy.run_path(str(BACKEND_DIR.parent / "scripts/audit_project_activity.py"))
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with pristine_postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0073_project_changes")
        assert not any(audit["_catalog_drift"](connection, "0073_project_changes").values())
        command.upgrade(config, "head")
        assert not any(audit["_catalog_drift"](connection, "0074_external_resources").values())


def test_replayed_renewal_preserves_historical_result_after_lease_ends(api, paths):
    agent, human = paths
    original = reservation(8)
    held = api.post(agent + "/claim", json=original).json()
    uri = "/leases/" + held["id"]
    renewal = operation(lease_token=held["lease_token"], quantity=2)
    first = api.post(agent + uri + "/renew", json=renewal)
    assert first.status_code == 200, first.text
    public = first.json()
    assert api.post(human + uri + "/revoke", json=operation(), headers=HEADERS).status_code == 200
    replacement = api.post(agent + "/claim", json=reservation(5)).json()
    assert api.post(agent + "/claim", json=original).status_code == 409
    replay = api.post(agent + uri + "/renew", json=renewal)
    assert replay.status_code == 200 and replay.json() == public
    assert (
        api.post(
            agent + uri + "/renew", json={**renewal, "lease_token": replacement["lease_token"]}
        ).status_code
        == 409
    )
    assert (
        api.post(
            agent + uri + "/renew", json=operation(lease_token=held["lease_token"])
        ).status_code
        == 409
    )
    current = api.get(agent).json()
    assert current["available"] == 11 and [lease["id"] for lease in current["leases"]] == (
        [replacement["id"]]
    )
    for private in (held["lease_token"], original["claim_request_id"], replacement["lease_token"]):
        assert private not in str(current) + str(public)
