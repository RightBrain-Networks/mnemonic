"""Restating a supersession keeps its original obligation only while evidence matches."""

from uuid import uuid4

import pytest

from .test_decision_changes_postgres import group
from .test_decision_impact_postgres import assessments, changed, decision
from .test_decision_reviews_postgres import assess
from .test_knowledge_postgres import domain, path, put, put_body
from .test_project_activity_audit_postgres import _audit

pytestmark = pytest.mark.postgres


def annotations(old, detailed):
    if detailed:
        return {"supersession_details": [{"decision_id": old["decision_id"], "revision": 0,
                "extent": "full", "reason": "Replace the previous rule."}]}
    return {"supersedes": [old["decision_id"]]}


def declaration(governing, kind, unchanged=()):
    values = {"unchanged_supersessions": list(unchanged)}
    if kind == "substantive":
        return {"kind": kind, **values}
    return {"kind": kind, "change_id": governing["change"]["change_id"],
            "governing_reference": governing["reference"],
            "reason": "Record the ruling while retaining an earlier supersession.", **values}


def unchanged(old, replacement):
    return {"target_reference": old["reference"],
            "governing_reference": replacement["reference"],
            "reason": "This target's supersession ruling is unchanged by the unrelated annotation."}


def setup_supersession(api, project, detailed=True):
    target = domain(api, project)
    old = decision(api, project, target, "Earlier rule")
    links = annotations(old, detailed)
    replacement = decision(api, project, target, "Replacement rule", **links)
    original, = assessments(api, project)
    return target, old, links, replacement, original


@pytest.mark.parametrize("kind", ["record-existing", "substantive"])
@pytest.mark.parametrize("detailed", [False, True])
def test_restatement_reuses_original_episode_across_changes(
    api, project, postgres_engine, kind, detailed,
):
    target, old, links, replacement, original = setup_supersession(api, project, detailed)
    assess(api, project, original["id"], old["reference"])
    original_detail = api.get(path(project) + f"/assessments/{original['id']}").json()
    governing = decision(api, project, target, "A different ruling governing the new annotation")
    # Neither the declared governing document nor the new substantive change owns
    # the original supersession. Its own replacement document does.
    body = put_body(target, "Replacement with an annotation from another ruling",
                    decision_id=replacement["decision_id"], expected_revision=0,
                    change=declaration(governing, kind, [unchanged(old, replacement)]), **links)
    amended = put(api, project, body)
    assert amended["decision"]["change"]["unchanged_supersessions"] == [unchanged(old, replacement)]
    assert put(api, project, body) == amended
    episodes = assessments(api, project)
    assert len(episodes) == 2
    assert [row["id"] for row in episodes if row["trigger"] == "supersession"] == [original["id"]]
    change_id = amended["decision"]["change"]["change_id"]
    linked = api.get(path(project) + "/assessments", params={"change_id": change_id}).json()
    assert {row["id"] for row in linked["items"]} == {row["id"] for row in episodes}
    assert group(api, project, change_id)["scope_version"] == 2
    retained = api.get(path(project) + f"/assessments/{original['id']}").json()
    for key in ("before_reference", "after_reference", "diff", "candidates", "coverage",
                "dispositions", "version"):
        assert retained[key] == original_detail[key]
    assert _audit(postgres_engine)["result"] == "pass"


def test_changed_extent_recaptures_only_its_target(api, project):
    target, old, links, replacement, original = setup_supersession(api, project)
    other = decision(api, project, target, "Another rule")
    details = [*links["supersession_details"], {
        "decision_id": other["decision_id"], "revision": 0,
        "extent": "full", "reason": "Replace the other rule.",
    }]
    replacement = changed(api, project, target, replacement, "Replacement of both rules",
                          supersession_details=details, change={
        "unchanged_supersessions": [unchanged(old, replacement)],
    })
    other_episode = next(row for row in assessments(api, project)
                         if row["before_reference"] == other["reference"])
    details[1] = {**details[1], "extent": "partial"}
    amended = changed(api, project, target, replacement, "Only replace part of the other rule",
                      supersession_details=details, change={"unchanged_supersessions": [{
        "target_reference": old["reference"], "governing_reference": original["after_reference"],
        "reason": "The first target's supersession ruling has not changed.",
    }]})
    episodes = assessments(api, project)
    first_target = [row for row in episodes if row["trigger"] == "supersession"
                    and row["before_reference"] == old["reference"]]
    assert [row["id"] for row in first_target] == [original["id"]]
    second_target = [row for row in episodes if row["trigger"] == "supersession"
                     and row["before_reference"] == other["reference"]]
    assert len(second_target) == 2
    assert {row["id"] for row in second_target} > {other_episode["id"]}
    assert second_target[0]["after_reference"] == amended["reference"]
    assert second_target[0]["extent"] == "partial"


