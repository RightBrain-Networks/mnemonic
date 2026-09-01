import {
  allowedQueryKeys,
  configuredOrigins,
  forbiddenMutationField,
  invalidMutationBody,
  trustedRequest,
  upstreamTimeoutMs
} from "@/lib/proxy-policy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 1024 * 1024;
const responseHeaders = {
  "Cache-Control": "no-store, max-age=0",
  "X-Content-Type-Options": "nosniff",
  "Cross-Origin-Resource-Policy": "same-origin"
};

function fail(status: number, detail: string): Response {
  return Response.json({ detail }, { status, headers: responseHeaders });
}

async function readBody(
  request: Request,
  route: string,
  required = true
): Promise<string | undefined | Response> {
  if (Number(request.headers.get("content-length")) > MAX_BODY_BYTES) return fail(413, "Request body is too large.");
  const reader = request.body?.getReader();
  if (!reader) return required ? fail(400, "A request body is required.") : undefined;
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY_BYTES) {
      await reader.cancel();
      return fail(413, "Request body is too large.");
    }
    chunks.push(value);
  }
  if (size === 0) return required ? fail(400, "A request body is required.") : undefined;
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
    return fail(415, "Send a JSON request body.");
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    const parsed: unknown = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return fail(400, "The request body must be a JSON object.");
    const forbidden = forbiddenMutationField(parsed);
    if (forbidden) return fail(400, `The request body contains an unsupported field: ${forbidden}.`);
    const invalid = invalidMutationBody(route, request.method, parsed);
    if (invalid) return fail(400, invalid);
    return text;
  } catch {
    return fail(400, "The request body is not valid JSON.");
  }
}

type Context = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, context: Context): Promise<Response> {
  let origins: Set<string>;
  try { origins = configuredOrigins(process.env.MNEMONIC_DASHBOARD_ORIGINS); }
  catch { return fail(503, "Dashboard origins are not configured correctly."); }
  if (!trustedRequest(request.headers, request.method, origins)) return fail(403, "This dashboard request is not from a trusted origin.");

  const { path } = await context.params;
  if (path.some((part) => !/^[a-zA-Z0-9-]+$/.test(part))) return fail(404, "Route not found.");
  const route = path.join("/");
  const keys = allowedQueryKeys(route, request.method);
  if (!keys) return fail(404, "Route not found.");
  const query = new URL(request.url).searchParams;
  for (const key of query.keys()) {
    if (!keys.includes(key) || query.getAll(key).length !== 1) return fail(400, "Unsupported or repeated query parameter.");
  }

  const key = process.env.MNEMONIC_API_KEY;
  if (!key || key.length < 32) return fail(503, "Mnemonic's API connection is not configured. Check the server environment.");
  let base: URL;
  try {
    base = new URL(process.env.MNEMONIC_API_URL ?? "http://api:8000");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.pathname !== "/" || base.search || base.hash) throw new Error();
  } catch { return fail(503, "Mnemonic's API address is not configured correctly."); }

  let body: string | undefined;
  if (request.method === "POST" || request.method === "PATCH") {
    const result = await readBody(request, route);
    if (result instanceof Response) return result;
    body = result;
  } else if (request.method === "DELETE") {
    const result = await readBody(request, route, false);
    if (result instanceof Response) return result;
    body = result;
  }
  const target = new URL(`/api/v1/${route}`, base);
  target.search = query.toString();
  try {
    const upstream = await fetch(target, {
      method: request.method,
      body,
      headers: { Authorization: `Bearer ${key}`, Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(upstreamTimeoutMs(query))
    });
    if (upstream.status >= 300 && upstream.status < 400) return fail(502, "Mnemonic's API returned an unexpected redirect.");
    if (upstream.status !== 204 && !upstream.headers.get("content-type")?.includes("application/json")) return fail(502, "Mnemonic's API returned an unexpected response.");
    return new Response(upstream.status === 204 ? null : upstream.body, {
      status: upstream.status,
      headers: { ...responseHeaders, ...(upstream.status === 204 ? {} : { "Content-Type": "application/json" }) }
    });
  } catch {
    return fail(502, "Cannot reach Mnemonic's API. Check that the API and database containers are running.");
  }
}

export { proxy as GET, proxy as POST, proxy as PATCH, proxy as DELETE };
