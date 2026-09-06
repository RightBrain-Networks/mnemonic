"""Stable, sanitized application errors with client-safe machine-readable codes."""

from typing import Any

from fastapi import HTTPException

SAFE_ERROR_CONTEXT_KEYS = frozenset(
    {
        "holder_client", "holder_session_id", "expires_at", "purpose", "code_review_id", "mode",
        "canonical_work_item_id",
    }
)
SAFE_ERROR_FIELD_LOCATIONS = frozenset(
    {
        "actor.actor_client",
        "actor.actor_model",
        "actor.actor_session_id",
        "body",
        "metadata.key",
        "metadata.value",
    }
)


def _safe_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {}
    safe = {key: value for key, value in context.items() if key in SAFE_ERROR_CONTEXT_KEYS}
    fields = context.get("fields")
    if isinstance(fields, list) and all(field in SAFE_ERROR_FIELD_LOCATIONS for field in fields):
        safe["fields"] = sorted(set(fields))
    return safe


class ApplicationError(HTTPException):
    """An HTTP error whose machine-readable code is safe to expose to clients."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
                "context": _safe_context(context),
            },
            headers=headers,
        )


def not_found(code: str, message: str) -> ApplicationError:
    return ApplicationError(404, code, message)


def conflict(
    code: str,
    message: str,
    *,
    context: dict[str, Any] | None = None,
) -> ApplicationError:
    return ApplicationError(409, code, message, context=context)


def client_operation_conflict() -> ApplicationError:
    return ApplicationError(
        409,
        "client_operation_conflict",
        (
            "This client operation ID is already bound to a different successful request. "
            "Use a new ID only for a genuinely new intent."
        ),
    )


def client_operation_unavailable() -> ApplicationError:
    return ApplicationError(
        503,
        "client_operation_unavailable",
        "Client operation safety is unavailable. Retry the same ID with the exact same request.",
    )


def client_operation_secret_echo() -> ApplicationError:
    return ApplicationError(
        422,
        "client_operation_secret_echo",
        "Client operation or capability data cannot appear in public content fields.",
    )


def completion_evidence_unavailable() -> ApplicationError:
    return ApplicationError(
        503,
        "completion_evidence_unavailable",
        "Structured completion evidence is temporarily unavailable.",
    )



def completion_episode_unsealed() -> ApplicationError:
    return ApplicationError(
        409,
        "completion_episode_unsealed",
        (
            "This work was completed without a sealed completion episode and cannot leave "
            "done. Its history is retained; record any continuation as new work."
        ),
    )


def closeout_report_unsealed() -> ApplicationError:
    return ApplicationError(
        409,
        "closeout_report_unsealed",
        "This terminal work item has no sealed closeout report and cannot be moved.",
    )


def gate_not_found() -> ApplicationError:
    return not_found("gate_not_found", "Human gate not found in this work item.")


def work_gated() -> ApplicationError:
    return conflict(
        "work_gated",
        "This work item has unresolved human input. Review and resolve every gate first.",
    )


def gate_already_resolved() -> ApplicationError:
    return conflict(
        "gate_already_resolved",
        "This human gate is already resolved and cannot be overwritten.",
    )


def gate_context_changed() -> ApplicationError:
    return conflict(
        "gate_context_changed",
        "The work context changed. Reload and review the current revision before resolving.",
    )


def gate_secret_echo() -> ApplicationError:
    return ApplicationError(
        422,
        "gate_secret_echo",
        "Credential or control data cannot appear in durable human-gate fields.",
    )


def duplicate_merge_required() -> ApplicationError:
    return conflict(
        "duplicate_merge_required",
        "Fresh duplicate-of relationships must be created by merge_work.",
    )


def duplicate_self() -> ApplicationError:
    return conflict("duplicate_self", "A work item cannot be merged into itself.")


def work_duplicate(canonical_work_item_id: object) -> ApplicationError:
    return conflict(
        "work_duplicate",
        "This work item is a retained duplicate alias and cannot be mutated or claimed.",
        context={"canonical_work_item_id": str(canonical_work_item_id)},
    )


def work_already_duplicate() -> ApplicationError:
    return conflict(
        "work_already_duplicate",
        "This merge source already has an authoritative destination.",
    )


def duplicate_destination_not_canonical() -> ApplicationError:
    return conflict(
        "duplicate_destination_not_canonical",
        "The merge destination is no longer canonical. Read both contexts again.",
    )


def duplicate_context_changed() -> ApplicationError:
    return conflict(
        "duplicate_context_changed",
        "A reviewed work context changed. Read and review both current contexts again.",
    )


def duplicate_source_gate_unresolved() -> ApplicationError:
    return conflict(
        "duplicate_source_gate_unresolved",
        "Resolve every human gate on the merge source, then review fresh contexts.",
    )


def duplicate_structural_relationships() -> ApplicationError:
    return conflict(
        "duplicate_structural_relationships",
        "Remove or reconcile source blocks and parent-child relationships before merging.",
    )


def duplicate_depth_exceeded() -> ApplicationError:
    return conflict(
        "duplicate_depth_exceeded",
        "The authoritative duplicate path would exceed 50 edges.",
    )


def duplicate_relationship_frozen() -> ApplicationError:
    return conflict(
        "duplicate_relationship_frozen",
        "Relationships incident to a duplicate alias are retained audit facts and cannot change.",
    )


def duplicate_graph_invalid() -> ApplicationError:
    return ApplicationError(
        503,
        "duplicate_graph_invalid",
        "The authoritative duplicate graph is invalid. Authority-changing work is unavailable.",
    )


def request_body_too_large() -> ApplicationError:
    return ApplicationError(
        413,
        "request_body_too_large",
        "The duplicate-suggestion request body exceeds the allowed size.",
    )


def duplicate_suggestion_busy() -> ApplicationError:
    return ApplicationError(
        429,
        "duplicate_suggestion_busy",
        "Duplicate suggestions are busy. Retry this safe read later.",
        headers={"Retry-After": "1"},
    )


def duplicate_suggestion_unavailable() -> ApplicationError:
    return ApplicationError(
        503,
        "duplicate_suggestion_unavailable",
        "Duplicate suggestions are unavailable. Work creation remains available.",
    )


def semantic_unavailable() -> ApplicationError:
    return ApplicationError(
        503,
        "semantic_unavailable",
        "Semantic search is unavailable. Turn it off to use lexical search.",
    )
