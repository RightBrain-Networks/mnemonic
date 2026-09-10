import assert from "node:assert/strict";
import test from "node:test";
import { artifactQueryKeys, artifactMaximumBytes, boundedArtifactStream, proxyArtifact, safeArtifactDisposition } from "../lib/artifact-proxy.ts";
import { artifactMetadataHeader, dispatchArtifactMutation } from "../lib/artifact-mutations.ts";
import { ARTIFACT_DISABLED_MESSAGE, artifactLibraryPath, artifactLocation, decodeArtifact, decodeArtifactLimitError, decodeArtifactPage, decodeArtifactSearchPage, decodeArtifactStatus, fetchArtifactStatus, validArtifactSearchRequest } from "../lib/artifacts.ts";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const artifact = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const operation = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";
const root = `projects/${project}/artifacts`;
const path = `${root}/${artifact}`;
const environment = { MNEMONIC_API_KEY: "a".repeat(64), MNEMONIC_API_URL: "http://api:8000", MNEMONIC_ARTIFACT_MAX_BYTES: "16" };
const metadata = { id: artifact, project_id: project, filename: "report.txt", description: null, revision: 1, size_bytes: 3, sha256: "b".repeat(64), mime_type: "text/plain", created_at: "2026-09-01T00:00:00Z", modified_at: "2026-09-01T00:00:00Z", deleted_at: null, content_available: true, created_by_agent_session_id: "tab-1", originating_work_item_id: null, related_work_item_ids: [], sensitive: false, related_artifact_ids: [] };

function request(route = root, method = "GET", headers = {}, body) {
  return new Request(`http://localhost:3000/api/artifacts/${route}`, { method, headers: { host: "localhost:3000", ...(method !== "GET" ? { origin: "http://localhost:3000", "content-type": "application/octet-stream", "X-Client-Operation-ID": operation, "X-Artifact-Metadata": artifactMetadataHeader({ filename: "report.txt" }) } : {}), ...headers }, body, ...(body instanceof ReadableStream ? { duplex: "half" } : {}) });
}

test("artifact proxy exposes only project-scoped operations and keeps provenance out of URLs", () => {
  assert.deepEqual(artifactQueryKeys(root, "POST"), []);
  assert.deepEqual(artifactQueryKeys(`${path}/content`, "PUT"), []);
  assert.deepEqual(artifactQueryKeys(path, "DELETE"), []);
  assert.equal(artifactQueryKeys(`${root}/../../settings`, "GET"), null);
  assert.deepEqual(artifactQueryKeys(`${root}/search-content`, "POST"), []);
  assert.equal(artifactQueryKeys(`artifacts/${artifact}`, "GET"), null);
  assert.equal(artifactMaximumBytes(), 64 * 1024 * 1024);
  assert.equal(artifactMaximumBytes("0"), 0);
  for (const value of ["", " ", "-1", "NaN", "1073741825", "0.5"]) assert.throws(() => artifactMaximumBytes(value));
});

test("artifact proxy refuses cross-origin requests, unsupported metadata, credentials and oversized bodies before upstream", async () => {
  let calls = 0;
  const fetcher = async () => { calls++; throw new Error("must not run"); };
  for (const [input, route, status] of [
    [request(root, "POST", { origin: "https://attacker.example" }, "abc"), root, 403],
    [request(root, "POST", { "content-length": "1073741825" }, "abc"), root, 413],
    [request(root, "POST", { "X-Artifact-Metadata": '{"filename":"x","lease_token":"secret"}' }, "abc"), root, 400],
    [request(root, "POST", { "X-Lease-Token": "secret" }, "abc"), root, 400],
    [request(root, "POST", { "X-Artifact-Metadata": "x".repeat(16385) }, "abc"), root, 400],
    [request(root, "POST", { "content-type": "text/html" }, "abc"), root, 415],
    [request(path, "DELETE", { "X-Artifact-Metadata": "{}" }), path, 400],
    [request(`${root}?filename=secret.txt`, "POST", {}, "abc"), root, 400]
  ]) assert.equal((await proxyArtifact(input, route.split("/"), environment, fetcher)).status, status);
  assert.equal(calls, 0);
});

