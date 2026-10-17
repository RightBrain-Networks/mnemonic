"""Exact browser action bodies, with dashboard provenance where the contract requires it."""

from typing import Any

from mnemonic_api.application.browser_policy.contracts import follow_up_answer
from mnemonic_api.application.browser_policy.values import (
    JsonObject,
    actor,
    allowed_keys,
    checkpoint,
    dashboard_actor,
    exact_keys,
    integer,
    metadata,
    nullable_text,
    optional,
    revision,
    text,
    timestamp,
    uuid,
)
from mnemonic_api.application.browser_policy.work import (
    OPERATION,
    RELATIONSHIP_TYPES,
    closeout_requirement,
)


def progress_event(body: JsonObject) -> bool:
    return (
        exact_keys(body, ["event_type", "body", "metadata", "actor", OPERATION])
        and body.get("event_type") == "progress"
        and text(body.get("body"), 4_000)
        and metadata(body.get("metadata"))
        and actor(body.get("actor"))
    )


def deferral(body: JsonObject) -> bool:
    return (
        allowed_keys(body, ["expected_version", "actor", OPERATION])
        and integer(body.get("expected_version"), 1)
        and actor(body.get("actor"))
    )


def deletion(body: JsonObject) -> bool:
    return (
        allowed_keys(body, ["expected_version", "actor", "subagent_transcripts", OPERATION])
        and body.get("subagent_transcripts") is None
        and integer(body.get("expected_version"), 1)
        and actor(body.get("actor"))
    )


def expected_active_lease(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and exact_keys(
            value, ["holder_client", "holder_session_id", "acquired_at", "renewed_at", "expires_at"]
        )
        and text(value.get("holder_client"), 80)
        and text(value.get("holder_session_id"), 200)
        and all(
            timestamp(value.get(field)) for field in ("acquired_at", "renewed_at", "expires_at")
        )
    )


def pending(body: JsonObject) -> bool:
    state = body.get("expected_lease_state")
    return (
        exact_keys(
            body, ["expected_version", "expected_lease_state", "expected_active_lease", "actor"]
        )
        and integer(body.get("expected_version"), 1)
        and state in ("active", "dropped")
        and (
            expected_active_lease(body.get("expected_active_lease"))
            if state == "active"
            else body.get("expected_active_lease") is None
        )
        and dashboard_actor(body.get("actor"), no_model=True)
    )


def move(path: str, body: JsonObject) -> bool:
    target = body.get("target_project_id")
    return (
        exact_keys(body, ["target_project_id", "expected_version", "actor", OPERATION])
        and uuid(target)
        and target.lower() != path.split("/")[1].lower()
        and integer(body.get("expected_version"), 1)
        and actor(body.get("actor"))
    )


def relationship_removal(body: JsonObject) -> bool:
    return exact_keys(body, ["actor", OPERATION]) and actor(body.get("actor"))


def relationship_creation(body: JsonObject) -> bool:
    source, target = body.get("source_work_item_id"), body.get("target_work_item_id")
    return (
        allowed_keys(
            body,
            [
                "relationship_type",
                "requires_child_closeout",
                "replace_relationship_id",
                "source_work_item_id",
                "target_work_item_id",
                "created_by_client",
                "created_by_session_id",
                "created_by_model",
                "context_checkpoint_id",
                OPERATION,
            ],
        )
        and closeout_requirement(body, "relationship_type")
        and optional(body, "replace_relationship_id", lambda item:
                     uuid(item) and body.get("relationship_type") == "parent-child")
        and body.get("relationship_type") in RELATIONSHIP_TYPES
        and uuid(source)
        and uuid(target)
        and source.lower() != target.lower()
        and text(body.get("created_by_client"), 80)
        and text(body.get("created_by_session_id"), 200)
        and optional(body, "created_by_model", lambda item: nullable_text(item, 120))
        and optional(body, "context_checkpoint_id", lambda item: item is None or uuid(item))
        and (
            body.get("relationship_type") != "discovered-from"
            or uuid(body.get("context_checkpoint_id"))
        )
    )


def follow_up(body: JsonObject) -> bool:
    return (
        allowed_keys(body, ["expected_follow_up_version", "actor", "answer", OPERATION])
        and integer(body.get("expected_follow_up_version"), 1, 2**31 - 1)
        and dashboard_actor(body.get("actor"))
        and follow_up_answer(body.get("answer"))
    )


def gate_resolution(body: JsonObject) -> bool:
    return (
        allowed_keys(
            body,
            [
                "resolution",
                "resolved_by_client",
                "resolved_by_session_id",
                "resolved_by_model",
                "reviewed_context_revision",
                "expected_question_version",
                OPERATION,
            ],
        )
        and text(body.get("resolution"), 4_000)
        and body.get("resolved_by_client") == "dashboard"
        and text(body.get("resolved_by_session_id"), 200)
        and body.get("resolved_by_model") is None
        and optional(body, "expected_question_version", lambda item: integer(item, 1))
        and revision(body.get("reviewed_context_revision"))
    )


def report_dismissal(body: JsonObject) -> bool:
    return allowed_keys(body, ["actor", OPERATION]) and dashboard_actor(body.get("actor"))


def report_follow_up(body: JsonObject) -> bool:
    author, initial = body.get("actor"), body.get("initial_checkpoint")
    if not dashboard_actor(author) or not checkpoint(initial):
        return False
    return (
        allowed_keys(
            body, ["title", "summary", "priority", "initial_checkpoint", "actor", OPERATION]
        )
        and text(body.get("title"), 200)
        and text(body.get("summary"), float("inf"))
        and optional(body, "priority", lambda item: integer(item, 0, 100))
        and all(
            initial.get("source_" + field) == author.get("actor_" + field)
            for field in ("client", "session_id", "model")
        )
    )


def merge(body: JsonObject) -> bool:
    fields = [
        "destination_work_item_id",
        "reviewed_source_revision",
        "reviewed_destination_revision",
        "rationale",
        "merged_by_client",
        "merged_by_session_id",
        "merged_by_model",
        OPERATION,
    ]
    if "subagent_transcripts" in body:
        fields.append("subagent_transcripts")
    return (
        exact_keys(body, fields)
        and body.get("subagent_transcripts") is None
        and uuid(body.get("destination_work_item_id"))
        and revision(body.get("reviewed_source_revision"), merge=True)
        and revision(body.get("reviewed_destination_revision"), merge=True)
        and text(body.get("rationale"), 4_000)
        and body.get("merged_by_client") == "dashboard"
        and text(body.get("merged_by_session_id"), 200)
        and body.get("merged_by_model") is None
    )
