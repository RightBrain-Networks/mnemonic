"""Decision reviews see durable member work without changing scope or readiness."""

from uuid import uuid4

import pytest

from .code_review_fixtures import (
    claim_review,
    close,
    configure,
    finding,
    handoff,
    result_payload,
    result_url,
)
from .test_decision_changes_postgres import group, root_review
from .test_decision_impact_postgres import changed, decision
from .test_decision_reviews_postgres import context, reviews, settings
from .test_duplicate_handling_postgres import merge_work
from .test_human_gates_postgres import create_work
from .test_knowledge_postgres import domain, path
from .test_leases_postgres import expire_lease
from .test_work_item_moves_postgres import _move_payload, _project

pytestmark = pytest.mark.postgres


def _claim(api, project, work):
    result = api.post(f"/api/v1/projects/{project['id']}/work-items/{work['id']}/claim", json={
        "holder_client": "codex", "holder_session_id": "lineage-test", "session_transcript": None,
        "claim_request_id": str(uuid4()),
    })
    assert result.status_code == 200, result.text
    return result.json()


def _lineage(api, project, change_id, **params):
    response = api.get(path(project) + f"/changes/{change_id}", params=params)
    assert response.status_code == 200, response.text
    return response.json()["lineage_work"]


def test_open_code_review_remediation_and_source_review_are_visible(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    settings(api, project, automatic_decision_reviews=True)
    source = create_work(api, project, work_payload,
                         title="Implement the revised ruling")["work_item"]
    target = domain(api, project)
    old = decision(api, project, target, "Original ruling")
    new = changed(api, project, target, old, "Revised ruling", source_work_item_id=source["id"])
    change_id = new["change"]["change_id"]
    review, = reviews(api, project)
    before = group(api, project, change_id)
    configure(api, project, code_review_required_min_priority=0)
    closed, _ = close(api, project, source, checkpoint_fields, code_review_handoff=handoff())
    assert closed.status_code == 200, closed.text
    completion = closed.json()
    pending = _lineage(api, project, change_id)["items"][0]
    assert pending["code_review"]["status"] == "to-review"
    lease = claim_review(api, project, completion, checkpoint_fields)
    result = api.post(result_url(project, completion), json=result_payload(
        completion, lease, findings=[finding()],
    ))
    assert result.status_code == 200, result.text
    remediation = result.json()["remediation_work"]["work_item"]
    active = _claim(api, project, remediation)
    page = _lineage(api, project, change_id)
    assert page["total"] == 2
    items = {item["work_item_id"]: item for item in page["items"]}
    assert review["work_item_id"] not in items
    assert items[source["id"]]["is_decision_source"] is True
    assert items[source["id"]]["code_review"] == {
        "review_id": completion["code_review_request"]["id"], "state": "completed", "status": None,
    }
    child = items[remediation["id"]]
    assert child["readiness"]["display_state"] == "active"
    assert child["readiness"]["active_lease"]["holder_session_id"] == "lineage-test"
    assert active["lease_token"] not in str(page)
    assert child["remediation_origin"] == {
        "review_id": completion["code_review_request"]["id"],
        "source_work_item_id": source["id"], "source_project_id": project["id"], "depth": 1,
    }
    assert child["discovered_from"]["items"] == [
        {"work_item_id": source["id"], "project_id": project["id"]},
    ]
    expire_lease(postgres_engine, remediation["id"])
    child = next(item for item in _lineage(api, project, change_id)["items"]
                 if item["work_item_id"] == remediation["id"])
    assert child["readiness"]["active_lease"] is None
    assert child["readiness"]["has_dropped_lease"] is True
    after = group(api, project, change_id)
    for key in ("scope_version", "acknowledged_scope_version", "assessment_count"):
        assert after[key] == before[key]
    assert context(api, project, review["work_item_id"])["readiness"]["is_ready"] is True


def test_lineage_pages_keep_moved_deleted_and_duplicate_members(api, project, work_payload):
    _, _, new, review = root_review(api, project)
    change_id = new["change"]["change_id"]
    children = [create_work(api, project, {**work_payload,
        "discovered_from_work_item_id": review["id"]}, title=title)["work_item"]
        for title in ("Moved", "Deleted", "Duplicate", "Canonical")]
    moved, deleted, duplicate, canonical = children
    destination = _project(api, "Another project")
    result = api.post(f"/api/v1/projects/{project['id']}/work-items/{moved['id']}/move",
                      json=_move_payload(destination, 1))
    assert result.status_code == 200, result.text
    edge = context(api, project, deleted["id"])["outgoing_relationships"][0]["relationship"]
    removed = api.delete(f"/api/v1/projects/{project['id']}/relationships/{edge['id']}")
    assert removed.status_code == 200, removed.text
    result = api.post(f"/api/v1/projects/{project['id']}/work-items/{deleted['id']}/delete", json={
        "expected_version": 1, "subagent_transcripts": None,
        "client_operation_id": str(uuid4()),
        "actor": {"actor_client": "codex", "actor_session_id": "lineage-test"},
    })
    assert result.status_code == 200, result.text
    merge_work(api, project, duplicate, canonical)
    first = _lineage(api, project, change_id, lineage_limit=2)
    second = _lineage(api, project, change_id, lineage_limit=2,
                      lineage_offset=first["next_offset"])
    assert first["total"] == second["total"] == 4
    assert first["next_offset"] == 2 and second["next_offset"] is None
    rows = first["items"] + second["items"]
    assert [row["work_item_id"] for row in rows] == [child["id"] for child in children]
    items = {item["work_item_id"]: item for item in rows}
    assert items[moved["id"]]["project_id"] == destination["id"]
    assert items[deleted["id"]]["deleted_at"] is not None
    assert items[deleted["id"]]["readiness"] is None
    assert items[deleted["id"]]["canonical_work_item_id"] == deleted["id"]
    assert items[duplicate["id"]]["canonical_work_item_id"] == canonical["id"]
    assert items[duplicate["id"]]["readiness"]["is_duplicate"] is True
    beyond = _lineage(api, project, change_id, lineage_offset=20)
    assert beyond["items"] == [] and beyond["next_offset"] is None and beyond["total"] == 4
    browser = api.get(path(project).replace("/api/v1/", "/api/mnemonic/")
                      + f"/changes/{change_id}", params={"lineage_limit": 2, "lineage_offset": 2},
                      headers={"host": "localhost:3000"})
    assert browser.status_code == 200, browser.text
    assert browser.json()["lineage_work"] == second


@pytest.mark.parametrize("params", [
    {"lineage_limit": 0}, {"lineage_limit": 101}, {"lineage_offset": -1},
])
def test_lineage_pagination_rejects_invalid_bounds(api, project, params):
    _, _, new, _ = root_review(api, project)
    result = api.get(path(project) + f"/changes/{new['change']['change_id']}", params=params)
    assert result.status_code == 422


def test_discovery_origins_are_bounded_with_explicit_omission(api, project, work_payload):
    _, _, new, review = root_review(api, project)
    origins = [create_work(api, project, work_payload, title=f"Origin {number}")["work_item"]
               for number in range(20)]
    child = create_work(api, project, {**work_payload,
        "discovered_from_work_item_id": review["id"],
    }, title="Investigation with several origins")["work_item"]
    for origin in origins:
        response = api.post(f"/api/v1/projects/{project['id']}/relationships", json={
            "relationship_type": "discovered-from", "source_work_item_id": child["id"],
            "target_work_item_id": origin["id"], "created_by_client": "codex",
            "created_by_session_id": "lineage-test",
            "context_checkpoint_id": origin["initial_checkpoint_id"],
        })
        assert response.status_code == 200, response.text
    page = _lineage(api, project, new["change"]["change_id"])
    assert page["total"] == 1 and page["items"][0]["work_item_id"] == child["id"]
    captured = page["items"][0]["discovered_from"]
    assert len(captured["items"]) == 20
    assert captured["total"] == 21 and captured["omitted_count"] == 1
