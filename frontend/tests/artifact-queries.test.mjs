import assert from "node:assert/strict";
import test from "node:test";
import { QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import { artifactKeys, artifactPageOptions, artifactDetailOptions, artifactPassageOptions, artifactPreviewCurrent, artifactStatusOptions, invalidateArtifactReads, prepareArtifactPageNavigation, reconcileArtifactMutation } from "../lib/artifact-queries.ts";

function createClient() {
  const client = createDashboardQueryClient();
  client.setDefaultOptions({ ...client.getDefaultOptions(), queries: { ...client.getDefaultOptions().queries, gcTime: 0 } });
  return client;
}

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const otherProject = "99999999-9999-4999-8999-999999999999";
const id = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const timestamp = "2026-09-16T00:00:00Z";
const artifact = { id, project_id: project, filename: "report.txt", description: null, revision: 1, size_bytes: 3, sha256: "a".repeat(64), mime_type: "text/plain", created_at: timestamp, modified_at: timestamp, deleted_at: null, content_available: true, created_by_agent_session_id: null, originating_work_item_id: null, related_work_item_ids: [], tags: null, sensitive: false, related_artifact_ids: [], extraction: { status: "ready", truncated: false, error_code: null, extracted_at: timestamp, metadata: {} } };
const filters = { projectId: project, search: "", fulltext: false, semantic: false, includeDeleted: false, workItemId: "", sort: "filename", order: "asc", offset: 0, limit: 50 };
const status = { enabled: true, max_bytes: 64 * 1024 * 1024, message: "Artifact library enabled." };
const page = { items: [artifact], total: 1, limit: 50, offset: 0 };
const turn = () => new Promise((resolve) => setTimeout(resolve, 0));

function observer(t, options, client = createClient()) {
  const read = new QueryObserver(client, options(client));
  const stop = read.subscribe(() => {});
  t.after(() => { stop(); client.clear(); });
  return { read, client, stop };
}

test("artifact pages share in-flight reads and cancel old scopes without publishing their rows", async (t) => {
  const held = Promise.withResolvers();
  const requests = [];
  let heldSignal;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    if (url === "/api/artifacts/status") return Response.json(status);
    requests.push(url);
    if (String(url).includes(`projects/${otherProject}`)) { heldSignal = init.signal; return held.promise; }
    return Response.json(page);
  });
  const { read, client } = observer(t, (client) => artifactPageOptions(client, filters));
  const duplicate = client.fetchQuery(artifactPageOptions(client, filters));
  await read.refetch(); await duplicate;
  assert.equal(requests.length, 1);
  assert.equal(read.getCurrentResult().data.page.items[0].id, id);
  read.setOptions(artifactPageOptions(client, { ...filters, projectId: otherProject }));
  assert.equal(read.getCurrentResult().data, undefined);
  await turn();
  read.setOptions(artifactPageOptions(client, filters));
  assert.equal(heldSignal.aborted, true);
  held.resolve(Response.json({ ...page, items: [{ ...artifact, project_id: otherProject }] }));
  await turn();
  assert.equal(read.getCurrentResult().data.page.items[0].project_id, project);
  assert.equal(client.getQueryData(artifactKeys.page({ ...filters, projectId: otherProject })), undefined);
});

test("sorting and pagination retain truthful old ranges while changing filters removes previous data", async (t) => {
  const held = Promise.withResolvers();
  let hold = false;
  t.mock.method(globalThis, "fetch", async (url) => url === "/api/artifacts/status" ? Response.json(status) : hold ? held.promise : Response.json(page));
  const { read, client } = observer(t, (client) => artifactPageOptions(client, filters));
  await read.refetch(); hold = true;
  read.setOptions(artifactPageOptions(client, { ...filters, offset: 50, order: "desc" }));
  assert.equal(read.getCurrentResult().isPlaceholderData, true);
  assert.equal(read.getCurrentResult().data.page.offset, 0);
  read.setOptions(artifactPageOptions(client, { ...filters, includeDeleted: true }));
  assert.equal(read.getCurrentResult().data, undefined);
  read.setOptions(artifactPageOptions(client, filters, false));
  assert.equal(read.getCurrentResult().data, undefined);
  held.resolve(Response.json(page));
  await turn();
});