test("chunked artifact streams fail at the byte ceiling and cancel their source", async () => {
  let cancelled = false;
  const stream = new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array(8)); }, cancel() { cancelled = true; } });
  await assert.rejects(new Response(boundedArtifactStream(stream, 10)).arrayBuffer(), /size limit/);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(cancelled, true);
});

test("artifact downloads are streamed as sandboxed attachments without forwarding executable MIME or sensitive headers", async () => {
  const response = await proxyArtifact(request(`${path}/content`), `${path}/content`.split("/"), environment, async (_target, init) => {
    assert.equal(init.headers.get("authorization"), `Bearer ${environment.MNEMONIC_API_KEY}`);
    return new Response("abc", { headers: { "content-type": "text/html", "content-length": "3", "content-disposition": "inline; filename*=UTF-8''report%20%CE%B1.html", "set-cookie": "secret=value" } });
  });
  assert.equal(response.headers.get("content-type"), "application/octet-stream");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.match(response.headers.get("content-security-policy"), /sandbox/);
  assert.match(response.headers.get("content-disposition"), /^attachment;/);
  assert.match(response.headers.get("content-disposition"), /report%20%CE%B1.html/);
  assert.equal(response.headers.get("set-cookie"), null);
  assert.equal(await response.text(), "abc");
  assert.match(safeArtifactDisposition('attachment; filename="../secret"'), /artifact.bin/);
});

test("artifact downloads reject partial status and detect truncated declared content", async () => {
  const partial = await proxyArtifact(request(`${path}/content`), `${path}/content`.split("/"), environment, async () => new Response("abc", { status: 206, headers: { "content-length": "3" } }));
  assert.equal(partial.status, 502);
  const truncated = await proxyArtifact(request(`${path}/content`), `${path}/content`.split("/"), environment, async () => new Response("abc", { headers: { "content-length": "5" } }));
  await assert.rejects(truncated.arrayBuffer(), /declared byte length/);
});

test("artifact proxy forwards exact raw upload metadata and operation identity without buffering files", async () => {
  const header = artifactMetadataHeader({ filename: "résumé.txt", agent_session_id: "tab-1" });
  assert.match(header, /\\u00e9/);
  const response = await proxyArtifact(request(root, "POST", { "X-Artifact-Metadata": header }, "abc"), root.split("/"), environment, async (target, init) => {
    assert.equal(new URL(target).search, "");
    assert.equal(init.headers.get("X-Artifact-Metadata"), header);
    assert.equal(init.headers.get("X-Client-Operation-ID"), operation);
    assert.ok(init.body instanceof ReadableStream);
    assert.equal(await new Response(init.body).text(), "abc");
    return Response.json(metadata, { status: 201, headers: { "X-Client-Operation-ID": operation } });
  });
  assert.equal(response.status, 201);
  assert.equal(response.headers.get("X-Client-Operation-ID"), operation);
});

test("artifact reads reject foreign project records, unbounded pages, and malformed metadata", () => {
  assert.equal(decodeArtifact(metadata, project).filename, "report.txt");
  assert.throws(() => decodeArtifact(metadata, artifact));
  assert.throws(() => decodeArtifact({ ...metadata, related_work_item_ids: ["bad"] }, project));
  assert.throws(() => decodeArtifactPage({ items: [metadata], total: 1, offset: 0, limit: 101 }, project));
});

function searchRequest(body, headers = {}) {
  return new Request(`http://localhost:3000/api/artifacts/${root}/search-content`, { method: "POST", headers: { host: "localhost:3000", origin: "http://localhost:3000", "content-type": "application/json", ...headers }, body: typeof body === "string" ? body : JSON.stringify(body) });
}

