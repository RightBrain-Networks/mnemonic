"""Explicit review inputs shared by HTTP and structural persistence tests."""

from uuid import uuid4

from tests.test_project_reports_postgres import actor as actor
from tests.test_project_reports_postgres import close as report_close
from tests.test_project_reports_postgres import create


def close(api, project, work, checkpoint_fields, **overrides):
    revision = api.get(f"/api/v1/projects/{project['id']}/settings").json()["revision"]
    report = {
        "summary": "Repair cache invalidation and validate concurrent readers.",
        "fyi_items": [],
        "prompt_revision": revision,
    }
    return report_close(
        api, project, work, checkpoint_fields, job_completion_report=report, **overrides
    )


def handoff():
    return {
        "scope": {
            "repositories": [
                {
                    "repository_key": "main",
                    "checkout_path": "/srv/example",
                    "object_format": "sha1",
                    "base_commit": "a" * 40,
                    "head_commit": "b" * 40,
                }
            ]
        },
        "handoff": {
            "change_summary": "Repair cache invalidation after branch changes.",
            "decisions": ["Retain the existing cache schema to avoid a migration."],
            "focus_areas": ["Branch transition invalidation and concurrent readers."],
            "traps": ["A fake clock concealed one stale cache scenario."],
            "validation_summary": "Unit and database regression tests pass.",
        },
    }


def configure(api, project, **fields):
    path = f"/api/v1/projects/{project['id']}/settings"
    revision = api.get(path).json()["revision"]
    response = api.patch(path, json={"expected_revision": revision, **fields})
    assert response.status_code == 200, response.text
    return response.json()


def mandatory(api, project, work_payload, checkpoint_fields, *, allow=False):
    configure(
        api, project, code_review_required_min_priority=0, allow_remediation_code_reviews=allow
    )
    work = create(api, project, work_payload)
    response, payload = close(api, project, work, checkpoint_fields, code_review_handoff=handoff())
    assert response.status_code == 200, response.text
    return response.json(), payload


def claim_review(api, project, completion, checkpoint_fields, *, mode="cold"):
    review = completion["code_review_request"]
    base = f"/api/v1/projects/{project['id']}/work-items/{review['work_item_id']}"
    response = api.post(
        base + "/claim",
        json={
            "holder_client": "review-client",
            "holder_session_id": "review-session",
            "claim_request_id": str(uuid4()),
            "purpose": "code_review",
            "code_review_id": review["id"],
            "mode": mode,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def finding(key="F001"):
    return {
        "finding_key": key,
        "severity": "high",
        "title": "Invalidation misses readers",
        "repository_key": "main",
        "path": "src/cache.py",
        "location_side": "head",
        "start_line": 20,
        "end_line": 24,
        "problem": "Readers retain the old cache.",
        "triggering_conditions": "A branch switch overlaps a read.",
        "impact": "Stale values leak into subsequent work.",
        "evidence": "The old handle remains reachable after invalidation.",
        "recommended_verification": "Add a concurrent branch-switch regression test.",
    }


def result_payload(completion, lease, *, findings=None):
    review = completion["code_review_request"]
    repository = handoff()["scope"]["repositories"][0]
    return {
        "client_operation_id": str(uuid4()),
        "expected_review_version": review["version"],
        "scope_sha256": review["scope_sha256"],
        "lease_token": lease["lease_token"],
        "actor": {"actor_client": "review-client", "actor_session_id": "review-session"},
        "result": {
            "mode": lease["mode"],
            "summary": "Adversarial review completed.",
            "coverage": [
                {key: repository[key] for key in ("repository_key", "base_commit", "head_commit")}
            ],
            "limitations": [],
            "findings": [] if findings is None else findings,
        },
    }


def result_url(project, completion):
    review = completion["code_review_request"]
    return (
        f"/api/v1/projects/{project['id']}/work-items/{review['work_item_id']}"
        f"/code-reviews/{review['id']}/complete"
    )
