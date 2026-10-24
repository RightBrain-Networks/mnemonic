"""Compact reads retain the exact ledger while omitting explicitly requested detail."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from mnemonic_api.models import DecisionScope
from tests.test_decision_impact_postgres import assessments, changed, decision, impact
from tests.test_knowledge_postgres import domain, path

pytestmark = pytest.mark.postgres
COMPACT = {"detail": "compact", "include_diff": False, "include_scopes": "summary"}


def _episode(api, project):
    target = domain(api, project)
    root = decision(api, project, target, "Original review rule")
    decision(api, project, target, "Incoming citation", related_decision_ids=[root["decision_id"]])
    changed(api, project, target, root, "Changed review rule")
    return root, assessments(api, project)[0]


def _get(api, project, episode, **options):
    response = api.get(path(project) + f"/assessments/{episode['id']}", params=options)
    assert response.status_code == 200, response.text
    return response


def _scope_rows(project, decisions, copies=1):
    return [DecisionScope(
        project_id=UUID(project["id"]), document_id=UUID(item["id"]), revision=item["revision"],
        commit_hash=f"{index + 1:040x}", repository="https://github.com/example/reviewed",
        agent_session_id=f"scope-writer-{copy}-" + "s" * 150,
    ) for index, item in enumerate(decisions) for copy in range(copies)]


def test_compact_projection_preserves_counts_pages_and_full_default(api, project):
    root, episode = _episode(api, project)
    full = _get(api, project, episode).json()
    compact = _get(api, project, episode, **COMPACT, limit=1).json()
    assert "projection" not in full
    assert full["diff"]["unified_diff"]
    assert "title" in full["candidates"][0] and "scopes" in full["candidates"][0]
    assert compact["projection"] == COMPACT
    assert "unified_diff" not in compact["diff"]
    assert compact["diff"] == {key: value for key, value in full["diff"].items()
                               if key != "unified_diff"}
    assert compact["total"] == full["total"] == 2
    assert compact["next_offset"] == 1
    rest = _get(api, project, episode, **COMPACT, limit=1, offset=1).json()
    assert rest["next_offset"] is None
    assert [compact["candidates"][0]["reference"], rest["candidates"][0]["reference"]] == [
        item["reference"] for item in full["candidates"]
    ]
    assert "active_work_items" not in compact["before_author"]
    assert compact["before_author"]["active_work_item_ids"] == []
    assert compact["coverage"] == full["coverage"]
    live = impact(api, project, root["reference"], **COMPACT)
    assert [row["reference"] for row in live["items"]] == [
        row["reference"] for row in full["candidates"]
    ]
    listed = api.get(path(project) + "/assessments", params=COMPACT).json()
    assert listed["items"][0]["projection"] == COMPACT
    assert "unified_diff" not in listed["items"][0]["diff"]


@pytest.mark.parametrize("detail", ["full", "compact"])
@pytest.mark.parametrize("scopes", ["full", "summary", "none"])
def test_scope_projection_modes_are_explicit_for_both_kinds(api, project, detail, scopes):
    _, episode = _episode(api, project)
    for kind in ("code-impact", "context"):
        result = _get(api, project, episode, detail=detail, include_scopes=scopes, kind=kind).json()
        assert "unified_diff" in result["diff"]
        for item in result["candidates"]:
            assert ("scopes" in item) == (scopes == "full")
            assert ("scope_summary" in item) == (scopes == "summary")
            assert ("title" in item) == (detail == "full")


def test_scope_summary_is_captured_evidence_not_later_live_scope(api, project, postgres_engine):
    target = domain(api, project)
    root = decision(api, project, target, "Pinned rule")
    with Session(postgres_engine) as database, database.begin():
        database.add_all(_scope_rows(project, [root], copies=10))
    changed(api, project, target, root, "Changed pinned rule")
    episode = assessments(api, project)[0]
    before = _get(api, project, episode, **COMPACT).json()["candidates"][0]
    with Session(postgres_engine) as database, database.begin():
        database.add(DecisionScope(
            project_id=UUID(project["id"]), document_id=UUID(root["id"]), revision=0,
            commit_hash="f" * 40, repository="https://github.com/example/later",
        ))
    after = _get(api, project, episode, **COMPACT).json()["candidates"][0]
    assert before == after
    assert after["scope_summary"] == {
        "captured_scope_count": 10, "commit_hashes": ["0" * 39 + "1"],
        "evidence_truncated": False,
    }
    live = impact(api, project, root["reference"], **COMPACT)["items"][0]
    assert live["scope_summary"]["captured_scope_count"] == 10
    assert live["evidence_truncated"] is True


def test_46_candidate_compact_response_omits_large_evidence_without_losing_ledger(
    api, project, postgres_engine,
):
    target = domain(api, project)
    root = decision(api, project, target, "Original rule\n" * 1000)
    records = [root, *[decision(
        api, project, target, f"Citing decision {index}",
        related_decision_ids=[root["decision_id"]], title=f"Long decision title {index} " * 8,
    ) for index in range(45)]]
    with Session(postgres_engine) as database, database.begin():
        database.add_all(_scope_rows(project, records, copies=10))
    changed(api, project, target, root, "Replacement rule\n" * 1000)
    episode = assessments(api, project)[0]
    original = _get(api, project, episode).json()
    for version, item in enumerate(original["candidates"]):
        response = api.post(path(project) + f"/assessments/{episode['id']}/dispositions", json={
            "client_operation_id": str(uuid4()), "candidate_reference": item["reference"],
            "expected_version": version, "disposition": "still-valid", "reason": "e" * 4000,
            "actor": {"actor_client": "codex", "actor_session_id": "projection-test"},
        })
        assert response.status_code == 201, response.text
    full = _get(api, project, episode)
    compact = _get(api, project, episode, **COMPACT)
    value = compact.json()
    assert len(compact.content) < 25_000
    assert len(full.content) > 300_000
    assert len(value["candidates"]) == len(value["dispositions"]) == 46
    assert value["version"] == value["disposition_history_total"] == 46
    assert value["unassessed_count"] == 0 and value["next_offset"] is None
    assert [row["reference"] for row in value["candidates"]] == [
        row["reference"] for row in original["candidates"]
    ]
    assert all("reason" not in row for row in value["dispositions"])
    history = api.get(path(project) + f"/assessments/{episode['id']}/dispositions").json()
    assert len(history["items"]) == 46
    assert all(row["reason"] == "e" * 4000 for row in history["items"])
    assert full.json()["candidates"] == original["candidates"]


@pytest.mark.parametrize("options", [{"detail": "tiny"}, {"include_scopes": "maybe"}])
def test_invalid_projection_is_rejected(api, project, options):
    root, episode = _episode(api, project)
    for suffix in (f"/decisions/{root['reference']}/impact", "/assessments",
                   f"/assessments/{episode['id']}"):
        assert api.get(path(project) + suffix, params=options).status_code == 422