test("artifact search POST is a bounded safe read with strict controls and no operation UUID", async () => {
  const body = { q: "report", fulltext: true, work_item_id: operation, artifact_id: artifact, include_deleted: false, limit: 50, offset: 0 };
  const response = await proxyArtifact(searchRequest(body), [...root.split("/"), "search-content"], environment, async (target, init) => {
    assert.equal(new URL(target).pathname, `/api/v1/${root}/search-content`);
    assert.equal(new URL(target).search, "");
    assert.equal(init.headers.get("X-Client-Operation-ID"), null);
    assert.equal(init.headers.get("X-Artifact-Metadata"), null);
    assert.equal(init.headers.get("Content-Type"), "application/json");
    assert.equal(init.headers.get("Accept-Encoding"), "identity");
    assert.deepEqual(JSON.parse(init.body), body);
    return Response.json({ items: [] }, { headers: { "X-Client-Operation-ID": operation } });
  });
  assert.equal(response.status, 200);
  assert.match(response.headers.get("cache-control"), /no-store/);
  assert.equal(response.headers.get("X-Client-Operation-ID"), null);
  let calls = 0;
  for (const [input, status] of [
    [searchRequest(body, { origin: "https://attacker.example" }), 403],
    [searchRequest(body, { "X-Client-Operation-ID": operation }), 400],
    [searchRequest(body, { "X-Artifact-Metadata": "{}" }), 400],
    [searchRequest(body, { "X-Artifact-Expected-Revision": "1" }), 400],
    [searchRequest(body, { "content-encoding": "gzip" }), 415],
    [searchRequest(body, { "content-type": "text/plain" }), 415],
    [searchRequest(body, { "content-length": "4097" }), 400],
    [searchRequest({ q: "x".repeat(4097) }), 400],
    [searchRequest({ ...body, client_operation_id: operation }), 400],
    [searchRequest({ ...body, fulltext: "false" }), 400],
    [searchRequest({ ...body, limit: 101 }), 400],
    [searchRequest({ ...body, offset: -1 }), 400],
    [searchRequest({ ...body, q: " " }), 400],
    [searchRequest("{"), 400]
  ]) assert.equal((await proxyArtifact(input, [...root.split("/"), "search-content"], environment, async () => { calls++; return Response.json({}); })).status, status);
  assert.equal(calls, 0);
  assert.equal(validArtifactSearchRequest({ q: "report" }), true);
  assert.equal(validArtifactSearchRequest({ q: "report", artifact_id: "invalid" }), false);
});

test("artifact search validates scope, pagination, fields, counts and untrusted plain snippets", () => {
  const match = { artifact: metadata, score: 1.5, snippet: "<script>untrusted text</script>", matched_fields: ["content"] };
  const result = { items: [match], total: 1, limit: 50, offset: 0, fulltext: true, sensitive_content_withheld: 0, indexing: { ready: 1, pending: 0, failed: 0, truncated: 0 } };
  assert.equal(decodeArtifactSearchPage(result, project, true).items[0].snippet, match.snippet);
  for (const invalid of [
    { ...result, fulltext: false }, { ...result, offset: 1 }, { ...result, total: 2 },
    { ...result, items: [match, match], total: 2 },
    { ...result, indexing: { ...result.indexing, pending: true } },
    { ...result, items: [{ ...match, score: Infinity }] },
    { ...result, items: [{ ...match, score: -1 }] },
    { ...result, items: [{ ...match, snippet: "x".repeat(1001) }] },
    { ...result, items: [{ ...match, artifact: { ...metadata, project_id: operation } }] },
    { ...result, items: [{ ...match, artifact: { ...metadata, deleted_at: metadata.created_at } }] },
    { ...result, items: [{ ...match, matched_fields: ["content", "content"] }] }
  ]) assert.throws(() => decodeArtifactSearchPage(invalid, project, true));
  assert.throws(() => decodeArtifactSearchPage({ ...result, fulltext: false }, project, false));
  const safe = { ...result, fulltext: false, items: [{ ...match, snippet: null, matched_fields: ["metadata"] }] };
  assert.equal(decodeArtifactSearchPage(safe, project, false).items[0].snippet, null);
});

test("artifact extraction metadata is independently bounded and extends current metadata", () => {
  const extraction = { status: "ready", metadata: { title: ["A private document"], author: ["<b>Author</b>"] }, truncated: false, error_code: null, extracted_at: metadata.created_at };
  assert.deepEqual(decodeArtifact({ ...metadata, extraction }, project).extraction, extraction);
  assert.equal(decodeArtifact(metadata, project).extraction.status, "pending");
  for (const invalid of [
    { ...extraction, status: "unknown" }, { ...extraction, truncated: "false" },
    { ...extraction, extracted_at: "invalid" }, { ...extraction, metadata: { title: ["x".repeat(513)] } },
    { ...extraction, metadata: { title: Array(9).fill("title") } },
    { ...extraction, metadata: Object.fromEntries(Array.from({ length: 65 }, (_, index) => [`key${index}`, []])) },
    { ...extraction, metadata: { a: Array(8).fill("界".repeat(512)) } },
    { ...extraction, metadata: { title: "not an array" } }
  ]) assert.throws(() => decodeArtifact({ ...metadata, extraction: invalid }, project));
});

