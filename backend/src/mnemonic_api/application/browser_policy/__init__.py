"""Pure request policy for the dashboard JSON facade.

This is the Python counterpart of frontend/lib/proxy-policy.ts. Browser-safe
requests still pass through normal REST validation, receipt replay and services.
"""

from collections.abc import Callable
from typing import Any

from mnemonic_api.application.browser_policy import actions, projects, work
from mnemonic_api.application.browser_policy.errors import (
    DEFINITIVE_PROXY_ERRORS,
    DefinitiveProxyError,
    unsupported_mutation_field_error,
)
from mnemonic_api.application.browser_policy.routes import (
    PROTECTED_MUTATIONS,
    allowed_query_keys,
    proxy_body_limit_bytes,
    route_name,
    valid_query_values,
)
from mnemonic_api.application.browser_policy.search import search_request
from mnemonic_api.application.browser_policy.transport import (
    forbidden_control_transport,
    trusted_request,
)
from mnemonic_api.application.browser_policy.values import JsonObject, checkpoint, uuid

__all__ = [
    "DEFINITIVE_PROXY_ERRORS",
    "DefinitiveProxyError",
    "allowed_query_keys",
    "client_operation_matches_secret",
    "forbidden_control_transport",
    "invalid_mutation_body",
    "proxy_body_limit_bytes",
    "route_name",
    "trusted_request",
    "valid_query_values",
]

_VALIDATORS: dict[tuple[str, str], tuple[Callable[[JsonObject], bool], str]] = {
    ("project", "PATCH"): (projects.project_patch, "invalidProjectPatch"),
    ("settings", "PATCH"): (projects.settings_patch, "invalidProjectSettingsPatch"),
    ("rubric", "PATCH"): (projects.rubric_patch, "invalidPriorityRubricPatch"),
    ("suggestions", "POST"): (projects.suggestions, "invalidDuplicateSuggestion"),
    ("work_items", "POST"): (work.work_creation, "invalidWorkCreation"),
    ("work_item", "PATCH"): (work.work_patch, "invalidWorkItemPatch"),
    ("checkpoints", "POST"): (
        lambda body: checkpoint(body, include_kind=True),
        "invalidCheckpoint",
    ),
    ("events", "POST"): (actions.progress_event, "invalidProgressEvent"),
    ("defer", "POST"): (actions.deferral, "invalidWorkItemDeferral"),
    ("pending", "POST"): (actions.pending, "invalidWorkPending"),
    ("delete", "POST"): (actions.deletion, "invalidWorkItemDeletion"),
    ("relationships", "POST"): (actions.relationship_creation, "invalidRelationshipCreation"),
    ("relationship", "DELETE"): (actions.relationship_removal, "invalidRelationshipRemoval"),
    ("complete", "POST"): (work.work_completion, "invalidWorkCompletion"),
    ("follow_up_answer", "POST"): (actions.follow_up, "invalidWorkFollowUp"),
    ("gate_resolve", "POST"): (actions.gate_resolution, "invalidHumanGateResolution"),
    ("report_dismiss", "POST"): (actions.report_dismissal, "invalidReportDismissal"),
    ("report_follow_ups", "POST"): (actions.report_follow_up, "invalidReportFollowUp"),
    ("merge", "POST"): (actions.merge, "invalidWorkMerge"),
}


def _forbidden_field(value: Any) -> str | None:
    if isinstance(value, list):
        return next((field for entry in value if (field := _forbidden_field(entry))), None)
    if isinstance(value, dict):
        for key, entry in value.items():
            if key.lower() in {"lease_token", "gate_id", "gate_type"}:
                return key
            if field := _forbidden_field(entry):
                return field
    return None


def _nested_operation(value: Any, *, root: bool = True) -> bool:
    if isinstance(value, list):
        return any(_nested_operation(entry, root=False) for entry in value)
    if isinstance(value, dict):
        return any(
            (not root and key.lower() == "client_operation_id")
            or _nested_operation(entry, root=False)
            for key, entry in value.items()
        )
    return False


def _control_error(name: str | None, method: str, body: JsonObject) -> str | None:
    if field := _forbidden_field(body):
        return unsupported_mutation_field_error(field)
    if _nested_operation(body):
        return DEFINITIVE_PROXY_ERRORS["nestedClientOperation"].detail
    protected = (name, method) in PROTECTED_MUTATIONS
    if not protected and any(key.lower() == "client_operation_id" for key in body):
        return DEFINITIVE_PROXY_ERRORS["unsupportedClientOperation"].detail
    if protected and not uuid(body.get("client_operation_id")):
        return DEFINITIVE_PROXY_ERRORS["invalidClientOperation"].detail
    return None


def _special_error(name: str | None, path: str, method: str, body: JsonObject) -> str | None:
    if name == "prompt" and method == "PUT" and not projects.prompt_update(path, body):
        return "The prompt update requires valid content and its current revision."
    if name == "prompt_render" and not projects.prompt_render(body):
        return "The prompt render request accepts only work and review IDs."
    if name == "search" and not search_request(body):
        return "Invalid unified search request."
    if name == "move" and not actions.move(path, body):
        return DEFINITIVE_PROXY_ERRORS["invalidWorkItemMove"].detail
    return None


def invalid_mutation_body(path: str, method: str, body: Any) -> str | None:
    if not isinstance(body, dict):
        return DEFINITIVE_PROXY_ERRORS["jsonObjectRequired"].detail
    name = route_name(path, method)
    if error := _control_error(name, method, body):
        return error
    if error := _special_error(name, path, method, body):
        return error
    entry = _VALIDATORS.get((name or "", method))
    if entry is not None and not entry[0](body):
        return DEFINITIVE_PROXY_ERRORS[entry[1]].detail
    return None


def client_operation_matches_secret(body: Any, secret: str) -> bool:
    return isinstance(body, dict) and body.get("client_operation_id") == secret
