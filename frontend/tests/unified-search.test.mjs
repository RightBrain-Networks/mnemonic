import assert from "node:assert/strict";
import test from "node:test";
import { artifactSearchRequest, decodeUnifiedArtifactSearchPage, decodeUnifiedTranscriptSearchPage, decodeUnifiedWorkSearchPage, transcriptSearchRequest, unifiedSearchPath, workSearchRequest } from "../lib/unified-search.ts";
import { validSearchRequest } from "../lib/search-request.ts";
import { allowedQueryKeys, browserTransportEffect, invalidMutationBody, isUnifiedSearchRoute, phase12ResponseLimitBytes, proxyBodyLimitBytes, upstreamTimeoutMs } from "../lib/proxy-policy.ts";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const id = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";
const timestamp = "2026-09-10T12:00:00Z";
const indexing = { pending: 2, ready: 1, failed: 0, truncated: 0 };
const artifact = { id, project_id: project, filename: "report.txt", description: null, revision: 1, size_bytes: 3, sha256: "a".repeat(64), mime_type: "text/plain", created_at: timestamp, modified_at: timestamp, deleted_at: null, content_available: true, created_by_agent_session_id: "tab-1", originating_work_item_id: work, related_work_item_ids: [], sensitive: false, related_artifact_ids: [] };
const transcript = { id, project_id: project, work_item_id: work, lease_generation_id: work, client: "claude-code", session_id: "session-1", source_path: "/shared/session.jsonl", filename: "session.jsonl", kind: "primary", status: "ready", indexing_started_at: timestamp, indexing_completed_at: timestamp, error_code: null, size_bytes: 128, mime_type: "application/x-ndjson", format: "claude-code-jsonl", sha256: "a".repeat(64), text_sha256: "a".repeat(64), metadata: {}, truncated: false, created_at: timestamp, snippet: null, score: null };
function result(facet, payload, limit = 50, offset = 0) {
  const key = facet === "work_items" ? "work_item" : facet === "artifacts" ? "artifact" : "transcript";
  return { items: [{ facet, id, created_at: timestamp, updated_at: timestamp, score: 0.5, [key]: payload }], total: offset + 1, limit, offset, facet_totals: { work_items: 0, artifacts: 0, transcripts: 0, [facet]: offset + 1 }, coverage: { artifacts: { enabled: true, indexing, sensitive_content_withheld: 3 }, transcripts: { indexing_incomplete: true } }, indexing_incomplete: true };
}

test("the separate dashboard searches encode their facet, filters and pagination", () => {
  const options = { status: "done", sort: "created", limit: 20, offset: 40, query: "  database  ", tag: " release ", sourceClient: "claude-code", sourceSessionId: "session-1", duplicateScope: "all", canonicalWorkItemId: work };
  assert.deepEqual(workSearchRequest(options), { q: "database", facets: ["work_items"], filters: { work_items: { status: "done", duplicate_scope: "all", tag: "release", source_client: "claude-code", source_session_id: "session-1", canonical_work_item_id: work } }, sort: { by: "created_at", direction: "desc" }, limit: 20, offset: 40 });
  assert.equal(workSearchRequest({ ...options, semantic: true }).sort.by, "relevance");
  assert.equal(workSearchRequest({ ...options, semantic: true }).filters.work_items.semantic, true);
  assert.throws(() => workSearchRequest({ ...options, duplicateScope: "canonical" }));
  const files = artifactSearchRequest("report", true, true, 50, 50, work);
  assert.deepEqual(files, { q: "report", facets: ["artifacts"], fulltext: true, filters: { artifacts: { include_deleted: true, work_item_id: work } }, limit: 50, offset: 50 });
  const sessions = transcriptSearchRequest("", false, 50, work);
  assert.equal(sessions.sort.by, "created_at");
  assert.deepEqual(sessions.filters, { transcripts: { work_item_id: work } });
  for (const body of [workSearchRequest(options), files, sessions]) assert.equal(validSearchRequest(body), true);
  assert.equal(unifiedSearchPath(project), `/projects/${project}/search`);
  assert.throws(() => unifiedSearchPath("../other"));
});

test("the project search POST is a bounded safe read without mutation credentials", () => {
  const path = `projects/${project}/search`;
  assert.deepEqual(allowedQueryKeys(path, "POST"), []);
  for (const method of ["GET", "PATCH", "DELETE"]) assert.equal(allowedQueryKeys(path, method), null);
  assert.equal(browserTransportEffect(path, "POST"), "safe_read");
  assert.equal(isUnifiedSearchRoute(path, "POST"), true);
  assert.equal(isUnifiedSearchRoute(path, "GET"), false);
  assert.equal(isUnifiedSearchRoute(`${path}/other`, "POST"), false);
  assert.equal(invalidMutationBody(path, "POST", {}), null);
  assert.equal(phase12ResponseLimitBytes(path, "POST"), 16 * 1024 * 1024);
  assert.equal(proxyBodyLimitBytes(path), 16_384);
  assert.equal(upstreamTimeoutMs(new URLSearchParams(), path, "POST"), 60000);
  const body = { q: "architecture", facets: ["artifacts", "work_items"], filters: { artifacts: { sensitive: true }, work_items: { status: "done" } }, facet_order: [{ facet: "artifacts", sort: { by: "relevance" } }, { facet: "work_items", sort: { by: "created_at", direction: "asc" } }], offset: 100, limit: 20 };
  assert.equal(invalidMutationBody(path, "POST", body), null);
  for (const invalid of [{ ...body, client_operation_id: work }, { ...body, lease_token: "secret" }, { ...body, q: "x".repeat(1001) }, { ...body, facets: ["artifacts", "artifacts"] }, { ...body, facets: [] }, { ...body, offset: -1 }, { ...body, limit: 101 }, { ...body, filters: { artifacts: { sensitive: "false" } } }, { ...body, filters: { transcripts: { lease_token: "secret" } } }, { ...body, facet_order: [{ facet: "transcripts" }] }]) assert.ok(invalidMutationBody(path, "POST", invalid));
});

