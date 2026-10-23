"""Decision dependencies and reassessments preserve exact historical evidence."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from mnemonic_api.impact_integrity import IMPACT_FINDINGS
from tests.test_knowledge_postgres import domain, path, put, put_body

pytestmark = pytest.mark.postgres


def decision(api, project, target, content, **changes):
    return put(api, project, put_body(target, content, **changes))["decision"]


def dependency(row, rationale="The implementation requires this earlier decision"):
    return {"decision_id": row["decision_id"], "revision": row["revision"], "rationale": rationale}


def changed(api, project, target, old, content="Amended decision", **changes):
    return decision(
        api,
        project,
        target,
        content,
        decision_id=old["decision_id"],
        expected_revision=old["revision"],
        **changes,
    )


def impact(api, project, reference, **filters):
    response = api.get(path(project) + f"/decisions/{reference}/impact", params=filters)
    assert response.status_code == 200, response.text
    return response.json()


def assessments(api, project):
    response = api.get(path(project) + "/assessments")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_exact_dependency_pins_cross_domains_and_transitive_paths(api, project):
    adr, bdr = domain(api, project), domain(api, project, "BDR", "Bookkeeping")
    first = decision(api, project, adr, "Foundational decision")
    second = decision(api, project, bdr, "Dependent decision", depends_on=[dependency(first)])
    third = decision(api, project, adr, "Indirect decision", depends_on=[dependency(second)])
    related = decision(
        api, project, adr, "Association only", related_decision_ids=[first["decision_id"]]
    )
    result = impact(api, project, first["reference"])
    assert [item["reference"] for item in result["items"]] == [
        first["reference"],
        second["reference"],
        third["reference"],
        related["reference"],
    ]
    assert result["items"][2]["path"] == [
        first["reference"],
        second["reference"],
        third["reference"],
    ]
    contextual = impact(api, project, first["reference"], kind="context")
    assert contextual["items"] == []
    assert result["items"][3]["relationship"] == "related-only"
    assert result["items"][3]["related_direction"] == "incoming"
    assert result["candidate_count"] == 4 and result["context_count"] == 0
    assert result["coverage"]["related_semantics"] == "unknown"
    assert result["coverage"]["traversal_complete"]
    assert not result["coverage"]["provenance_complete"]
    changed(api, project, adr, first)
    assert impact(api, project, first["decision_id"])["items"][0]["revision"] == 1
    historic = impact(api, project, first["reference"])
    assert historic["items"][0]["is_current"] is False
    assert any(item["reference"] == second["reference"] for item in historic["items"])


def test_impact_depth_and_pagination_are_explicit_and_stable(api, project):
    target = domain(api, project)
    first = decision(api, project, target, "Root")
    second = decision(api, project, target, "Child", depends_on=[dependency(first)])
    decision(api, project, target, "Grandchild", depends_on=[dependency(second)])
    limited = impact(api, project, first["reference"], max_depth=1)
    assert limited["total"] == 2
    assert limited["coverage"]["reasons"] == ["depth_limit"]
    assert limited["coverage"]["traversal_complete"] is False
    page = impact(api, project, first["reference"], limit=1)
    assert page["next_offset"] == 1 and page["total"] == 3
    assert (
        impact(api, project, first["reference"], limit=1, offset=1)["items"][0]["reference"]
        == second["reference"]
    )


def test_detailed_partial_supersession_pins_revision_and_preserves_legacy_links(api, project):
    target = domain(api, project)
    old = decision(api, project, target, "Original")
    details = [
        {
            "decision_id": old["decision_id"],
            "revision": 0,
            "extent": "partial",
            "reason": "Only the transport selection is replaced",
        }
    ]
    new = decision(api, project, target, "Replacement", supersession_details=details)
    assert new["supersession_details"] == details
    assert new["supersedes"] == [old["decision_id"]]
    episode = assessments(api, project)[0]
    assert (episode["trigger"], episode["before_reference"], episode["after_reference"]) == (
        "supersession",
        old["reference"],
        new["reference"],
    )
    assert episode["extent"] == "partial" and episode["reason"] == details[0]["reason"]
    legacy = decision(api, project, target, "Legacy replacement", supersedes=[new["decision_id"]])
    assert "supersession_details" not in legacy
    assert assessments(api, project)[0]["extent"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"depends_on": [{"decision_id": "ADR-00001", "revision": 99, "rationale": "Required"}]},
        {"depends_on": [{"decision_id": "ADR-00001", "revision": 0, "rationale": " "}]},
        {
            "supersession_details": [
                {"decision_id": "ADR-00001", "revision": 0, "extent": "partial", "reason": " "}
            ]
        },
    ],
)
def test_invalid_pinned_edges_fail_without_allocating_document(api, project, changes):
    target = domain(api, project)
    decision(api, project, target, "Original")
    response = api.put(path(project) + "/decisions", json=put_body(target, "Invalid", **changes))
    assert response.status_code in {404, 422}, response.text
    assert decision(api, project, target, "Valid")["ordinal"] == 2


def test_adding_dependency_to_same_bytes_revises_and_exact_repeat_is_noop(api, project):
    target = domain(api, project)
    first = decision(api, project, target, "Origin")
    second = decision(api, project, target, "Consumer")
    body = put_body(
        target,
        "Consumer",
        decision_id=second["decision_id"],
        expected_revision=0,
        depends_on=[dependency(first)],
    )
    new = put(api, project, body)
    assert new["created"] and new["decision"]["revision"] == 1
    assert put(api, project, body) == new
    body["client_operation_id"] = str(uuid4())
    assert put(api, project, body)["created"] is False
    assert len(assessments(api, project)) == 1


def test_pinned_annotation_update_noop_and_historical_reads_keep_original_author(api, project):
    target = domain(api, project)
    original = decision(api, project, target, "Original transport policy")
    details = [{"decision_id": original["decision_id"], "revision": 0,
                "extent": "partial", "reason": "Replace only the transport selection"}]
    annotations = {"depends_on": [dependency(original)], "supersession_details": details}
    initial = decision(api, project, target, "Replacement transport policy", **annotations)
    intent = put_body(target, "Amended replacement policy", **annotations,
                      decision_id=initial["decision_id"], expected_revision=0)
    revised = put(api, project, intent)["decision"]
    assert revised["revision"] == 1 and revised["supersedes"] == [original["decision_id"]]
    repeated = put(api, project, {**intent, "client_operation_id": str(uuid4()),
                                 "agent_session_id": "other-session", "model_id": "other-model"})
    assert repeated["created"] is False
    assert repeated["decision"] == revised
    stale = api.put(path(project) + "/decisions", json={
        **intent, "content": "Unreviewed stale change", "client_operation_id": str(uuid4()),
    })
    assert stale.status_code == 409, stale.text
    endpoint = path(project) + f"/decisions/{initial['decision_id']}"
    current = api.get(endpoint)
    assert current.status_code == 200, current.text
    assert current.json()["depends_on"] == annotations["depends_on"]
    assert current.json()["supersession_details"] == details
    historic = api.get(endpoint, params={"revision": 0})
    assert historic.status_code == 200, historic.text
    assert historic.json()["content"] == "Replacement transport policy"
    history = api.get(endpoint + "/history")
    assert history.status_code == 200, history.text
    assert [item["revision"] for item in history.json()["items"]] == [1, 0]
    assert all(item["supersession_details"] == details for item in history.json()["items"])


def test_assessment_snapshot_retains_before_after_and_does_not_change_with_later_evidence(
    api, project
):
    target = domain(api, project)
    first = decision(api, project, target, "Old policy")
    dependent = decision(api, project, target, "Consumer", depends_on=[dependency(first)])
    changed(api, project, target, first, "New policy")
    episode = assessments(api, project)[0]
    downstream = api.get(
        path(project) + "/assessments", params={"decision_id": dependent["decision_id"]}
    )
    assert downstream.status_code == 200, downstream.text
    assert downstream.json()["items"][0]["id"] == episode["id"]
    endpoint = path(project) + f"/assessments/{episode['id']}"
    original = api.get(endpoint).json()
    assert original["unassessed_count"] == 2 and original["status"] == "pending"
    assert original["coverage"]["snapshot_at"] == original["created_at"]
    assert "-Old policy" in original["diff"]["unified_diff"]
    assert "+New policy" in original["diff"]["unified_diff"]
    assert original["before_reference"] == first["reference"]
    assert original["candidate_count"] == 2
    scope = api.post(
        path(project) + f"/decisions/{dependent['decision_id']}/scope",
        json={
            "revision": 0,
            "commit_hash": "a" * 40,
            "repository": "github.com/acme/project",
            "client_operation_id": str(uuid4()),
        },
    )
    assert scope.status_code == 200, scope.text
    retained = api.get(endpoint).json()
    for key in ("before_author", "after_author"):
        retained[key].pop("work_observed_at")
        original[key].pop("work_observed_at")
    assert retained == original
    live = impact(api, project, first["reference"])
    assert live["items"][1]["scopes"][0]["commit_hash"] == "a" * 40
    assert live["items"][1]["scopes"][0]["repository"] == "github.com/acme/project"


def test_dispositions_are_versioned_receipted_and_keep_human_provenance(
    api, project, postgres_engine
):
    target = domain(api, project)
    first = decision(api, project, target, "Old")
    changed(api, project, target, first)
    episode = assessments(api, project)[0]
    endpoint = path(project) + f"/assessments/{episode['id']}/dispositions"
    body = {
        "candidate_reference": first["reference"],
        "expected_version": 0,
        "disposition": "change-required",
        "reason": "Implementation assumes the old policy",
        "actor": {"actor_client": "dashboard", "actor_session_id": "human-session"},
        "client_operation_id": str(uuid4()),
    }
    initial = api.post(endpoint, json=body)
    assert initial.status_code == 201, initial.text
    assert initial.json()["version"] == 1
    assert api.post(endpoint, json=body).json() == initial.json()
    assert api.post(endpoint, json={**body, "reason": "Changed request"}).status_code == 409
    assert api.post(endpoint, json={**body, "client_operation_id": str(uuid4())}).status_code == 409
    resolved = api.post(
        endpoint,
        json={
            **body,
            "client_operation_id": str(uuid4()),
            "expected_version": 1,
            "disposition": "resolved",
            "reason": "Implementation corrected",
        },
    )
    assert resolved.status_code == 201, resolved.text
    history = api.get(endpoint).json()
    assert [row["disposition"] for row in history["items"]] == ["resolved", "change-required"]
    detail = api.get(path(project) + f"/assessments/{episode['id']}").json()
    assert detail["version"] == detail["disposition_history_total"] == 2
    assert detail["unassessed_count"] == detail["change_required_count"] == 0
    assert detail["status"] == "resolved"
    assert (
        len(detail["dispositions"]) == 1 and detail["dispositions"][0]["disposition"] == "resolved"
    )
    with postgres_engine.connect() as connection:
        findings = {key: connection.scalar(text(sql)) for key, sql in IMPACT_FINDINGS.items()}
    assert all(value == 0 for value in findings.values()), findings


def test_disposition_remediation_is_project_owned_and_move_returns_structured_conflict(
    api, project, work_payload,
):
    target = domain(api, project)
    first = decision(api, project, target, "Original policy")
    changed(api, project, target, first)
    episode = assessments(api, project)[0]
    other = api.post("/api/v1/projects", json={"name": "Other remediation project"}).json()
    works = []
    for owner in (project, other):
        created = api.post(f"/api/v1/projects/{owner['id']}/work-items", json=work_payload)
        assert created.status_code == 201, created.text
        works.append(created.json()["work_item"])
    work, foreign = works
    endpoint = path(project) + f"/assessments/{episode['id']}/dispositions"
    body = {
        "candidate_reference": first["reference"], "expected_version": 0,
        "disposition": "change-required", "reason": "Update the affected implementation",
        "actor": {"actor_client": "dashboard", "actor_session_id": "move-regression"},
    }
    invalid = api.post(endpoint, json={
        **body, "remediation_work_item_id": foreign["id"], "client_operation_id": str(uuid4()),
    })
    assert invalid.status_code == 404, invalid.text
    saved = api.post(endpoint, json={
        **body, "remediation_work_item_id": work["id"], "client_operation_id": str(uuid4()),
    })
    assert saved.status_code == 201, saved.text
    work_path = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    moved = api.post(work_path + "/move", json={
        "target_project_id": other["id"], "expected_version": work["version"],
        "actor": body["actor"], "client_operation_id": str(uuid4()),
    })
    assert moved.status_code == 409, moved.text
    assert moved.json()["detail"]["code"] == "work_has_knowledge_links"
    assert "cannot change projects" in moved.json()["detail"]["message"]
    assert api.get(work_path).json()["work_item"] == work
    assert api.get(endpoint).json()["items"] == [saved.json()]


@pytest.mark.parametrize(
    "table",
    [
        "decision_dependencies",
        "decision_supersession_details",
        "decision_impact_assessments",
        "decision_impact_dispositions",
    ],
)
def test_impact_tables_refuse_truncate(api, project, postgres_engine, table):
    with postgres_engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(text(f"TRUNCATE {table} CASCADE"))


def test_dependency_links_are_sealed_after_creation_transaction(api, project, postgres_engine):
    target = domain(api, project)
    first = decision(api, project, target, "Root")
    second = decision(api, project, target, "Consumer")
    with postgres_engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("""
            INSERT INTO decision_dependencies(project_id,revision_id,target_revision_id,rationale)
            SELECT :project,source.id,target.id,'Late mutation' FROM decision_revisions source,
                decision_revisions target WHERE source.document_id=:source
                AND target.document_id=:target
        """),
            {"project": project["id"], "source": second["id"], "target": first["id"]},
        )


def test_outgoing_related_links_use_the_selected_source_revision(api, project):
    target = domain(api, project)
    other = decision(api, project, target, "Other")
    first = decision(api, project, target, "Old", related_decision_ids=[other["decision_id"]])
    changed(api, project, target, first)
    assert impact(api, project, first["decision_id"])["total"] == 1
    assert impact(api, project, first["reference"])["total"] == 1
    assert impact(api, project, first["reference"], kind="context")["total"] == 1


def test_document_level_dependency_cycle_still_follows_finite_revision_pins(api, project):
    target = domain(api, project)
    first = decision(api, project, target, "A original")
    second = decision(api, project, target, "B depends A", depends_on=[dependency(first)])
    amended = changed(api, project, target, first, depends_on=[dependency(second)])
    refs = [item["reference"] for item in impact(api, project, first["reference"])["items"]]
    assert refs == [first["reference"], second["reference"], amended["reference"]]


@pytest.mark.parametrize(
    "forgery,finding",
    [
        ("path", "invalid_decision_impact_paths"),
        ("scope", "invalid_decision_impact_scope_evidence"),
        ("work", "invalid_decision_impact_resource_evidence"),
        ("artifact", "invalid_decision_impact_resource_evidence"),
    ],
)
def test_restore_audit_rejects_forged_snapshot_evidence(
    api, project, postgres_engine, forgery, finding
):
    target = domain(api, project)
    first = decision(api, project, target, "Root")
    dependent = decision(api, project, target, "Consumer", depends_on=[dependency(first)])
    unrelated = decision(api, project, target, "Unrelated")
    response = api.post(
        path(project) + f"/decisions/{dependent['decision_id']}/scope",
        json={"revision": 0, "commit_hash": "b" * 40, "client_operation_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    changed(api, project, target, first)
    episode = assessments(api, project)[0]
    with postgres_engine.begin() as connection:
        findings = {key: connection.scalar(text(sql)) for key, sql in IMPACT_FINDINGS.items()}
        assert all(value == 0 for value in findings.values()), findings
        candidates = connection.scalar(
            text("SELECT candidates FROM decision_impact_assessments WHERE id=:id"),
            {"id": episode["id"]},
        )
        selected = next(item for item in candidates if item["reference"] == dependent["reference"])
        if forgery == "path":
            selected["path"].insert(1, unrelated["reference"])
        elif forgery == "scope":
            selected["scopes"][0]["commit_hash"] = "c" * 40
        else:
            selected["related_work_item_ids" if forgery == "work" else "related_artifact_ids"] = [
                str(uuid4())
            ]
        connection.execute(
            text("ALTER TABLE decision_impact_assessments DISABLE TRIGGER knowledge_immutable")
        )
        connection.execute(
            text(
                "UPDATE decision_impact_assessments SET candidates=CAST(:value AS jsonb) "
                "WHERE id=:id"
            ),
            {"id": episode["id"], "value": json.dumps(candidates)},
        )
        connection.execute(
            text("ALTER TABLE decision_impact_assessments ENABLE TRIGGER knowledge_immutable")
        )
        assert connection.scalar(text(IMPACT_FINDINGS[finding])) == 1


def test_restore_audit_binds_dependency_rationale_to_creation_receipt(
    api, project, postgres_engine
):
    target = domain(api, project)
    first = decision(api, project, target, "Origin")
    decision(api, project, target, "Consumer", depends_on=[dependency(first)])
    with postgres_engine.begin() as connection:
        assert connection.scalar(text(IMPACT_FINDINGS["invalid_decision_pinned_receipts"])) == 0
        connection.execute(
            text("ALTER TABLE decision_dependencies DISABLE TRIGGER knowledge_immutable")
        )
        connection.execute(text("UPDATE decision_dependencies SET rationale='Forged explanation'"))
        connection.execute(
            text("ALTER TABLE decision_dependencies ENABLE TRIGGER knowledge_immutable")
        )
        assert connection.scalar(text(IMPACT_FINDINGS["invalid_decision_pinned_receipts"])) == 1
