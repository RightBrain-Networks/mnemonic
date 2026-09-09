"""Cold-review regressions: existing title grammar and real after-cursor traversal."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.code_review_fixtures import actor, claim_review, close, configure, create, handoff

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("optional", [False, True])
def test_review_pages_and_details_preserve_valid_multiline_title_edits(
    api, project, work_payload, checkpoint_fields, optional,
):
    configure(api, project, **{
        "code_review_optional_min_priority" if optional
        else "code_review_required_min_priority": 0,
    })
    rows = []
    for index in range(2):
        work = create(api, project, {**work_payload, "title": f"Review source {index}"})
        extra = {} if optional else {"code_review_handoff": handoff()}
        response, _ = close(api, project, work, checkpoint_fields, **extra)
        assert response.status_code == 200, response.text
        completion = response.json()
        resource = (completion["agent_follow_ups"][0] if optional
                    else completion["code_review_request"])
        base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
        title = f"Repair\n\tcache {index}"
        edited = api.patch(base, json={
            "expected_version": completion["work_item"]["version"], "title": title,
            "actor": actor(checkpoint_fields), "client_operation_id": str(uuid4()),
        })
        assert edited.status_code == 200, edited.text
        suffix = "agent-follow-ups" if optional else "code-reviews"
        detail = api.get(f"{base}/{suffix}/{resource['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["source_work_state"]["title"] == title
        rows.append((resource["id"], title))

    suffix = "work-agent-follow-ups" if optional else "code-reviews"
    url = f"/api/v1/projects/{project['id']}/{suffix}"
    first = api.get(url, params={"limit": 1})
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["has_more"] is True
    assert (page["items"][0]["id"], page["items"][0]["title"]) == rows[1]
    second = api.get(url, params={"limit": 1, "after": page["next_cursor"]})
    assert second.status_code == 200, second.text
    page = second.json()
    assert page["has_more"] is False
    assert (page["items"][0]["id"], page["items"][0]["title"]) == rows[0]


def test_review_descendants_search_pagination_and_expired_leases(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    configure(api, project, code_review_required_min_priority=0)
    parent = create(api, project, {**work_payload, "title": "Review parent"})
    children = []
    for index in range(2):
        work = create(api, project, {
            **work_payload, "title": f"Review child {index}",
            "initial_relationships": [{"type": "parent-child", "direction": "incoming",
                                       "other_work_item_id": parent["id"]}],
        })
        response, _ = close(api, project, work, checkpoint_fields, code_review_handoff=handoff())
        assert response.status_code == 200, response.text
        children.append(response.json())
    claimed = children[0]
    claim_review(api, project, claimed, checkpoint_fields)
    base = f"/api/v1/projects/{project['id']}/work-items"
    response = api.get(base, params={"view": "roots", "status": "to-review"})
    assert response.status_code == 200, response.text
    roots = response.json()
    assert roots["total"] == 1
    root = roots["items"][0]
    assert root["summary"]["work_item"]["id"] == parent["id"]
    assert root["self_matches_filter"] is False
    assert root["has_matching_descendants"] is True
    expected = {item["work_item"]["id"] for item in children}
    found = set()
    for offset in range(2):
        response = api.get(f"{base}/{parent['id']}/children", params={
            "status": "to-review", "limit": 1, "offset": offset,
        })
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2
        summary = response.json()["items"][0]["summary"]
        found.add(summary["work_item"]["id"])
        assert summary["readiness"]["display_state"] == "to-review"
        if summary["work_item"]["id"] == claimed["work_item"]["id"]:
            assert summary["readiness"]["active_lease"]["purpose"] == "code_review"
    assert found == expected
    response = api.get(base, params={"status": "to-review", "q": "Review child"})
    assert response.status_code == 200, response.text
    assert {row["summary"]["work_item"]["id"] for row in response.json()["items"]} == expected
    context = api.get(f"{base}/{parent['id']}/context").json()
    assert {edge["counterpart"]["readiness"]["display_state"]
            for edge in context["outgoing_relationships"]} == {"to-review"}
    with postgres_engine.begin() as connection:
        connection.execute(text(
            "UPDATE work_leases SET expires_at = now() "
            "WHERE work_item_id = :id"
        ), {"id": claimed["work_item"]["id"]})
    response = api.get(f"{base}/{claimed['work_item']['id']}/context")
    assert response.status_code == 200, response.text
    readiness = response.json()["readiness"]
    assert readiness["has_dropped_lease"] is True
    assert readiness["display_state"] == "to-review"
    assert api.get(base, params={"status": "dropped"}).json()["total"] == 0
