"""Merge-time scope registration and closeout retain one implementation citation."""

import io
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError
from mnemonic_api.knowledge_integrity import KNOWLEDGE_FINDINGS
from mnemonic_api.models import WorkItem
from mnemonic_api.services.decision_scope_citations import completion_citations
from mnemonic_api.services.knowledge import require_no_knowledge_links
from mnemonic_backup.archive import BackupError, restore_project

from .test_decision_provenance_postgres import ORIGINAL, REPOSITORY, declaration, report
from .test_knowledge_postgres import domain, path, put, put_body
from .test_project_backup_archive import _export, _rewrite, _snapshot
from .test_project_reports_postgres import close, create

pytestmark = pytest.mark.postgres


def registered(api, project, decision, **changes):
    intent = {
        "revision": decision["revision"], "commit_hash": ORIGINAL, "repository": REPOSITORY,
        "agent_session_id": "merge-session", "client_operation_id": str(uuid4()), **changes,
    }
    endpoint = path(project) + f"/decisions/{decision['decision_id']}/scope"
    response = api.post(endpoint, json=intent)
    assert response.status_code == 200, response.text
    return endpoint, intent, response.json()


def check_integrity(engine):
    with engine.connect() as database:
        for name, query in KNOWLEDGE_FINDINGS.items():
            assert database.scalar(text(query)) == 0, name


def test_closeout_reuses_merge_scope_and_preserves_each_session_and_work(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    decision = put(api, project, put_body(domain(api, project)))["decision"]
    earlier_work = create(api, project, work_payload)
    endpoint, intent, original = registered(
        api, project, decision, work_item_id=earlier_work["id"],
    )
    report_ids, work_ids = [], []
    for session in ("first-closeout-session", "second-closeout-session"):
        work = create(api, project, work_payload)
        result, close_intent = close(
            api, project, work, {**checkpoint_fields, "source_session_id": session},
            job_completion_report=report(declaration(decision)),
        )
        assert result.status_code == 200, result.text
        retained = result.json()["job_completion_report"]
        assert retained["decision_provenance"] == declaration(decision)
        assert retained["actor_session_id"] == session
        report_ids.append(retained["id"])
        work_ids.append(work["id"])
        complete = f"/api/v1/projects/{project['id']}/work-items/{work['id']}/complete"
        assert api.post(complete, json=close_intent).json() == result.json()

    page = api.get(endpoint).json()
    assert page["total"] == 1
    scope = page["items"][0]
    assert scope["id"] == original["scope"]["id"]
    assert scope["work_item_id"] == earlier_work["id"]
    assert scope["agent_session_id"] == "merge-session"
    assert scope.get("completion_report_id") is None
    assert [item["report_id"] for item in scope["completion_citations"]] == report_ids
    assert [item["work_item_id"] for item in scope["completion_citations"]] == work_ids
    assert [item["actor_session_id"] for item in scope["completion_citations"]] == [
        "first-closeout-session", "second-closeout-session",
    ]
    # The old receipt remains the original response; later evidence is a live read.
    assert api.post(endpoint, json=intent).json() == original
    lookup = api.get(path(project) + "/commits", params={
        "repository": REPOSITORY, "commit_hash": ORIGINAL,
    }).json()
    assert lookup["total"] == 1 and lookup["items"] == page["items"]
    read = api.get(path(project) + f"/decisions/{decision['decision_id']}").json()
    assert read["scope_count"] == 1
    check_integrity(postgres_engine)


@pytest.mark.parametrize("change", ["unknown-repository", "other-repository", "other-revision"])
def test_closeout_does_not_guess_an_existing_scope_identity(
    api, project, work_payload, checkpoint_fields, postgres_engine, change,
):
    owner = domain(api, project)
    decision = put(api, project, put_body(owner))["decision"]
    changes = {}
    if change == "other-revision":
        put(api, project, put_body(owner, "Later text", decision_id=decision["decision_id"],
                                   expected_revision=0))
        changes["revision"] = 1
    else:
        changes["repository"] = None if change == "unknown-repository" else "other/repo"
    endpoint, _, original = registered(api, project, decision, **changes)
    work = create(api, project, work_payload)
    result, _ = close(api, project, work, checkpoint_fields,
                      job_completion_report=report(declaration(decision)))
    assert result.status_code == 200, result.text
    page = api.get(endpoint).json()
    assert page["total"] == 2
    previous = next(item for item in page["items"] if item["id"] == original["scope"]["id"])
    assert previous.get("completion_citations", []) == []
    current = next(item for item in page["items"] if item["id"] != previous["id"])
    assert current["completion_report_id"] == result.json()["job_completion_report"]["id"]
    assert len(current["completion_citations"]) == 1
    check_integrity(postgres_engine)


def test_reused_scope_work_link_survives_restore_and_blocks_project_move(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    decision = put(api, project, put_body(domain(api, project)))["decision"]
    registered(api, project, decision)
    work = create(api, project, work_payload)
    result, intent = close(api, project, work, checkpoint_fields,
                           job_completion_report=report(declaration(decision)))
    assert result.status_code == 200, result.text
    before = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    after = _snapshot(postgres_engine, project)
    before["project_activity_heads"] = after["project_activity_heads"]
    assert before == after
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    assert api.post(base + "/complete", json=intent).json() == result.json()
    # The knowledge guard must retain this link independently of the review-history
    # guard that also prevents moving ordinary completed work.
    with Session(postgres_engine) as database:
        retained = database.get(WorkItem, UUID(work["id"]))
        assert retained is not None
        with pytest.raises(ApplicationError) as error:
            require_no_knowledge_links(database, retained)
    assert error.value.detail["code"] == "work_has_knowledge_links"
    check_integrity(postgres_engine)


def test_reused_completion_citations_report_bounded_evidence(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    decision = put(api, project, put_body(domain(api, project)))["decision"]
    registered(api, project, decision)
    for _ in range(2):
        work = create(api, project, work_payload)
        result, _ = close(api, project, work, checkpoint_fields,
                           job_completion_report=report(declaration(decision)))
        assert result.status_code == 200, result.text
    with Session(postgres_engine) as database:
        citations, truncated = completion_citations(
            database, UUID(project["id"]), decision["decision_id"], 0,
            REPOSITORY, ORIGINAL, limit=1,
        )
    assert len(citations) == 1 and truncated


def test_impact_snapshot_retains_reused_report_evidence_and_rejects_forgery(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    owner = domain(api, project)
    decision = put(api, project, put_body(owner))["decision"]
    registered(api, project, decision)
    work = create(api, project, work_payload)
    result, _ = close(api, project, work, checkpoint_fields,
                      job_completion_report=report(declaration(decision)))
    assert result.status_code == 200, result.text
    put(api, project, put_body(owner, "Changed rule", decision_id=decision["decision_id"],
                               expected_revision=0))
    assessment = api.get(path(project) + "/assessments").json()["items"][0]
    detail = api.get(path(project) + f"/assessments/{assessment['id']}").json()
    cited = detail["candidates"][0]["scopes"][0]["completion_citations"]
    assert len(cited) == 1
    assert cited[0]["report_id"] == result.json()["job_completion_report"]["id"]
    assert cited[0]["work_item_id"] == work["id"]
    check_integrity(postgres_engine)
    archive = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))

    def forge(_header, rows):
        scope = rows["decision_impact_assessments"][0]["candidates"][0]["scopes"][0]
        scope["completion_citations"][0]["actor_session_id"] = "forged-session"

    with pytest.raises(BackupError, match="integrity"):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(archive, forge)))
    check_integrity(postgres_engine)
