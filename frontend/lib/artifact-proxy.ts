import { ARTIFACT_DEFAULT_MAX_BYTES } from "./artifacts.ts";
import { readBoundedBytes } from "./bounded-json.ts";
import { configuredOrigins, forbiddenControlTransport, trustedRequest } from "./proxy-policy.ts";
import { UUID_PATTERN, validUuid } from "./wire-guards.ts";

const UUID = UUID_PATTERN.source.slice(1, -1);
const COLLECTION = new RegExp(`^projects/${UUID}/artifacts$`);
const ITEM = new RegExp(`^projects/${UUID}/artifacts/${UUID}$`);
const CONTENT = new RegExp(`^projects/${UUID}/artifacts/${UUID}/content$`);
const HISTORY = new RegExp(`^projects/${UUID}/artifacts/${UUID}/history$`);
const SECURITY_HEADERS = {
  "Cache-Control": "no-store, max-age=0, no-transform",
  "X-Content-Type-Options": "nosniff",
  "Cross-Origin-Resource-Policy": "same-origin",
  "Content-Security-Policy": "sandbox; default-src 'none'"
};

export function artifactQueryKeys(path: string, method: string): readonly string[] | null {
  if (COLLECTION.test(path)) {
    if (method === "GET") return ["q", "sort", "order", "limit", "offset", "include_deleted", "work_item_id"];
    if (method === "POST") return [];
  }
  if (ITEM.test(path)) {
    if (method === "GET") return [];
    if (method === "DELETE") return [];
  }
  if (CONTENT.test(path)) {
    if (method === "GET") return [];
    if (method === "PUT") return [];
  }
  if (HISTORY.test(path) && method === "GET") return ["limit", "offset"];
  return null;
}

export function artifactMaximumBytes(configured?: string): number {
  if (configured === undefined) return ARTIFACT_DEFAULT_MAX_BYTES;
  const size = Number(configured);
  if (!Number.isSafeInteger(size) || size < 1 || size > 1024 * 1024 * 1024) throw new Error("Invalid artifact size configuration.");
  return size;
}

export function boundedArtifactStream(body: ReadableStream<Uint8Array>, maximum: number, exact = false): ReadableStream<Uint8Array> {
  let size = 0;
  return body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      size += chunk.byteLength;
      if (size > maximum) throw new Error("Artifact content exceeds the configured size limit.");
      controller.enqueue(chunk);
    },
    flush() {
      if (exact && size !== maximum) throw new Error("Artifact content ended before its declared byte length.");
    }
  }));
}

