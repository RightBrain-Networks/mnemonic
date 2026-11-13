"""Real PostgreSQL delta queries preserve history, cursor windows and current facets."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from .test_knowledge_postgres import domain, path, put, put_body
from .test_project_reports_postgres import actor, create

pytestmark = pytest.mark.postgres


def endpoint(project):
    return f"/api/v1/projects/{project['id']}/activity/query"


def query(api, project, **payload):
    response = api.post(endpoint(project), json=payload)
    assert response.status_code == 200, response.text
    assert len(response.content) <= 32768
    return response.json()


def edit_work(api, project, work, checkpoint_fields, title):
    response = api.patch(f"/api/v1/projects/{project['id']}/work-items/{work['id']}", json={
        "expected_version": work["version"], "title": title,
        "actor": actor(checkpoint_fields), "client_operation_id": str(uuid4()),
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_activity_bootstrap_reports_grouping_and_replay(api, project, work_payload):
    bootstrap = query(api, project, start="now", facets=["work_items"])
    assert bootstrap["items"] == []
    assert bootstrap["checkpoint"] and not bootstrap["has_more"]
    payload = {**work_payload, "client_operation_id": str(uuid4())}
    created = create(api, project, payload)
    replayed = create(api, project, payload)
    assert replayed == created
    result = query(api, project, after=bootstrap["checkpoint"], facets=["work_items"])
    assert result["counts"]["resources"] == 1
    assert result["counts"]["changes"] >= 1
    assert result["items"][0]["resource_id"] == created["id"]
    assert result["items"][0]["current"]["status"] == "pending"
    assert query(api, project, after=result["checkpoint"], facets=["work_items"])["items"] == []


def test_later_edit_does_not_erase_earlier_window(
    api, project, work_payload, checkpoint_fields, monkeypatch,
):
    work = create(api, project, work_payload)
    tuesday = datetime(2026, 11, 10, 12, tzinfo=UTC)
    friday = datetime(2026, 11, 13, 12, tzinfo=UTC)
    monkeypatch.setattr("mnemonic_api.services.work_items.database_now", lambda _: tuesday)
    work = edit_work(api, project, work, checkpoint_fields, "Tuesday title")
    monkeypatch.setattr("mnemonic_api.services.work_items.database_now", lambda _: friday)
    work = edit_work(api, project, work, checkpoint_fields, "Friday title")
    result = query(api, project, facets=["work_items"], since="2026-11-09T00:00:00Z",
                   until="2026-11-12T00:00:00Z")
    item = result["items"][0]
    assert item["resource_id"] == work["id"]
    assert item["title"] == "Tuesday title"
    assert item["current"]["title"] == "Friday title"
    assert item["change_count"] == 1
    assert "title" in item["changed_fields"]
    assert result["checkpoint"] is None
    exact = query(api, project, facets=["work_items"], since=tuesday.isoformat(),
                  until=friday.isoformat())
    assert exact["counts"]["changes"] == 1
    assert exact["items"][0]["title"] == "Tuesday title"


def test_frozen_highwater_and_cursor_scope(api, project, work_payload):
    original = [create(api, project, {**work_payload, "title": f"Work {i}"}) for i in range(3)]
    initial = query(api, project, facets=["work_items"], limit=1)
    assert initial["has_more"] and initial["next_cursor"] and initial["checkpoint"] is None
    later = create(api, project, {**work_payload, "title": "After snapshot"})
    result = query(api, project, facets=["work_items"], limit=100, cursor=initial["next_cursor"])
    assert result["through_sequence"] == initial["through_sequence"]
    assert result["counts"] == initial["counts"]
    returned = initial["items"] + result["items"]
    assert {item["resource_id"] for item in returned} == {work["id"] for work in original}
    polled = query(api, project, facets=["work_items"], after=result["checkpoint"])
    assert [item["resource_id"] for item in polled["items"]] == [later["id"]]
    bad = api.post(endpoint(project), json={"facets": ["artifacts"],
                                            "cursor": initial["next_cursor"]})
    assert bad.status_code == 422


def test_current_decision_filters_are_project_scoped(api, project):
    architecture = domain(api, project)
    business = domain(api, project, "BDR", "Business")
    selected = put(api, project, put_body(architecture, title="Selected ADR"))
    put(api, project, put_body(business, title="Excluded BDR"))
    result = query(api, project, facets=["decisions"], filters={
        "decisions": {"domain_prefixes": ["ADR"], "states": ["proposed"]},
    })
    assert len(result["items"]) == 1
    assert result["items"][0]["current"]["domain_prefix"] == "ADR"
    assert result["counts"]["resources"] == 1
    assert "content" not in result["items"][0]["after_state"]
    assert selected is not None


def test_decision_changes_supply_canonical_lookup_locator_and_exact_revision(api, project):
    from .test_knowledge_management_postgres import (
        HEADERS,
        browser_path,
        deletion_body,
        preview,
    )

    architecture = domain(api, project)
    first = put(api, project, put_body(architecture, "Original decision"))["decision"]
    put(api, project, put_body(architecture, "Revised decision",
                              decision_id=first["decision_id"], expected_revision=0))
    changes = query(api, project, facets=["decisions"], view="events")["items"]
    assert len(changes) == 2
    for change in changes:
        current = change["current"]
        assert current["decision_id"] == first["decision_id"] == "ADR-00001"
        assert current["revision"] == "1"
        exact = api.get(path(project) + "/decisions/" + current["decision_id"],
                        params={"revision": change["revision"]})
        assert exact.status_code == 200, exact.text
        assert exact.json()["revision"] == int(change["revision"])
    suffix = "/decisions/" + first["decision_id"]
    removed = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS,
                       json=deletion_body(preview(api, project, suffix)))
    assert removed.status_code == 200, removed.text
    deleted = query(api, project, facets=["decisions"])["items"][0]
    assert deleted["current"]["deleted"]
    assert deleted["current"]["decision_id"] == first["decision_id"]
    assert deleted["current"]["revision"] == deleted["revision"] == "1"


def test_query_is_strict_safe_read(api, project, postgres_engine):
    before = query(api, project)["through_sequence"]
    for body in ({"since": "2026-11-10"}, {"limit": True}, {"facets": []},
                 {"start": "now", "since": "2026-11-10T00:00:00Z"}):
        assert api.post(endpoint(project), json=body).status_code == 422
    duplicate = api.post(endpoint(project), content='{"limit":1,"limit":2}',
                         headers={"Content-Type": "application/json"})
    assert duplicate.status_code == 422
    assert api.post(endpoint(project) + "?limit=1", json={}).status_code == 422
    assert query(api, project)["through_sequence"] == before
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM client_operations "
                                      "WHERE operation_kind='query_activity'")) == 0


def test_current_pending_excludes_claimed_work(api, project, work_payload):
    pending = create(api, project, {**work_payload, "title": "Unclaimed work"})
    active = create(api, project, {**work_payload, "title": "Claimed work"})
    claimed = api.post(f"/api/v1/projects/{project['id']}/work-items/{active['id']}/claim", json={
        "holder_client": "codex", "holder_session_id": "activity-test",
        "claim_request_id": str(uuid4()), "session_transcript": None,
    })
    assert claimed.status_code == 200, claimed.text
    result = query(api, project, facets=["work_items"], filters={
        "work_items": {"statuses": ["pending"]},
    })
    assert [item["resource_id"] for item in result["items"]] == [pending["id"]]
    active_page = query(api, project, facets=["work_items"], filters={
        "work_items": {"statuses": ["active"]},
    })
    assert [item["resource_id"] for item in active_page["items"]] == [active["id"]]


def test_resource_grouping_and_events_count_same_changes(
    api, project, work_payload, checkpoint_fields,
):
    work = create(api, project, work_payload)
    work = edit_work(api, project, work, checkpoint_fields, "First edited title")
    edit_work(api, project, work, checkpoint_fields, "Second edited title")
    resources = query(api, project, facets=["work_items"])
    events = query(api, project, facets=["work_items"], view="events")
    assert resources["counts"] == events["counts"]
    assert resources["counts"]["resources"] == 1
    assert resources["counts"]["changes"] == len(events["items"])
    assert resources["items"][0]["change_count"] == len(events["items"])
    assert resources["items"][0]["title"] == "Second edited title"
    assert all(item["change_count"] == 1 for item in events["items"])
    assert all(item["first_sequence"] == item["last_sequence"] for item in events["items"])


def test_byte_limited_page_drains_without_omitting_resources(api, project, work_payload):
    expected = set()
    for i in range(30):
        work = create(api, project, {**work_payload, "title": f"{i}: " + "界" * 180})
        expected.add(work["id"])
    payload = {"facets": ["work_items"], "limit": 100}
    first = query(api, project, **payload)
    assert first["page_limited_by_bytes"] and first["has_more"]
    seen = {item["resource_id"] for item in first["items"]}
    page = first
    while page["has_more"]:
        page = query(api, project, **payload, cursor=page["next_cursor"])
        assert page["counts"] == first["counts"]
        fresh = {item["resource_id"] for item in page["items"]}
        assert not seen.intersection(fresh)
        seen |= fresh
    assert seen == expected
    assert page["checkpoint"]


def test_deletion_and_project_moves_remain_visible(api, project, work_payload, checkpoint_fields):
    deleted = create(api, project, {**work_payload, "title": "Delete this"})
    removed = api.post(f"/api/v1/projects/{project['id']}/work-items/{deleted['id']}/delete", json={
        "subagent_transcripts": None, "expected_version": deleted["version"],
        "actor": actor(checkpoint_fields), "client_operation_id": str(uuid4()),
    })
    assert removed.status_code == 200, removed.text
    moved = create(api, project, {**work_payload, "title": "Move this"})
    target = api.post("/api/v1/projects", json={"name": "Move target"}).json()
    movement = api.post(f"/api/v1/projects/{project['id']}/work-items/{moved['id']}/move", json={
        "target_project_id": target["id"], "expected_version": moved["version"],
        "actor": actor(checkpoint_fields), "client_operation_id": str(uuid4()),
    })
    assert movement.status_code == 200, movement.text
    source = query(api, project, facets=["work_items"])
    by_id = {item["resource_id"]: item for item in source["items"]}
    assert "work_deleted" in by_id[deleted["id"]]["operations"]
    assert by_id[deleted["id"]]["current"]["deleted"]
    assert "work_moved" in by_id[moved["id"]]["operations"]
    assert not by_id[moved["id"]]["current"]["exists"]
    target_page = query(api, target, facets=["work_items"])
    assert [item["resource_id"] for item in target_page["items"]] == [moved["id"]]


def test_rollback_does_not_publish_changes(api, project, postgres_engine):
    bootstrap = query(api, project, start="now")
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("UPDATE projects SET name='Uncommitted title' WHERE id=:id"),
                           {"id": project["id"]})
        transaction.rollback()
    result = query(api, project, after=bootstrap["checkpoint"])
    assert result["items"] == []
    assert result["through_sequence"] == bootstrap["through_sequence"]


def test_poll_window_can_be_paged_and_bound_to_original_checkpoint(api, project, work_payload):
    bootstrap = query(api, project, start="now", facets=["work_items"])
    for i in range(3):
        create(api, project, {**work_payload, "title": f"New work {i}"})
    scope = {"after": bootstrap["checkpoint"], "facets": ["work_items"]}
    first = query(api, project, **scope, limit=1)
    final = query(api, project, **scope, cursor=first["next_cursor"])
    assert len(first["items"]) + len(final["items"]) == 3
    assert final["checkpoint"] and not final["has_more"]
    omitted = api.post(endpoint(project), json={
        "facets": ["work_items"], "cursor": first["next_cursor"],
    })
    assert omitted.status_code == 422


def test_intrinsic_decision_review_tags_remain_distinct_from_assignments(api, project):
    from .test_decision_impact_postgres import changed, decision
    from .test_decision_reviews_postgres import settings

    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    initial = decision(api, project, target, "Initial rule")
    changed(api, project, target, initial)
    result = query(api, project, facets=["work_items"], filters={
        "work_items": {"tags": ["dir"]},
    })
    assert len(result["items"]) == 1
    current = result["items"][0]["current"]
    assert current["tags"] is None
    assert current["system_tags"] == ["dir"]


def test_intrinsic_remediation_tag_is_exposed_for_tag_filter_validation(
    api, project, work_payload, checkpoint_fields,
):
    from .code_review_fixtures import claim_review, finding, mandatory, result_payload, result_url

    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    lease = claim_review(api, project, completion, checkpoint_fields)
    body = result_payload(completion, lease, findings=[finding()])
    reviewed = api.post(result_url(project, completion), json=body)
    assert reviewed.status_code == 200, reviewed.text
    result = query(api, project, facets=["work_items"], filters={
        "work_items": {"tags": ["remediation"]},
    })
    assert len(result["items"]) == 1
    current = result["items"][0]["current"]
    assert current["tags"] is None
    assert current["system_tags"] == ["remediation"]
