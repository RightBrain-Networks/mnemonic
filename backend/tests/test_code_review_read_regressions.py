"""Cold-review regressions: existing title grammar and real after-cursor traversal."""

from uuid import uuid4

import pytest

from tests.code_review_fixtures import actor, close, configure, create, handoff

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
