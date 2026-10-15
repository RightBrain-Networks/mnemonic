"""Stable browser rejection envelopes used to classify definitive mutation failures."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DefinitiveProxyError:
    status: int
    detail: str


DEFINITIVE_PROXY_ERRORS = {
    "requestBodyRequired": DefinitiveProxyError(
        400,
        "A request body is required.",
    ),
    "jsonObjectRequired": DefinitiveProxyError(
        400,
        "The request body must be a JSON object.",
    ),
    "invalidJson": DefinitiveProxyError(
        400,
        "The request body is not valid JSON.",
    ),
    "forbiddenControlTransport": DefinitiveProxyError(
        400,
        "Gate, operation, and lease controls are not accepted in headers or cookies.",
    ),
    "nestedClientOperation": DefinitiveProxyError(
        400,
        "The client operation ID is accepted only at the top level.",
    ),
    "unsupportedClientOperation": DefinitiveProxyError(
        400,
        "The client operation ID is not supported for this route.",
    ),
    "invalidClientOperation": DefinitiveProxyError(
        400,
        "The client operation ID must be a UUID.",
    ),
    "unsupportedQuery": DefinitiveProxyError(
        400,
        "Unsupported or repeated query parameter.",
    ),
    "invalidWorkCreation": DefinitiveProxyError(
        400,
        "The work-creation body does not match the dashboard allowlist.",
    ),
    "invalidDuplicateSuggestion": DefinitiveProxyError(
        400,
        "The duplicate-suggestion body does not match the dashboard allowlist.",
    ),
    "invalidCheckpoint": DefinitiveProxyError(
        400,
        "The checkpoint body does not match the dashboard allowlist.",
    ),
    "invalidProgressEvent": DefinitiveProxyError(
        400,
        "The progress-event body does not match the dashboard allowlist.",
    ),
    "invalidWorkItemPatch": DefinitiveProxyError(
        400,
        "The work-item patch does not match the dashboard allowlist.",
    ),
    "invalidWorkItemDeferral": DefinitiveProxyError(
        400,
        "The work-item deferral does not match the dashboard allowlist.",
    ),
    "invalidWorkPending": DefinitiveProxyError(
        400,
        "The work-item Pending action does not match the dashboard allowlist.",
    ),
    "invalidProjectSettingsPatch": DefinitiveProxyError(
        400,
        "The project-settings patch does not match the dashboard allowlist.",
    ),
    "invalidPriorityRubricPatch": DefinitiveProxyError(
        400,
        "The priority-rubric patch does not match the dashboard allowlist.",
    ),
    "invalidProjectPatch": DefinitiveProxyError(
        400,
        "The project patch does not match the dashboard allowlist.",
    ),
    "invalidWorkItemDeletion": DefinitiveProxyError(
        400,
        "The work-item deletion does not match the dashboard allowlist.",
    ),
    "invalidWorkItemMove": DefinitiveProxyError(
        400,
        "The work-item move does not match the dashboard allowlist.",
    ),
    "invalidRelationshipRemoval": DefinitiveProxyError(
        400,
        "The relationship-removal body does not match the dashboard allowlist.",
    ),
    "invalidRelationshipCreation": DefinitiveProxyError(
        400,
        "The relationship-creation body does not match the dashboard allowlist.",
    ),
    "invalidWorkCompletion": DefinitiveProxyError(
        400,
        "The work-completion body does not match the dashboard allowlist.",
    ),
    "invalidWorkFollowUp": DefinitiveProxyError(
        400,
        "The follow-up answer does not match the dashboard allowlist.",
    ),
    "invalidHumanGateResolution": DefinitiveProxyError(
        400,
        "The human-gate resolution body does not match the dashboard allowlist.",
    ),
    "invalidReportDismissal": DefinitiveProxyError(
        400,
        "The report-dismissal body does not match the dashboard allowlist.",
    ),
    "invalidReportFollowUp": DefinitiveProxyError(
        400,
        "The report follow-up body does not match the dashboard allowlist.",
    ),
    "invalidWorkMerge": DefinitiveProxyError(
        400,
        "The work-merge body does not match the dashboard allowlist.",
    ),
    "untrustedOrigin": DefinitiveProxyError(
        403,
        "This dashboard request is not from a trusted origin.",
    ),
    "routeNotFound": DefinitiveProxyError(
        404,
        "Route not found.",
    ),
    "bodyTooLarge": DefinitiveProxyError(
        413,
        "Request body is too large.",
    ),
    "jsonContentTypeRequired": DefinitiveProxyError(
        415,
        "Send a JSON request body.",
    ),
    "clientOperationCredentialMatch": DefinitiveProxyError(
        422,
        "The client operation ID cannot match a request credential.",
    ),
}


def unsupported_mutation_field_error(field: str) -> str:
    return "The request body contains an unsupported field: " + field + "."
