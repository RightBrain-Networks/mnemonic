"""Restated supersessions retain new evidence and pin only inspected unchanged rows."""

import io
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from mnemonic_api.candidate_reuse_integrity import CANDIDATE_REUSE_FINDINGS
from mnemonic_backup.archive import BackupError, restore_project

from .test_decision_impact_postgres import assessments, changed, decision
from .test_decision_reviews_postgres import assess
from .test_knowledge_postgres import domain, path
from .test_project_backup_archive import _export, _rewrite, _snapshot
from .test_supersession_reuse_postgres import annotations, unchanged

pytestmark = pytest.mark.postgres


def detail(api, project, episode, **params):
    result = api.get(path(project) + f"/assessments/{episode['id']}", params=params)
    assert result.status_code == 200, result.text
    return result.json()


def setup(api, project, *, inspected=True):
    target = domain(api, project)
    old = decision(api, project, target, "Earlier rule")
    links = {**annotations(old, True), "related_decision_ids": [old["decision_id"]]}
    replacement = decision(api, project, target, "Superseding rule with incoming citation", **links)
    original, = assessments(api, project)
    if inspected:
        for candidate in detail(api, project, original)["candidates"]:
            assess(api, project, original["id"], candidate["reference"])
    return target, old, links, replacement, original


def restate(api, project, target, old, links, replacement):
    revised = changed(
        api, project, target, replacement,
        f"Same ruling, provenance annotation {replacement['revision'] + 1}",
        **links, change={"unchanged_supersessions": [unchanged(old, replacement)]},
    )
    fresh = next(row for row in assessments(api, project)
                 if row["trigger"] == "supersession"
                 and row["after_reference"] == revised["reference"])
    return revised, fresh


def test_self_citing_revision_keeps_rows_and_reuses_only_reviewed_evidence(
    api, project, postgres_engine,
):
    target, old, links, replacement, original = setup(api, project)
    previous = detail(api, project, original)
    revised, fresh = restate(api, project, target, old, links, replacement)
    current = detail(api, project, fresh)
    assert current["candidate_count"] == 3
    assert current["reused_candidate_count"] == 2 and current["unassessed_count"] == 1
    assert current["version"] == 0 and current["disposition_history_total"] == 0
    assert {row["reference"] for row in current["candidates"]} == {
        old["reference"], replacement["reference"], revised["reference"],
    }
    for row in current["dispositions"]:
        source = next(item for item in previous["dispositions"]
                      if item["candidate_reference"] == row["candidate_reference"])
        assert row == {**source, "reused_from": {
            "assessment_id": original["id"], "disposition_id": source["id"],
            "disposition_version": source["version"],
        }}
    retained = detail(api, project, original)
    for key in ("candidates", "coverage", "diff", "dispositions", "version"):
        assert retained[key] == previous[key]
    compact = detail(api, project, fresh, detail="compact", include_diff=False)
    assert all("reused_from" in row and "reason" not in row for row in compact["dispositions"])
    change_id = revised["change"]["change_id"]
    grouped = api.get(path(project) + f"/changes/{change_id}/candidates").json()
    occurrence = next(row for group in grouped["items"] for row in group["occurrences"]
                      if row["assessment_id"] == fresh["id"]
                      and row["candidate_reference"] == old["reference"])
    assert occurrence["assessment_version"] == 0
    assert occurrence["latest_disposition"]["reused_from"]["assessment_id"] == original["id"]
    with postgres_engine.connect() as connection:
        assert all(connection.scalar(text(sql)) == 0 for sql in CANDIDATE_REUSE_FINDINGS.values())


def test_unreviewed_prior_rows_remain_required(api, project):
    target, old, links, replacement, original = setup(api, project, inspected=False)
    assess(api, project, original["id"], old["reference"])
    _, fresh = restate(api, project, target, old, links, replacement)
    current = detail(api, project, fresh)
    assert current["reused_candidate_count"] == 1 and current["unassessed_count"] == 2


def test_current_override_and_source_correction_do_not_rewrite_pins(api, project):
    target, old, links, replacement, original = setup(api, project)
    revised, fresh = restate(api, project, target, old, links, replacement)
    pinned = detail(api, project, fresh)["dispositions"]
    assess(api, project, original["id"], old["reference"], "not-affected")
    assert detail(api, project, fresh)["dispositions"] == pinned
    assess(api, project, fresh["id"], old["reference"], "not-affected")
    current = detail(api, project, fresh)
    assert current["reused_candidate_count"] == 1 and current["version"] == 1
    local = next(row for row in current["dispositions"]
                 if row["candidate_reference"] == old["reference"])
    assert "reused_from" not in local and local["assessment_id"] == fresh["id"]
    _, newest = restate(api, project, target, old, links, revised)
    rows = detail(api, project, newest)["dispositions"]
    assert {row["reused_from"]["assessment_id"] for row in rows} == {original["id"], fresh["id"]}


