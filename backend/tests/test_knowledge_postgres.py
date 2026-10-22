"""Immutable revision history and exact code provenance survive retries and graph edits."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.postgres


def path(project):
    return f"/api/v1/projects/{project['id']}/knowledge"


def domain(api, project, prefix="ADR", name="Architecture"):
    response = api.post(
        path(project) + "/domains",
        json={
            "name": name,
            "prefix": prefix,
            "client_operation_id": str(uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def put_body(domain, content="# Decision\n\nKeep every revision.\n", **changes):
    return {
        "domain_id": domain["id"],
        "title": "Retain decisions",
        "content": content,
        "media_type": "text/markdown",
        "state": "proposed",
        "author_kind": "agent",
        "agent_session_id": "native-session",
        "model_id": "model-1",
        "client_operation_id": str(uuid4()),
        **changes,
    }


def put(api, project, body):
    response = api.put(path(project) + "/decisions", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_domains_are_unique_prefixes_stable_and_renames_receipted(api, project):
    body = {"name": "Architecture", "prefix": "adr", "client_operation_id": str(uuid4())}
    first = api.post(path(project) + "/domains", json=body)
    assert first.status_code == 201, first.text
    created = first.json()
    assert created["prefix"] == "ADR"
    assert api.post(path(project) + "/domains", json=body).json() == created
    for changes in (
        {"name": "architecture", "prefix": "BDR"},
        {"name": "Bookkeeping", "prefix": "ADR"},
    ):
        result = api.post(
            path(project) + "/domains",
            json={
                **body,
                **changes,
                "client_operation_id": str(uuid4()),
            },
        )
        assert result.status_code == 409, result.text
    target = path(project) + f"/domains/{created['id']}"
    rename = {
        "name": "Technical decisions",
        "expected_version": 1,
        "client_operation_id": str(uuid4()),
    }
    renamed = api.patch(target, json=rename)
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["version"] == 2
    assert api.patch(target, json=rename).json() == renamed.json()
    assert api.patch(target, json={**rename, "prefix": "NEW"}).status_code == 422
    assert (
        api.patch(target, json={**rename, "client_operation_id": str(uuid4())}).status_code == 409
    )
    assert api.post(path(project) + "/domains", json={**body, "name": "Changed"}).status_code == 409


@pytest.mark.parametrize("prefix", ["", "ABCDEF", "a1", "a-b", "Å"])
def test_domain_prefix_validation(api, project, prefix):
    response = api.post(
        path(project) + "/domains",
        json={
            "name": "Invalid",
            "prefix": prefix,
            "client_operation_id": str(uuid4()),
        },
    )
    assert response.status_code == 422, response.text


def test_revision_history_is_exact_utf8_with_explicit_states_and_line_ranges(api, project):
    target_domain = domain(api, project)
    content = "# café 🧭\r\n\nDecision with untouched whitespace.  \n"
    body = put_body(target_domain, content)
    original = put(api, project, body)
    decision = original["decision"]
    assert decision["reference"] == "ADR-00001-0"
    assert decision["size_bytes"] == len(content.encode("utf-8"))
    assert decision["sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert put(api, project, body) == original
    duplicate = put(
        api,
        project,
        {
            **body,
            "title": "Other name",
            "state": "accepted",
            "agent_session_id": "another-session",
            "client_operation_id": str(uuid4()),
        },
    )
    assert duplicate["created"] is False
    assert duplicate["decision"]["title"] == decision["title"]
    updated = put(
        api,
        project,
        {
            **body,
            "decision_id": decision["decision_id"],
            "expected_revision": 0,
            "state": "accepted",
            "client_operation_id": str(uuid4()),
        },
    )
    assert updated["decision"]["revision"] == 1
    base = path(project) + "/decisions/ADR-00001"
    assert api.get(base).json()["state"] == "accepted"
    assert api.get(base + "-0").json()["state"] == "proposed"
    assert api.get(base + "-0").json()["content"] == content
    assert (
        api.get(base, params={"revision": 0, "line_start": 2, "line_end": 2}).json()["content"]
        == "\n"
    )
    assert api.get(base + "-0", params={"revision": 1}).status_code == 422
    assert api.get(base, params={"line_start": 10}).status_code == 416
    history = api.get(base + "/history").json()
    assert [item["revision"] for item in history["items"]] == [1, 0]
    assert history["total"] == 2
    assert put(api, project, body) == original
    assert (
        api.put(
            path(project) + "/decisions",
            json={
                **body,
                "decision_id": "ADR-00001",
                "expected_revision": 0,
                "content": "stale change",
                "client_operation_id": str(uuid4()),
            },
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"state": None},
        {"author_kind": "agent", "model_id": None},
        {"author_kind": "human"},
        {"media_type": "application/pdf"},
        {"content": "bad\x00text"},
        {"content": "🧭" * 262_145},
        {"decision_id": "ADR-00001"},
    ],
)
def test_invalid_decision_inputs_do_not_allocate_ordinals(api, project, changes):
    target_domain = domain(api, project)
    response = api.put(path(project) + "/decisions", json=put_body(target_domain, **changes))
    assert response.status_code == 422, response.text
    assert put(api, project, put_body(target_domain))["decision"]["ordinal"] == 1


def test_no_default_state_and_human_author_marker(api, project):
    target_domain = domain(api, project)
    body = put_body(target_domain)
    del body["state"]
    assert api.put(path(project) + "/decisions", json=body).status_code == 422
    body.update(state="rejected", author_kind="human", agent_session_id=None, model_id=None)
    decision = put(api, project, body)["decision"]
    assert (decision["state"], decision["author_kind"], decision["model_id"]) == (
        "rejected",
        "human",
        None,
    )


def test_concurrent_allocations_and_hash_idempotence(api, project):
    target_domain = domain(api, project)
    bodies = [put_body(target_domain, f"Document {index}") for index in range(6)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda body: put(api, project, body), bodies))
    assert sorted(item["decision"]["ordinal"] for item in results) == list(range(1, 7))
    repeated = [put_body(target_domain, "One shared document") for _ in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda body: put(api, project, body), repeated))
    assert len({item["decision"]["reference"] for item in results}) == 1
    assert sum(item["created"] for item in results) == 1


def test_cross_domain_references_supersession_and_immutable_link_history(api, project):
    adr, odr = domain(api, project), domain(api, project, "ODR", "Owner decisions")
    first = put(api, project, put_body(adr, "First"))["decision"]
    owner = put(api, project, put_body(odr, "Owner"))["decision"]
    second = put(
        api,
        project,
        put_body(
            adr,
            "Second",
            supersedes=[first["decision_id"]],
            related_decision_ids=[owner["decision_id"]],
        ),
    )["decision"]

    def get(reference):
        return api.get(path(project) + "/decisions/" + reference).json()

    assert get(first["decision_id"])["superseded_by"] == [second["reference"]]
    assert get(owner["decision_id"])["referenced_by"] == [second["reference"]]
    bad = put_body(adr, "Cross-domain supersession", supersedes=[owner["decision_id"]])
    assert api.put(path(project) + "/decisions", json=bad).status_code == 422
    cycle = put_body(
        adr,
        "Cycle",
        decision_id=first["decision_id"],
        expected_revision=0,
        supersedes=[second["decision_id"]],
    )
    response = api.put(path(project) + "/decisions", json=cycle)
    assert response.status_code == 409, response.text
    put(
        api,
        project,
        put_body(adr, "Second updated", decision_id=second["decision_id"], expected_revision=0),
    )
    assert get(second["reference"])["supersedes"] == [first["decision_id"]]
    assert get(first["decision_id"])["superseded_by"] == []
    assert get(owner["decision_id"])["referenced_by"] == []


def test_scope_is_revision_pinned_idempotent_and_project_scoped(api, project, work_payload):
    target_domain = domain(api, project)
    decision = put(api, project, put_body(target_domain))["decision"]
    work = api.post(f"/api/v1/projects/{project['id']}/work-items", json=work_payload).json()[
        "work_item"
    ]
    target = path(project) + f"/decisions/{decision['decision_id']}/scope"
    body = {
        "revision": 0,
        "commit_hash": "AB" * 20,
        "work_item_id": work["id"],
        "agent_session_id": "implementation-session",
        "client_operation_id": str(uuid4()),
    }
    response = api.post(target, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["scope"]["commit_hash"] == "ab" * 20
    assert api.post(target, json=body).json() == response.json()
    assert (
        api.post(target, json={**body, "client_operation_id": str(uuid4())}).json()["created"]
        is False
    )
    assert api.post(target, json={**body, "commit_hash": "abc123"}).status_code == 422
    assert (
        api.post(
            target, json={key: value for key, value in body.items() if key != "revision"}
        ).status_code
        == 422
    )
    assert (
        api.post(
            target, json={**body, "revision": 9, "client_operation_id": str(uuid4())}
        ).status_code
        == 404
    )
    page = api.get(target).json()
    assert page["total"] == 1 and page["items"][0]["revision"] == 0
    other = api.post("/api/v1/projects", json={"name": "Other"}).json()
    foreign = api.post(f"/api/v1/projects/{other['id']}/work-items", json=work_payload).json()[
        "work_item"
    ]
    assert (
        api.post(
            target,
            json={**body, "work_item_id": foreign["id"], "client_operation_id": str(uuid4())},
        ).status_code
        == 404
    )
    moved = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move",
        json={
            "target_project_id": other["id"],
            "expected_version": work["version"],
            "actor": {"actor_client": "codex", "actor_session_id": "test"},
            "client_operation_id": str(uuid4()),
        },
    )
    assert moved.status_code == 409, moved.text
    assert moved.json()["detail"]["code"] == "work_has_knowledge_links"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE decision_revisions SET content='changed'",
        "DELETE FROM decision_revisions",
        "TRUNCATE decision_revisions CASCADE",
        "UPDATE knowledge_domains SET prefix='ODR'",
        "DELETE FROM knowledge_domains",
        "UPDATE decision_documents SET ordinal=10",
        "TRUNCATE knowledge_domains CASCADE",
        "UPDATE decision_scopes SET commit_hash=repeat('b',40)",
        "DELETE FROM decision_scopes",
        "TRUNCATE decision_scopes",
    ],
)
def test_database_guards_preserve_decision_history(api, project, postgres_engine, statement):
    target_domain = domain(api, project)
    put(api, project, put_body(target_domain))
    result = api.post(
        path(project) + "/decisions/ADR-00001/scope",
        json={
            "revision": 0,
            "commit_hash": "a" * 40,
            "client_operation_id": str(uuid4()),
        },
    )
    assert result.status_code == 200, result.text
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text(statement))


def test_revision_links_cannot_be_added_after_original_transaction(api, project, postgres_engine):
    target_domain = domain(api, project)
    first = put(api, project, put_body(target_domain, "First"))["decision"]
    second = put(api, project, put_body(target_domain, "Second"))["decision"]
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO decision_relationships (revision_id,target_document_id,kind,project_id)
            SELECT id,:target,'related',project_id FROM decision_revisions WHERE document_id=:source
        """),
            {"target": second["id"], "source": first["id"]},
        )