test("artifact history reserves a bounded envelope for both metadata pages", async () => {
  const route = `${path}/history`;
  const payload = { revisions: { metadata: "x".repeat(4 * 1024 * 1024) }, audit: {} };
  const response = await proxyArtifact(request(route), route.split("/"), environment, async () => Response.json(payload));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).revisions.metadata.length, 4 * 1024 * 1024);
  const rejected = await proxyArtifact(request(route), route.split("/"), environment, async () => Response.json({}, { headers: { "content-length": String(6 * 1024 * 1024 + 1) } }));
  assert.equal(rejected.status, 502);
});

test("lost artifact responses retry identical bytes and UUIDs, and mismatched receipts remain unresolved", async () => {
  const file = new File(["abc"], "report.txt");
  const intent = Object.freeze({ method: "POST", path: `/api/artifacts/${root}`, projectId: project, operationId: operation, metadata: artifactMetadataHeader({ filename: file.name }), file });
  const attempts = [];
  const fetcher = async (_target, init) => {
    attempts.push({ operation: init.headers.get("X-Client-Operation-ID"), file: init.body, metadata: init.headers.get("X-Artifact-Metadata") });
    if (attempts.length === 1) throw new Error("connection lost");
    return Response.json(metadata, { status: 201, headers: { "X-Client-Operation-ID": operation } });
  };
  assert.equal((await dispatchArtifactMutation(intent, fetcher)).type, "unresolved");
  assert.equal((await dispatchArtifactMutation(intent, fetcher)).type, "success");
  assert.deepEqual(attempts[0], attempts[1]);
  assert.equal(attempts[0].file, file);
  const mismatch = await dispatchArtifactMutation(intent, async () => Response.json(metadata, { status: 201 }));
  assert.equal(mismatch.type, "unresolved");
  const wrongRevision = await dispatchArtifactMutation(intent, async () => Response.json({ ...metadata, revision: 2 }, { headers: { "X-Client-Operation-ID": operation } }));
  assert.equal(wrongRevision.type, "unresolved");
});

test("unknown and malformed artifact errors preserve the original file and operation for a successful retry", async () => {
  const file = new File(["abc"], "report.txt");
  const intent = Object.freeze({ method: "POST", path: `/api/artifacts/${root}`, projectId: project, operationId: operation, metadata: artifactMetadataHeader({ filename: file.name }), file });
  const attempts = [];
  const errorBodies = [
    { unrelated: "not an artifact error" },
    { detail: "An unknown upstream error" },
    { detail: { code: "unrecognized_error", message: "Unknown", context: {} } },
    { detail: { code: "artifact_metadata_invalid", message: "Invalid", context: {} }, extra: true },
    { detail: { code: "artifact_metadata_invalid", message: "Invalid" } }
  ];
  for (const status of [400, 401, 403, 404, 409, 410, 413, 415, 422, 429, 500]) {
    for (const body of errorBodies) {
      const outcome = await dispatchArtifactMutation(intent, async (_target, init) => {
        attempts.push({ id: init.headers.get("X-Client-Operation-ID"), metadata: init.headers.get("X-Artifact-Metadata"), file: init.body });
        return Response.json(body, { status });
      });
      assert.equal(outcome.type, "unresolved", `status ${status}: ${JSON.stringify(body)}`);
    }
  }
  const outcome = await dispatchArtifactMutation(intent, async (_target, init) => {
    assert.equal(init.body, file);
    assert.equal(init.headers.get("X-Client-Operation-ID"), operation);
    return Response.json(metadata, { status: 201, headers: { "X-Client-Operation-ID": operation } });
  });
  assert.equal(outcome.type, "success");
  for (const attempt of attempts) assert.deepEqual(attempt, { id: operation, metadata: intent.metadata, file });
});