def test_references_are_immutable_and_backup_roundtrips(api, project, postgres_engine):
    target, old, links, replacement, _ = setup(api, project)
    _, fresh = restate(api, project, target, old, links, replacement)
    expected = detail(api, project, fresh)
    backup = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(backup))
    restored = detail(api, project, fresh)
    for key in ("candidates", "dispositions", "version", "reused_candidate_count"):
        assert restored[key] == expected[key]
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text("DELETE FROM decision_candidate_reuses"))
    before = _snapshot(postgres_engine, project)

    def corrupt(_header, rows):
        rows["decision_candidate_reuses"][0]["candidate_reference"] = "ADR-99999-0"

    with pytest.raises(BackupError):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(backup, corrupt)))
    assert _snapshot(postgres_engine, project) == before


def test_changed_candidate_scope_requires_review_without_invalidating_other_rows(api, project):
    from uuid import uuid4

    target, old, links, replacement, _ = setup(api, project)
    result = api.post(path(project) + f"/decisions/{replacement['decision_id']}/scope", json={
        "revision": 0, "commit_hash": "a" * 40, "repository": "example/repo",
        "client_operation_id": str(uuid4()),
    })
    assert result.status_code == 200, result.text
    _, fresh = restate(api, project, target, old, links, replacement)
    current = detail(api, project, fresh)
    assert current["reused_candidate_count"] == 1 and current["unassessed_count"] == 2
    assert current["dispositions"][0]["candidate_reference"] == old["reference"]


@pytest.mark.parametrize("damage", ["coverage", "candidate", "unknown"])
def test_incomplete_or_unknown_evidence_keeps_required_review(api, project, monkeypatch, damage):
    from mnemonic_api.services import decision_impact

    target, old, links, replacement, _ = setup(api, project)
    if damage == "candidate":
        original = decision_impact.candidate

        def incomplete(*args):
            result = original(*args)
            result.evidence_truncated = True
            return result

        monkeypatch.setattr(decision_impact, "candidate", incomplete)
    else:
        original_episode = decision_impact._episode

        def incomplete_episode(*args, **kwargs):
            result = original_episode(*args, **kwargs)
            if damage == "unknown":
                result.coverage.pop("path_policy")
            else:
                result.coverage["traversal_complete"] = False
                result.coverage["reasons"] = ["candidate_limit"]
            return result

        monkeypatch.setattr(decision_impact, "_episode", incomplete_episode)
    _, fresh = restate(api, project, target, old, links, replacement)
    current = detail(api, project, fresh)
    assert "reused_candidate_count" not in current and current["unassessed_count"] == 3


def test_reused_change_required_conclusion_preserves_completion_guard_and_findings(
    api, project, work_payload, postgres_engine,
):
    from sqlalchemy.orm import Session

    from mnemonic_api.errors import ApplicationError
    from mnemonic_api.models import WorkItem
    from mnemonic_api.services.decision_reviews import require_decision_impact_review_complete

    from .test_decision_changes_postgres import group, update
    from .test_decision_reviews_postgres import settings
    from .test_work_items_postgres import create_work

    settings(api, project, automatic_decision_reviews=True)
    target, old, links, replacement, original = setup(api, project)
    assess(api, project, original["id"], old["reference"], "change-required")
    revised, fresh = restate(api, project, target, old, links, replacement)
    change_id = revised["change"]["change_id"]
    change = group(api, project, change_id)
    assert change["finding_summary"]["unlabelled_change_required_count"] == 1
    episodes = api.get(path(project) + "/assessments", params={"change_id": change_id}).json()
    for episode in episodes["items"]:
        current = detail(api, project, episode)
        reviewed = {row["candidate_reference"] for row in current["dispositions"]}
        for candidate in current["candidates"]:
            if candidate["reference"] not in reviewed:
                assess(api, project, episode["id"], candidate["reference"])
    acknowledged, _ = update(api, project, "acknowledge", change_id=change_id,
                            expected_scope_version=change["scope_version"])
    assert acknowledged.status_code == 200, acknowledged.text

    def guard():
        with Session(postgres_engine) as database:
            work = database.get(WorkItem, UUID(change["review_work_item_id"]))
            require_decision_impact_review_complete(database, work)

    with pytest.raises(ApplicationError) as failure:
        guard()
    assert failure.value.detail["context"]["untracked_changes"] == 1
    remediation = create_work(api, project, work_payload)["work_item"]
    assess(api, project, fresh["id"], old["reference"], "change-required", remediation["id"])
    guard()
    assert detail(api, project, fresh)["reused_candidate_count"] == 1


