"""External reference persistence, ledger, discovery and replay against PostgreSQL."""

import json
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .conftest import BACKEND_DIR
from .test_external_references import FIXTURE, REFERENCE
from .test_work_items_postgres import collection, create_work, item_path

pytestmark = pytest.mark.postgres
ACTOR = {"actor_client": "pytest", "actor_session_id": "external-references"}


@pytest.mark.parametrize("case", FIXTURE["url_cases"])
def test_sql_url_shared_contract(postgres_engine, case):
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT mnemonic_external_url_is_valid(:url)"), {"url": case["value"]}
            )
            is case["valid"]
        )


@pytest.mark.parametrize("case", FIXTURE["label_cases"])
def test_sql_label_shared_contract(postgres_engine, case):
    if "\x00" in case["value"]:
        return  # PostgreSQL JSONB itself rejects NUL before the validator.
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT mnemonic_external_references_is_valid(CAST(:refs AS jsonb))"),
                {"refs": json.dumps([{**REFERENCE, "label": case["value"]}])},
            )
            is case["valid"]
        )


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        "scalar",
        {},
        [None],
        [1],
        [[]],
        [{"url": None}],
        [REFERENCE, REFERENCE],
        [{**REFERENCE, "label": None}],
        [{**REFERENCE, "state_observed_at": None}],
        [{**REFERENCE, "unknown": "field"}],
    ],
)
def test_sql_validator_is_total_and_rejects_shapes(postgres_engine, value):
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT mnemonic_external_references_is_valid(CAST(:refs AS jsonb))"),
                {"refs": json.dumps(value)},
            )
            is False
        )


def test_create_replace_clear_preserve_replay_and_discovery(api, project, work_payload):
    original = [{**REFERENCE, "state_observed_at": "2026-09-05T10:20:00.120000-04:00"}]
    expected = [{**REFERENCE, "state_observed_at": "2026-09-05T14:20:00.12Z"}]
    create_request = {
        **work_payload,
        "external_references": original,
        "client_operation_id": str(uuid4()),
    }
    created = create_work(api, project, create_request)
    work = created["work_item"]
    path = item_path(project, work)
    assert work["external_references"] == expected
    ready = api.get(f"/api/v1/projects/{project['id']}/ready-work").json()
    assert ready["items"][0]["work_item"]["external_references"] == expected
    for url in (path, path + "/context"):
        result = api.get(url)
        assert result.status_code == 200, result.text
        assert result.json()["work_item"]["external_references"] == expected
    found = api.get(collection(project), params={"external_url": REFERENCE["url"]}).json()
    assert found["total"] == 1
    assert (
        api.get(
            collection(project),
            params={
                "external_url": REFERENCE["url"],
                "q": "no matching prose",
            },
        ).json()["total"]
        == 0
    )
    assert (
        api.get(
            collection(project),
            params={
                "external_url": REFERENCE["url"],
                "view": "roots",
            },
        ).status_code
        == 422
    )
    events = api.get(path + "/events").json()["items"]
    assert events[0]["metadata"]["initial"]["external_references"] == expected
    preserved = api.patch(path, json={"expected_version": 1, "summary": "Already filed"}).json()
    assert preserved["external_references"] == expected
    patch = {
        "expected_version": 2,
        "external_references": [],
        "actor": ACTOR,
        "client_operation_id": str(uuid4()),
    }
    cleared = api.patch(path, json=patch)
    assert cleared.status_code == 200, cleared.text
    assert "external_references" not in cleared.json()
    assert api.patch(path, json=patch).json() == cleared.json()
    assert api.post(collection(project), json=create_request).json() == created
    event = api.get(path + "/events", params={"order": "newest"}).json()["items"][0]
    assert event["metadata"]["changes"]["external_references"] == {"before": expected, "after": []}
    mismatch = {**patch, "title": work["title"]}
    mismatch.pop("external_references")
    assert api.patch(path, json=mismatch).status_code == 409


