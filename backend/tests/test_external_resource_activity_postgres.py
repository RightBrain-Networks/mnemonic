"""External reservations through both cursor feeds and the project archive."""

import io
from uuid import UUID

import pytest

from mnemonic_backup.archive import BackupError, restore_project

from .test_activity_query_postgres import query
from .test_external_resources_postgres import DEFINITION, operation, reservation
from .test_project_backup_archive import _export, _rewrite
from .test_project_tags import HEADERS

pytestmark = pytest.mark.postgres


def _resource(api, project):
    path = f"/api/mnemonic/projects/{project['id']}/external-resources"
    created = api.post(path, json=operation(**DEFINITION), headers=HEADERS)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_both_activity_cursors_report_lease_changes_once_without_credentials(api, project):
    path = f"/api/v1/projects/{project['id']}"
    cursor = api.get(path + "/activity", params={"start": "now"}).json()["next_cursor"]
    checkpoint = query(api, project, start="now", view="events")["checkpoint"]
    resource = _resource(api, project)
    base = f"{path}/external-resources/{resource}"
    claim = reservation(4)
    held = api.post(base + "/claim", json=claim).json()
    lease_path = f"{base}/leases/{held['id']}"
    renewal = operation(lease_token=held["lease_token"], quantity=7)
    for _ in range(2):
        assert api.post(lease_path + "/renew", json=renewal).status_code == 200
    refused = operation(lease_token=held["lease_token"], quantity=17)
    assert api.post(lease_path + "/renew", json=refused).status_code == 409
    assert api.post(lease_path + "/release",
                    json=operation(lease_token=held["lease_token"])).status_code == 200
    expected = ["external_resource_created", "external_lease_claimed",
                "external_lease_renewed", "external_lease_released"]
    events = api.get(path + "/activity", params={"after": cursor}).json()["items"]
    assert [event["kind"] for event in events] == expected
    assert all(event["external_resource_id"] == resource for event in events)
    assert [event["external_lease_id"] for event in events] == [None] + [held["id"]] * 3
    page = query(api, project, after=checkpoint, view="events", limit=2)
    remaining = query(api, project, after=checkpoint, view="events", cursor=page["next_cursor"])
    changes = page["items"] + remaining["items"]
    assert [item["operations"] for item in changes] == [[action] for action in expected]
    assert all(item["resource_id"] == resource for item in changes)
    assert changes[2]["before_state"]["quantity"] == 4
    assert changes[2]["after_state"]["quantity"] == 7
    for private in (held["lease_token"], claim["claim_request_id"], "lease_token"):
        assert private not in str(changes) + str(events)
    grouped = query(api, project, facets=["external_leases"])["items"]
    assert len(grouped) == 1 and grouped[0]["change_count"] == 4
    assert grouped[0]["current"]["title"] == DEFINITION["name"]


def test_archive_restores_resources_and_rejects_overallocation(api, project, postgres_engine):
    resource = _resource(api, project)
    base = f"/api/v1/projects/{project['id']}/external-resources/{resource}"
    held = api.post(base + "/claim", json=reservation(8)).json()
    archive = _export(postgres_engine, project)
    assert api.post(f"{base}/leases/{held['id']}/release",
                    json=operation(lease_token=held["lease_token"])).status_code == 200

    def overallocate(_header, rows):
        rows["external_resources"][0]["capacity"] = 7

    with pytest.raises(BackupError, match="integrity"):
        damaged = io.BytesIO(_rewrite(archive, overallocate))
        restore_project(postgres_engine, UUID(project["id"]), damaged)
    assert api.get(base).json()["available"] == 16
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    assert api.get(base).json()["available"] == 8
