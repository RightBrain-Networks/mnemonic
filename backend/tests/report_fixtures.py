"""Explicit authored closeout inputs for tests whose subject predates Phase 12."""

from uuid import uuid4


def reported(payload: dict, *, retirement: bool = False) -> dict:
    """Add a fresh report intent at the call site; never intercept the test client."""
    fields = {
        "client_operation_id": str(uuid4()),
        "job_completion_report": {
            "summary": "This test work reached its closeout. Its outcome is ready to review.",
            "fyi_items": [],
            "prompt_revision": "1",
        },
    }
    if retirement:
        fields["actor"] = {"actor_client": "test-human", "actor_session_id": "report-fixture"}
    return {**fields, **payload}