test("artifact errors require a known code, exact status and valid envelope before clearing an intent", async () => {
  const intent = Object.freeze({ method: "POST", path: `/api/artifacts/${root}`, projectId: project, operationId: operation, metadata: artifactMetadataHeader({ filename: "report.txt" }), file: new File(["abc"], "report.txt") });
  for (const [status, code, expected] of [
    [404, "artifact_work_item_not_found", "rejected"],
    [409, "artifact_revision_conflict", "rejected"],
    [410, "artifact_deleted", "rejected"],
    [413, "artifact_too_large", "rejected"],
    [422, "artifact_metadata_invalid", "rejected"],
    [409, "client_operation_conflict", "safety_conflict"],
    [422, "client_operation_conflict", "unresolved"],
    [409, "future_operation_mismatch", "unresolved"],
    [503, "artifact_revision_conflict", "unresolved"],
    [422, "artifact_revision_conflict", "unresolved"]
  ]) {
    const outcome = await dispatchArtifactMutation(intent, async () => Response.json({ detail: { code, message: "Artifact request failed.", context: {} } }, { status }));
    assert.equal(outcome.type, expected, `${status}: ${code}`);
  }
});

test("artifact work links carry project identity and refuse ambiguous or unbound work selection", () => {
  const target = new URL(artifactLibraryPath(project, artifact), "http://localhost:3000");
  assert.equal(target.pathname, "/artifacts");
  assert.deepEqual(artifactLocation(target.search), { projectId: project, workItemId: artifact });
  assert.deepEqual(artifactLocation(new URL(artifactLibraryPath(operation), target).search), { projectId: operation, workItemId: null });
  for (const query of [`?work=${artifact}`, `?project=invalid&work=${artifact}`, `?project=${project}&project=${operation}&work=${artifact}`]) {
    assert.deepEqual(artifactLocation(query), { projectId: null, workItemId: null });
  }
  assert.deepEqual(artifactLocation(`?project=${project}&work=${artifact}&work=${operation}`), { projectId: project, workItemId: null });
});

test("artifact status validates enabled/zero consistency, bounds and exact fields", async () => {
  const enabled = { enabled: true, max_bytes: 16, message: "Up to 16 bytes per file." };
  const disabled = { enabled: false, max_bytes: 0, message: ARTIFACT_DISABLED_MESSAGE };
  assert.deepEqual(decodeArtifactStatus(enabled), enabled);
  assert.deepEqual(decodeArtifactStatus(disabled), disabled);
  for (const malformed of [
    { ...enabled, max_bytes: 0 }, { ...disabled, max_bytes: 1 }, { ...enabled, max_bytes: "16" },
    { ...enabled, enabled: 1 }, { ...enabled, max_bytes: -1 }, { ...enabled, max_bytes: 1073741825 },
    { ...enabled, max_bytes: 1.5 }, { ...enabled, message: "" }, { ...enabled, unknown: true }
  ]) assert.throws(() => decodeArtifactStatus(malformed));
  const controller = new AbortController();
  assert.deepEqual(await fetchArtifactStatus(controller.signal, async (target, init) => {
    assert.equal(target, "/api/artifacts/status"); assert.equal(init.signal, controller.signal);
    assert.equal(init.cache, "no-store"); return Response.json(disabled);
  }), disabled);
  await assert.rejects(fetchArtifactStatus(undefined, async () => Response.json(enabled, { status: 503 })), /status is unavailable/);
});

test("status remains reachable with local zero and exposes only validated effective limits", async () => {
  assert.deepEqual(artifactQueryKeys("status", "GET"), []);
  assert.equal(artifactQueryKeys("status", "POST"), null);
  for (const [local, backend, expected] of [[0, 16, 0], [16, 0, 0], [16, 8, 8], [8, 16, 8]]) {
    let calls = 0;
    const response = await proxyArtifact(request("status"), ["status"], { ...environment, MNEMONIC_ARTIFACT_MAX_BYTES: String(local) }, async (target, init) => {
      calls++; assert.equal(new URL(target).pathname, "/api/v1/artifacts/status");
      assert.equal(init.headers.get("authorization"), `Bearer ${environment.MNEMONIC_API_KEY}`);
      return Response.json({ enabled: backend > 0, max_bytes: backend, message: "Configured status." });
    });
    assert.equal(calls, 1); assert.equal(response.status, 200);
    const status = decodeArtifactStatus(await response.json());
    assert.equal(status.enabled, expected > 0); assert.equal(status.max_bytes, expected);
    assert.match(status.message, expected === 0 ? /disabled.*preserved/ : /bytes.*per file/);
    assert.match(response.headers.get("cache-control"), /no-store/);
  }
  for (const value of [{ enabled: true, max_bytes: 0, message: "Invalid" }, { enabled: false, max_bytes: 0, message: "Valid", secret: "not allowed" }]) {
    assert.equal((await proxyArtifact(request("status"), ["status"], environment, async () => Response.json(value))).status, 502);
  }
});

