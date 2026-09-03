import {
  UUID_PATTERN,
  boundedText,
  finiteInteger,
  nullableBoundedText,
  objectValue as jsonObject,
  validBoundedMetadata,
  validUuid
} from "./wire-guards.ts";

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
  invalidProjectSettingsPatch: {
    status: 400,
    detail: "The project-settings patch does not match the dashboard allowlist."
  },
  invalidWorkItemDeletion: {
    status: 400,
    detail: "The work-item deletion does not match the dashboard allowlist."
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
  invalidHumanGateResolution: {
    status: 400,
    detail: "The human-gate resolution body does not match the dashboard allowlist."
  },
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

const UUID = UUID_PATTERN.source.slice(1, -1);
const PROJECT = new RegExp(`^projects/${UUID}$`);
const PROJECT_SETTINGS = new RegExp(`^projects/${UUID}/settings$`);
const WORK_ITEMS = new RegExp(`^projects/${UUID}/work-items$`);
const DUPLICATE_SUGGESTIONS = new RegExp(`^projects/${UUID}/duplicate-suggestions$`);
const WORK_ITEM = new RegExp(`^projects/${UUID}/work-items/${UUID}$`);
const CHECKPOINTS = new RegExp(`^projects/${UUID}/work-items/${UUID}/checkpoints$`);
const WORK_CONTEXT = new RegExp(`^projects/${UUID}/work-items/${UUID}/context$`);
const WORK_CHILDREN = new RegExp(`^projects/${UUID}/work-items/${UUID}/children$`);
const WORK_RELATIONSHIPS = new RegExp(`^projects/${UUID}/work-items/${UUID}/relationships$`);
const RELATIONSHIPS = new RegExp(`^projects/${UUID}/relationships$`);
const RELATIONSHIP = new RegExp(`^projects/${UUID}/relationships/${UUID}$`);
const WORK_COMPLETE = new RegExp(`^projects/${UUID}/work-items/${UUID}/complete$`);
const WORK_DEFER = new RegExp(`^projects/${UUID}/work-items/${UUID}/defer$`);
const WORK_DELETE = new RegExp(`^projects/${UUID}/work-items/${UUID}/delete$`);
const WORK_MERGE = new RegExp(`^projects/${UUID}/work-items/${UUID}/merge$`);
const WORK_EVENTS = new RegExp(`^projects/${UUID}/work-items/${UUID}/events$`);
const HUMAN_ATTENTION = new RegExp(`^projects/${UUID}/human-attention$`);
const WORK_GATES = new RegExp(`^projects/${UUID}/work-items/${UUID}/gates$`);
const GATE_CONTEXT = new RegExp(`^projects/${UUID}/work-items/${UUID}/gates/${UUID}/context$`);
const GATE_RESOLVE = new RegExp(`^projects/${UUID}/work-items/${UUID}/gates/${UUID}/resolve$`);
const LEASE_CAPABILITY = new RegExp(`^projects/${UUID}/work-items/${UUID}/(?:claim|claim-and-recall|renew-claim|release-claim)$`);
const RESERVED_METADATA_KEYS = new Set([
  "lease_token",
  "claim_request_id",
  "client_operation_id",
  "api_key",
  "authorization",
  "cookie",
  "secret",
  "gate_id",
  "gate_type"
]);
const CLIENT_OPERATION_FIELD = "client_operation_id";
const FORBIDDEN_CONTROL_TRANSPORT_NAMES = new Set([
  "client_operation_id",
  "client-operation-id",
  "idempotency-key",
  "x-idempotency-key",
  "x-client-operation-id",
  "gate_id",
  "gate-id",
  "human-gate-id",
  "x-gate-id",
  "x-human-gate-id",
  "lease_token",
  "lease-token",
  "x-lease-token"
]);


export function allowedQueryKeys(path: string, method: string): string[] | null {
  // Lease receipts and arguments carry browser-forbidden capabilities.
  if (LEASE_CAPABILITY.test(path)) return null;
  if (path === "projects") {
    if (method === "GET") return ["limit", "offset"];
    if (method === "POST") return [];
  }
  if (PROJECT.test(path) && (method === "GET" || method === "PATCH")) return [];
  if (PROJECT_SETTINGS.test(path) && (method === "GET" || method === "PATCH")) return [];
  if (DUPLICATE_SUGGESTIONS.test(path) && method === "POST") return [];
  if (WORK_ITEMS.test(path)) {
    if (method === "GET") {
      return [
        "q", "semantic", "status", "sort", "tag", "source_client",
        "source_session_id", "view", "duplicate_scope", "canonical_work_item_id",
        "limit", "offset"
      ];
    }
    if (method === "POST") return [];
  }
  if (WORK_ITEM.test(path) && (method === "GET" || method === "PATCH")) return [];
  if (CHECKPOINTS.test(path)) {
    if (method === "GET") return ["order", "limit", "offset"];
    if (method === "POST") return [];
  }
  if (WORK_CONTEXT.test(path) && method === "GET") {
    return ["recent_limit", "recent_event_limit"];
  }
  if (WORK_CHILDREN.test(path) && method === "GET") {
    return ["status", "sort", "tag", "source_client", "source_session_id", "limit", "offset"];
  }
  if (WORK_RELATIONSHIPS.test(path) && method === "GET") {
    return ["direction", "type", "limit", "offset"];
  }
  if (RELATIONSHIPS.test(path) && method === "POST") return [];
  if (RELATIONSHIP.test(path) && method === "DELETE") return [];
  if (WORK_EVENTS.test(path)) {
    if (method === "GET") return ["order", "event_type", "limit", "offset"];
    if (method === "POST") return [];
  }
  if (HUMAN_ATTENTION.test(path) && method === "GET") {
    return ["work_item_id", "limit", "cursor"];
  }
  if (WORK_GATES.test(path) && method === "GET") return ["status", "limit", "cursor"];
  if (GATE_CONTEXT.test(path) && method === "GET") {
    return ["recent_limit", "recent_event_limit"];
  }
  if (GATE_RESOLVE.test(path) && method === "POST") return [];
  if (WORK_COMPLETE.test(path) && method === "POST") return [];
  if (WORK_DEFER.test(path) && method === "POST") return [];
  if (WORK_DELETE.test(path) && method === "POST") return [];
  if (WORK_MERGE.test(path) && method === "POST") return [];
  return null;
}

export function forbiddenMutationField(value: unknown): string | null {
  if (Array.isArray(value)) {
    for (const entry of value) {
      const forbidden = forbiddenMutationField(entry);
      if (forbidden) return forbidden;
    }
    return null;
  }
  if (!value || typeof value !== "object") return null;
  for (const [key, entry] of Object.entries(value)) {
    const normalizedKey = key.toLowerCase();
    if (
      normalizedKey === "lease_token"
      || normalizedKey === "gate_id"
      || normalizedKey === "gate_type"
    ) return key;
    const forbidden = forbiddenMutationField(entry);
    if (forbidden) return forbidden;
  }
  return null;
}

function allowedKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function validProgressMetadata(value: unknown): boolean {
  return validBoundedMetadata(value, RESERVED_METADATA_KEYS);
}

function validActor(value: unknown): boolean {
  const actor = jsonObject(value);
  if (!actor || !allowedKeys(actor, ["actor_client", "actor_session_id", "actor_model"])) {
    return false;
  }
  return boundedText(actor.actor_client, 80)
    && boundedText(actor.actor_session_id, 200)
    && (actor.actor_model === undefined
      || actor.actor_model === null
      || boundedText(actor.actor_model, 120));
}

function validHumanGateRevision(value: unknown): boolean {
  const revision = jsonObject(value);
  return Boolean(
    revision
    && allowedKeys(revision, [
      "work_version", "context_checkpoint_id", "relationship_event_count"
    ])
    && Object.keys(revision).length === 3
    && finiteInteger(revision.work_version, 1)
    && validUuid(revision.context_checkpoint_id)
    && finiteInteger(revision.relationship_event_count, 0)
  );
}

function validMergeReviewRevision(value: unknown): boolean {
  const revision = jsonObject(value);
  return Boolean(
    revision
    && allowedKeys(revision, [
      "work_version", "context_checkpoint_id", "work_event_count"
    ])
    && Object.keys(revision).length === 3
    && finiteInteger(revision.work_version, 1)
    && validUuid(revision.context_checkpoint_id)
    && finiteInteger(revision.work_event_count, 1)
  );
}


function validStringArray(value: unknown, maximumItems: number, maximumLength: number): boolean {
  return Array.isArray(value)
    && value.length <= maximumItems
    && value.every((entry) => boundedText(entry, maximumLength));
}

function validCheckpointPayload(value: unknown, includeKind: boolean): boolean {
  const checkpoint = jsonObject(value);
  if (!checkpoint) return false;
  const keys = [
    ...(includeKind ? ["kind"] : []),
    "prompt",
    "source_client",
    "source_session_id",
    "source_model",
    "source_session_url",
    "repository_branch",
    "verified_against",
    "tags",
    "source_metadata",
    CLIENT_OPERATION_FIELD
  ];
  if (!allowedKeys(checkpoint, keys)) return false;
  if (includeKind && checkpoint.kind !== "context" && checkpoint.kind !== "progress") return false;
  return boundedText(checkpoint.prompt, 100_000)
    && boundedText(checkpoint.source_client, 80)
    && boundedText(checkpoint.source_session_id, 200)
    && (checkpoint.source_model === undefined || nullableBoundedText(checkpoint.source_model, 120))
    && (checkpoint.source_session_url === undefined
      || nullableBoundedText(checkpoint.source_session_url, 2_000))
    && (checkpoint.repository_branch === undefined
      || nullableBoundedText(checkpoint.repository_branch, 200))
    && (checkpoint.verified_against === undefined
      || checkpoint.verified_against === null
      || typeof checkpoint.verified_against === "string"
        && /^[a-fA-F0-9]{7,64}$/.test(checkpoint.verified_against))
    && (checkpoint.tags === undefined || validStringArray(checkpoint.tags, 50, 50))
    && (checkpoint.source_metadata === undefined || validProgressMetadata(checkpoint.source_metadata));
}

function nestedClientOperationField(value: unknown, root = true): boolean {
  if (Array.isArray(value)) return value.some((entry) => nestedClientOperationField(entry, false));
  const object = jsonObject(value);
  if (!object) return false;
  return Object.entries(object).some(([key, entry]) => (
    (!root && key.toLowerCase() === CLIENT_OPERATION_FIELD)
    || nestedClientOperationField(entry, false)
  ));
}

function coveredMutation(path: string, method: string): boolean {
  return method === "POST" && WORK_ITEMS.test(path)
    || method === "POST" && CHECKPOINTS.test(path)
    || method === "POST" && WORK_EVENTS.test(path)
    || method === "POST" && RELATIONSHIPS.test(path)
    || method === "PATCH" && WORK_ITEM.test(path)
    || method === "POST" && WORK_COMPLETE.test(path)
    || method === "POST" && WORK_DEFER.test(path)
    || method === "POST" && WORK_DELETE.test(path)
    || method === "POST" && WORK_MERGE.test(path)
    || method === "DELETE" && RELATIONSHIP.test(path)
    || method === "POST" && GATE_RESOLVE.test(path);
}

function validClientOperation(body: Record<string, unknown>): boolean {
  return validUuid(body.client_operation_id);
}

function validInitialRelationships(value: unknown): boolean {
  if (value === undefined) return true;
  if (!Array.isArray(value) || value.length > 10) return false;
  return value.every((entry) => {
    const relationship = jsonObject(entry);
    return Boolean(
      relationship
      && allowedKeys(relationship, ["type", "direction", "other_work_item_id", "context_checkpoint_id"])
      && typeof relationship.type === "string"
      && ["blocks", "parent-child", "discovered-from", "related"].includes(relationship.type)
      && (relationship.direction === "incoming" || relationship.direction === "outgoing")
      && validUuid(relationship.other_work_item_id)
      && (relationship.context_checkpoint_id === undefined
        || relationship.context_checkpoint_id === null
        || validUuid(relationship.context_checkpoint_id))
      && (relationship.type !== "discovered-from"
        || relationship.direction === "outgoing" && validUuid(relationship.context_checkpoint_id))
    );
  });
}

export function forbiddenControlTransport(headers: Headers): string | null {
  for (const [key] of headers) {
    if (FORBIDDEN_CONTROL_TRANSPORT_NAMES.has(key.toLowerCase())) return "header";
  }
  const cookie = headers.get("cookie");
  if (cookie?.split(";").some((entry) => (
    FORBIDDEN_CONTROL_TRANSPORT_NAMES.has(entry.split("=", 1)[0]?.trim().toLowerCase() ?? "")
  ))) return "cookie";
  return null;
}

export function clientOperationMatchesSecret(bodyText: string | undefined, secret: string): boolean {
  if (!bodyText) return false;
  try {
    const body = jsonObject(JSON.parse(bodyText));
    return body?.client_operation_id === secret;
  } catch {
    return false;
  }
}

export function invalidMutationBody(path: string, method: string, value: unknown): string | null {
  const body = jsonObject(value);
  if (!body) return DEFINITIVE_PROXY_ERRORS.jsonObjectRequired.detail;
  const forbidden = forbiddenMutationField(body);
  if (forbidden) return unsupportedMutationFieldError(forbidden).detail;
  if (nestedClientOperationField(body)) {
    return DEFINITIVE_PROXY_ERRORS.nestedClientOperation.detail;
  }
  const protectedMutation = coveredMutation(path, method);
  if (!protectedMutation && Object.keys(body).some((key) => (
    key.toLowerCase() === CLIENT_OPERATION_FIELD
  ))) {
    return DEFINITIVE_PROXY_ERRORS.unsupportedClientOperation.detail;
  }
  if (protectedMutation && !validClientOperation(body)) {
    return DEFINITIVE_PROXY_ERRORS.invalidClientOperation.detail;
  }

  if (DUPLICATE_SUGGESTIONS.test(path) && method === "POST") {
    if (
      !allowedKeys(body, [
        "title", "summary", "initial_prompt", "tags", "exclude_work_item_id", "limit"
      ])
      || !boundedText(body.title, 200)
      || !boundedText(body.summary, 1_000)
      || !boundedText(body.initial_prompt, 100_000)
      || !(body.tags === undefined || validStringArray(body.tags, 20, 50))
      || !(body.exclude_work_item_id === undefined
        || body.exclude_work_item_id === null
        || validUuid(body.exclude_work_item_id))
      || !(body.limit === undefined || finiteInteger(body.limit, 1, 10))
    ) return DEFINITIVE_PROXY_ERRORS.invalidDuplicateSuggestion.detail;
  }
  if (WORK_ITEMS.test(path) && method === "POST") {
    if (
      !allowedKeys(body, [
        "title", "summary", "priority", "status", "initial_checkpoint",
        "initial_relationships", CLIENT_OPERATION_FIELD
      ])
      || !boundedText(body.title, 200)
      || !boundedText(body.summary, 1_000)
      || !finiteInteger(body.priority, 0, 100)
      || !["pending", "wont-do", "promoted"].includes(String(body.status))
      || !validCheckpointPayload(body.initial_checkpoint, false)
      || !validInitialRelationships(body.initial_relationships)
    ) return DEFINITIVE_PROXY_ERRORS.invalidWorkCreation.detail;
  }
  if (CHECKPOINTS.test(path) && method === "POST") {
    if (
      !allowedKeys(body, [
        "kind", "prompt", "source_client", "source_session_id", "source_model",
        "source_session_url", "repository_branch", "verified_against", "tags",
        "source_metadata", CLIENT_OPERATION_FIELD
      ])
      || !validCheckpointPayload(body, true)
    ) return DEFINITIVE_PROXY_ERRORS.invalidCheckpoint.detail;
  }
  if (WORK_EVENTS.test(path) && method === "POST") {
    if (
      !allowedKeys(body, ["event_type", "body", "metadata", "actor", CLIENT_OPERATION_FIELD])
      || Object.keys(body).length !== 5
      || body.event_type !== "progress"
      || !boundedText(body.body, 4000)
      || !validProgressMetadata(body.metadata)
      || !validActor(body.actor)
    ) return DEFINITIVE_PROXY_ERRORS.invalidProgressEvent.detail;
  }
  if (WORK_ITEM.test(path) && method === "PATCH") {
    if (
      !allowedKeys(body, [
        "expected_version", "title", "summary", "priority", "status", "actor",
        CLIENT_OPERATION_FIELD
      ])
      || !finiteInteger(body.expected_version, 1)
      || !validActor(body.actor)
      || !["title", "summary", "priority", "status"].some((key) => key in body)
      || (body.title !== undefined && !boundedText(body.title, 200))
      || (body.summary !== undefined && !boundedText(body.summary, 1_000))
      || (body.priority !== undefined && !finiteInteger(body.priority, 0, 100))
      || (body.status !== undefined
        && !["pending", "wont-do", "promoted"].includes(String(body.status)))
    ) return DEFINITIVE_PROXY_ERRORS.invalidWorkItemPatch.detail;
  }
  if (WORK_DEFER.test(path) && method === "POST") {
    if (
      !allowedKeys(body, ["expected_version", "actor", CLIENT_OPERATION_FIELD])
      || !finiteInteger(body.expected_version, 1)
      || !validActor(body.actor)
    ) return DEFINITIVE_PROXY_ERRORS.invalidWorkItemDeferral.detail;
  }
  if (PROJECT_SETTINGS.test(path) && method === "PATCH") {
    if (
      !allowedKeys(body, ["recall_pointer_template"])
      || Object.keys(body).length !== 1
      || (body.recall_pointer_template !== null
        && !boundedText(body.recall_pointer_template, 100000))
    ) return DEFINITIVE_PROXY_ERRORS.invalidProjectSettingsPatch.detail;
  }
  if (WORK_DELETE.test(path) && method === "POST") {
    if (
      !allowedKeys(body, ["expected_version", "actor", CLIENT_OPERATION_FIELD])
      || !finiteInteger(body.expected_version, 1)
      || !validActor(body.actor)
    ) return DEFINITIVE_PROXY_ERRORS.invalidWorkItemDeletion.detail;
  }
  if (RELATIONSHIP.test(path) && method === "DELETE") {
    if (
      !allowedKeys(body, ["actor", CLIENT_OPERATION_FIELD])
      || Object.keys(body).length !== 2
      || !validActor(body.actor)
    ) return DEFINITIVE_PROXY_ERRORS.invalidRelationshipRemoval.detail;
  }
  if (RELATIONSHIPS.test(path) && method === "POST") {
    if (
      !allowedKeys(body, [
        "relationship_type", "source_work_item_id", "target_work_item_id",
        "created_by_client", "created_by_session_id", "created_by_model",
        "context_checkpoint_id", CLIENT_OPERATION_FIELD
      ])
      || !["blocks", "parent-child", "discovered-from", "related"].includes(
        String(body.relationship_type)
      )
      || !validUuid(body.source_work_item_id)
      || !validUuid(body.target_work_item_id)
      || body.source_work_item_id.toLowerCase() === body.target_work_item_id.toLowerCase()
      || !boundedText(body.created_by_client, 80)
      || !boundedText(body.created_by_session_id, 200)
      || !(body.created_by_model === undefined || nullableBoundedText(body.created_by_model, 120))
      || !(body.context_checkpoint_id === undefined
        || body.context_checkpoint_id === null
        || validUuid(body.context_checkpoint_id))
      || (body.relationship_type === "discovered-from" && !validUuid(body.context_checkpoint_id))
    ) return DEFINITIVE_PROXY_ERRORS.invalidRelationshipCreation.detail;
  }
  if (WORK_COMPLETE.test(path) && method === "POST") {
    if (
      !allowedKeys(body, ["expected_version", "checkpoint", CLIENT_OPERATION_FIELD])
      || !finiteInteger(body.expected_version, 1)
      || !validCheckpointPayload(body.checkpoint, false)
    ) return DEFINITIVE_PROXY_ERRORS.invalidWorkCompletion.detail;
  }
  if (GATE_RESOLVE.test(path) && method === "POST") {
    if (
      !allowedKeys(body, [
        "resolution", "resolved_by_client", "resolved_by_session_id", "resolved_by_model",
        "reviewed_context_revision", CLIENT_OPERATION_FIELD
      ])
      || !boundedText(body.resolution, 4_000)
      || body.resolved_by_client !== "dashboard"
      || !boundedText(body.resolved_by_session_id, 200)
      || !(body.resolved_by_model === undefined || body.resolved_by_model === null)
      || !validHumanGateRevision(body.reviewed_context_revision)
    ) return DEFINITIVE_PROXY_ERRORS.invalidHumanGateResolution.detail;
  }
  if (WORK_MERGE.test(path) && method === "POST") {
    if (
      !allowedKeys(body, [
        "destination_work_item_id", "reviewed_source_revision",
        "reviewed_destination_revision", "rationale", "merged_by_client",
        "merged_by_session_id", "merged_by_model", CLIENT_OPERATION_FIELD
      ])
      || Object.keys(body).length !== 8
      || !validUuid(body.destination_work_item_id)
      || !validMergeReviewRevision(body.reviewed_source_revision)
      || !validMergeReviewRevision(body.reviewed_destination_revision)
      || !boundedText(body.rationale, 4_000)
      || body.merged_by_client !== "dashboard"
      || !boundedText(body.merged_by_session_id, 200)
      || body.merged_by_model !== null
    ) return DEFINITIVE_PROXY_ERRORS.invalidWorkMerge.detail;
  }
  return null;
}

export type BrowserTransportEffect =
  | "safe_read"
  | "receipt_protected_write"
  | "lease_claim";

export function browserTransportEffect(
  path: string,
  method: string
): BrowserTransportEffect | null {
  if (method === "POST" && DUPLICATE_SUGGESTIONS.test(path)) return "safe_read";
  if (LEASE_CAPABILITY.test(path)) return "lease_claim";
  if (method === "GET" && allowedQueryKeys(path, method) !== null) return "safe_read";
  if (coveredMutation(path, method)) return "receipt_protected_write";
  return null;
}
export function upstreamTimeoutMs(
  query: URLSearchParams,
  path = "",
  method = "GET"
): number {
  return method === "POST" && DUPLICATE_SUGGESTIONS.test(path)
    || query.get("semantic") === "true" && Boolean(query.get("q")?.trim())
    ? 60_000
    : 15_000;
}

export function upstreamAbortSignal(
  requestSignal: AbortSignal,
  query: URLSearchParams,
  path = "",
  method = "GET"
): AbortSignal {
  return AbortSignal.any([
    requestSignal,
    AbortSignal.timeout(upstreamTimeoutMs(query, path, method))
  ]);
}

export async function readBodyChunk(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal: AbortSignal
): Promise<ReadableStreamReadResult<Uint8Array>> {
  return await new Promise<ReadableStreamReadResult<Uint8Array>>((resolve, reject) => {
    let settled = false;
    const abort = () => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", abort);
      void reader.cancel(signal.reason).catch(() => undefined);
      reject(signal.reason ?? new Error("Proxy request aborted."));
    };
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) {
      abort();
      return;
    }
    void reader.read().then(
      (result) => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", abort);
        resolve(result);
      },
      (error: unknown) => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", abort);
        reject(error);
      }
    );
  });
}

