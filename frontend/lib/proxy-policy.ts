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
const WORK_DELETE = new RegExp(`^projects/${UUID}/work-items/${UUID}/delete$`);
const WORK_EVENTS = new RegExp(`^projects/${UUID}/work-items/${UUID}/events$`);
const LEASE_CAPABILITY = new RegExp(`^projects/${UUID}/work-items/${UUID}/(?:claim|claim-and-recall|renew-claim|release-claim)$`);
const EVENT_SECRET_KEYS = new Set([
  "lease_token",
  "claim_request_id",
  "api_key",
  "authorization",
  "cookie",
  "secret"
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
      return ["q", "semantic", "status", "tag", "source_client", "source_session_id", "view", "limit", "offset"];
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
    return ["status", "tag", "source_client", "source_session_id", "limit", "offset"];
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

export function invalidMutationBody(path: string, method: string, value: unknown): string | null {
  const body = jsonObject(value);
  if (!body) return "The request body must be a JSON object.";
  const forbidden = forbiddenMutationField(body);
  if (forbidden) return `The request body contains an unsupported field: ${forbidden}.`;

  if (WORK_EVENTS.test(path) && method === "POST") {
    if (
      !allowedKeys(body, ["event_type", "body", "metadata", "actor"])
      || Object.keys(body).length !== 4
      || body.event_type !== "progress"
      || !boundedText(body.body, 4000)
      || !validProgressMetadata(body.metadata)
      || !validActor(body.actor)
    ) {
      return "The progress-event body does not match the dashboard allowlist.";
    }
  }
  if (WORK_ITEM.test(path) && method === "PATCH") {
    if (
      !allowedKeys(body, ["expected_version", "title", "summary", "priority", "status", "actor"])
      || !("expected_version" in body)
      || ("actor" in body && !validActor(body.actor))
    ) {
      return "The work-item patch does not match the dashboard allowlist.";
    }
  }
  if (PROJECT_SETTINGS.test(path) && method === "PATCH") {
    if (
      !allowedKeys(body, ["recall_pointer_template"])
      || Object.keys(body).length !== 1
      || (body.recall_pointer_template !== null
        && !boundedText(body.recall_pointer_template, 100000))
    ) {
      return "The project-settings patch does not match the dashboard allowlist.";
    }
  }
  if (WORK_DELETE.test(path) && method === "POST") {
    if (
      !allowedKeys(body, ["expected_version", "actor"])
      || !("expected_version" in body)
      || ("actor" in body && !validActor(body.actor))
    ) {
      return "The work-item deletion does not match the dashboard allowlist.";
    }
  }
  if (RELATIONSHIP.test(path) && method === "DELETE") {
    if (!allowedKeys(body, ["actor"]) || Object.keys(body).length !== 1 || !validActor(body.actor)) {
      return "The relationship-removal body does not match the dashboard allowlist.";
    }
  }
  return null;
}

export async function classifyRequestBody(
  request: Request,
  maxBytes: number
): Promise<"empty" | "present" | "too-large"> {
  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) return "too-large";
  const reader = request.body?.getReader();
  if (!reader) return "empty";
  let size = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) return size === 0 ? "empty" : "present";
    size += value.byteLength;
    if (size > maxBytes) {
      await reader.cancel();
      return "too-large";
    }
    if (size > 0) {
      await reader.cancel();
      return "present";
    }
  }
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
