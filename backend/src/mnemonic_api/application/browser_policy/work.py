"""Browser work creation, patches and completion payloads."""

from typing import Any

from mnemonic_api.application.browser_policy.contracts import (
    completion_evidence,
    external_references,
    job_report,
    review_handoff,
)
from mnemonic_api.application.browser_policy.values import (
    JsonObject,
    actor,
    allowed_keys,
    checkpoint,
    dashboard_actor,
    integer,
    optional,
    text,
    uuid,
)

OPERATION = "client_operation_id"
WORK_STATUSES = ("pending", "wont-do", "promoted")
RELATIONSHIP_TYPES = ("blocks", "parent-child", "discovered-from", "related")


def initial_relationship(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and allowed_keys(
            value, ["type", "direction", "other_work_item_id", "context_checkpoint_id",
                    "requires_child_closeout"]
        )
        and closeout_requirement(value, "type")
        and value.get("type") in RELATIONSHIP_TYPES
        and value.get("direction") in ("incoming", "outgoing")
        and uuid(value.get("other_work_item_id"))
        and optional(value, "context_checkpoint_id", lambda item: item is None or uuid(item))
        and (
            value.get("type") != "discovered-from"
            or value.get("direction") == "outgoing"
            and uuid(value.get("context_checkpoint_id"))
        )
    )


def work_creation(body: JsonObject) -> bool:
    return (
        allowed_keys(
            body,
            [
                "title",
                "summary",
                "priority",
                "status",
                "initial_checkpoint",
                "initial_relationships",
                "external_references",
                OPERATION,
            ],
        )
        and text(body.get("title"), 200)
        and text(body.get("summary"), float("inf"))
        and integer(body.get("priority"), 0, 100)
        and body.get("status") in WORK_STATUSES
        and checkpoint(body.get("initial_checkpoint"))
        and optional(
            body,
            "initial_relationships",
            lambda item: (
                isinstance(item, list)
                and len(item) <= 10
                and all(initial_relationship(entry) for entry in item)
            ),
        )
        and optional(body, "external_references", external_references)
    )


def review_supersession(body: JsonObject) -> bool:
    review = "supersede_code_review_id" in body or "expected_code_review_version" in body
    question = "supersede_follow_up_id" in body or "expected_follow_up_version" in body
    if not review and not question:
        return True
    if review and question or body.get("status") != "pending":
        return False
    if review:
        return uuid(body.get("supersede_code_review_id")) and integer(
            body.get("expected_code_review_version"), 1, 2**31 - 1
        )
    return uuid(body.get("supersede_follow_up_id")) and integer(
        body.get("expected_follow_up_version"), 1, 2**31 - 1
    )


def manual_review_request(body: JsonObject) -> bool:
    options = (
        "request_code_review_mode",
        "request_code_review_checkpoint_id",
        "code_review_handoff",
    )
    if "request_code_review" not in body:
        return all(key not in body for key in options)
    return (
        body.get("request_code_review") is True
        and allowed_keys(
            body, ["expected_version", "actor", OPERATION, "request_code_review", *options]
        )
        and dashboard_actor(body.get("actor"), no_model=True)
        and optional(body, "request_code_review_mode", lambda item: item in ("warm", "cold"))
        and optional(body, "request_code_review_checkpoint_id", uuid)
        and optional(body, "code_review_handoff", review_handoff)
        and (
            body.get("request_code_review_mode") != "cold"
            or review_handoff(body.get("code_review_handoff"))
        )
    )


def human_review_decision(body: JsonObject) -> bool:
    decision = body.get("review_decision")
    if (
        not isinstance(decision, dict)
        or not dashboard_actor(body.get("actor"), no_model=True)
        or not allowed_keys(body, ["expected_version", "actor", OPERATION, "review_decision"])
        or not allowed_keys(
            decision,
            ["resource_id", "expected_decision_version", "status", "job_completion_report"],
        )
        or not uuid(decision.get("resource_id"))
        or not integer(decision.get("expected_decision_version"))
        or decision.get("status") not in ("to-review", "deferred", "done", "wont-do", "promoted")
    ):
        return False
    if decision.get("status") in ("done", "wont-do", "promoted"):
        return job_report(decision.get("job_completion_report"))
    return "job_completion_report" not in decision


def work_patch(body: JsonObject) -> bool:
    keys = [
        "expected_version",
        "title",
        "summary",
        "priority",
        "status",
        "actor",
        "job_completion_report",
        "external_references",
        "review_decision",
        "request_code_review",
        "request_code_review_mode",
        "request_code_review_checkpoint_id",
        "code_review_handoff",
        "supersede_code_review_id",
        "expected_code_review_version",
        "supersede_follow_up_id",
        "expected_follow_up_version",
        "subagent_transcripts",
        OPERATION,
    ]
    changes = [
        "title",
        "summary",
        "priority",
        "status",
        "external_references",
        "review_decision",
        "request_code_review",
    ]
    return (
        allowed_keys(body, keys)
        and body.get("subagent_transcripts") is None
        and integer(body.get("expected_version"), 1)
        and actor(body.get("actor"))
        and optional(body, "job_completion_report", job_report)
        and review_supersession(body)
        and manual_review_request(body)
        and ("review_decision" not in body or human_review_decision(body))
        and any(key in body for key in changes)
        and _patch_fields(body)
    )


def _patch_fields(body: JsonObject) -> bool:
    return (
        optional(body, "external_references", external_references)
        and optional(body, "title", lambda item: text(item, 200))
        and optional(body, "summary", lambda item: text(item, float("inf")))
        and optional(body, "priority", lambda item: integer(item, 0, 100))
        and optional(body, "status", lambda item: item in WORK_STATUSES)
    )


def work_completion(body: JsonObject) -> bool:
    return (
        allowed_keys(
            body,
            [
                "expected_version",
                "checkpoint",
                "completion_evidence",
                "job_completion_report",
                "code_review_handoff",
                "subagent_transcripts",
                OPERATION,
            ],
        )
        and body.get("subagent_transcripts") is None
        and integer(body.get("expected_version"), 1, 2**31 - 2)
        and checkpoint(body.get("checkpoint"))
        and optional(body, "completion_evidence", completion_evidence)
        and optional(body, "job_completion_report", job_report)
        and optional(body, "code_review_handoff", review_handoff)
    )


def closeout_requirement(value: JsonObject, type_field: str) -> bool:
    return ("requires_child_closeout" not in value
            or isinstance(value["requires_child_closeout"], bool)
            and (not value["requires_child_closeout"] or value.get(type_field) == "parent-child"))