@pytest.mark.parametrize("kind", ["record-existing", "substantive"])
@pytest.mark.parametrize("mutation", ["scope", "incoming-candidate", "coverage"])
def test_changed_evidence_captures_fresh_episode_without_copying_dispositions(
    api, project, monkeypatch, kind, mutation,
):
    target, old, links, replacement, original = setup_supersession(api, project)
    assess(api, project, original["id"], old["reference"])
    governing = decision(api, project, target, "Other ruling")
    if mutation == "scope":
        response = api.post(path(project) + f"/decisions/{old['decision_id']}/scope", json={
            "revision": 0, "commit_hash": "a" * 40, "repository": "example/repo",
            "client_operation_id": str(uuid4()),
        })
        assert response.status_code == 200, response.text
    elif mutation == "incoming-candidate":
        decision(api, project, target, "New citing record",
                 related_decision_ids=[old["decision_id"]])
    else:
        monkeypatch.setattr("mnemonic_api.services.decision_impact_graph.MAX_CANDIDATES", 499)
    amended = changed(api, project, target, replacement, "Restated replacement",
                      change=declaration(governing, kind, [unchanged(old, replacement)]), **links)
    episodes = [row for row in assessments(api, project) if row["trigger"] == "supersession"]
    assert len(episodes) == 2
    fresh = next(row for row in episodes if row["id"] != original["id"])
    assert fresh["after_reference"] == amended["reference"]
    assert fresh["version"] == 0 and fresh["unassessed_count"] > 0
    assert api.get(path(project) + f"/assessments/{original['id']}").json()["version"] == 1


@pytest.mark.parametrize("interrupt", ["removed", "extent", "reason", "pin"])
def test_interrupted_declaration_cannot_reuse_older_episode(api, project, interrupt):
    target, old, links, replacement, original = setup_supersession(api, project)
    if interrupt == "removed":
        intermediate = {}
    else:
        detail = dict(links["supersession_details"][0])
        if interrupt == "pin":
            changed(api, project, target, old, "Revised earlier rule")
            detail["revision"] = 1
        else:
            detail[interrupt] = "partial" if interrupt == "extent" else "Different replaced scope."
        intermediate = {"supersession_details": [detail]}
    # Maintenance does not capture an episode, but still breaks continuity.
    maintained = changed(api, project, target, replacement, "Intermediate metadata",
                         change={"kind": "maintenance", "reason": "Historical correction."},
                         **intermediate)
    amended = changed(api, project, target, maintained, "Restore the original declaration", **links)
    fresh = next(row for row in assessments(api, project)
                 if row["trigger"] == "supersession" and row["id"] != original["id"])
    assert fresh["after_reference"] == amended["reference"]
    assert fresh["version"] == 0


def test_maintenance_with_unchanged_declaration_preserves_reuse(api, project):
    target, old, links, replacement, original = setup_supersession(api, project)
    maintained = changed(api, project, target, replacement, "Replacement with formatting note",
                         change={"kind": "maintenance", "reason": "Normalize formatting."}, **links)
    changed(api, project, target, maintained, "Replacement with new unrelated text", **links,
            change={"unchanged_supersessions": [unchanged(old, replacement)]})
    supersessions = [row for row in assessments(api, project) if row["trigger"] == "supersession"]
    assert [row["id"] for row in supersessions] == [original["id"]]


def test_unpinned_target_revision_change_requires_new_episode(api, project):
    target, old, links, replacement, original = setup_supersession(api, project, detailed=False)
    latest = changed(api, project, target, old, "The superseded document has another revision")
    amended = changed(api, project, target, replacement, "Restated unpinned supersession", **links)
    fresh = next(row for row in assessments(api, project)
                 if row["trigger"] == "supersession" and row["id"] != original["id"])
    assert fresh["before_reference"] == latest["reference"]
    assert fresh["after_reference"] == amended["reference"]


@pytest.mark.parametrize("kind", ["substantive", "record-existing"])
def test_changed_rule_without_explicit_reuse_does_not_infer_semantics_from_metadata(
    api, project, kind,
):
    target, old, links, replacement, original = setup_supersession(api, project)
    assess(api, project, original["id"], old["reference"])
    governing = decision(api, project, target, "An independently governed replacement policy")
    amended = changed(api, project, target, replacement,
                      "Replace the old rule with Y instead of X, retaining its declared target",
                      change=declaration(governing, kind), **links)
    fresh = next(row for row in assessments(api, project)
                 if row["trigger"] == "supersession" and row["id"] != original["id"])
    assert fresh["after_reference"] == amended["reference"]
    assert fresh["version"] == 0 and fresh["unassessed_count"] > 0


@pytest.mark.parametrize("damage", ["governing-document", "target-revision", "removed", "extent"])
def test_invalid_explicit_declaration_rolls_back_revision(api, project, damage):
    target, old, links, replacement, _ = setup_supersession(api, project)
    entry = unchanged(old, replacement)
    if damage == "governing-document":
        entry["governing_reference"] = old["reference"]
    elif damage == "target-revision":
        revised = changed(api, project, target, old, "Another target revision")
        entry["target_reference"] = revised["reference"]
    elif damage == "removed":
        replacement = changed(api, project, target, replacement, "Temporarily removed supersession",
                              change={"kind": "maintenance", "reason": "Historical correction."})
    else:
        links["supersession_details"][0]["extent"] = "partial"
    response = api.put(path(project) + "/decisions", json=put_body(
        target, "Invalid unchanged assertion", decision_id=replacement["decision_id"],
        expected_revision=replacement["revision"], change={"unchanged_supersessions": [entry]},
        **links,
    ))
    assert response.status_code == 422 and "decision_supersession_reuse_invalid" in response.text
    retained = api.get(path(project) + f"/decisions/{replacement['decision_id']}").json()
    assert retained["revision"] == replacement["revision"]