def test_replace_reorder_identical_and_conflicts_are_writes(api, project, work_payload):
    refs = [REFERENCE, {**REFERENCE, "url": REFERENCE["url"] + "?second=1", "kind": "references"}]
    work = create_work(api, project, work_payload, external_references=refs)["work_item"]
    path = item_path(project, work)
    for version, replacement in ((1, refs), (2, list(reversed(refs)))):
        response = api.patch(
            path, json={"expected_version": version, "external_references": replacement}
        )
        assert response.status_code == 200, response.text
        assert response.json()["version"] == version + 1
        assert response.json()["external_references"] == replacement
    for patch, status in (
        ({"expected_version": 1}, 409),
        ({"expected_version": 3, "lease_token": "invalid-token-32-characters"}, 409),
    ):
        response = api.patch(path, json={**patch, "external_references": []})
        assert response.status_code == status, response.text
    assert api.get(path).json()["work_item"]["external_references"] == list(reversed(refs))


def test_direct_sql_column_and_creation_fact_guards(api, project, work_payload, postgres_engine):
    work = create_work(api, project, work_payload)["work_item"]
    with postgres_engine.connect() as connection:
        for malformed in ([{**REFERENCE, "kind": "invalid"}], [REFERENCE, REFERENCE], {"url": "x"}):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE work_items SET external_references=CAST(:refs AS jsonb) "
                        "WHERE id=:id"
                    ),
                    {"refs": json.dumps(malformed), "id": work["id"]},
                )
        source = connection.scalar(
            text(
                "SELECT pg_get_functiondef(oid) FROM pg_proc WHERE "
                "pronamespace=current_schema()::regnamespace AND "
                "proname='mnemonic_guard_work_event_source_fact'"
            )
        )
        assert "v_work.external_references" in source