test("local zero disables every artifact data operation before upstream or body consumption", async () => {
  let calls = 0;
  let reads = 0;
  for (const [route, method] of [[root, "GET"], [path, "GET"], [`${path}/content`, "GET"], [`${path}/history`, "GET"], [`${root}/search-content`, "POST"], [root, "POST"], [`${path}/content`, "PUT"], [path, "DELETE"]]) {
    const body = method === "POST" || method === "PUT" ? new ReadableStream({ pull(controller) { reads++; controller.enqueue(new Uint8Array([1])); } }, { highWaterMark: 0 }) : undefined;
    const response = await proxyArtifact(request(route, method, {}, body), route.split("/"), { ...environment, MNEMONIC_ARTIFACT_MAX_BYTES: "0" }, async () => { calls++; throw new Error("Disabled operation was forwarded"); });
    assert.equal(response.status, 503);
    assert.deepEqual(decodeArtifactLimitError(await response.json(), 503), { code: "artifact_library_disabled", message: ARTIFACT_DISABLED_MESSAGE, maxBytes: 0 });
  }
  assert.equal(calls, 0); assert.equal(reads, 0);
});

test("enabled proxy allows API receipt decisions and existing downloads above a lowered positive limit", async () => {
  const bytes = "a".repeat(32);
  const oversizedError = { detail: { code: "artifact_too_large", message: "The per-file limit is 16 bytes.", context: { max_bytes: 16 } } };
  for (const status of [201, 413]) {
    const response = await proxyArtifact(request(root, "POST", { "content-length": "32" }, bytes), root.split("/"), environment, async (_target, init) => {
      assert.equal(await new Response(init.body).text(), bytes);
      assert.equal(init.headers.get("X-Client-Operation-ID"), operation);
      return Response.json(status === 201 ? { ...metadata, size_bytes: 32 } : oversizedError, { status, headers: { "X-Client-Operation-ID": operation } });
    });
    assert.equal(response.status, status);
    if (status === 413) assert.deepEqual(await response.json(), oversizedError);
  }
  const downloaded = await proxyArtifact(request(`${path}/content`), `${path}/content`.split("/"), environment, async () => new Response(bytes, { headers: { "content-length": "32" } }));
  assert.equal(downloaded.status, 200); assert.equal(await downloaded.text(), bytes);
});

test("disabled responses preserve an uncertain file and UUID for the same retry after re-enable", async () => {
  const file = new File(["abc"], "report.txt");
  const intent = Object.freeze({ method: "POST", path: `/api/artifacts/${root}`, projectId: project, operationId: operation, metadata: artifactMetadataHeader({ filename: file.name }), file });
  const attempts = [];
  const fetcher = async (_target, init) => {
    attempts.push({ id: init.headers.get("X-Client-Operation-ID"), metadata: init.headers.get("X-Artifact-Metadata"), file: init.body });
    if (attempts.length === 1) throw new Error("Response lost");
    if (attempts.length === 2) return Response.json({ detail: { code: "artifact_library_disabled", message: ARTIFACT_DISABLED_MESSAGE, context: { max_bytes: 0 } } }, { status: 503 });
    return Response.json(metadata, { status: 201, headers: { "X-Client-Operation-ID": operation } });
  };
  assert.equal((await dispatchArtifactMutation(intent, fetcher)).type, "unresolved");
  assert.deepEqual(await dispatchArtifactMutation(intent, fetcher), { type: "disabled", message: ARTIFACT_DISABLED_MESSAGE });
  assert.equal((await dispatchArtifactMutation(intent, fetcher)).type, "success");
  assert.deepEqual(attempts[0], attempts[1]); assert.deepEqual(attempts[0], attempts[2]);
  assert.equal(attempts[0].file, file);
  const excessive = await dispatchArtifactMutation(intent, async () => Response.json({ detail: { code: "artifact_too_large", message: "Limit: 2 bytes per file.", context: { max_bytes: 2 } } }, { status: 413 }));
  assert.deepEqual(excessive, { type: "rejected", message: "Limit: 2 bytes per file." });
});