test("returning to a cached sort preserves the displayed page until its fresh response arrives", async (t) => {
  const client = createClient();
  const held = Promise.withResolvers();
  const nextFilters = { ...filters, offset: 50 };
  t.mock.method(globalThis, "fetch", async (url) => url === "/api/artifacts/status" ? Response.json(status) : held.promise);
  const current = { page: { ...page, total: 100, offset: 50 }, searchPage: null };
  client.setQueryData(artifactKeys.page(nextFilters), current);
  client.setQueryData(artifactKeys.page(filters), { page, searchPage: null });
  const { read } = observer(t, (client) => ({ ...artifactPageOptions(client, nextFilters), staleTime: Infinity }), client);
  prepareArtifactPageNavigation(client, nextFilters);
  assert.deepEqual(client.getQueryData(artifactKeys.page(nextFilters)), current, "active page remains intact");
  prepareArtifactPageNavigation(client, filters);
  assert.equal(client.getQueryData(artifactKeys.page(filters)), undefined);
  read.setOptions(artifactPageOptions(client, filters));
  assert.equal(read.getCurrentResult().isPlaceholderData, true);
  assert.equal(read.getCurrentResult().data.page.offset, 50);
  await turn(); held.resolve(Response.json(page)); await read.refetch();
  assert.equal(read.getCurrentResult().data.page.offset, 0);
});

test("unavailable status prevents page requests and disabled resource responses update shared availability", async (t) => {
  const client = createClient(); t.after(() => client.clear());
  let unavailable = true;
  let pageCalls = 0;
  t.mock.method(globalThis, "fetch", async (url) => {
    if (url === "/api/artifacts/status") return unavailable ? Response.json({}, { status: 503 }) : Response.json(status);
    pageCalls += 1;
    return Response.json({ detail: { code: "artifact_library_disabled", message: "Library disabled.", context: { max_bytes: 0 } } }, { status: 503 });
  });
  await assert.rejects(client.fetchQuery(artifactPageOptions(client, filters)), /status is unavailable/);
  assert.equal(pageCalls, 0);
  unavailable = false;
  await assert.rejects(client.fetchQuery(artifactPageOptions(client, filters)), /Library disabled/);
  assert.equal(pageCalls, 1);
  assert.deepEqual(client.getQueryData(artifactKeys.status), { enabled: false, max_bytes: 0, message: "Library disabled." });
});

test("semantic invalidation marks results stale without repeating inference; only explicit retry repeats a failure", async (t) => {
  const semanticFilters = { ...filters, search: "listener session", fulltext: true, semantic: true };
  const client = createClient();
  const options = artifactPageOptions(client, semanticFilters);
  let requests = 0;
  const read = new QueryObserver(client, { ...options, queryFn: async () => { requests += 1; return { page, searchPage: null }; } });
  const stop = read.subscribe(() => {}); t.after(() => { stop(); client.clear(); });
  await read.refetch();
  assert.equal(options.refetchInterval, false);
  assert.equal(options.retry, false);
  assert.equal(options.refetchOnWindowFocus, false);
  await invalidateArtifactReads(client, project, { semantic: false });
  assert.equal(requests, 1);
  assert.equal(client.getQueryState(options.queryKey).isInvalidated, false, "visibility alone is not evidence that semantic results changed");
  await invalidateArtifactReads(client, project);
  assert.equal(requests, 1);
  assert.equal(client.getQueryState(options.queryKey).isInvalidated, true);
  await read.refetch();
  assert.equal(requests, 2);
  assert.equal(client.getQueryState(options.queryKey).isInvalidated, false);
  read.setOptions({ ...options, queryFn: async () => { requests += 1; throw new Error("capacity exhausted"); } });
  await read.refetch(); await turn();
  assert.equal(requests, 3);
  assert.equal(read.getCurrentResult().isError, true);
  read.setOptions(artifactPageOptions(client, semanticFilters, false));
  await invalidateArtifactReads(client, project);
  read.setOptions({ ...artifactPageOptions(client, semanticFilters), queryFn: async () => { requests += 1; throw new Error("capacity exhausted"); } });
  await turn();
  assert.equal(requests, 3, "status recovery must not repeat a failed semantic request");
  await read.refetch();
  assert.equal(requests, 4, "explicit retry remains available");
});

test("confirmed mutations cancel stale details and invalidate related pages within their project", async (t) => {
  const client = createClient(); t.after(() => client.clear());
  const originalKey = artifactKeys.detail(project, id);
  const relatedKey = artifactKeys.detail(project, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
  const unrelatedKey = artifactKeys.page({ ...filters, projectId: otherProject });
  client.setQueryData(originalKey, artifact); client.setQueryData(relatedKey, artifact); client.setQueryData(unrelatedKey, { page });
  const changed = { ...artifact, revision: 2, sensitive: true };
  await reconcileArtifactMutation(client, changed);
  assert.deepEqual(client.getQueryData(originalKey), changed);
  assert.equal(client.getQueryState(relatedKey).isInvalidated, true);
  assert.equal(client.getQueryState(unrelatedKey).isInvalidated, false);
  assert.equal(artifactDetailOptions(client, project, id, false).enabled, false);
});

test("confirmed artifact mutations cancel pending old pages and clear retained passage bodies", async (t) => {
  const client = createClient(); t.after(() => client.clear());
  const held = Promise.withResolvers(); let signal;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    if (url === "/api/artifacts/status") return Response.json(status);
    signal = init.signal; return held.promise;
  });
  const pending = client.fetchQuery(artifactPageOptions(client, filters)).catch(() => null);
  await turn();
  const evidenceKey = artifactKeys.passage(match);
  client.setQueryData(evidenceKey, "private old text");
  const updated = { ...artifact, revision: 2, sensitive: true };
  await reconcileArtifactMutation(client, updated);
  assert.equal(signal.aborted, true);
  assert.equal(client.getQueryData(evidenceKey), undefined);
  held.resolve(Response.json(page)); await pending; await turn();
  assert.equal(client.getQueryData(artifactKeys.page(filters)), undefined);
  assert.equal(artifactPreviewCurrent(artifact, updated), false);
  for (const change of [{ revision: 2 }, { sha256: "d".repeat(64) }, { sensitive: true }, { content_available: false }, { deleted_at: timestamp }]) {
    assert.equal(artifactPreviewCurrent(artifact, { ...artifact, ...change }), false);
  }
  assert.equal(artifactPreviewCurrent(artifact, artifact), true);
  assert.equal(artifactPassageOptions(match, false).enabled, false);
});

