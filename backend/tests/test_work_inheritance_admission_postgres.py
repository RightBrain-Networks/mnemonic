"""Cross-project grouping and explicit opt-outs preserve closed review history."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from .test_decision_changes_postgres import answer_question, group, retire, root_review
from .test_decision_impact_postgres import decision
from .test_decision_reviews_postgres import context, reviews, settings
from .test_knowledge_postgres import domain, path, put, put_body
from .test_project_activity_audit_postgres import _audit
from .test_work_inheritance_postgres import excluded
from .test_work_item_moves_postgres import _project
from .test_work_lineage_postgres import collection, create

pytestmark = pytest.mark.postgres


def captured_episodes(engine, project):
    # Author activity timestamps are live projections; compare every stored episode field.
    with engine.connect() as connection:
        return list(connection.scalars(text(
            "SELECT to_jsonb(episode) FROM decision_impact_assessments episode "
            "WHERE project_id=:project_id ORDER BY id"
        ), {"project_id": project["id"]}))


def test_cross_project_opt_out_authors_independently_without_admitting_closed_origin(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    origin_project = _project(api, "Closed originating review")
    _, _, origin_revision, origin = root_review(api, origin_project)
    origin_change_id = origin_revision["change"]["change_id"]
    retire(api, origin_project, origin, checkpoint_fields)
    closed_change = group(api, origin_project, origin_change_id)
    assert closed_change["review_closed"] is True
    assert closed_change["successor_change_id"] is None
    original_episodes = captured_episodes(postgres_engine, origin_project)
    assert len(original_episodes) == 1

    default = create(api, project, work_payload, discovered_from_work_item_id=origin["id"],
                     client_operation_id=str(uuid4()))
    independent, _ = excluded(api, project, work_payload, origin)
    default_id, independent_id = default["work_item"]["id"], independent["work_item"]["id"]
    assert default["work_item"]["decision_change_ids"] == [origin_change_id]
    assert "decision_change_ids" not in independent["work_item"]

    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    earlier = decision(api, project, target, "Earlier local tooling rule")
    amendment = put_body(target, "A new local tooling rule", decision_id=earlier["decision_id"],
                         expected_revision=0, source_work_item_id=default_id,
                         change={"kind": "substantive"})
    saved = put(api, project, amendment)
    assert saved["decision"]["revision"] == 1
    assert len(reviews(api, project)) == 1

    # An owner answer joins the existing local cycle while retaining foreign lineage.
    answer = answer_question(api, project, default)
    held = api.get(path(project) + f"/decisions/{answer['decision']['reference']}").json()
    assert not held["change"].get("requires_admission", False)
    held_change = group(api, project, held["change"]["change_id"])
    assert held_change["pending_revision_count"] == 0
    assert held_change["review_work_item_id"] is not None
    assert len(reviews(api, project)) == 1

    independent_intent = {**amendment, "source_work_item_id": independent_id,
                          "expected_revision": 1, "content": "Another independent tooling rule",
                          "client_operation_id": str(uuid4())}
    written = put(api, project, independent_intent)
    authored = written["decision"]["change"]
    assert authored["kind"] == "substantive"
    assert authored["source_work_item_id"] == independent_id
    assert not authored.get("requires_admission", False)
    assert authored["change_id"] not in {origin_change_id, held_change["id"]}
    own_change = group(api, project, authored["change_id"])
    assert own_change["parent_change_id"] is None
    assert own_change["pending_revision_count"] == 0
    assert own_change["assessment_count"] == 1
    assert own_change["review_closed"] is False
    assert len(reviews(api, project)) == 2
    assert own_change["review_work_item_id"] in {
        row["work_item_id"] for row in reviews(api, project)
    }
    assert put(api, project, independent_intent) == written
    assert context(api, project, independent_id)["work_item"]["decision_change_ids"] == [
        own_change["id"],
    ]
    assert origin_change_id in context(api, project, default_id)["work_item"]["decision_change_ids"]

    # Authorship and held answers preserve both original discovery assertions and history.
    for created in (default, independent):
        child = created["work_item"]
        edge, = created["initial_relationships"]
        adjacent = api.get(collection(project) + f"/{child['id']}/relationships").json()
        retained, = [item["relationship"] for item in adjacent["items"]
                     if item["relationship"]["id"] == edge["id"]]
        assert retained == edge
        assert retained["relationship_type"] == "discovered-from"
        assert retained["source_work_item_id"] == child["id"]
        assert retained["target_work_item_id"] == origin["id"]
        assert retained["context_checkpoint_id"] == origin["initial_checkpoint_id"]
    current_change = group(api, origin_project, origin_change_id)
    # New members are live read context, without changing the closed review's obligations.
    assert {key: value for key, value in current_change.items() if key != "lineage_work"} == {
        key: value for key, value in closed_change.items() if key != "lineage_work"
    }
    assert [item["work_item_id"] for item in current_change["lineage_work"]["items"]] == [
        default_id,
    ]
    assert captured_episodes(postgres_engine, origin_project) == original_episodes
    assert context(api, origin_project, origin["id"])["work_item"]["status"] == "wont-do"
    assert group(api, project, held_change["id"]) == held_change
    assert _audit(postgres_engine)["result"] == "pass"