test("malformed disabled/size errors cannot claim authoritative status or clear a pending file", async () => {
  const error = { detail: { code: "artifact_library_disabled", message: ARTIFACT_DISABLED_MESSAGE, context: { max_bytes: 0 } } };
  for (const [value, status] of [[error, 500], [{ ...error, extra: true }, 503], [{ detail: { ...error.detail, context: { max_bytes: "0" } } }, 503], [{ detail: { ...error.detail, context: { max_bytes: 1 } } }, 503], [{ detail: { ...error.detail, context: { max_bytes: 0, extra: true } } }, 503]]) assert.equal(decodeArtifactLimitError(value, status), null);
});

test("artifact metadata updates preserve exact JSON and receipt identity through the proxy", async () => {
  const body = JSON.stringify({ client_operation_id: operation, expected_revision: 1, sensitive: true, related_artifact_ids: [operation], related_work_item_ids: [project], actor_client: "dashboard", agent_session_id: "tab-1" });
  const updateRequest = (body, headers = {}) => new Request(`http://localhost:3000/api/artifacts/${path}`, { method: "PATCH", headers: { host: "localhost:3000", origin: "http://localhost:3000", "Content-Type": "application/json", "X-Client-Operation-ID": operation, ...headers }, body });
  assert.deepEqual(artifactQueryKeys(path, "PATCH"), []);
  let calls = 0;
  const response = await proxyArtifact(updateRequest(body), path.split("/"), environment, async (_target, init) => {
    calls++; assert.equal(init.method, "PATCH"); assert.equal(init.body, body);
    assert.equal(init.headers.get("X-Client-Operation-ID"), null);
    assert.equal(init.headers.get("X-Artifact-Metadata"), null);
    assert.equal(init.headers.get("Content-Type"), "application/json");
    return Response.json({ ...metadata, revision: 2, sensitive: true, related_artifact_ids: [operation], related_work_item_ids: [project] }, { headers: { "X-Client-Operation-ID": operation } });
  });
  assert.equal(response.status, 200); assert.equal(calls, 1);
  assert.equal(response.headers.get("X-Client-Operation-ID"), operation);
  const parsed = JSON.parse(body);
  for (const invalid of [
    { ...parsed, client_operation_id: artifact }, { ...parsed, expected_revision: 0 },
    { ...parsed, sensitive: "true" }, { ...parsed, related_artifact_ids: ["invalid"] },
    { ...parsed, related_artifact_ids: [operation, operation] },
    { ...parsed, approval_token: "not-browser-input" }, { ...parsed, actor_client: "agent" }
  ]) assert.equal((await proxyArtifact(updateRequest(JSON.stringify(invalid)), path.split("/"), environment, async () => { throw new Error("must not forward"); })).status, 400);
  assert.equal((await proxyArtifact(updateRequest(body, { origin: "https://untrusted.example" }), path.split("/"), environment)).status, 403);
});

test("sensitive human dashboard reads use server-owned context without forwarding caller policy headers", async () => {
  for (const input of [request(`${path}/content`, "GET", { "X-Artifact-Access": "arbitrary", "X-Artifact-Approval": "untrusted" }), searchRequest({ q: "report", fulltext: true }, { "X-Artifact-Access": "arbitrary" })]) {
    const route = input.method === "GET" ? `${path}/content` : `${root}/search-content`;
    await proxyArtifact(input, route.split("/"), environment, async (_target, init) => {
      assert.equal(init.headers.get("X-Artifact-Access"), "human-dashboard");
      assert.equal(init.headers.get("X-Artifact-Approval"), null);
      return input.method === "GET" ? new Response("abc", { headers: { "content-length": "3" } }) : Response.json({ items: [] });
    });
  }
});

test("sensitive metadata and artifact links fail closed on invalid flags, self-links and duplicate identities", () => {
  assert.equal(decodeArtifact({ ...metadata, sensitive: true, related_artifact_ids: [operation] }, project).sensitive, true);
  for (const invalid of [
    { ...metadata, sensitive: "false" }, { ...metadata, sensitive: undefined },
    { ...metadata, related_artifact_ids: [artifact] }, { ...metadata, related_artifact_ids: ["bad"] },
    { ...metadata, related_artifact_ids: [operation, operation.toUpperCase()] }
  ]) assert.throws(() => decodeArtifact(invalid, project));
});