def test_assessment_capture_and_reuse_enrollment_are_sealed(api, project, postgres_engine):
    target, old, links, replacement, original = setup(api, project, inspected=False)
    _, fresh = restate(api, project, target, old, links, replacement)
    # A conclusion written after capture cannot be injected into that historical episode.
    assess(api, project, original["id"], old["reference"])
    source = detail(api, project, original)["dispositions"][0]
    assess(api, project, fresh["id"], replacement["reference"])
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO decision_candidate_reuses
                (project_id,assessment_id,candidate_reference,source_disposition_id)
            VALUES (:project,:assessment,:reference,:source)
        """), {"project": project["id"], "assessment": fresh["id"],
               "reference": old["reference"], "source": source["id"]})
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text("UPDATE decision_impact_assessments SET diff=diff WHERE id=:id"),
                           {"id": fresh["id"]})


def test_legacy_answer_recovery_enrolls_reuse_in_new_assessment_transaction(
    api, project, work_payload, checkpoint_fields, monkeypatch, postgres_engine,
):
    from sqlalchemy.orm import Session

    from mnemonic_api.models import DecisionRevisionChange
    from mnemonic_api.services import decision_impact
    from mnemonic_api.services.decision_change_recovery import recover_held_answers
    from mnemonic_api.services.decision_supersession_declarations import record_declarations

    from .test_decision_changes_postgres import group, retire
    from .test_decision_reviews_postgres import context, settings
    from .test_work_items_postgres import create_work

    settings(api, project, automatic_decision_reviews=True)
    target, old, links, replacement, original = setup(api, project)
    change_id = replacement["change"]["change_id"]
    review_id = group(api, project, change_id)["review_work_item_id"]
    work = context(api, project, review_id)["work_item"]
    child = create_work(api, project, {**work_payload, "discovered_from_work_item_id": review_id})[
        "work_item"
    ]
    retire(api, project, work, checkpoint_fields)
    def hold(database, document, revision, payload, **kwargs):
        database.add(DecisionRevisionChange(
            revision_id=revision.id, change_id=UUID(change_id), kind="substantive",
            source_work_item_id=UUID(child["id"]), requires_admission=True,
        ))
        record_declarations(database, revision, payload)
        database.flush()

    # Exercise the held-revision service path used by retained owner decisions.
    with monkeypatch.context() as scoped:
        scoped.setattr(decision_impact, "create_assessments", hold)
        revised = changed(
            api, project, target, replacement, "Retained owner clarification", **links,
            source_work_item_id=child["id"],
            change={"unchanged_supersessions": [unchanged(old, replacement)]},
        )
    assert revised["change"]["requires_admission"]
    assess(api, project, original["id"], old["reference"], "not-affected")
    latest_source = next(row for row in detail(api, project, original)["dispositions"]
                         if row["candidate_reference"] == old["reference"])
    with Session(postgres_engine, info={"prompt_root": api.app.state.settings.prompt_root}) as db:
        assert recover_held_answers(db) == 1
    fresh = next(row for row in assessments(api, project)
                 if row["trigger"] == "supersession"
                 and row["after_reference"] == revised["reference"])
    current = detail(api, project, fresh)
    assert current["reused_candidate_count"] == 2
    reused = next(row for row in current["dispositions"]
                  if row["candidate_reference"] == old["reference"])
    assert reused["reused_from"]["disposition_id"] == latest_source["id"]


def test_migration_is_additive_and_refuses_to_discard_reuse_history(api, project, postgres_engine):
    from .test_product_decision_migration_postgres import migrate
    from .test_work_inheritance_migration_postgres import _retained_rows

    target, old, links, replacement, _ = setup(api, project)
    migrate(postgres_engine, "0065_knowledge_management", downgrade=True)
    before = _retained_rows(postgres_engine)
    migrate(postgres_engine, "0066_candidate_reuse")
    after = _retained_rows(postgres_engine)
    assert after.pop("decision_candidate_reuses") == [] and after == before
    migrate(postgres_engine, "head")
    restate(api, project, target, old, links, replacement)
    retained = _retained_rows(postgres_engine)
    with pytest.raises(RuntimeError, match="Candidate conclusion references"):
        migrate(postgres_engine, "0065_knowledge_management", downgrade=True)
    assert _retained_rows(postgres_engine) == retained
