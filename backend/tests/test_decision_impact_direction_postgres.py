"""Causal impact and optional context retain their distinct evidence and obligations."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from mnemonic_api.impact_integrity import IMPACT_FINDINGS
from tests.test_decision_impact_postgres import assessments, changed, decision, dependency, impact
from tests.test_decision_reviews_postgres import assess, context, finish, reviews, settings
from tests.test_human_gates_postgres import create_work
from tests.test_knowledge_postgres import domain, path

pytestmark = pytest.mark.postgres


def test_reverse_impact_excludes_prerequisites_and_pages_context_separately(
    api, project, postgres_engine,
):
    target = domain(api, project)
    prerequisite = decision(api, project, target, "Upstream contract")
    mentioned = decision(api, project, target, "Informational reference")
    origin = decision(api, project, target, "Changing policy",
                      depends_on=[dependency(prerequisite)],
                      related_decision_ids=[prerequisite["decision_id"], mentioned["decision_id"]])
    dependent = decision(api, project, target, "Consumer", depends_on=[dependency(origin)])
    indirect = decision(api, project, target, "Indirect consumer",
                        depends_on=[dependency(dependent)])
    incoming = decision(api, project, target, "A mention of the policy",
                        related_decision_ids=[origin["decision_id"]])
    result = impact(api, project, origin["reference"])
    assert [item["reference"] for item in result["items"]] == [
        origin["reference"], dependent["reference"], indirect["reference"],
    ]
    assert result["kind"] == "code-impact"
    assert result["candidate_count"] == result["total"] == 3
    assert result["context_count"] == 3
    contexts = []
    for offset in range(3):
        page = impact(api, project, origin["reference"], kind="context", limit=1, offset=offset)
        assert page["total"] == 3 and page["candidate_count"] == 3
        assert page["next_offset"] == (offset + 1 if offset < 2 else None)
        contexts.extend(page["items"])
    assert {item["reference"] for item in contexts} == {
        prerequisite["reference"], mentioned["reference"], incoming["reference"],
    }
    assert contexts[0]["relationship"] == "depends-on-prerequisite"
    assert contexts[0]["path"] == [origin["reference"], prerequisite["reference"]]
    changed(api, project, target, origin)
    episode = assessments(api, project)[0]
    detail_path = path(project) + f"/assessments/{episode['id']}"
    detail = api.get(detail_path).json()
    assert detail["candidate_count"] == detail["total"] == detail["unassessed_count"] == 3
    assert detail["context_count"] == 3
    page = api.get(detail_path, params={"kind": "context", "limit": 2}).json()
    assert page["total"] == 3 and page["next_offset"] == 2
    assert {item["relationship"] for item in page["candidates"]} <= {
        "depends-on-prerequisite", "related-only",
    }
    with postgres_engine.begin() as connection:
        findings = {key: connection.scalar(text(sql)) for key, sql in IMPACT_FINDINGS.items()}
        assert not any(findings.values()), findings


def test_missing_scope_warning_names_exact_revision_and_snapshot_does_not_gain_later_scope(
    api, project,
):
    target = domain(api, project)
    original = decision(api, project, target, "No known implementation")
    warning, = impact(api, project, original["reference"])["coverage"]["warnings"]
    assert warning["code"] == "origin_has_no_linked_commits"
    assert warning["reference"] == original["reference"]
    assert f"{original['reference']} has no linked commits" in warning["message"]
    updated = changed(api, project, target, original)
    episode = assessments(api, project)[0]
    scope = api.post(path(project) + f"/decisions/{original['decision_id']}/scope", json={
        "revision": 0, "commit_hash": "a" * 40, "client_operation_id": str(uuid4()),
    })
    assert scope.status_code == 200, scope.text
    assert impact(api, project, original["reference"])["coverage"]["warnings"] == []
    assert impact(api, project, updated["reference"])["coverage"]["warnings"][0]["reference"] == \
        updated["reference"]
    retained = api.get(path(project) + f"/assessments/{episode['id']}").json()
    assert retained["coverage"]["warnings"] == [warning]


def test_optional_context_needs_no_verdict_but_recorded_changes_still_need_remediation(
    api, project, checkpoint_fields, work_payload, postgres_engine,
):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    prerequisite = decision(api, project, target, "Prerequisite")
    mention = decision(api, project, target, "A related decision")
    original = decision(api, project, target, "Policy", depends_on=[dependency(prerequisite)],
                        related_decision_ids=[mention["decision_id"]])
    changed(api, project, target, original)
    work_id = reviews(api, project)[0]["work_item_id"]
    retained = context(api, project, work_id)
    assessment_id = retained["initial_checkpoint"]["source_metadata"]["assessment_ids"][0]
    endpoint = path(project) + f"/assessments/{assessment_id}"
    with postgres_engine.begin() as connection:
        original_snapshot = connection.scalar(text(
            "SELECT candidates FROM decision_impact_assessments WHERE id=:id"
        ), {"id": assessment_id})
    assess(api, project, assessment_id, original["reference"], "not-affected")
    summary = api.get(endpoint).json()
    assert summary["unassessed_count"] == 0 and summary["status"] == "resolved"
    # Optional context findings still carry their full remediation obligation.
    assess(api, project, assessment_id, mention["reference"], "change-required")
    blocked = finish(api, project, retained["work_item"], checkpoint_fields)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["context"] == {
        "missing_dispositions": 0, "untracked_changes": 1,
    }
    remediation = create_work(api, project, {
        **work_payload, "discovered_from_work_item_id": work_id,
    }, title="Reconcile the documented contradiction")["work_item"]
    assess(api, project, assessment_id, mention["reference"], "change-required", remediation["id"])
    completed = finish(api, project, retained["work_item"], checkpoint_fields)
    assert completed.status_code == 200, completed.text
    with postgres_engine.begin() as connection:
        assert connection.scalar(text(
            "SELECT candidates FROM decision_impact_assessments WHERE id=:id"
        ), {"id": assessment_id}) == original_snapshot
    assert api.get(endpoint + "/dispositions").json()["total"] == 3
    assert all(item["candidate_reference"] != prerequisite["reference"]
               for item in api.get(endpoint + "/dispositions").json()["items"])


def test_historical_coverage_without_warning_uses_retained_snapshot_not_live_scope(
    api, project, postgres_engine,
):
    target = domain(api, project)
    original = decision(api, project, target, "Legacy policy")
    changed(api, project, target, original)
    episode = assessments(api, project)[0]
    with postgres_engine.begin() as connection:
        coverage = connection.scalar(text(
            "SELECT coverage FROM decision_impact_assessments WHERE id=:id"
        ), {"id": episode["id"]})
        coverage.pop("warnings")
        connection.execute(text(
            "ALTER TABLE decision_impact_assessments DISABLE TRIGGER knowledge_immutable"
        ))
        connection.execute(text(
            "UPDATE decision_impact_assessments SET coverage=CAST(:coverage AS jsonb) WHERE id=:id"
        ), {"id": episode["id"], "coverage": json.dumps(coverage)})
        connection.execute(text(
            "ALTER TABLE decision_impact_assessments ENABLE TRIGGER knowledge_immutable"
        ))
    saved = api.post(path(project) + f"/decisions/{original['decision_id']}/scope", json={
        "revision": 0, "commit_hash": "b" * 40, "client_operation_id": str(uuid4()),
    })
    assert saved.status_code == 200, saved.text
    retained = api.get(path(project) + f"/assessments/{episode['id']}").json()
    assert retained["coverage"]["warnings"][0]["reference"] == original["reference"]
    assert not retained["candidates"][0]["scopes"]