def test_maximum_reference_events_fit_expanded_caps(api, project, work_payload, postgres_engine):
    refs = [
        {
            **REFERENCE,
            "url": "https://example.com/" + str(index) + "a" * 1979,
            "label": "😀" * 120,
            "state_observed_at": "2026-09-05T14:20:00.123456Z",
        }
        for index in range(10)
    ]
    title = "a" + "\x01" * 199
    summary = "a" + "\x01" * 999
    work = create_work(
        api, project, work_payload, title=title, summary=summary, external_references=refs
    )["work_item"]
    response = api.patch(
        item_path(project, work),
        json={
            "expected_version": 1,
            "title": title.replace("a", "b"),
            "summary": summary.replace("a", "b"),
            "priority": 100,
            "status": "pending",
            "external_references": list(reversed(refs)),
            "actor": ACTOR,
            "client_operation_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    events = api.get(item_path(project, work) + "/events").json()["items"]
    assert len(json.dumps(events[-1]["metadata"], ensure_ascii=False).encode()) > 65536
    with postgres_engine.connect() as connection:
        size = connection.scalar(text("SELECT max(octet_length(metadata::text)) FROM work_events"))
        assert 65536 < size <= 131072
        assert (
            connection.scalar(
                text("SELECT max(octet_length(response_body::text)) FROM client_operations")
            )
            < 1048576
        )


def test_empty_feature_downgrade_restores_predecessor_and_refuses_cleared_history(
    api,
    project,
    work_payload,
    postgres_engine,
):
    work = create_work(api, project, work_payload)["work_item"]
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0021_job_completion_reports")
        rows = connection.execute(text("SELECT to_jsonb(w) FROM work_items w")).scalars().all()
        functions = connection.execute(
            text(
                "SELECT proname,prosrc FROM pg_proc WHERE "
                "pronamespace=current_schema()::regnamespace "
                "ORDER BY proname"
            )
        ).all()
        command.upgrade(config, "0022_external_references")
        assert (
            connection.execute(text("SELECT to_jsonb(w)-'external_references' FROM work_items w"))
            .scalars()
            .all()
            == rows
        )
        command.downgrade(config, "0021_job_completion_reports")
        assert (
            connection.execute(
                text(
                    "SELECT proname,prosrc FROM pg_proc WHERE "
                    "pronamespace=current_schema()::regnamespace "
                    "ORDER BY proname"
                )
            ).all()
            == functions
        )
        command.upgrade(config, "0022_external_references")
    path = item_path(project, work)
    assert (
        api.patch(
            path, json={"expected_version": 1, "external_references": [REFERENCE]}
        ).status_code
        == 200
    )
    assert (
        api.patch(path, json={"expected_version": 2, "external_references": []}).status_code == 200
    )
    with (
        pytest.raises(RuntimeError, match="Cannot downgrade"),
        postgres_engine.begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0021_job_completion_reports")


def test_alias_ownership_filters_and_counterpart_hierarchy_projection(
    api,
    project,
    work_payload,
    postgres_engine,
):
    from .test_duplicate_suggestions_postgres import merge

    source = create_work(api, project, work_payload, external_references=[REFERENCE])["work_item"]
    destination = create_work(api, project, work_payload, title="Canonical objective")["work_item"]
    prefix = collection(project)
    relationship = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "parent-child",
            "source_work_item_id": destination["id"],
            "target_work_item_id": source["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "external-reference-projection",
        },
    )
    assert relationship.status_code == 200, relationship.text
    context = api.get(item_path(project, destination) + "/context").json()
    assert REFERENCE["url"] in json.dumps(context["outgoing_relationships"])
    roots = api.get(prefix, params={"view": "roots"}).json()
    assert roots["total"] == 1
    children = api.get(item_path(project, destination) + "/children").json()
    assert children["items"][0]["summary"]["work_item"]["external_references"] == [REFERENCE]
    # Remove the parent edge before merging to avoid introducing a self-edge.
    removed = api.delete(
        f"/api/v1/projects/{project['id']}/relationships/"
        f"{relationship.json()['relationship']['id']}"
    )
    assert removed.status_code == 200, removed.text
    merge(api, project, source, destination)
    for scope, expected_count in (("canonical", 0), ("aliases", 1), ("all", 1)):
        response = api.get(
            prefix,
            params={"external_url": REFERENCE["url"], "duplicate_scope": scope, "status": "all"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == expected_count
        if expected_count:
            assert response.json()["items"][0]["summary"]["work_item"]["id"] == source["id"]
    assert "external_references" not in api.get(item_path(project, destination)).json()["work_item"]
    source_now = api.get(item_path(project, source)).json()["work_item"]
    assert source_now["external_references"] == [REFERENCE]
    assert (
        api.patch(
            item_path(project, source),
            json={
                "expected_version": source_now["version"],
                "external_references": [],
            },
        ).status_code
        == 409
    )
    with (
        postgres_engine.connect() as connection,
        pytest.raises(DBAPIError),
        connection.begin_nested(),
    ):
        connection.execute(
            text("UPDATE work_items SET external_references='[]'::jsonb WHERE id=:id"),
            {"id": source["id"]},
        )


def test_reference_closeout_is_atomic_and_reopen_keeps_explicit_clear(api, project, work_payload):
    from .report_fixtures import reported

    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    refused = api.patch(
        path, json={"expected_version": 1, "status": "promoted", "external_references": [REFERENCE]}
    )
    assert refused.status_code == 422, refused.text
    unchanged = api.get(path).json()["work_item"]
    assert unchanged["version"] == 1 and "external_references" not in unchanged
    close = api.patch(
        path,
        json=reported(
            {
                "expected_version": 1,
                "status": "promoted",
                "actor": ACTOR,
                "external_references": [REFERENCE],
            },
            retirement=True,
        ),
    )
    assert close.status_code == 200, close.text
    assert close.json()["external_references"] == [REFERENCE]
    edited = api.patch(
        path,
        json={"expected_version": 2, "external_references": [{**REFERENCE, "state": "closed"}]},
    )
    assert edited.status_code == 200, edited.text
    reopened = api.patch(
        path, json={"expected_version": 3, "status": "pending", "external_references": []}
    )
    assert reopened.status_code == 200, reopened.text
    event = api.get(path + "/events", params={"order": "newest"}).json()["items"][0]
    assert event["event_type"] == "work_reopened"
    assert event["metadata"]["changes"]["external_references"]["after"] == []


def test_sql_rejects_false_creation_snapshot_and_malformed_diffs(
    api,
    project,
    work_payload,
    postgres_engine,
):
    from sqlalchemy.orm import Session

    from mnemonic_api.models import Checkpoint, WorkItem
    from mnemonic_api.services.work_events import stage_work_created

    with Session(postgres_engine) as database, database.begin():
        checkpoint_id = uuid4()
        work = WorkItem(
            project_id=project["id"],
            title="Truthful snapshot",
            summary="Keep refs",
            initial_checkpoint_id=checkpoint_id,
            external_references=[REFERENCE],
        )
        database.add(work)
        database.flush()
        checkpoint = Checkpoint(
            id=checkpoint_id,
            work_item_id=work.id,
            prompt="Context",
            source_client="pytest",
            source_session_id="source-fact",
        )
        database.add(checkpoint)
        database.flush()
        for false_refs in (None, [{**REFERENCE, "state": "closed"}]):
            with pytest.raises(DBAPIError), database.begin_nested():
                event = stage_work_created(database, work, checkpoint)
                event.event_metadata = {
                    "initial": {
                        "title": work.title,
                        "summary": work.summary,
                        "status": work.status,
                        "priority": work.priority,
                        "version": work.version,
                        **({"external_references": false_refs} if false_refs is not None else {}),
                    }
                }
                database.flush()
        stage_work_created(database, work, checkpoint)
        database.flush()
        for diff in (
            {"before": [], "after": None},
            {"before": [], "after": [REFERENCE, REFERENCE]},
            {"before": [], "after": [], "extra": True},
            [[], []],
        ):
            with pytest.raises(DBAPIError), database.begin_nested():
                database.execute(
                    text("""
                    INSERT INTO work_events(project_id,work_item_id,event_type,actor_kind,metadata)
                    VALUES(:p,:w,'work_updated','unattributed',CAST(:metadata AS jsonb))
                """),
                    {
                        "p": work.project_id,
                        "w": work.id,
                        "metadata": json.dumps(
                            {"changes": {"external_references": diff}, "work_version": 2}
                        ),
                    },
                )
        # Ordinary update diffs are shape-validated; no transition authenticity is claimed.
        database.execute(
            text("""
            INSERT INTO work_events(project_id,work_item_id,event_type,actor_kind,metadata)
            VALUES(:p,:w,'work_updated','unattributed',CAST(:metadata AS jsonb))
        """),
            {
                "p": work.project_id,
                "w": work.id,
                "metadata": json.dumps(
                    {
                        "changes": {"external_references": {"before": [], "after": []}},
                        "work_version": 2,
                    }
                ),
            },
        )


@pytest.mark.parametrize("case", FIXTURE["timestamp_cases"])
def test_sql_observation_requires_canonical_utc_representation(postgres_engine, case):
    with postgres_engine.connect() as connection:
        raw = connection.scalar(
            text("SELECT mnemonic_external_references_is_valid(CAST(:refs AS jsonb))"),
            {"refs": json.dumps([{**REFERENCE, "state_observed_at": case["value"]}])},
        )
        assert raw is (case["normalized"] == case["value"])
        if case["normalized"] is not None:
            assert connection.scalar(
                text("SELECT mnemonic_external_references_is_valid(CAST(:refs AS jsonb))"),
                {"refs": json.dumps([{**REFERENCE, "state_observed_at": case["normalized"]}])},
            )


def test_progress_metadata_retains_ordinary_bound(api, project, work_payload):
    work = create_work(api, project, work_payload)["work_item"]
    response = api.post(
        item_path(project, work) + "/events",
        json={
            "actor": ACTOR,
            "body": "Progress stays bounded.",
            "metadata": {"content": "x" * 16384},
        },
    )
    assert response.status_code == 422, response.text


def test_downgrade_waits_for_writer_before_checking_reference_history(
    api, project, work_payload, postgres_engine
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    work = create_work(api, project, work_payload)["work_item"]
    started = Event()

    def downgrade():
        with postgres_engine.begin() as connection:
            connection.execute(text("SET application_name='external-downgrade-race'"))
            config = Config(str(BACKEND_DIR / "alembic.ini"))
            config.attributes["connection"] = connection
            started.set()
            command.downgrade(config, "0021_job_completion_reports")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with postgres_engine.begin() as writer:
            writer.execute(
                text("UPDATE work_items SET external_references=CAST(:refs AS jsonb) WHERE id=:id"),
                {"id": work["id"], "refs": json.dumps([REFERENCE])},
            )
            result = executor.submit(downgrade)
            assert started.wait(1)
            deadline = monotonic() + 2
            while monotonic() < deadline:
                with postgres_engine.connect() as observer:
                    waiting = observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE application_name='external-downgrade-race' "
                            "AND wait_event_type='Lock'"
                        )
                    )
                if waiting:
                    break
                sleep(0.01)
            assert waiting, "Downgrade must wait for the existing writer"
        with pytest.raises(RuntimeError, match="Cannot downgrade"):
            result.result(timeout=3)
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0022_external_references"
        )
        assert connection.scalar(
            text("SELECT external_references FROM work_items WHERE id=:id"), {"id": work["id"]}
        ) == [REFERENCE]