export function safeArtifactDisposition(value: string | null): string {
  let name = "artifact.bin";
  const encoded = value?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const quoted = value?.match(/filename="([^"\\]+)"/i)?.[1];
  try {
    const candidate = encoded ? decodeURIComponent(encoded) : quoted;
    if (candidate && candidate.length <= 255 && !/[\x00-\x1f\x7f/\\]/.test(candidate)) name = candidate;
  } catch { /* Use the fixed fallback for malformed headers. */ }
  return `attachment; filename="artifact.bin"; filename*=UTF-8''${encodeURIComponent(name).replace(/['()*]/g, (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`)}`;
}

function fail(status: number, detail: string): Response {
  return Response.json({ detail }, { status, headers: SECURITY_HEADERS });
}

type Environment = { MNEMONIC_DASHBOARD_ORIGINS?: string; MNEMONIC_API_KEY?: string; MNEMONIC_API_URL?: string; MNEMONIC_ARTIFACT_MAX_BYTES?: string };

export async function proxyArtifact(request: Request, path: string[], environment: Environment, fetcher: typeof fetch = fetch): Promise<Response> {
  let origins: Set<string>;
  let maximum: number;
  try {
    origins = configuredOrigins(environment.MNEMONIC_DASHBOARD_ORIGINS);
    maximum = artifactMaximumBytes(environment.MNEMONIC_ARTIFACT_MAX_BYTES);
  } catch { return fail(503, "Artifact dashboard settings are not configured correctly."); }
  if (!trustedRequest(request.headers, request.method, origins)) return fail(403, "This dashboard request is not from a trusted origin.");
  if (path.some((part) => !/^[a-zA-Z0-9-]+$/.test(part))) return fail(404, "Route not found.");
  const route = path.join("/");
  const keys = artifactQueryKeys(route, request.method);
  if (!keys) return fail(404, "Route not found.");
  const query = new URL(request.url).searchParams;
  if (query.toString().length > 16000) return fail(400, "Artifact metadata is too large.");
  for (const field of query.keys()) {
    if (!keys.includes(field) || query.getAll(field).length !== 1) return fail(400, "The artifact query contains an unsupported or repeated field.");
  }
  const mutation = request.method !== "GET";
  const metadata = request.headers.get("X-Artifact-Metadata");
  if (mutation && metadata && (metadata.length > 16384 || /[^\x20-\x7e]/.test(metadata))) return fail(400, "Artifact metadata must be bounded ASCII JSON.");
  let parsedMetadata: Record<string, unknown> = {};
  if (mutation && metadata) {
    try {
      const parsed: unknown = JSON.parse(metadata);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      parsedMetadata = parsed as Record<string, unknown>;
      const allowed = request.method === "DELETE" ? ["agent_session_id", "actor_client"] : ["filename", "description", "agent_session_id", "actor_client", "work_item_id", "related_work_item_ids"];
      if (Object.keys(parsedMetadata).some((field) => !allowed.includes(field))) throw new Error();
    } catch { return fail(400, "Artifact metadata does not match the dashboard allowlist."); }
  }
  const operationId = request.headers.get("X-Client-Operation-ID");
  const revision = request.headers.get("X-Artifact-Expected-Revision");
  const controlHeaders = new Headers(request.headers);
  if (mutation) controlHeaders.delete("X-Client-Operation-ID");
  if (forbiddenControlTransport(controlHeaders)) return fail(400, "The request contains a forbidden control header.");
  if (mutation && !validUuid(operationId)) return fail(400, "An artifact operation UUID is required.");
  if ((request.method === "PUT" || request.method === "DELETE") && (!revision || !/^[1-9][0-9]{0,9}$/.test(revision) || Number(revision) > 2147483647)) return fail(400, "An expected artifact revision is required.");
  const key = environment.MNEMONIC_API_KEY;
  if (!key || key.length < 32) return fail(503, "Mnemonic's API connection is not configured.");
  if (operationId?.toLowerCase() === key.toLowerCase()) return fail(422, "The client operation ID cannot match a request credential.");
  let base: URL;
  try {
    base = new URL(environment.MNEMONIC_API_URL ?? "http://api:8000");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.pathname !== "/" || base.search || base.hash) throw new Error();
  } catch { return fail(503, "Mnemonic's API address is not configured correctly."); }
  const upload = request.method === "POST" || request.method === "PUT";
  if (upload && Number(request.headers.get("content-length")) > maximum) return fail(413, "Artifact content exceeds the configured size limit.");
  if (upload && request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/octet-stream") return fail(415, "Send artifact bytes as application/octet-stream.");
  if (upload && (typeof parsedMetadata.filename !== "string" || !parsedMetadata.filename)) return fail(400, "An artifact filename is required.");
  const encoding = request.headers.get("content-encoding");
  if (encoding && encoding.toLowerCase() !== "identity") return fail(415, "Encoded artifact request bodies are not supported.");
  try {
    const target = new URL(`/api/v1/${route}`, base);
    target.search = query.toString();
    const headers = new Headers({ Authorization: `Bearer ${key}`, "Accept-Encoding": "identity" });
    if (mutation) headers.set("X-Client-Operation-ID", operationId!);
    if (mutation && metadata) headers.set("X-Artifact-Metadata", metadata);
    if (revision && mutation) headers.set("X-Artifact-Expected-Revision", revision);
    if (upload) headers.set("Content-Type", "application/octet-stream");
    const init: RequestInit & { duplex?: "half" } = {
      method: request.method, headers, cache: "no-store", redirect: "manual",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(300000)])
    };
    if (upload && request.body) { init.body = boundedArtifactStream(request.body, maximum); init.duplex = "half"; }
    const upstream = await fetcher(target, init);
    if (upstream.status >= 300 && upstream.status < 400) { await upstream.body?.cancel(); return fail(502, "Mnemonic's API returned an unexpected redirect."); }
    const contentEncoding = upstream.headers.get("content-encoding");
    if (contentEncoding && contentEncoding.toLowerCase() !== "identity") { await upstream.body?.cancel(); return fail(502, "Mnemonic's API returned an encoded artifact response."); }
    if (request.method === "GET" && CONTENT.test(route) && upstream.ok) {
      const length = upstream.headers.get("content-length");
      if (upstream.status !== 200 || !length || !/^[0-9]+$/.test(length) || Number(length) > maximum || !upstream.body) { await upstream.body?.cancel(); return fail(502, "Mnemonic's API returned an invalid artifact response."); }
      return new Response(boundedArtifactStream(upstream.body, Number(length), true), {
        status: 200,
        headers: { ...SECURITY_HEADERS, "Content-Type": "application/octet-stream", "Content-Length": length, "Content-Disposition": safeArtifactDisposition(upstream.headers.get("content-disposition")) }
      });
    }
    if (!upstream.headers.get("content-type")?.includes("application/json")) { await upstream.body?.cancel(); return fail(502, "Mnemonic's API returned an unexpected response."); }
    const bytes = await readBoundedBytes(upstream, mutation ? 1024 * 1024 : 4 * 1024 * 1024);
    JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    const responseHeaders = new Headers({ ...SECURITY_HEADERS, "Content-Type": "application/json" });
    const echoed = upstream.headers.get("X-Client-Operation-ID");
    if (mutation && echoed === operationId) responseHeaders.set("X-Client-Operation-ID", echoed!);
    return new Response(bytes, { status: upstream.status, headers: responseHeaders });
  } catch { return fail(502, "The artifact request could not be completed. Retry the same pending action if its outcome is unknown."); }
}
