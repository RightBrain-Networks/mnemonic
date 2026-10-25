"""Review lifecycle events retain their references across every history read."""

from uuid import uuid4

import pytest

from tests.code_review_fixtures import (
    actor,
    claim_review,
    close,
    configure,
    create,
    handoff,
    mandatory,
    result_payload,
    result_url,
)

pytestmark = pytest.mark.postgres


def assert_event_pages_match_context(api, base, expected_types):
    response = api.get(base + "/context", params={"recent_event_limit": 20})
    assert response.status_code == 200, response.text
    context = response.json()
    assert context["omitted_event_count"] == 0
    events = context["recent_events"]
    assert expected_types <= {event["event_type"] for event in events}
    for order in ("oldest", "newest"):
        ordered = events if order == "oldest" else events[::-1]
        for event_type in (None, *sorted({event["event_type"] for event in events})):
            params = {"order": order}
            expected = ordered
            if event_type is not None:
                params["event_type"] = event_type
                expected = [event for event in ordered if event["event_type"] == event_type]
            response = api.get(base + "/events", params=params)
            assert response.status_code == 200, response.text
            assert response.json()["items"] == expected
            assert response.json()["total"] == len(expected)
            for offset in range(len(expected) + 1):
                response = api.get(base + "/events", params={
                    **params, "offset": offset, "limit": 1,
                })
                assert response.status_code == 200, response.text
                page = response.json()
                assert page["items"] == expected[offset:offset + 1]
                assert (page["total"], page["offset"], page["limit"]) == (len(expected), offset, 1)


@pytest.mark.parametrize("mode", ["cold", "warm"])
def test_review_and_follow_up_events_are_pageable(
    api, project, work_payload, checkpoint_fields, mode,
):
    configure(api, project, code_review_optional_min_priority=0)
    work = create(api, project, work_payload)
    response, _ = close(api, project, work, checkpoint_fields)
    assert response.status_code == 200, response.text
    question = response.json()["agent_follow_ups"][0]
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    response = api.post(base + f"/agent-follow-ups/{question['id']}/answer", json={
        "client_operation_id": str(uuid4()),
        "expected_follow_up_version": question["version"],
        "actor": actor(checkpoint_fields),
        "answer": {
            "kind": "code_review_recommendation",
            "recommend_review": True,
            "rationale": "Exercise review lifecycle history.",
            "code_review_handoff": handoff(),
        },
    })
    assert response.status_code == 200, response.text
    completion = response.json()
    lease = claim_review(api, project, completion, checkpoint_fields, mode=mode)
    response = api.post(result_url(project, completion), json=result_payload(completion, lease))
    assert response.status_code == 200, response.text
    assert_event_pages_match_context(api, base, {
        "work_follow_up_requested", "work_follow_up_answered", "code_review_requested",
        "work_claimed", "code_review_completed", "work_released",
    })


@pytest.mark.parametrize("optional", [False, True])
def test_superseded_review_and_follow_up_events_are_pageable(
    api, project, work_payload, checkpoint_fields, optional,
):
    if optional:
        configure(api, project, code_review_optional_min_priority=0)
        work = create(api, project, work_payload)
        response, _ = close(api, project, work, checkpoint_fields)
        assert response.status_code == 200, response.text
        completion = response.json()
        resource = completion["agent_follow_ups"][0]
        fields = {"supersede_follow_up_id": resource["id"], "expected_follow_up_version": 1}
        event_type = "work_follow_up_superseded"
    else:
        completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
        resource = completion["code_review_request"]
        claim_review(api, project, completion, checkpoint_fields)
        fields = {"supersede_code_review_id": resource["id"], "expected_code_review_version": 1}
        event_type = "code_review_superseded"
    work = completion["work_item"]
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    response = api.patch(base, json={
        "expected_version": work["version"],
        "status": "pending",
        "client_operation_id": str(uuid4()),
        "actor": actor(checkpoint_fields),
        **fields,
    })
    assert response.status_code == 200, response.text
    assert_event_pages_match_context(api, base, {event_type})
