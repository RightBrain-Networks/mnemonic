const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const PROJECT = new RegExp(`^projects/${UUID}$`);
const PROJECT_SETTINGS = new RegExp(`^projects/${UUID}/settings$`);
const WORK_ITEMS = new RegExp(`^projects/${UUID}/work-items$`);
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
const WORK_EVENTS = new RegExp(`^projects/${UUID}/work-items/${UUID}/events$`);
const LEASE_CAPABILITY = new RegExp(`^projects/${UUID}/work-items/${UUID}/(?:claim|claim-and-recall|renew-claim|release-claim)$`);
const EVENT_SECRET_KEYS = new Set([
  "lease_token",
  "claim_request_id",
  "client_operation_id",
  "api_key",
  "authorization",
  "cookie",
  "secret"
]);
const CLIENT_OPERATION_FIELD = "client_operation_id";
const CLIENT_OPERATION_HEADERS = new Set([
  "client_operation_id",
  "client-operation-id",
  "idempotency-key",
  "x-idempotency-key",
  "x-client-operation-id"
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
  if (WORK_ITEMS.test(path)) {
    if (method === "GET") {
      return ["q", "semantic", "status", "sort", "tag", "source_client", "source_session_id", "view", "limit", "offset"];
    }
    if (method === "POST") return [];
  }
  if (WORK_ITEM.test(path) && (method === "GET" || method === "PATCH")) return [];
  if (CHECKPOINTS.test(path)) {
    if (method === "GET") return ["order", "limit", "offset"];
    if (method === "POST") return [];
  }
  if (WORK_CONTEXT.test(path) && method === "GET") return ["recent_limit", "recent_event_limit"];
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
  if (WORK_COMPLETE.test(path) && method === "POST") return [];
  if (WORK_DEFER.test(path) && method === "POST") return [];
  if (WORK_DELETE.test(path) && method === "POST") return [];
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
    if (key === "lease_token") return key;
    const forbidden = forbiddenMutationField(entry);
    if (forbidden) return forbidden;
  }
  return null;
}

function jsonObject(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null
    ? value as Record<string, unknown>
    : null;
}

function allowedKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function validUnicode(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function boundedText(value: unknown, maximum: number): value is string {
  return typeof value === "string"
    && validUnicode(value)
    && !value.includes("\0")
    && Array.from(value).length >= 1
    && Array.from(value).length <= maximum
    && value.trim().length > 0;
}

function validProgressMetadata(value: unknown): boolean {
  const stack = new WeakSet<object>();
  let separatorBytes = 0;
  const visit = (item: unknown): boolean => {
    if (item === null || typeof item === "boolean") return true;
    if (typeof item === "number") return Number.isFinite(item);
    if (typeof item === "string") return validUnicode(item) && !item.includes("\0");
    if (typeof item !== "object" || stack.has(item)) return false;
    stack.add(item);
    if (Array.isArray(item)) {
      separatorBytes += Math.max(0, item.length - 1);
      const valid = item.every(visit);
      stack.delete(item);
      return valid;
    }
    const object = jsonObject(item);
    if (!object) {
      stack.delete(item);
      return false;
    }
    const entries = Object.entries(object);
    separatorBytes += entries.length ? (2 * entries.length) - 1 : 0;
    const valid = entries.every(([key, entry]) => (
      validUnicode(key)
      && !key.includes("\0")
      && !EVENT_SECRET_KEYS.has(key.toLowerCase())
      && visit(entry)
    ));
    stack.delete(item);
    return valid;
  };

  if (!jsonObject(value) || !visit(value)) return false;
  try {
    const encoded = JSON.stringify(value);
    return encoded !== undefined
      && new TextEncoder().encode(encoded).byteLength + separatorBytes <= 16_384;
  } catch {
    return false;
  }
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


function finiteInteger(value: unknown, minimum: number, maximum = Number.MAX_SAFE_INTEGER): boolean {
  return Number.isSafeInteger(value) && Number(value) >= minimum && Number(value) <= maximum;
}

function nullableBoundedText(value: unknown, maximum: number): boolean {
  return value === null || boundedText(value, maximum);
}

function validUuid(value: unknown): value is string {
  return typeof value === "string"
    && /^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/.test(value);
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
    || method === "DELETE" && RELATIONSHIP.test(path);
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
      && ["blocks", "parent-child", "discovered-from", "duplicate-of", "related"].includes(relationship.type)
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

export function forbiddenOperationTransport(headers: Headers): string | null {
  for (const [key] of headers) {
    if (CLIENT_OPERATION_HEADERS.has(key.toLowerCase())) return "header";
  }
  const cookie = headers.get("cookie");
  if (cookie?.split(";").some((entry) => (
    CLIENT_OPERATION_HEADERS.has(entry.split("=", 1)[0]?.trim().toLowerCase() ?? "")
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
  if (!body) return "The request body must be a JSON object.";
  const forbidden = forbiddenMutationField(body);
  if (forbidden) return `The request body contains an unsupported field: ${forbidden}.`;
  if (nestedClientOperationField(body)) {
    return "The client operation ID is accepted only at the top level.";
  }
  const protectedMutation = coveredMutation(path, method);
  if (!protectedMutation && Object.keys(body).some((key) => (
    key.toLowerCase() === CLIENT_OPERATION_FIELD
  ))) {
    return "The client operation ID is not supported for this route.";
  }
  if (protectedMutation && !validClientOperation(body)) {
    return "The client operation ID must be a UUID.";
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
    ) return "The work-creation body does not match the dashboard allowlist.";
  }
  if (CHECKPOINTS.test(path) && method === "POST") {
    if (
      !allowedKeys(body, [
        "kind", "prompt", "source_client", "source_session_id", "source_model",
        "source_session_url", "repository_branch", "verified_against", "tags",
        "source_metadata", CLIENT_OPERATION_FIELD
      ])
      || !validCheckpointPayload(body, true)
    ) return "The checkpoint body does not match the dashboard allowlist.";
  }
  if (WORK_EVENTS.test(path) && method === "POST") {
    if (
      !allowedKeys(body, ["event_type", "body", "metadata", "actor", CLIENT_OPERATION_FIELD])
      || Object.keys(body).length !== 5
      || body.event_type !== "progress"
      || !boundedText(body.body, 4000)
      || !validProgressMetadata(body.metadata)
      || !validActor(body.actor)
    ) return "The progress-event body does not match the dashboard allowlist.";
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
    ) return "The work-item patch does not match the dashboard allowlist.";
  }
  if (WORK_DEFER.test(path) && method === "POST") {
    if (
      !allowedKeys(body, ["expected_version", "actor", CLIENT_OPERATION_FIELD])
      || !finiteInteger(body.expected_version, 1)
      || !validActor(body.actor)
    ) return "The work-item deferral does not match the dashboard allowlist.";
  }
  if (PROJECT_SETTINGS.test(path) && method === "PATCH") {
    if (
      !allowedKeys(body, ["recall_pointer_template"])
      || Object.keys(body).length !== 1
      || (body.recall_pointer_template !== null
        && !boundedText(body.recall_pointer_template, 100000))
    ) return "The project-settings patch does not match the dashboard allowlist.";
  }
  if (WORK_DELETE.test(path) && method === "POST") {
    if (
      !allowedKeys(body, ["expected_version", "actor", CLIENT_OPERATION_FIELD])
      || !finiteInteger(body.expected_version, 1)
      || !validActor(body.actor)
    ) return "The work-item deletion does not match the dashboard allowlist.";
  }
  if (RELATIONSHIP.test(path) && method === "DELETE") {
    if (
      !allowedKeys(body, ["actor", CLIENT_OPERATION_FIELD])
      || Object.keys(body).length !== 2
      || !validActor(body.actor)
    ) return "The relationship-removal body does not match the dashboard allowlist.";
  }
  if (RELATIONSHIPS.test(path) && method === "POST") {
    if (
      !allowedKeys(body, [
        "relationship_type", "source_work_item_id", "target_work_item_id",
        "created_by_client", "created_by_session_id", "created_by_model",
        "context_checkpoint_id", CLIENT_OPERATION_FIELD
      ])
      || !["blocks", "parent-child", "discovered-from", "duplicate-of", "related"].includes(
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
    ) return "The relationship-creation body does not match the dashboard allowlist.";
  }
  if (WORK_COMPLETE.test(path) && method === "POST") {
    if (
      !allowedKeys(body, ["expected_version", "checkpoint", CLIENT_OPERATION_FIELD])
      || !finiteInteger(body.expected_version, 1)
      || !validCheckpointPayload(body.checkpoint, false)
    ) return "The work-completion body does not match the dashboard allowlist.";
  }
  return null;
}
export function upstreamTimeoutMs(query: URLSearchParams): number {
  return query.get("semantic") === "true" && Boolean(query.get("q")?.trim()) ? 60_000 : 15_000;
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
