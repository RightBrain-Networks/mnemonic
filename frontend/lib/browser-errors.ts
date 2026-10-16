// Browser-visible rejection envelopes shared with FastAPI. These exact messages
// distinguish a definite pre-dispatch rejection from an uncertain mutation.
export interface DefinitiveProxyError {
  readonly status: 400 | 403 | 404 | 413 | 415 | 422;
  readonly detail: string;
}

export const DEFINITIVE_PROXY_ERRORS = {
  requestBodyRequired: { status: 400, detail: "A request body is required." },
  jsonObjectRequired: { status: 400, detail: "The request body must be a JSON object." },
  invalidJson: { status: 400, detail: "The request body is not valid JSON." },
  forbiddenControlTransport: {
    status: 400,
    detail: "Gate, operation, and lease controls are not accepted in headers or cookies."
  },
  nestedClientOperation: {
    status: 400,
    detail: "The client operation ID is accepted only at the top level."
  },
  unsupportedClientOperation: {
    status: 400,
    detail: "The client operation ID is not supported for this route."
  },
  invalidClientOperation: {
    status: 400,
    detail: "The client operation ID must be a UUID."
  },
  unsupportedQuery: { status: 400, detail: "Unsupported or repeated query parameter." },
  invalidWorkCreation: {
    status: 400,
    detail: "The work-creation body does not match the dashboard allowlist."
  },
  invalidDuplicateSuggestion: {
    status: 400,
    detail: "The duplicate-suggestion body does not match the dashboard allowlist."
  },
  invalidCheckpoint: {
    status: 400,
    detail: "The checkpoint body does not match the dashboard allowlist."
  },
  invalidProgressEvent: {
    status: 400,
    detail: "The progress-event body does not match the dashboard allowlist."
  },
  invalidWorkItemPatch: {
    status: 400,
    detail: "The work-item patch does not match the dashboard allowlist."
  },
  invalidWorkItemDeferral: {
    status: 400,
    detail: "The work-item deferral does not match the dashboard allowlist."
  },
  invalidWorkPending: {
    status: 400,
    detail: "The work-item Pending action does not match the dashboard allowlist."
  },
  invalidProjectSettingsPatch: {
    status: 400,
    detail: "The project-settings patch does not match the dashboard allowlist."
  },
  invalidPriorityRubricPatch: {
    status: 400,
    detail: "The priority-rubric patch does not match the dashboard allowlist."
  },
  invalidProjectPatch: {
    status: 400,
    detail: "The project patch does not match the dashboard allowlist."
  },
  invalidWorkItemDeletion: {
    status: 400,
    detail: "The work-item deletion does not match the dashboard allowlist."
  },
  invalidWorkItemMove: {
    status: 400,
    detail: "The work-item move does not match the dashboard allowlist."
  },
  invalidRelationshipRemoval: {
    status: 400,
    detail: "The relationship-removal body does not match the dashboard allowlist."
  },
  invalidRelationshipCreation: {
    status: 400,
    detail: "The relationship-creation body does not match the dashboard allowlist."
  },
  invalidWorkCompletion: {
    status: 400,
    detail: "The work-completion body does not match the dashboard allowlist."
  },
  invalidWorkFollowUp: { status: 400, detail: "The follow-up answer does not match the dashboard allowlist." },
  invalidHumanGateResolution: {
    status: 400,
    detail: "The human-gate resolution body does not match the dashboard allowlist."
  },
  invalidReportDismissal: { status: 400, detail: "The report-dismissal body does not match the dashboard allowlist." },
  invalidReportFollowUp: { status: 400, detail: "The report follow-up body does not match the dashboard allowlist." },
  invalidWorkMerge: {
    status: 400,
    detail: "The work-merge body does not match the dashboard allowlist."
  },
  untrustedOrigin: {
    status: 403,
    detail: "This dashboard request is not from a trusted origin."
  },
  routeNotFound: { status: 404, detail: "Route not found." },
  bodyTooLarge: { status: 413, detail: "Request body is too large." },
  jsonContentTypeRequired: { status: 415, detail: "Send a JSON request body." },
  clientOperationCredentialMatch: {
    status: 422,
    detail: "The client operation ID cannot match a request credential."
  }
} as const satisfies Readonly<Record<string, DefinitiveProxyError>>;

const UNSUPPORTED_MUTATION_FIELD_PREFIX =
  "The request body contains an unsupported field: ";
const definitiveProxyErrorLookup = new Map<number, ReadonlySet<string>>();
for (const error of Object.values(DEFINITIVE_PROXY_ERRORS)) {
  const messages = definitiveProxyErrorLookup.get(error.status);
  definitiveProxyErrorLookup.set(
    error.status,
    new Set(messages ? [...messages, error.detail] : [error.detail])
  );
}

export function unsupportedMutationFieldError(field: string): DefinitiveProxyError {
  return {
    status: 400,
    detail: UNSUPPORTED_MUTATION_FIELD_PREFIX + field + "."
  };
}

export function isDefinitiveProxyError(status: number, detail: string): boolean {
  return definitiveProxyErrorLookup.get(status)?.has(detail) === true
    || status === 400
      && detail.startsWith(UNSUPPORTED_MUTATION_FIELD_PREFIX)
      && detail.length > UNSUPPORTED_MUTATION_FIELD_PREFIX.length + 1
      && detail.endsWith(".");
}
