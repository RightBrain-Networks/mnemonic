"""A restore preserves delta history and invalidates pre-restore positions."""

import io
from uuid import UUID

import pytest

from mnemonic_backup.archive import restore_project

from .test_activity_query_postgres import endpoint, query
from .test_project_backup_archive import _export
from .test_project_reports_postgres import create

pytestmark = pytest.mark.postgres


def test_restore_preserves_changes_and_requires_new_checkpoint(api, project, work_payload,
                                                              postgres_engine):
    for index in range(2):
        create(api, project, {**work_payload, "title": f"Retained delta {index}"})
    scope = {"facets": ["work_items"]}
    before = query(api, project, **scope)
    page = query(api, project, **scope, limit=1)
    archive = _export(postgres_engine, project)
    create(api, project, {**work_payload, "title": "After archive"})
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    restored = query(api, project, **scope)
    assert restored["stream_id"] != before["stream_id"]
    assert restored["items"] == before["items"]
    assert restored["counts"] == before["counts"]
    assert restored["coverage"] == before["coverage"]
    for field, value in (("after", before["checkpoint"]), ("cursor", page["next_cursor"])):
        rejected = api.post(endpoint(project), json={**scope, field: value})
        assert rejected.status_code == 409, rejected.text
    create(api, project, {**work_payload, "title": "After restore"})
    fresh = query(api, project, **scope, after=restored["checkpoint"])
    assert fresh["counts"]["resources"] == 1
    assert fresh["items"][0]["title"] == "After restore"