test("artifact results retain snippets and incomplete coverage while rejecting scope leaks", () => {
  const payload = { artifact, score: 0.5, snippet: "<script>untrusted text</script>", matched_fields: ["content"] };
  const page = result("artifacts", payload, 50, 50);
  const decoded = decodeUnifiedArtifactSearchPage(page, project, true, 50, 50, false, work);
  assert.equal(decoded.items[0].snippet, payload.snippet);
  assert.equal(decoded.sensitive_content_withheld, 3);
  assert.deepEqual(decoded.indexing, indexing);
  assert.throws(() => decodeUnifiedArtifactSearchPage(page, project, false, 50, 50));
  assert.throws(() => decodeUnifiedArtifactSearchPage(page, project, true, 50, 0));
  assert.throws(() => decodeUnifiedArtifactSearchPage(page, id, true, 50, 50));
  assert.throws(() => decodeUnifiedArtifactSearchPage(page, project, true, 50, 50, false, id));
  const deleted = result("artifacts", { ...payload, artifact: { ...artifact, deleted_at: timestamp }, snippet: null, matched_fields: ["metadata"] });
  assert.throws(() => decodeUnifiedArtifactSearchPage(deleted, project, true, 50, 0));
  assert.equal(decodeUnifiedArtifactSearchPage(deleted, project, true, 50, 0, true).items.length, 1);
});

test("transcript unified results preserve work scope and metadata-only content boundaries", () => {
  const page = result("transcripts", transcript);
  assert.equal(decodeUnifiedTranscriptSearchPage(page, project, 0, false, work).indexing_incomplete, true);
  const content = result("transcripts", { ...transcript, snippet: "untrusted transcript" });
  assert.throws(() => decodeUnifiedTranscriptSearchPage(content, project));
  assert.equal(decodeUnifiedTranscriptSearchPage(content, project, 0, true).items[0].snippet, "untrusted transcript");
  assert.throws(() => decodeUnifiedTranscriptSearchPage(page, project, 0, false, id));
  assert.throws(() => decodeUnifiedTranscriptSearchPage(page, work));
});

test("single-facet views reject mixed, duplicate, mismatched, and truncated envelopes", () => {
  const page = result("transcripts", transcript);
  const mutations = [
    { ...page, total: 2, facet_totals: { ...page.facet_totals, transcripts: 2 } },
    { ...page, facet_totals: { ...page.facet_totals, artifacts: 1 } },
    { ...page, items: [{ ...page.items[0], id: work }] },
    { ...page, items: [{ ...page.items[0], facet: "artifacts" }] },
    { ...page, items: [{ ...page.items[0], artifact: {} }] },
    { ...page, items: [{ ...page.items[0], score: NaN }] },
    { ...page, items: [page.items[0], page.items[0]], total: 2, facet_totals: { ...page.facet_totals, transcripts: 2 } }
  ];
  for (const invalid of mutations) assert.throws(() => decodeUnifiedTranscriptSearchPage(invalid, project));
  assert.throws(() => decodeUnifiedWorkSearchPage(page, project));
  const empty = { ...page, items: [], total: 0, facet_totals: { work_items: 0, artifacts: 0, transcripts: 0 } };
  assert.deepEqual(decodeUnifiedWorkSearchPage(empty, project), { items: [], total: 0, limit: 50, offset: 0 });
});


test("work facet results preserve canonical matched-member attribution and page scope", () => {
  const item = { id, project_id: project, title: "Canonical search", summary: "Durable context", status: "pending", priority: 5, initial_checkpoint_id: work, version: 1, created_at: timestamp, updated_at: timestamp };
  const context = { id: work, work_item_id: id, kind: "context", source_client: "dashboard", source_session_id: "tab-1", source_model: null, repository_branch: null, verified_against: null, tags: [], migration_origin: null, legacy_record_id: null, created_at: timestamp };
  const readiness = { lifecycle_status: "pending", is_terminal: false, has_active_lease: false, has_dropped_lease: false, active_lease: null, unresolved_blocker_count: 0, is_blocked: false, unresolved_gate_count: 0, is_gated: false, is_duplicate: false, canonical_work_item_id: id, is_ready: true, display_state: "pending" };
  const summary = { work_item: item, checkpoint_count: 1, ancestor_path: [], ancestor_path_truncated: false, current_context: context, readiness };
  const matched_member = { id: work, title: "An alias matched", status: "done" };
  const page = result("work_items", { summary, matched_member }, 20, 40);
  const options = { query: "alias", expectedLimit: 20, expectedOffset: 40 };
  assert.deepEqual(decodeUnifiedWorkSearchPage(page, project, options).items[0].matched_member, matched_member);
  assert.throws(() => decodeUnifiedWorkSearchPage(page, work, options));
  assert.throws(() => decodeUnifiedWorkSearchPage(page, project, { ...options, duplicateScope: "aliases" }));
  assert.throws(() => decodeUnifiedWorkSearchPage(page, project, { ...options, query: "" }));
});
