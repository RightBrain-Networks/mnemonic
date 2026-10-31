"""Closed-cycle continuation retains exact governance and existing obligations."""

import pytest
from sqlalchemy import text

from .test_decision_changes_postgres import group, retire
from .test_decision_impact_postgres import decision
from .test_decision_reviews_postgres import context, reviews, settings
from .test_human_gates_postgres import create_work
from .test_knowledge_postgres import domain, path

pytestmark = pytest.mark.postgres


def test_exact_record_existing_supersession_does_not_create_another_cycle(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    old = decision(api, project, target, "Original rule")
    governing = decision(api, project, target, "Replacement ruling",
                         supersedes=[old["decision_id"]])
    change_id = governing["change"]["change_id"]
    driver = group(api, project, change_id)["review_work_item_id"]
    review = context(api, project, driver)["work_item"]
    child = create_work(api, project, {
        **work_payload, "discovered_from_work_item_id": review["id"],
    }, title="Record existing supersession")["work_item"]
    retire(api, project, review, checkpoint_fields)
    before = api.get(path(project) + "/assessments", params={"change_id": change_id}).json()
    with postgres_engine.connect() as connection:
        snapshot = connection.execute(text("SELECT * FROM decision_impact_assessments")).all()
    recorded = decision(api, project, target, "Separate record of the same replacement ruling",
                        supersedes=[old["decision_id"]], source_work_item_id=child["id"], change={
        "kind": "record-existing", "change_id": change_id,
        "governing_reference": governing["reference"], "reason": "Record the exact same ruling.",
    })
    assert recorded["change"]["governing_reference"] == governing["reference"]
    assert group(api, project, change_id)["successor_change_id"] is None
    after = api.get(path(project) + "/assessments", params={"change_id": change_id}).json()
    assert [item["id"] for item in after["items"]] == [item["id"] for item in before["items"]]
    with postgres_engine.connect() as connection:
        retained = connection.execute(text("SELECT * FROM decision_impact_assessments")).all()
        assert retained == snapshot
    assert len(reviews(api, project)) == 1