export function proxyBodyLimitBytes(path: string): number {
  return DUPLICATE_SUGGESTIONS.test(path) ? 2_097_152 : 1_048_576;
}

export function forwardedRetryAfter(status: number, headers: Headers): string | null {
  if (status !== 429) return null;
  const value = headers.get("retry-after");
  if (!value || value.length > 128) return null;
  if (/^\d+$/.test(value)) return value;
  return Number.isNaN(Date.parse(value)) ? null : value;
}

export function configuredOrigins(value?: string): Set<string> {
  const origins = (value ?? "http://localhost:3000,http://127.0.0.1:3000").split(",");
  if (!origins.length) throw new Error("No dashboard origins configured");
  return new Set(origins.map((entry) => {
    const url = new URL(entry.trim());
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
      throw new Error("Invalid dashboard origin");
    }
    return url.origin;
  }));
}

export function trustedRequest(headers: Headers, method: string, origins: Set<string>): boolean {
  const host = headers.get("host")?.toLowerCase();
  if (!host || ![...origins].some((origin) => new URL(origin).host.toLowerCase() === host)) return false;
  const fetchSite = headers.get("sec-fetch-site");
  if (fetchSite && fetchSite !== "same-origin" && fetchSite !== "none") return false;
  const origin = headers.get("origin");
  if (!origin) return method === "GET";
  try {
    const parsed = new URL(origin);
    return origin === parsed.origin && origins.has(origin) && parsed.host.toLowerCase() === host;
  } catch {
    return false;
  }
}
