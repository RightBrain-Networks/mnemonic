import { BACKUP_FILENAME, backupMaximumBytes } from "./backups.ts";
import { readBoundedBytes } from "./bounded-json.ts";
import { configuredOrigins, forbiddenControlTransport, trustedRequest } from "./proxy-policy.ts";
import { validUuid } from "./wire-guards.ts";

const SECURITY_HEADERS = {
  "Cache-Control": "no-store, max-age=0, no-transform",
  "X-Content-Type-Options": "nosniff",
  "Cross-Origin-Resource-Policy": "same-origin",
  "Content-Security-Policy": "sandbox; default-src 'none'"
};

type Environment = {
  MNEMONIC_DASHBOARD_ORIGINS?: string;
  MNEMONIC_BACKUP_URL?: string;
  MNEMONIC_BACKUP_TOKEN?: string;
  MNEMONIC_BACKUP_MAX_BYTES?: string;
};

export function backupRoute(path: string[], method: string): "list" | "create" | "download" | "restore" | null {
  if (path[0] !== "projects" || !validUuid(path[1])) return null;
  if (path.length === 3 && path[2] === "backups") {
    if (method === "GET") return "list";
    if (method === "POST") return "create";
  }
  if (path.length === 4 && path[2] === "backups" && BACKUP_FILENAME.test(path[3]) && method === "GET") return "download";
  if (path.length === 3 && path[2] === "restore" && method === "POST") return "restore";
  return null;
}

export function boundedBackupStream(body: ReadableStream<Uint8Array>, maximum: number, exact = false): ReadableStream<Uint8Array> {
  let size = 0;
  return body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      size += chunk.byteLength;
      if (size > maximum) throw new Error("Backup archive exceeds the configured size limit.");
      controller.enqueue(chunk);
    },
    flush() {
      if (exact && size !== maximum) throw new Error("Backup archive ended before its declared byte length.");
    }
  }));
}

function fail(status: number, detail: string): Response {
  return Response.json({ detail }, { status, headers: SECURITY_HEADERS });
}

export async function proxyBackup(request: Request, path: string[], environment: Environment, fetcher: typeof fetch = fetch): Promise<Response> {
  let origins: Set<string>;
  let maximum: number;
  try {
    origins = configuredOrigins(environment.MNEMONIC_DASHBOARD_ORIGINS);
    maximum = backupMaximumBytes(environment.MNEMONIC_BACKUP_MAX_BYTES);
  } catch { return fail(503, "Backup dashboard settings are not configured correctly."); }
  if (!trustedRequest(request.headers, request.method, origins)) return fail(403, "This dashboard request is not from a trusted origin.");
  const action = backupRoute(path, request.method);
  if (!action) return fail(404, "Route not found.");
  if (new URL(request.url).search) return fail(400, "Backup queries are not supported.");
  if (forbiddenControlTransport(request.headers)) return fail(400, "The request contains a forbidden control header.");
  const token = environment.MNEMONIC_BACKUP_TOKEN;
  if (!token || token.length < 32) return fail(503, "The dashboard backup connection is not configured.");
  let base: URL;
  try {
    base = new URL(environment.MNEMONIC_BACKUP_URL ?? "http://backup:8002");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.pathname !== "/" || base.search || base.hash) throw new Error();
  } catch { return fail(503, "The backup service address is not configured correctly."); }
  const encoding = request.headers.get("content-encoding");
  if (encoding && encoding.toLowerCase() !== "identity") return fail(415, "Encoded backup request bodies are not supported.");
  const length = request.headers.get("content-length");
  if (length !== null && (!/^[0-9]+$/.test(length) || !Number.isSafeInteger(Number(length)))) return fail(400, "Invalid backup content length.");
  if (action === "restore") {
    if (request.headers.get("X-Confirm-Project") !== path[1]) return fail(400, "Confirm the selected project before restoring.");
    if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/octet-stream") return fail(415, "Send the compressed archive as application/octet-stream.");
    if (!request.body || length === "0") return fail(400, "A backup archive is required.");
    if (length !== null && Number(length) > maximum) return fail(413, "Backup archive exceeds the configured size limit.");
  } else {
    if (length !== null && Number(length) !== 0) return fail(400, "This backup action does not accept a request body.");
    // Next supplies an empty stream for a bodyless browser POST. Verify EOF;
    // testing stream presence alone rejects legitimate manual backup requests.
    if (request.body) {
      try { await readBoundedBytes(new Response(request.body), 0); }
      catch { return fail(400, "This backup action does not accept a request body."); }
    }
  }
  let uploadExceeded = false;
  try {
    const headers = new Headers({ Authorization: `Bearer ${token}`, "Accept-Encoding": "identity" });
    const init: RequestInit & { duplex?: "half" } = {
      method: request.method, headers, cache: "no-store", redirect: "manual",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(30 * 60_000)])
    };
    if (action === "restore") {
      headers.set("Content-Type", "application/octet-stream");
      headers.set("X-Confirm-Project", path[1]);
      let uploaded = 0;
      init.body = request.body!.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
        transform(chunk, controller) {
          uploaded += chunk.byteLength;
          if (uploaded > maximum) { uploadExceeded = true; throw new Error("Archive too large."); }
          controller.enqueue(chunk);
        }
      }));
      init.duplex = "half";
    }
    const upstream = await fetcher(new URL(`/${path.join("/")}`, base), init);
    if (upstream.status >= 300 && upstream.status < 400) { await upstream.body?.cancel(); return fail(502, "The backup service returned an unexpected redirect."); }
    const contentEncoding = upstream.headers.get("content-encoding");
    if (contentEncoding && contentEncoding.toLowerCase() !== "identity") { await upstream.body?.cancel(); return fail(502, "The backup service returned an encoded response."); }
    if (action === "download" && upstream.ok) {
      const size = upstream.headers.get("content-length");
      if (upstream.status !== 200 || !size || !/^[0-9]+$/.test(size) || !Number.isSafeInteger(Number(size)) || Number(size) < 1 || !upstream.body) {
        await upstream.body?.cancel(); return fail(502, "The backup service returned an invalid archive response.");
      }
      return new Response(boundedBackupStream(upstream.body, Number(size), true), {
        status: 200,
        headers: { ...SECURITY_HEADERS, "Content-Type": "application/octet-stream", "Content-Length": size, "Content-Disposition": `attachment; filename="${path[3]}"` }
      });
    }
    if (!upstream.headers.get("content-type")?.includes("application/json")) { await upstream.body?.cancel(); return fail(502, "The backup service returned an unexpected response."); }
    const bytes = await readBoundedBytes(upstream, 4 * 1024 * 1024);
    JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    return new Response(bytes, { status: upstream.status, headers: { ...SECURITY_HEADERS, "Content-Type": "application/json" } });
  } catch {
    return uploadExceeded
      ? fail(413, "Backup archive exceeds the configured size limit.")
      : fail(502, "The backup request could not be completed. Its outcome may be uncertain; reload and inspect the project before starting another action.");
  }
}