def test_work_links_are_revision_metadata_and_invalid_links_rollback(api, project, work_payload):
    target_domain = domain(api, project)
    work = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json=work_payload,
    ).json()["work_item"]
    original = put(
        api,
        project,
        put_body(
            target_domain,
            "Linked",
            related_work_item_ids=[work["id"]],
        ),
    )["decision"]
    assert original["related_work_item_ids"] == [work["id"]]
    updated = put(
        api,
        project,
        put_body(
            target_domain,
            "Revised links",
            decision_id=original["decision_id"],
            expected_revision=0,
        ),
    )["decision"]
    assert updated["related_work_item_ids"] == []
    old = api.get(path(project) + "/decisions/" + original["reference"]).json()
    assert old["related_work_item_ids"] == [work["id"]]
    missing = api.put(
        path(project) + "/decisions",
        json=put_body(
            target_domain,
            "Missing artifact",
            related_artifact_ids=[str(uuid4())],
        ),
    )
    assert missing.status_code == 404, missing.text
    next_document = put(api, project, put_body(target_domain, "After rollback"))["decision"]
    assert next_document["ordinal"] == 2


def test_scope_retains_citations_to_old_revisions(api, project):
    target_domain = domain(api, project)
    original = put(api, project, put_body(target_domain))["decision"]
    put(
        api,
        project,
        put_body(
            target_domain,
            "Changed text",
            decision_id=original["decision_id"],
            expected_revision=0,
        ),
    )
    target = path(project) + "/decisions/ADR-00001/scope"
    for revision in (0, 1):
        response = api.post(
            target,
            json={
                "revision": revision,
                "commit_hash": "a" * 40,
                "client_operation_id": str(uuid4()),
            },
        )
        assert response.status_code == 200, response.text
    assert api.get(target).json()["total"] == 2
    assert api.get(target, params={"revision": 0}).json()["items"][0]["revision"] == 0
    assert api.get(target, params={"revision": 9}).status_code == 404


