"""SQL attack setup using the same real HTTP fixtures as lifecycle tests."""

from .code_review_fixtures import (
    claim_review,
    configure,
    finding,
    handoff,
    result_payload,
    result_url,
)
from .test_project_reports_postgres import close, create


def create_work(api, project: dict, payload: dict) -> dict:
    return create(api, project, payload)


def policy(
    api, project: dict, *, required: int = 0, optional: int = 100, remediation: bool = False
) -> dict:
    return configure(
        api,
        project,
        code_review_required_min_priority=required,
        code_review_optional_min_priority=optional,
        allow_remediation_code_reviews=remediation,
    )


def close_work(api, project: dict, work: dict, checkpoint: dict, *, review: bool = True) -> dict:
    settings = api.get(f"/api/v1/projects/{project['id']}/settings").json()
    overrides = {
        "job_completion_report": {
            "summary": "Fixed the cache regression.",
            "fyi_items": [],
            "prompt_revision": settings["revision"],
        }
    }
    if review:
        overrides["code_review_handoff"] = handoff()
    response, _ = close(api, project, work, checkpoint, **overrides)
    assert response.status_code == 200, response.text
    return response.json()


def finish_review(api, project: dict, work: dict, review: dict, *, count: int = 1) -> dict:
    completion = {"work_item": work, "code_review_request": review}
    lease = claim_review(api, project, completion, {})
    payload = result_payload(
        completion, lease, findings=[finding(f"F{i + 1:03}") for i in range(count)]
    )
    response = api.post(result_url(project, completion), json=payload)
    assert response.status_code == 200, response.text
    return response.json()
