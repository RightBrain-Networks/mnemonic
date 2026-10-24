"""Fresh identity normalization never rewrites receipts, authorship, or old evidence."""

import copy
from uuid import uuid4

import pytest

from mnemonic_api.services import job_completion_reports, knowledge

from .test_decision_provenance_postgres import ORIGINAL, declaration, report
from .test_decision_scope_reuse_postgres import check_integrity, registered
from .test_knowledge_postgres import domain, path, put, put_body
from .test_project_reports_postgres import close, create

pytestmark = pytest.mark.postgres
CANONICAL = "https://github.com/team/repo"


def test_fresh_aliases_reuse_only_the_same_attribution(api, project, postgres_engine):
    decision = put(api, project, put_body(domain(api, project)))["decision"]
    endpoint, intent, first = registered(api, project, decision,
                                         repository="HTTP://GitHub.com/Team/Repo.git/")
    _, _, same = registered(api, project, decision, repository="team/repo")
    assert same["created"] is False and same["scope"]["id"] == first["scope"]["id"]
    assert first["scope"]["repository"] == CANONICAL
    _, _, other = registered(api, project, decision, repository="team/repo",
                              agent_session_id="another-session")
    assert other["created"] is True and other["scope"]["id"] != first["scope"]["id"]
    for unknown in (None, "ssh://github.com/team/repo", "https://gitlab.com/team/repo"):
        _, _, retained = registered(api, project, decision, repository=unknown)
        assert retained["scope"].get("repository") == unknown
    assert api.post(endpoint, json=intent).json() == first
    changed_intent = intent | {"repository": CANONICAL}
    assert api.post(endpoint, json=changed_intent).status_code == 409
    check_integrity(postgres_engine)


def test_lookup_preserves_legacy_spellings_and_reconciliation_is_exact(
    api, project, monkeypatch, postgres_engine,
):
    owner = domain(api, project)
    decision = put(api, project, put_body(owner))["decision"]
    spellings = ["GitHub.com/Team/Repo.git/", "http://github.com/team/repo"]
    historical = []
    with monkeypatch.context() as patch:
        patch.setattr(knowledge, "canonical_repository", lambda value: value)
        for spelling in spellings:
            historical.append(registered(api, project, decision, repository=spelling))
    put(api, project, put_body(owner, "Changed rule", decision_id=decision["decision_id"],
                               expected_revision=0))
    assessment = api.get(path(project) + "/assessments").json()["items"][0]
    assessment_path = path(project) + f"/assessments/{assessment['id']}"
    snapshot = api.get(assessment_path).json()["candidates"]
    assert {scope["repository"] for candidate in snapshot for scope in candidate["scopes"]} == {
        *spellings,
    }
    endpoint, _, fresh = registered(api, project, decision, repository="team/repo")
    assert fresh["created"] is True  # Do not silently merge historical alias rows.
    registered(api, project, decision, repository=None)
    for query in ("TEAM/REPO", "https://github.com/team/repo.git/", spellings[0]):
        page = api.get(path(project) + "/commits", params={
            "repository": query, "commit_hash": ORIGINAL,
        }).json()
        assert page["total"] == 3
        assert {row["repository"] for row in page["items"]} == {*spellings, CANONICAL}
    mapping = {"repository": "team/repo", "original_commit_hash": ORIGINAL,
               "merged_commit_hash": "b" * 40, "client_operation_id": str(uuid4())}
    absent = api.post(path(project) + "/commits/reconcile", json=mapping)
    assert absent.status_code == 404
    assert absent.json()["detail"]["code"] == "decision_scope_not_found"
    mapped = api.post(path(project) + "/commits/reconcile", json={
        **mapping, "repository": spellings[0], "client_operation_id": str(uuid4()),
    })
    assert mapped.status_code == 200, mapped.text
    found = api.get(path(project) + "/commits", params={
        "repository": CANONICAL, "commit_hash": "b" * 40,
    }).json()
    assert found["total"] == 1 and found["items"][0]["repository"] == spellings[0]
    for _, intent, response in historical:
        assert api.post(endpoint, json=intent).json() == response
    assert api.get(assessment_path).json()["candidates"] == snapshot
    check_integrity(postgres_engine)


