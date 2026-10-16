import { ranking, hitRanking, unifiedRanking, semanticDisposition, evidence } from "./search-ranking-fixtures.mjs";
import { disclosure } from "./search-disclosure-fixtures.mjs";
import assert from "node:assert/strict";
import test from "node:test";

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

test("artifact reads reject foreign project records, unbounded pages, and malformed metadata", () => {
  assert.equal(decodeArtifact(metadata, project).filename, "report.txt");
  assert.throws(() => decodeArtifact(metadata, artifact));
  assert.throws(() => decodeArtifact({ ...metadata, related_work_item_ids: ["bad"] }, project));
  assert.throws(() => decodeArtifactPage({ items: [metadata], total: 1, offset: 0, limit: 101 }, project));
});

function searchRequest(body, headers = {}) {
  return new Request(`http://localhost:3000/api/artifacts/${root}/search-content`, { method: "POST", headers: { host: "localhost:3000", origin: "http://localhost:3000", "content-type": "application/json", ...headers }, body: typeof body === "string" ? body : JSON.stringify(body) });
}

test("artifact search validates scope, pagination, fields, counts and untrusted plain snippets", () => {
  const match = { evidence: "lexical", passage: null, ...hitRanking("", "artifacts"), artifact: metadata, score: 0, snippet: "<script>untrusted text</script>", matched_fields: ["content"] };
  const result = { ...ranking("", "artifacts"), next_offset: null, page_truncated: false, detail: "full", ...disclosure(project, ["artifacts"], { fulltext: true }), match_mode: "all_terms", embedding: null, term_diagnostics: [], items: [match], total: 1, limit: 50, offset: 0, fulltext: true, sensitive_content_withheld: 0, indexing: { ready: 1, pending: 0, failed: 0, truncated: 0 } };
  assert.equal(decodeArtifactSearchPage(result, project, true).items[0].snippet, match.snippet);
  for (const invalid of [
    { ...result, fulltext: false }, { ...result, offset: 1 }, { ...result, total: 2 },
    { ...result, items: [match, match], total: 2 },
    { ...result, indexing: { ...result.indexing, pending: true } },
    { ...result, match_mode: "semantic_passages" },
    { ...result, items: [{ ...match, score: Infinity }] },
    { ...result, items: [{ ...match, score: -1 }] },
    { ...result, items: [{ ...match, snippet: "x".repeat(1001) }] },
    { ...result, items: [{ ...match, artifact: { ...metadata, project_id: operation } }] },
    { ...result, items: [{ ...match, artifact: { ...metadata, deleted_at: metadata.created_at } }] },
    { ...result, items: [{ ...match, matched_fields: ["content", "content"] }] }
  ]) assert.throws(() => decodeArtifactSearchPage(invalid, project, true));
  assert.throws(() => decodeArtifactSearchPage({ ...result, fulltext: false }, project, false));
  const safe = { ...result, ...disclosure(project, ["artifacts"]), fulltext: false, items: [{ ...match, snippet: null, matched_fields: ["metadata"] }] };
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

test("related artifact metadata is bound to the identity requested for the durable link", () => {
  assert.equal(decodeArtifact(metadata, project, artifact.toUpperCase()).id, artifact);
  assert.throws(() => decodeArtifact(metadata, project, operation));
});

test("pre-0029 receipts retain original bytes and normalize absent fields only on an authenticated receipt replay", async () => {
  const { sensitive: _sensitive, related_artifact_ids: _relatedArtifacts, ...historical } = metadata;
  const file = new File(["abc"], "report.txt");
  const intent = Object.freeze({ method: "POST", path: `/api/artifacts/${root}`, projectId: project, operationId: operation, metadata: artifactMetadataHeader({ filename: "report.txt" }), file });
  for (const replayed of [null, "false", "true"]) {
    const headers = { "X-Client-Operation-ID": operation, ...(replayed ? { "X-Artifact-Operation-Replayed": replayed } : {}) };
    const proxied = Response.json(historical, { status: 201, headers });
    const outcome = await dispatchArtifactMutation(intent, async () => proxied);
    assert.equal(outcome.type, replayed === "true" ? "success" : "unresolved");
    if (outcome.type === "success") { assert.equal(outcome.artifact.sensitive, false); assert.deepEqual(outcome.artifact.related_artifact_ids, []); }
  }
  assert.throws(() => decodeArtifact(historical, project));
  const patch = { ...intent, method: "PATCH", artifactId: artifact, expectedRevision: 1, metadata: "{}" };
  assert.equal((await dispatchArtifactMutation(patch, async () => Response.json({ ...historical, revision: 2 }, { headers: { "X-Client-Operation-ID": operation, "X-Artifact-Operation-Replayed": "true" } }))).type, "unresolved");
});
