import { readBoundedBytes, readBoundedJson } from "./bounded-json.ts";
import { configuredOrigins, forbiddenControlTransport, trustedRequest } from "./proxy-policy.ts";
import { TRANSCRIPT_JSON_MAX_BYTES, TRANSCRIPT_MAX_BYTES, TRANSCRIPT_PROXY_REJECTION_MESSAGES, transcriptDigest } from "./transcripts.ts";
import { exactKeys, finiteInteger, objectValue, validUuid } from "./wire-guards.ts";

const SECURITY_HEADERS = {
  "Cache-Control": "no-store, max-age=0, no-transform",
  "X-Content-Type-Options": "nosniff",
  "Cross-Origin-Resource-Policy": "same-origin",
  "Content-Security-Policy": "sandbox; default-src 'none'"
};
type Environment = { MNEMONIC_DASHBOARD_ORIGINS?: string; MNEMONIC_API_URL?: string; MNEMONIC_API_KEY?: string };
type Action = "list" | "detail" | "text" | "content" | "settings" | "save" | "rebuild";
export function transcriptRoute(path: string[], method: string): Action | null {
  if (path[0] !== "projects" || !validUuid(path[1])) return null;
  if (path.length === 3 && path[2] === "transcript-settings") return method === "GET" ? "settings" : method === "PATCH" ? "save" : null;
  if (path[2] !== "transcripts") return null;
  if (path.length === 3 && method === "GET") return "list";
  if (path.length === 4 && path[3] === "rebuild" && method === "POST") return "rebuild";
  if (!validUuid(path[3]) || method !== "GET") return null;
  if (path.length === 4) return "detail";
  if (path.length === 5 && ["text", "content"].includes(path[4])) return path[4] as Action;
  return null;
}
export function validTranscriptQuery(query: URLSearchParams, action: Action): boolean {
  const allowed = action === "list" ? ["query", "fulltext", "work_item_id", "limit", "offset"] : action === "text" ? ["limit", "offset", "expected_sha256"] : action === "content" ? ["expected_sha256"] : [];
  for (const [key, value] of query) {
    if (!allowed.includes(key) || query.getAll(key).length !== 1) return false;
    if (key === "query" && (Array.from(value).length > 200 || !value.trim())) return false;
    if (key === "work_item_id" && !validUuid(value)) return false;
    if (key === "fulltext" && !["true", "false"].includes(value)) return false;
    if (key === "expected_sha256" && !transcriptDigest(value)) return false;
    if (["limit", "offset"].includes(key) && (!/^\d+$/.test(value) || !finiteInteger(Number(value), key === "limit" ? 1 : 0, key === "limit" ? action === "text" ? 20000 : 100 : 10000000))) return false;
  }
  return true;
}
function fail(status: number, detail: string): Response { return Response.json({ detail }, { status, headers: SECURITY_HEADERS }); }
function reject(status: number, message: string): Response {
  if (!TRANSCRIPT_PROXY_REJECTION_MESSAGES[status]?.includes(message)) return fail(status, message);
  return Response.json({ detail: { code: "transcript_proxy_rejected", message } }, { status, headers: SECURITY_HEADERS });
}
export async function readTranscriptMutationBody(request: Request, timeoutMs = 10000): Promise<Uint8Array<ArrayBuffer>> {
  const deadline = new AbortController();
  const timer = setTimeout(() => deadline.abort(new DOMException("Transcript request body timed out.", "TimeoutError")), timeoutMs);
  try {
    return await readBoundedBytes(new Response(request.body, { headers: request.headers }), 4096,
      AbortSignal.any([request.signal, deadline.signal]));
  } finally { clearTimeout(timer); }
}
export async function proxyTranscript(request: Request, path: string[], environment: Environment, fetcher: typeof fetch = fetch): Promise<Response> {
  let origins: Set<string>;
  try { origins = configuredOrigins(environment.MNEMONIC_DASHBOARD_ORIGINS); }
  catch { return fail(503, "Dashboard origins are not configured correctly."); }
  if (!trustedRequest(request.headers, request.method, origins)) return reject(403, "This dashboard request is not from a trusted origin.");
  const action = transcriptRoute(path, request.method);
  if (!action) return reject(404, "Route not found.");
  if (forbiddenControlTransport(request.headers)) return reject(400, "The request contains a forbidden control header.");
  const query = new URL(request.url).searchParams;
  if (!validTranscriptQuery(query, action)) return reject(400, "Invalid transcript query.");
  let base: URL;
  try {
    base = new URL(environment.MNEMONIC_API_URL ?? "http://api:8000");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.pathname !== "/" || base.search || base.hash) throw new Error();
  } catch { return fail(503, "The API address is not configured correctly."); }
  if (!environment.MNEMONIC_API_KEY) return fail(503, "The dashboard API connection is not configured.");
  const encoding = request.headers.get("content-encoding");
  if (encoding && encoding.toLowerCase() !== "identity") return reject(415, "Encoded requests are not supported.");
  let body: string | undefined;
  if (action === "save" || action === "rebuild") {
    if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") return reject(415, "Send transcript settings as JSON.");
    try {
      const bytes = await readTranscriptMutationBody(request);
      body = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      const value = objectValue(JSON.parse(body));
      if (!value || (action === "save"
        ? !exactKeys(value, ["enabled", "max_file_size_bytes", "expected_revision"]) || typeof value.enabled !== "boolean" || !finiteInteger(value.max_file_size_bytes, 1, TRANSCRIPT_MAX_BYTES) || !finiteInteger(value.expected_revision, 1)
        : !exactKeys(value, ["client_operation_id"]) || !validUuid(value.client_operation_id))) return reject(400, "Invalid transcript settings request.");
    } catch { return reject(400, "Invalid or oversized transcript settings request."); }
  } else if (request.body) return reject(400, "Transcript reads do not accept a body.");
  try {
    const target = new URL(`/api/v1/${path.join("/")}`, base);
    target.search = query.toString();
    const upstream = await fetcher(target, {
      method: request.method, body, headers: { Authorization: `Bearer ${environment.MNEMONIC_API_KEY}`, "Accept-Encoding": "identity", ...(body ? { "Content-Type": "application/json" } : {}) },
      cache: "no-store", redirect: "manual", signal: AbortSignal.any([request.signal, AbortSignal.timeout(60000)])
    });
    if (upstream.status >= 300 && upstream.status < 400) { await upstream.body?.cancel(); return fail(502, "Mnemonic returned an unexpected redirect."); }
    const upstreamEncoding = upstream.headers.get("content-encoding");
    if (upstreamEncoding && upstreamEncoding.toLowerCase() !== "identity") { await upstream.body?.cancel(); return fail(502, "Mnemonic returned an encoded response."); }
    if (action === "content" && upstream.ok) {
      if (upstream.status !== 200) { await upstream.body?.cancel(); return fail(502, "Mnemonic returned incomplete transcript content."); }
      const bytes = await readBoundedBytes(upstream, 32 * 1024 * 1024);
      return new Response(bytes, { headers: { ...SECURITY_HEADERS, "Content-Type": "application/octet-stream", "Content-Length": String(bytes.length), "Content-Disposition": `attachment; filename="${path[3]}.txt"` } });
    }
    const value = await readBoundedJson(upstream, TRANSCRIPT_JSON_MAX_BYTES);
    if (objectValue(objectValue(value)?.detail)?.code === "transcript_proxy_rejected") return fail(502, "Mnemonic returned an invalid rejection response.");
    return Response.json(value, { status: upstream.status, headers: SECURITY_HEADERS });
  } catch { return fail(502, "The transcript request could not be completed. For a pending rebuild, retry the preserved request."); }
}