@pytest.mark.parametrize("value", ["Name\x00tail", "Name\ud800tail"])
def test_invalid_unicode_metadata_is_rejected_before_database_execution(api, project, value):
    import json

    response = api.post(
        path(project) + "/domains",
        content=json.dumps(
            {
                "name": value,
                "prefix": "ADR",
                "client_operation_id": str(uuid4()),
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text


def test_dashboard_can_manage_domains_and_read_but_cannot_write_decisions(api, project):
    headers = {"Host": "localhost:3000", "Origin": "http://localhost:3000"}
    browser = path(project).replace("/api/v1/", "/api/mnemonic/")
    response = api.post(
        browser + "/domains",
        headers=headers,
        json={
            "name": "Architecture",
            "prefix": "adr",
            "client_operation_id": str(uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    target_domain = response.json()
    put(api, project, put_body(target_domain))
    assert api.get(browser + "/decisions", headers=headers).status_code == 200
    assert api.get(browser + "/decisions/ADR-00001", headers=headers).status_code == 200
    assert (
        api.put(
            browser + "/decisions", headers=headers, json=put_body(target_domain, "Forbidden")
        ).status_code
        == 404
    )
    assert (
        api.post(
            browser + "/decisions/ADR-00001/scope",
            headers=headers,
            json={
                "revision": 0,
                "commit_hash": "a" * 40,
                "client_operation_id": str(uuid4()),
            },
        ).status_code
        == 404
    )
