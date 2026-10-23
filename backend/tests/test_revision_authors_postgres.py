"""Impact authorship is pinned while work holdings are fresh, scoped observations."""

from uuid import uuid4

import pytest

from .test_decision_reviews_postgres import context, reviews, settings
from .test_knowledge_postgres import domain, path, put, put_body
from .test_leases_postgres import claim_payload, create_work, expire_lease, item_path

pytestmark = pytest.mark.postgres


def held_work(api, project, payload, session, title="Author implementation"):
    work = create_work(api, project, payload, title=title)["work_item"]
    response = api.post(item_path(project, work) + "/claim", json=claim_payload(
        str(uuid4()), client="codex", session=session,
    ))
    assert response.status_code == 200, response.text
    return work


def test_author_is_pinned_but_active_work_observation_excludes_expired_and_other_projects(
    api, project, work_payload, postgres_engine,
):
    settings(api, project, automatic_decision_reviews=True)
    target = domain(api, project)
    first = put(api, project, put_body(target, "Original", agent_session_id="previous"))["decision"]
    work = held_work(api, project, work_payload, "author-session")
    held_work(api, project, work_payload, "different-session", "Unrelated implementation")
    other = api.post("/api/v1/projects", json={"name": "Other author context"}).json()
    hidden = held_work(api, other, work_payload, "author-session", "Other project private title")
    second = put(api, project, put_body(
        target, "Changed scope in related records", decision_id=first["decision_id"],
        expected_revision=0, agent_session_id="author-session", model_id="author-model",
    ))["decision"]
    endpoint = path(project) + f"/decisions/{second['reference']}/impact"
    author = api.get(endpoint).json()["origin_author"]
    assert author["reference"] == second["reference"]
    assert author["agent_session_id"] == "author-session" and author["model_id"] == "author-model"
    assert [item["work_item_id"] for item in author["active_work_items"]] == [work["id"]]
    assert hidden["id"] not in str(author)
    assessment = api.get(path(project) + "/assessments").json()["items"][0]
    assert assessment["before_author"]["agent_session_id"] == "previous"
    assert assessment["after_author"]["active_work_items"] == author["active_work_items"]
    review, = reviews(api, project)
    checkpoint = context(api, project, review["work_item_id"])["initial_checkpoint"]
    assert "author-session" in checkpoint["prompt"] and work["id"] in checkpoint["prompt"]
    expire_lease(postgres_engine, work["id"])
    refreshed = api.get(endpoint).json()["origin_author"]
    assert refreshed["active_work_items"] == []
    assert refreshed["authored_at"] == author["authored_at"]
    assert context(api, project, review["work_item_id"])["initial_checkpoint"] == checkpoint


def test_author_context_is_bounded_and_human_authorship_has_no_session_holdings(
    api, project, work_payload,
):
    target = domain(api, project)
    for index in range(11):
        held_work(api, project, work_payload, "prolific-author", f"Author task {index}")
    decision = put(api, project, put_body(
        target, "Agent-authored policy", agent_session_id="prolific-author",
    ))["decision"]
    author = api.get(path(project) + f"/decisions/{decision['reference']}/impact").json()[
        "origin_author"
    ]
    assert len(author["active_work_items"]) == 10 and author["active_work_items_truncated"]
    human = put(api, project, put_body(
        target, "Human-authored policy", author_kind="human", agent_session_id=None, model_id=None,
    ))["decision"]
    author = api.get(path(project) + f"/decisions/{human['reference']}/impact").json()[
        "origin_author"
    ]
    assert author["author_kind"] == "human" and author["active_work_items"] == []
    assert author["agent_session_id"] is None and not author["active_work_items_truncated"]
