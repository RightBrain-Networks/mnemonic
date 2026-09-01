const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const PROJECT = new RegExp(`^projects/${UUID}$`);
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
const LEASE_CAPABILITY = new RegExp(`^projects/${UUID}/work-items/${UUID}/(?:claim|claim-and-recall|renew-claim|release-claim)$`);

export function allowedQueryKeys(path: string, method: string): string[] | null {
  // Lease receipts and arguments carry browser-forbidden capabilities.
  if (LEASE_CAPABILITY.test(path)) return null;
  if (path === "projects") {
    if (method === "GET") return ["limit", "offset"];
    if (method === "POST") return [];
  }
  if (PROJECT.test(path) && (method === "GET" || method === "PATCH")) return [];
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
  if (WORK_CONTEXT.test(path) && method === "GET") return ["recent_limit"];
  if (WORK_CHILDREN.test(path) && method === "GET") {
    return ["status", "tag", "source_client", "source_session_id", "limit", "offset"];
  }
  if (WORK_RELATIONSHIPS.test(path) && method === "GET") {
    return ["direction", "type", "limit", "offset"];
  }
  if (RELATIONSHIPS.test(path) && method === "POST") return [];
  if (RELATIONSHIP.test(path) && method === "DELETE") return [];
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