const passage = { passage_id: "c".repeat(64), artifact_revision: 1, text_sha256: "b".repeat(64), start_offset: 0, end_offset: 3, model: "test-model", chunk_config: "test-config", token_count: 1, token_limit: 512, cosine_similarity: 0.75, score_is_probability: false, score_type: "cosine_similarity" };
const match = { artifact, passage, snippet: "abc", matched_fields: ["content"], evidence: "semantic", rank: 1, score: 1 / 61, score_type: "semantic_reciprocal_rank" };
const passageResponse = { project_id: project, artifact_id: id, revision: 1, sha256: artifact.sha256, text_sha256: passage.text_sha256, extraction: { status: "ready", truncated: false, error_code: null, extracted_at: timestamp }, text: "abc", offset: 0, limit: 3, total_chars: 3, next_offset: null };

test("passage reads key and validate exact revision/hash/offset and discard sensitive content on close", async (t) => {
  const client = createClient(); t.after(() => client.clear());
  let body = passageResponse;
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url) => { requests.push(String(url)); return Response.json(body); });
  const options = artifactPassageOptions(match);
  assert.equal(await client.fetchQuery(options), "abc");
  const url = new URL(requests[0], "http://mnemonic.test");
  assert.equal(url.searchParams.get("expected_revision"), "1");
  assert.equal(url.searchParams.get("expected_text_sha256"), passage.text_sha256);
  assert.notDeepEqual(options.queryKey, artifactPassageOptions({ ...match, passage: { ...passage, text_sha256: "d".repeat(64) } }).queryKey);
  assert.notDeepEqual(options.queryKey, artifactPassageOptions({ ...match, artifact: { ...artifact, sensitive: true } }).queryKey);
  body = { ...passageResponse, revision: 2 };
  await assert.rejects(client.fetchQuery(options), /outside the selected passage/);
  await turn();
  assert.equal(client.getQueryData(options.queryKey), undefined);
  const held = Promise.withResolvers(); let signal;
  t.mock.method(globalThis, "fetch", (_url, init) => { signal = init.signal; return held.promise; });
  const { read } = observer(t, () => options, client);
  read.setOptions(artifactPassageOptions(undefined));
  assert.equal(signal.aborted, true);
  held.resolve(Response.json(passageResponse));
  await turn();
  assert.equal(client.getQueryData(options.queryKey), undefined);
  assert.equal(artifactStatusOptions().retry, false);
});

test("artifact tag filters retain AND scope, reset old rows and verify returned assignments", async (t) => {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    if (url === "/api/artifacts/status") return Response.json(status);
    requests.push(new URL(url, "https://mnemonic.test"));
    return Response.json({ ...page, items: [{ ...artifact, tags: ["tests", "infra"] }] });
  });
  const client = createClient();
  t.after(() => client.clear());
  const scoped = { ...filters, tags: ["tests", "infra"], tagMode: "and" };
  const read = artifactPageOptions(client, scoped);
  const result = await client.fetchQuery(read);
  assert.deepEqual(requests[0].searchParams.getAll("tags"), scoped.tags);
  assert.equal(requests[0].searchParams.get("tag_mode"), "and");
  assert.deepEqual(result.page.items[0].tags, scoped.tags);
  const previous = { queryKey: artifactKeys.page(scoped) };
  assert.equal(artifactPageOptions(client, { ...scoped, offset: 50 }).placeholderData(result, previous), result);
  assert.equal(artifactPageOptions(client, { ...scoped, tagMode: "or" }).placeholderData(result, previous), undefined);
  assert.equal(artifactPageOptions(client, { ...scoped, tags: ["tests"] }).placeholderData(result, previous), undefined);
  await assert.rejects(client.fetchQuery(artifactPageOptions(client, { ...scoped, tags: ["security"] })), /outside the selected tags/);
});
