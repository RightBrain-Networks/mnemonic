"""Native activity survives index refresh and exact work reads expose bounded links."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from mnemonic_api.models import Transcript
from mnemonic_api.services.transcripts import rebuild_transcripts
from mnemonic_api.transcript_indexing import index_next_transcript

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import read, register, run

pytestmark = pytest.mark.postgres


def test_native_timeline_models_and_sessions_survive_rebuild(
    api,
    project,
    work_payload,
    tmp_path,
    postgres_engine,
):
    work, _, row, source = register(api, project, work_payload, tmp_path)
    source.write_text(
        "\n".join(
            json.dumps(value)
            for value in [
                {
                    "type": "user",
                    "sessionId": "native-child",
                    "timestamp": "2026-02-01T09:00:00-05:00",
                    "message": {"role": "user", "content": "Start of session"},
                },
                {
                    "type": "assistant",
                    "sessionId": "native-child",
                    "timestamp": "2026-02-01T14:30:00Z",
                    "message": {"role": "assistant", "model": "fixture-model", "content": "Done"},
                },
            ]
        )
    )
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    first = read(api, project, row)
    assert first["last_updated_at"] == "2026-02-01T14:30:00Z"
    assert first["index_created_at"] == first["indexing_completed_at"]
    assert first["session_ids"] == ["native-child"]
    assert first["models"] == ["fixture-model"]
    assert first["metadata"]["transcript:session_started_at"] == ["2026-02-01T14:00:00Z"]
    assert first["metadata"]["transcript:last_updated_basis"] == ["session_event"]
    assert first["metadata"]["transcript:index_created_at"] == [first["index_created_at"]]
    factory = api.app.state.session_factory
    with factory() as database:
        rebuild_transcripts(database, UUID(project["id"]), uuid4())
        database.commit()
    assert index_next_transcript(factory, api.app.state.settings)
    rebuilt = read(api, project, row)
    assert rebuilt["last_updated_at"] == first["last_updated_at"]
    assert rebuilt["index_created_at"] > first["index_created_at"]
    assert rebuilt["normalized_revision"] == first["normalized_revision"]
    assert rebuilt["sha256"] == first["sha256"]
    for suffix in ("", "/context"):
        response = api.get(f"/api/v1/projects/{project['id']}/work-items/{work['id']}{suffix}")
        assert response.status_code == 200, response.text
        links = response.json()["transcripts"]
        assert links["total"] == 1 and links["omitted_count"] == 0
        assert links["items"][0]["id"] == row["id"]
        assert links["items"][0]["work_item_id"] == work["id"]
        assert links["items"][0]["session_ids"] == ["native-child"]
        assert "text" not in links["items"][0]
    status = api.get(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}", params={"status_only": True}
    )
    assert "transcripts" not in status.json()
    page = api.post(
        f"/api/v1/projects/{project['id']}/search",
        json={
            "q": "fixture-model",
            "facets": ["transcripts"],
            "detail": "compact",
            "filters": {"transcripts": {"updated_before": "2026-02-02T00:00:00Z"}},
        },
    )
    assert page.status_code == 200, page.text
    hit = page.json()["items"][0]
    assert hit["updated_at"] == first["last_updated_at"]
    assert hit["transcript"]["models"] == ["fixture-model"]


def test_native_activity_beyond_search_prefix_is_retained_on_rebuild(
    api,
    project,
    work_payload,
    tmp_path,
    postgres_engine,
):
    work, _, row, source = register(api, project, work_payload, tmp_path)
    source.write_text(
        "\n".join(
            json.dumps(value)
            for value in [
                {"role": "user", "timestamp": "2026-02-01T00:00:00Z", "content": "a" * 5000},
                {"role": "assistant", "timestamp": "2026-02-02T00:00:00Z", "content": "later"},
                {"role": "assistant", "timestamp": "not a time", "content": "invalid time"},
            ]
        )
    )
    api.app.state.settings.artifact_extraction_max_chars = 1000
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    first = read(api, project, row)
    assert not first["truncated"] and first["last_updated_at"] == "2026-02-02T00:00:00Z"
    with api.app.state.session_factory() as database:
        database.execute(
            update(Transcript)
            .where(Transcript.id == UUID(row["id"]))
            .values(reindex_status="pending", attempts=0)
        )
        database.commit()
    assert run(api)
    assert read(api, project, row)["last_updated_at"] == first["last_updated_at"]


def test_timestamp_free_source_uses_verified_source_mtime(
    api,
    project,
    work_payload,
    tmp_path,
    postgres_engine,
):
    work, _, row, source = register(api, project, work_payload, tmp_path)
    expected = datetime.fromtimestamp(source.stat().st_mtime, UTC)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    result = read(api, project, row)
    assert datetime.fromisoformat(result["last_updated_at"]) == expected
    assert result["metadata"]["transcript:last_updated_basis"] == ["source_mtime"]
    with api.app.state.session_factory() as database:
        persisted = database.scalar(select(Transcript).where(Transcript.id == UUID(row["id"])))
        assert persisted.source_modified_at == expected


def test_upgrade_backfills_session_times_without_touching_capture_or_jobs(
    api,
    project,
    work_payload,
    tmp_path,
    postgres_engine,
):
    from sqlalchemy import text

    from .test_artifact_extraction_migration_postgres import migrate

    work, _, row, source = register(api, project, work_payload, tmp_path)
    source.write_text(
        json.dumps(
            {
                "role": "user",
                "timestamp": "2026-02-01T00:00:00Z",
                "content": "retained historical session",
            }
        )
    )
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    before = read(api, project, row)
    migrate(postgres_engine, "0043_transcript_source_identity", downgrade=True)
    with postgres_engine.begin() as connection:
        # Reproduce an old record with no derived timeline properties.
        connection.execute(
            text("UPDATE transcripts SET extracted_metadata='{}'::jsonb WHERE id=:id"),
            {"id": row["id"]},
        )
        leases = connection.execute(text("SELECT * FROM work_leases ORDER BY work_item_id")).all()
    migrate(postgres_engine, "head")
    after = read(api, project, row)
    assert after["last_updated_at"] == "2026-02-01T00:00:00Z"
    assert after["metadata"]["transcript:last_updated_at"] == [after["last_updated_at"]]
    for key in (
        "source_path",
        "sha256",
        "text_sha256",
        "normalized_revision",
        "copied_at",
        "copy_status",
        "index_status",
        "index_created_at",
    ):
        assert after[key] == before[key]
    with postgres_engine.connect() as connection:
        restored = connection.execute(text("SELECT * FROM work_leases ORDER BY work_item_id")).all()
        assert restored == leases


def test_work_links_disclose_omissions_and_can_be_paged(
    api,
    project,
    work_payload,
    tmp_path,
):
    work, _, row, _ = register(api, project, work_payload, tmp_path)
    with api.app.state.session_factory() as database:
        for number in range(25):
            database.add(
                Transcript(
                    id=uuid4(),
                    work_item_id=UUID(work["id"]),
                    lease_generation_id=UUID(row["lease_generation_id"]),
                    client=row["client"],
                    session_id=row["session_id"],
                    source_path=f"/fixture/agent-{number}.jsonl",
                    kind="subagent",
                )
            )
        database.commit()
    detail = api.get(f"/api/v1/projects/{project['id']}/work-items/{work['id']}").json()
    links = detail["transcripts"]
    assert links["total"] == 26 and len(links["items"]) == 20 and links["omitted_count"] == 6
    response = api.post(
        f"/api/v1/projects/{project['id']}/transcripts/search-content",
        json={
            "work_item_id": work["id"],
            "limit": 20,
            "offset": 20,
        },
    )
    assert response.status_code == 200, response.text
    remaining = response.json()
    assert remaining["total"] == 26 and len(remaining["items"]) == 6
    assert {item["id"] for item in links["items"]}.isdisjoint(
        item["id"] for item in remaining["items"]
    )


def test_copy_metadata_survives_an_unsupported_native_format(
    api,
    project,
    work_payload,
    tmp_path,
    postgres_engine,
):
    work, _, row, source = register(api, project, work_payload, tmp_path)
    source.write_text('{"type":"journal","timestamp":"2026-01-01T00:00:00Z"}\n')
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    value = read(api, project, row)
    assert value["status"] == "failed" and value["copy_status"] == "ready"
    assert value["last_updated_at"] is not None
    assert value["metadata"]["transcript:last_updated_at"] == [value["last_updated_at"]]
    assert value["metadata"]["transcript:last_updated_basis"] == ["source_mtime"]
    assert value["index_created_at"] is None
