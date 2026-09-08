import assert from "node:assert/strict";
import test from "node:test";
import { artifactQueryKeys, artifactMaximumBytes, boundedArtifactStream, proxyArtifact, safeArtifactDisposition } from "../lib/artifact-proxy.ts";
import { artifactMetadataHeader, dispatchArtifactMutation } from "../lib/artifact-mutations.ts";
import { artifactLibraryPath, artifactLocation, decodeArtifact, decodeArtifactPage } from "../lib/artifacts.ts";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const artifact = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const operation = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";
const root = `projects/${project}/artifacts`;
const path = `${root}/${artifact}`;
const environment = { MNEMONIC_API_KEY: "a".repeat(64), MNEMONIC_API_URL: "http://api:8000", MNEMONIC_ARTIFACT_MAX_BYTES: "16" };
const metadata = { id: artifact, project_id: project, filename: "report.txt", description: null, revision: 1, size_bytes: 3, sha256: "b".repeat(64), mime_type: "text/plain", created_at: "2026-09-01T00:00:00Z", modified_at: "2026-09-01T00:00:00Z", deleted_at: null, content_available: true, created_by_agent_session_id: "tab-1", originating_work_item_id: null, related_work_item_ids: [] };

function request(route = root, method = "GET", headers = {}, body) {
  return new Request(`http://localhost:3000/api/artifacts/${route}`, { method, headers: { host: "localhost:3000", ...(method !== "GET" ? { origin: "http://localhost:3000", "content-type": "application/octet-stream", "X-Client-Operation-ID": operation, "X-Artifact-Metadata": artifactMetadataHeader({ filename: "report.txt" }) } : {}), ...headers }, body, ...(body instanceof ReadableStream ? { duplex: "half" } : {}) });
}

test("artifact proxy exposes only project-scoped operations and keeps provenance out of URLs", () => {
  assert.deepEqual(artifactQueryKeys(root, "POST"), []);
  assert.deepEqual(artifactQueryKeys(`${path}/content`, "PUT"), []);
  assert.deepEqual(artifactQueryKeys(path, "DELETE"), []);
  assert.equal(artifactQueryKeys(`${root}/../../settings`, "GET"), null);
  assert.equal(artifactQueryKeys(`${root}/search-content`, "POST"), null);
  assert.equal(artifactQueryKeys(`artifacts/${artifact}`, "GET"), null);
  assert.equal(artifactMaximumBytes(), 64 * 1024 * 1024);
  for (const value of ["0", "-1", "NaN", "1073741825", "0.5"]) assert.throws(() => artifactMaximumBytes(value));
});

test("artifact proxy refuses cross-origin requests, unsupported metadata, credentials and oversized bodies before upstream", async () => {
  let calls = 0;
  const fetcher = async () => { calls++; throw new Error("must not run"); };
  for (const [input, route, status] of [
    [request(root, "POST", { origin: "https://attacker.example" }, "abc"), root, 403],
    [request(root, "POST", { "content-length": "17" }, "abc"), root, 413],
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