def test_fresh_completion_reuses_canonical_scope_and_retains_raw_intent(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    decision = put(api, project, put_body(domain(api, project)))["decision"]
    endpoint, _, original = registered(api, project, decision, repository=CANONICAL)
    work = create(api, project, work_payload)
    provenance = declaration(decision)
    provenance["commits"][0]["repository"] = "Team/Repo.git"
    response, intent = close(api, project, work, checkpoint_fields,
                             job_completion_report=report(provenance))
    assert response.status_code == 200, response.text
    stored = response.json()["job_completion_report"]["decision_provenance"]
    assert stored["commits"][0]["repository"] == CANONICAL
    assert intent["job_completion_report"]["decision_provenance"] == provenance
    scopes = api.get(endpoint).json()
    assert scopes["total"] == 1 and scopes["items"][0]["id"] == original["scope"]["id"]
    assert scopes["items"][0]["agent_session_id"] == "merge-session"
    complete = f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete"
    assert api.post(complete, json=intent).json() == response.json()
    altered = copy.deepcopy(intent)
    altered["job_completion_report"]["decision_provenance"] = stored
    assert api.post(complete, json=altered).status_code == 409
    check_integrity(postgres_engine)


@pytest.mark.parametrize("unsupported, supported", [
    ("Team/K", "team/k"), ("Team/İ", "team/i"),
    ("https://gİthub.com/Team/Repo", "team/repo"),
    ("HTTP://GİtHub.com/TKam/Repo.git/", "tkam/repo"),
    ("github.com/Keam/Repo", "keam/repo"),
])
def test_lookup_does_not_fold_unknown_unicode_into_github_aliases(
    api, project, unsupported, supported,
):
    decision = put(api, project, put_body(domain(api, project)))["decision"]
    _, _, known = registered(api, project, decision, repository=supported)
    _, _, unknown = registered(api, project, decision, repository=unsupported)
    assert unknown["scope"]["repository"] == unsupported
    endpoint = path(project) + "/commits"
    original = api.get(endpoint, params={
        "repository": supported.upper(), "commit_hash": ORIGINAL,
    }).json()
    assert original["total"] == 1 and original["items"][0]["id"] == known["scope"]["id"]
    mapping = api.post(endpoint + "/reconcile", json={
        "repository": unsupported, "original_commit_hash": ORIGINAL,
        "merged_commit_hash": "b" * 40, "client_operation_id": str(uuid4()),
    })
    assert mapping.status_code == 200, mapping.text
    assert api.get(endpoint, params={
        "repository": supported, "commit_hash": "b" * 40,
    }).json()["total"] == 0
    for commit in (ORIGINAL, "b" * 40):
        exact = api.get(endpoint, params={
            "repository": unsupported, "commit_hash": commit,
        }).json()
        assert exact["total"] == 1 and exact["items"][0]["id"] == unknown["scope"]["id"]


def test_duplicate_canonical_pairs_fail_atomically_but_historical_receipts_replay(
    api, project, work_payload, checkpoint_fields, monkeypatch, postgres_engine,
):
    decision = put(api, project, put_body(domain(api, project)))["decision"]
    provenance = declaration(decision)
    provenance["commits"] = [{"repository": repo, "commit_hash": ORIGINAL}
                             for repo in ("team/repo", "https://github.com/Team/Repo.git")]
    work = create(api, project, work_payload)
    failed, _ = close(api, project, work, checkpoint_fields,
                      job_completion_report=report(provenance))
    assert failed.status_code == 422, failed.text
    assert failed.json()["detail"]["code"] == "decision_provenance_duplicate_commits"
    endpoint = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    assert api.get(endpoint).json()["work_item"]["version"] == work["version"]
    scope_path = path(project) + f"/decisions/{decision['decision_id']}/scope"
    assert api.get(scope_path).json()["total"] == 0
    with monkeypatch.context() as patch:
        patch.setattr(knowledge, "canonical_repository", lambda value: value)
        patch.setattr(job_completion_reports, "canonical_provenance", lambda value: value)
        historical, intent = close(api, project, work, checkpoint_fields,
                                   job_completion_report=report(provenance))
    assert historical.status_code == 200, historical.text
    assert api.post(endpoint + "/complete", json=intent).json() == historical.json()
    retained = historical.json()["job_completion_report"]
    detail = api.get(f"/api/v1/projects/{project['id']}/job-completion-reports/{retained['id']}")
    assert detail.json()["report"]["decision_provenance"] == provenance
    assert {row["repository"] for row in api.get(scope_path).json()["items"]} == {
        commit["repository"] for commit in provenance["commits"]
    }
    check_integrity(postgres_engine)
