"""Supersession reads compare replacement revisions and preserve historical snapshots."""

from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mnemonic_api.models import DecisionImpactAssessment, DecisionRevision
from mnemonic_api.services.supersession_delta import supersession_delta

from .test_decision_impact_postgres import assessments, changed, decision
from .test_knowledge_postgres import domain, path

pytestmark = pytest.mark.postgres


def setup(api, project, *, detailed=True):
    target = domain(api, project)
    old = decision(api, project, target, "Original rule has long explanatory prose.\n" * 100)
    detail = {"decision_id": old["decision_id"], "revision": old["revision"],
              "extent": "partial", "reason": "Replace the queue ordering requirement."}
    links = {"supersession_details": [detail]} if detailed else {"supersedes": [old["decision_id"]]}
    replacement = decision(api, project, target,
        f"# Replacement\n## Supersedes\n{old['reference']}: only queue ordering.\n## Rule\nUse X.",
        **links)
    return target, old, replacement, links


def get_episode(api, project, replacement):
    result, = [item for item in assessments(api, project) if item["trigger"] == "supersession"
               and item["after_reference"] == replacement["reference"]]
    detail = api.get(path(project) + f"/assessments/{result['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["supersession_delta"] == result["supersession_delta"]
    return result


def stored(engine, identity):
    with engine.connect() as connection:
        return dict(connection.execute(text(
            "SELECT * FROM decision_impact_assessments WHERE id=:id"
        ), {"id": identity}).mappings().one())


@pytest.mark.parametrize("detailed", [False, True])
def test_new_supersession_projects_evidence_and_missing_metadata_warning_without_rewriting(
    api, project, postgres_engine, detailed,
):
    _, old, replacement, _ = setup(api, project, detailed=detailed)
    episode = get_episode(api, project, replacement)
    captured = stored(postgres_engine, episode["id"])
    delta = episode["supersession_delta"]
    assert delta["target_reference"] == old["reference"]
    assert delta["before_reference"] is None
    assert delta["after_reference"] == replacement["reference"]
    assert delta["kind"] == "new-supersession" and delta["metadata_changed"]
    assert not delta["section_before"]["found"] and delta["section_after"]["found"]
    assert "only queue ordering" in delta["unified_diff"]
    assert "Use X" not in delta["unified_diff"]
    assert "Original rule" in episode["diff"]["unified_diff"]
    warning = [row for row in episode["coverage"]["warnings"]
               if row["code"] == "supersession_extent_reason_unknown"]
    assert bool(warning) == (not detailed)
    if warning:
        assert warning[0]["reference"] == old["reference"]
    assert not any(row["code"] == "supersession_extent_reason_unknown"
                   for row in captured["coverage"].get("warnings", []))
    get_episode(api, project, replacement)
    assert stored(postgres_engine, episode["id"]) == captured


def test_supersession_delta_compares_previous_replacement_and_exact_target_detail(api, project):
    target, old, replacement, links = setup(api, project)
    next_target = changed(api, project, target, old, "Revised original target rule")
    links["supersession_details"][0].update(revision=1, extent="full", reason="Replace all scope.")
    revised = changed(api, project, target, replacement,
        f"# Replacement\n## Supersedes\n{next_target['reference']}: all scope.\n## Rule\nUse Y.",
        **links)
    delta = get_episode(api, project, revised)["supersession_delta"]
    assert delta["before_reference"] == replacement["reference"]
    assert delta["after_reference"] == revised["reference"]
    assert delta["target_reference"] == next_target["reference"]
    assert delta["kind"] == "continued-supersession"
    assert delta["details_before"]["revision"] == 0
    assert delta["details_before"]["extent"] == "partial"
    assert delta["details_after"] == links["supersession_details"][0]
    assert delta["metadata_changed"]
    assert "-" + old["reference"] in delta["unified_diff"]
    assert "+" + next_target["reference"] in delta["unified_diff"]
    assert "Use Y" not in delta["unified_diff"]


def test_unrelated_annotation_keeps_empty_supersession_delta_and_full_diff_compatible(api, project):
    target, old, replacement, links = setup(api, project)
    revised = changed(api, project, target, replacement,
        f"# Replacement\n## Supersedes\n{old['reference']}: only queue ordering.\n## Rule\nUse X.\n"
        "## References\nAn unrelated citation.", **links)
    episode = get_episode(api, project, revised)
    delta = episode["supersession_delta"]
    assert not delta["metadata_changed"] and delta["unified_diff"] == ""
    assert episode["diff"]["unified_diff"]
    revision_episode, = [item for item in assessments(api, project)
                         if item["trigger"] == "revision"]
    assert "supersession_delta" not in revision_episode


def test_compact_projection_omits_prose_but_retains_identity_details_and_bounds(
    api, project, postgres_engine,
):
    _, _, replacement, _ = setup(api, project)
    episode = get_episode(api, project, replacement)
    captured = stored(postgres_engine, episode["id"])
    with Session(postgres_engine) as database:
        row = database.get(DecisionImpactAssessment, UUID(episode["id"]))
        revisions = {item.id: item for item in database.scalars(select(DecisionRevision).where(
            DecisionRevision.id.in_([row.origin_revision_id, row.replacement_revision_id]),
        ))}
        compact = supersession_delta(database, row, revisions[row.origin_revision_id],
            revisions[row.replacement_revision_id], episode["before_reference"],
            episode["after_reference"], include_diff=False).model_dump(mode="json")
    assert "unified_diff" not in compact
    assert "text" not in compact["section_before"] and "text" not in compact["section_after"]
    full = episode["supersession_delta"]
    for key in ("target_reference", "before_reference", "after_reference", "details_before",
                "details_after", "metadata_changed", "section_scan_limit", "section_byte_limit",
                "diff_byte_limit", "truncated"):
        assert compact[key] == full[key]
    query = {"detail": "compact", "include_diff": False, "include_scopes": "none"}
    base = path(project) + "/assessments"
    for endpoint in (base, base + f"/{episode['id']}"):
        response = api.get(endpoint, params=query)
        assert response.status_code == 200, response.text
        result = response.json()
        projected = (next(item for item in result["items"] if item["id"] == episode["id"])
                     if endpoint == base else result)
        assert projected["supersession_delta"] == compact
        assert "unified_diff" not in projected["diff"]
    assert stored(postgres_engine, episode["id"]) == captured
