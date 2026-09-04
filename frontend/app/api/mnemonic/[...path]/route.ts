import {
  DEFINITIVE_PROXY_ERRORS,
  allowedQueryKeys,
  clientOperationMatchesSecret,
  configuredOrigins,
  forbiddenControlTransport,
  forwardedRetryAfter,
  isCompletionEvidenceRoute,
  invalidMutationBody,
  proxyBodyLimitBytes,
  readBodyChunk,
  trustedRequest,
  upstreamAbortSignal,
  type DefinitiveProxyError
} from "@/lib/proxy-policy";
import {
  decodeIdentityEvidenceJson,
  identityContentEncoding,
  readIdentityEvidenceBytes
} from "@/lib/completion-evidence";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const responseHeaders = {
  "Cache-Control": "no-store, max-age=0, no-transform",
  "X-Content-Type-Options": "nosniff",
  "Cross-Origin-Resource-Policy": "same-origin"
};

function fail(status: number, detail: string, identity = false): Response {
  return Response.json({ detail }, {
    status,
    headers: {
      ...responseHeaders,
      ...(identity ? { "Content-Encoding": "identity" } : {})
    }
  });
}

function definitiveFail(error: DefinitiveProxyError, identity = false): Response {
  return fail(error.status, error.detail, identity);
}

async function readBody(
  request: Request,
  route: string,
  signal: AbortSignal
): Promise<string | Response> {
  const maximumBytes = proxyBodyLimitBytes(route);
  if (Number(request.headers.get("content-length")) > maximumBytes) return definitiveFail(DEFINITIVE_PROXY_ERRORS.bodyTooLarge);
  const reader = request.body?.getReader();
  if (!reader) return definitiveFail(DEFINITIVE_PROXY_ERRORS.requestBodyRequired);
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { value, done } = await readBodyChunk(reader, signal);
    if (done) break;
    size += value.byteLength;
    if (size > maximumBytes) {
      await reader.cancel();
      return definitiveFail(DEFINITIVE_PROXY_ERRORS.bodyTooLarge);
    }
    chunks.push(value);
  }
  if (size === 0) return definitiveFail(DEFINITIVE_PROXY_ERRORS.requestBodyRequired);
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
    return definitiveFail(DEFINITIVE_PROXY_ERRORS.jsonContentTypeRequired);
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
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return definitiveFail(DEFINITIVE_PROXY_ERRORS.jsonObjectRequired);
    const invalid = invalidMutationBody(route, request.method, parsed);
    if (invalid) return fail(400, invalid);
    return text;
  } catch {
    return definitiveFail(DEFINITIVE_PROXY_ERRORS.invalidJson);
  }
}

type Context = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, context: Context): Promise<Response> {
  let origins: Set<string>;
  try { origins = configuredOrigins(process.env.MNEMONIC_DASHBOARD_ORIGINS); }
  catch { return fail(503, "Dashboard origins are not configured correctly."); }
  if (!trustedRequest(request.headers, request.method, origins)) return definitiveFail(DEFINITIVE_PROXY_ERRORS.untrustedOrigin);
  if (forbiddenControlTransport(request.headers)) {
    return definitiveFail(DEFINITIVE_PROXY_ERRORS.forbiddenControlTransport);
  }

  const { path } = await context.params;
  if (path.some((part) => !/^[a-zA-Z0-9-]+$/.test(part))) return definitiveFail(DEFINITIVE_PROXY_ERRORS.routeNotFound);
  const route = path.join("/");
  const keys = allowedQueryKeys(route, request.method);
  if (!keys) return definitiveFail(DEFINITIVE_PROXY_ERRORS.routeNotFound);
  const evidenceRoute = isCompletionEvidenceRoute(route, request.method);
  const query = new URL(request.url).searchParams;
  for (const key of query.keys()) {
    if (!keys.includes(key) || query.getAll(key).length !== 1) {
      return definitiveFail(DEFINITIVE_PROXY_ERRORS.unsupportedQuery, evidenceRoute);
    }
  }

  const key = process.env.MNEMONIC_API_KEY;
  if (!key || key.length < 32) return fail(503, "Mnemonic's API connection is not configured. Check the server environment.", evidenceRoute);
  let base: URL;
  try {
    base = new URL(process.env.MNEMONIC_API_URL ?? "http://api:8000");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.pathname !== "/" || base.search || base.hash) throw new Error();
  } catch { return fail(503, "Mnemonic's API address is not configured correctly.", evidenceRoute); }

  try {
    const requestSignal = upstreamAbortSignal(
      request.signal,
      query,
      route,
      request.method
    );
    let body: string | undefined;
    if (request.method === "POST" || request.method === "PATCH") {
      const result = await readBody(request, route, requestSignal);
      if (result instanceof Response) return result;
      body = result;
    } else if (request.method === "DELETE") {
      const result = await readBody(request, route, requestSignal);
      if (result instanceof Response) return result;
      body = result;
    }
    if (clientOperationMatchesSecret(body, key)) {
      return definitiveFail(DEFINITIVE_PROXY_ERRORS.clientOperationCredentialMatch);
    }
    const target = new URL(`/api/v1/${route}`, base);
    target.search = query.toString();
    const upstream = await fetch(target, {
      method: request.method,
      body,
      headers: {
        Authorization: `Bearer ${key}`,
        Accept: "application/json",
        ...(evidenceRoute ? { "Accept-Encoding": "identity" } : {}),
        ...(body ? { "Content-Type": "application/json" } : {})
      },
      cache: "no-store",
      redirect: "manual",
      signal: requestSignal
    });
    if (evidenceRoute && !identityContentEncoding(upstream.headers)) {
      try { await upstream.body?.cancel(); } catch { /* best effort */ }
      return fail(502, "Mnemonic's API returned an incomplete response.", true);
    }
    if (upstream.status >= 300 && upstream.status < 400) return fail(502, "Mnemonic's API returned an unexpected redirect.", evidenceRoute);
    if (upstream.status !== 204 && !upstream.headers.get("content-type")?.includes("application/json")) return fail(502, "Mnemonic's API returned an unexpected response.", evidenceRoute);
    let responseBody: BodyInit | null = null;
    if (upstream.status !== 204) {
      try {
        if (evidenceRoute) {
          const evidenceBytes = await readIdentityEvidenceBytes(upstream);
          decodeIdentityEvidenceJson(evidenceBytes);
          responseBody = evidenceBytes;
        } else {
          responseBody = await upstream.arrayBuffer();
          JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(responseBody));
        }
      } catch {
        return fail(502, "Mnemonic's API returned an incomplete response.", evidenceRoute);
      }
    }
    const retryAfter = forwardedRetryAfter(upstream.status, upstream.headers);
    return new Response(responseBody, {
      status: upstream.status,
      headers: {
        ...responseHeaders,
        ...(evidenceRoute ? { "Content-Encoding": "identity" } : {}),
        ...(upstream.status === 204 ? {} : { "Content-Type": "application/json" }),
        ...(retryAfter ? { "Retry-After": retryAfter } : {})
      }
    });
  } catch {
    return fail(502, "Cannot reach Mnemonic's API. Check that the API and database containers are running.", evidenceRoute);
  }
}

export { proxy as GET, proxy as POST, proxy as PATCH, proxy as DELETE };