test("uncertain metadata updates retry unchanged and verify the changed flag and durable links", async () => {
  const body = JSON.stringify({ client_operation_id: operation, expected_revision: 1, sensitive: true, related_artifact_ids: [operation], related_work_item_ids: [project] });
  const intent = Object.freeze({ method: "PATCH", path: `/api/artifacts/${path}`, projectId: project, artifactId: artifact, expectedRevision: 1, operationId: operation, metadata: body });
  const successful = { ...metadata, revision: 2, sensitive: true, related_artifact_ids: [operation], related_work_item_ids: [project] };
  let attempts = 0;
  const fetcher = async (_target, init) => {
    attempts++; assert.equal(init.method, "PATCH"); assert.equal(init.body, body);
    assert.equal(init.headers.get("X-Artifact-Metadata"), null);
    assert.equal(init.headers.get("X-Artifact-Expected-Revision"), null);
    assert.equal(init.headers.get("X-Client-Operation-ID"), operation);
    if (attempts === 1) throw new Error("Response lost after commit");
    return Response.json(successful, { headers: { "X-Client-Operation-ID": operation } });
  };
  assert.equal((await dispatchArtifactMutation(intent, fetcher)).type, "unresolved");
  assert.equal((await dispatchArtifactMutation(intent, fetcher)).type, "success");
  for (const invalid of [{ ...successful, sensitive: false }, { ...successful, revision: 1 }, { ...successful, related_artifact_ids: [] }, { ...successful, related_work_item_ids: [] }]) {
    assert.equal((await dispatchArtifactMutation(intent, async () => Response.json(invalid, { headers: { "X-Client-Operation-ID": operation } }))).type, "unresolved");
  }
});


test("an artifact work-link receipt can satisfy a requested link through immutable originating work", async () => {
  const intent = Object.freeze({ method: "PATCH", path: `/api/artifacts/${path}`, projectId: project, artifactId: artifact, expectedRevision: 1, operationId: operation, metadata: JSON.stringify({ related_work_item_ids: [project] }) });
  const outcome = await dispatchArtifactMutation(intent, async () => Response.json({ ...metadata, revision: 2, originating_work_item_id: project }, { headers: { "X-Client-Operation-ID": operation } }));
  assert.equal(outcome.type, "success");
});


test("pre-0029 receipts retain original bytes and normalize absent fields only on an authenticated receipt replay", async () => {
  const { sensitive: _sensitive, related_artifact_ids: _relatedArtifacts, ...historical } = metadata;
  const file = new File(["abc"], "report.txt");
  const intent = Object.freeze({ method: "POST", path: `/api/artifacts/${root}`, projectId: project, operationId: operation, metadata: artifactMetadataHeader({ filename: "report.txt" }), file });
  for (const replayed of [null, "false", "true"]) {
    const headers = { "X-Client-Operation-ID": operation, ...(replayed ? { "X-Artifact-Operation-Replayed": replayed } : {}) };
    const upstream = () => Response.json(historical, { status: 201, headers });
    const proxied = await proxyArtifact(request(root, "POST", {}, "abc"), root.split("/"), environment, upstream);
    assert.deepEqual(await proxied.clone().json(), historical);
    assert.equal(proxied.headers.get("X-Artifact-Operation-Replayed"), replayed === "true" ? "true" : null);
    const outcome = await dispatchArtifactMutation(intent, async () => proxied);
    assert.equal(outcome.type, replayed === "true" ? "success" : "unresolved");
    if (outcome.type === "success") { assert.equal(outcome.artifact.sensitive, false); assert.deepEqual(outcome.artifact.related_artifact_ids, []); }
  }
  assert.throws(() => decodeArtifact(historical, project));
  const patch = { ...intent, method: "PATCH", artifactId: artifact, expectedRevision: 1, metadata: "{}" };
  assert.equal((await dispatchArtifactMutation(patch, async () => Response.json({ ...historical, revision: 2 }, { headers: { "X-Client-Operation-ID": operation, "X-Artifact-Operation-Replayed": "true" } }))).type, "unresolved");
});


test("related artifact metadata is bound to the identity requested for the durable link", () => {
  assert.equal(decodeArtifact(metadata, project, artifact.toUpperCase()).id, artifact);
  assert.throws(() => decodeArtifact(metadata, project, operation));
});
