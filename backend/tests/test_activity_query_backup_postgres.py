"""A restore preserves delta history and invalidates pre-restore positions."""

import io
from uuid import UUID

import pytest

from mnemonic_backup.archive import BackupError, restore_project

from .test_activity_query_postgres import endpoint, query
from .test_project_backup_archive import _export, _rewrite
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


@pytest.mark.parametrize("damage", ["head", "gap"])
def test_restore_refuses_incomplete_activity_history_atomically(
    api, project, work_payload, postgres_engine, damage,
):
    create(api, project, work_payload)
    before = query(api, project)

    def corrupt(_header, rows):
        if damage == "head":
            rows["project_change_heads"][0]["last_sequence"] += 1
        else:
            rows["project_changes"].pop(0)

    archive = _rewrite(_export(postgres_engine, project), corrupt)
    with pytest.raises(BackupError, match="integrity"):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    assert query(api, project) == before
