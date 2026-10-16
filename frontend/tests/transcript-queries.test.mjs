import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import {
  invalidateTranscriptReads, transcriptDetailOptions, transcriptEvidenceChanged, transcriptHealthOptions, transcriptKeys,
  transcriptListOptions, transcriptSegmentOptions, transcriptSettingsOptions, transcriptTextOptions
} from "../lib/transcript-queries.ts";
import { disclosure } from "./search-disclosure-fixtures.mjs";
import { ranking } from "./search-ranking-fixtures.mjs";

const project = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const transcript = "33333333-3333-4333-8333-333333333333";
const digest = "a".repeat(64), revision = "b".repeat(64);
const filters = { search: "", fulltext: false, offset: 0, workItemId: "", contentKind: "", sortBy: null, sortDirection: "desc" };
const settings = { enabled: true, max_file_size_bytes: 67108864, revision: 1, operator_max_file_size_bytes: 1073741824, allowed_roots: ["/shared"] };
const row = {
  id: transcript, project_id: project, work_item_id: null, lease_generation_id: null,
  client: "codex", session_id: null, source_path: "/shared/session.jsonl", filename: "session.jsonl",
  kind: "imported", status: "ready", index_status: "ready", index_error_code: null,
  copy_status: "ready", copy_error_code: null, copied_at: null, indexing_started_at: null,
  indexing_completed_at: null, error_code: null, size_bytes: 100, mime_type: "application/x-ndjson",
  format: "codex-jsonl", sha256: digest, text_sha256: digest, metadata: {}, truncated: false,
  created_at: "2026-10-15T12:00:00Z", snippet: null, score: null, rank: 1, score_type: "none",
  matched_fields: [], normalization_status: "ready", normalization_error_code: null,
  normalized_revision: revision, normalized_sha256: digest, normalization_schema_version: 1,
  normalizer_version: 3, normalized_size_bytes: 100, segment_count: 1,
  normalization_incomplete: false, segment_id: null, content_kind: null, snippet_omission_reason: null
};
function listing(items = [row], projectId = project, changes = {}) {
  return { ...ranking("", "transcripts"), ...disclosure(projectId, ["transcripts"]),
    detail: "full", unsegmented_content_omitted: 0, next_offset: null, page_truncated: false,
    term_diagnostics: [], items, total: items.length, limit: 50, offset: 0,
    indexing_incomplete: false, ...changes };
}
function textPage(hash = digest, changes = {}) {
  return { transcript_id: transcript, project_id: project, text_sha256: hash, text: "Text",
    total_chars: 4, offset: 0, limit: 20000, next_offset: null, status: "ready", truncated: false,
    normalized_revision: revision, segments: null, segment_window: null,
    next_segment_id: null, next_segment_offset: null, next_segment_after: null, ...changes };
}
function clientFor(t) {
  const client = createDashboardQueryClient();
  const subscriptions = [];
  t.after(() => { subscriptions.forEach((unsubscribe) => unsubscribe()); client.clear(); });
  return { client, observe(options) {
    const observer = new QueryObserver(client, options);
    subscriptions.push(observer.subscribe(() => {}));
    return observer;
  } };
}

test("transcript list keys include request scope, normalize disabled content filters and cancel old project reads", async (t) => {
  const { client, observe } = clientFor(t);
  const base = transcriptListOptions(project, filters);
  assert.deepEqual(transcriptListOptions(project, { ...filters, contentKind: "human_text" }).queryKey, base.queryKey);
  for (const change of [{ search: "needle" }, { fulltext: true }, { offset: 50 },
    { workItemId: other }, { fulltext: true, contentKind: "assistant_text" }, { sortBy: "name" }, { sortDirection: "asc" }]) {
    assert.notDeepEqual(transcriptListOptions(project, { ...filters, ...change }).queryKey, base.queryKey);
  }
  const reads = [];
  t.mock.method(globalThis, "fetch", (url, init) => new Promise((resolve) => reads.push({ url, init, resolve })));
  const observer = observe(base);
  observer.setOptions(transcriptListOptions(other, filters));
  assert.equal(reads[0].init.signal.aborted, true);
  assert.equal(observer.getCurrentResult().data, undefined);
  reads[0].resolve(Response.json(listing()));
  reads[1].resolve(Response.json(listing([{ ...row, project_id: other }], other)));
  await setImmediate();
  assert.equal(observer.getCurrentResult().data.items[0].project_id, other);
  assert.equal(client.getQueryData(base.queryKey), undefined);
});

test("bounded text pages hide previous hashes and offsets, cancel late results and reject mismatched digests", async (t) => {
  const { client, observe } = clientFor(t);
  const reads = [];
  t.mock.method(globalThis, "fetch", (url, init) => new Promise((resolve) => reads.push({ url, init, resolve })));
  const base = transcriptTextOptions(project, transcript, digest, 0);
  const observer = observe(base);
  reads[0].resolve(Response.json(textPage()));
  await setImmediate();
  assert.equal(observer.getCurrentResult().data.text, "Text");
  observer.setOptions(transcriptTextOptions(project, transcript, digest, 20000));
  assert.equal(observer.getCurrentResult().data, undefined);
  observer.setOptions(transcriptTextOptions(project, transcript, revision, 0));
  assert.equal(reads[1].init.signal.aborted, true);
  assert.equal(observer.getCurrentResult().data, undefined);
  reads[1].resolve(Response.json(textPage(digest, { text: "", offset: 20000 })));
  reads[2].resolve(Response.json(textPage()));
  await setImmediate();
  assert.equal(observer.getCurrentResult().isError, true);
  assert.equal(observer.getCurrentResult().data, undefined);
  assert.equal(client.getQueryData(transcriptKeys.text(project, transcript, digest, 20000)), undefined);
  assert.equal(client.getQueryData(base.queryKey).text, "Text");
});

test("conversation requests cache only validated revision and locator windows", async (t) => {
  const { client } = clientFor(t);
  const hash = (value) => createHash("sha256").update(value).digest("hex").slice(0, 24);
  const eventId = hash(`${revision}:1`), segmentId = hash(`${eventId}:0`);
  const locator = { revision, segmentId, offset: 0, before: 0, after: 0 };
  const segment = { segment_id: segmentId, event_id: eventId, ordinal: 0, source_record: 1,
    source_block: "0", role: "assistant", content_kind: "assistant_text", text: "Text", timestamp: null,
    native_event_id: null, native_parent_id: null, native_branch_id: null, channel: null,
    is_sidechain: null, is_error: null, tool_name: null, call_id: null, payload: null,
    dispositions: [], related_segment_id: null, text_offset: 0, text_truncated: false };
  const response = textPage(digest, { segments: [segment], segment_window: {
    anchor_segment_id: segmentId, anchor_ordinal: 0, first_ordinal: 0, last_ordinal: 0
  } });
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(response));
  const options = transcriptSegmentOptions(project, transcript, digest, locator);
  assert.equal((await client.fetchQuery(options)).segments[0].text, "Text");
  assert.match(fetch.mock.calls[0].arguments[0], new RegExp(`expected_normalized_revision=${revision}`));
  for (const change of [{ revision: digest }, { segmentId: "c".repeat(24) }, { offset: 1 }, { before: 1 }, { after: 1 }]) {
    const changed = transcriptSegmentOptions(project, transcript, digest, { ...locator, ...change });
    assert.notDeepEqual(changed.queryKey, options.queryKey);
    if (change.before === undefined && change.after === undefined) {
      await assert.rejects(client.fetchQuery(changed), /revision changed/);
      assert.equal(client.getQueryData(changed.queryKey), undefined);
    }
  }
});

test("transcript search retries only admitted busy reads and cancellation interrupts the backoff", async (t) => {
  const { client } = clientFor(t);
  let attempts = 0;
  const fetch = t.mock.method(globalThis, "fetch", async () => ++attempts === 1
    ? Response.json({ detail: { code: "transcript_search_busy", message: "Busy" } }, { status: 503 })
    : Response.json(listing()));
  await client.fetchQuery(transcriptListOptions(project, filters));
  assert.equal(attempts, 2);
  const options = transcriptListOptions(other, filters);
  fetch.mock.mockImplementation(async () => Response.json({ detail: { code: "transcript_search_busy", message: "Busy" } }, { status: 503 }));
  const pending = client.fetchQuery(options);
  const rejection = assert.rejects(pending);
  await setImmediate();
  const count = fetch.mock.callCount();
  await client.cancelQueries({ queryKey: options.queryKey });
  await rejection;
  await setImmediate();
  assert.equal(fetch.mock.callCount(), count);
  fetch.mock.mockImplementation(async () => Response.json({ detail: { code: "search_deadline", message: "Timed out" } }, { status: 503 }));
  await assert.rejects(client.fetchQuery(options), /Timed out/);
  assert.equal(fetch.mock.callCount(), count + 1);
});

test("same-scope refresh preserves valid transcript rows on failure and recovers without replacing other project caches", async (t) => {
  const { client, observe } = clientFor(t);
  const options = transcriptListOptions(project, filters);
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(listing()));
  await client.fetchQuery(options);
  client.setQueryData(transcriptKeys.settings(other), settings);
  const observer = observe(options);
  const initial = observer.getCurrentResult().data;
  fetch.mock.mockImplementation(async () => Response.json(listing([{ ...row, project_id: other }])));
  await invalidateTranscriptReads(client, project);
  assert.equal(observer.getCurrentResult().data, initial);
  assert.equal(observer.getCurrentResult().isRefetchError, true);
  assert.equal(client.getQueryState(transcriptKeys.settings(other)).isInvalidated, false);
  fetch.mock.mockImplementation(async () => Response.json(listing([{ ...row, filename: "updated.jsonl" }])));
  await invalidateTranscriptReads(client, null);
  assert.equal(observer.getCurrentResult().data.items[0].filename, "updated.jsonl");
  assert.equal(client.getQueryState(transcriptKeys.settings(other)).isInvalidated, true);
});

test("discovery continues for new captures while detail polling stops terminal and every interval pauses hidden", () => {
  const list = transcriptListOptions(project, filters), detail = transcriptDetailOptions(project, transcript);
  const health = transcriptHealthOptions(project);
  for (const options of [list, detail, health]) assert.equal(options.refetchIntervalInBackground, false);
  assert.equal(list.refetchInterval, 30000);
  assert.equal(detail.refetchInterval({ state: { data: row } }), false);
  for (const status of ["pending", "processing", "waiting"]) {
    assert.equal(detail.refetchInterval({ state: { data: { ...row, index_status: status } } }), 30000);
  }
  assert.equal(detail.refetchInterval({ state: { data: { ...row, index_status: "failed" } } }), false);
  assert.equal(health.refetchInterval, 30000);
});

test("a changed lifecycle or revision invalidates an open evidence snapshot without reinterpreting its locator", () => {
  assert.equal(transcriptEvidenceChanged(row, undefined), false);
  assert.equal(transcriptEvidenceChanged(row, { ...row, filename: "renamed.jsonl" }), false);
  for (const patch of [{ text_sha256: revision }, { normalized_revision: digest }, { status: "pending" },
    { normalization_status: "processing" }, { project_id: other }, { id: other }]) {
    assert.equal(transcriptEvidenceChanged(row, { ...row, ...patch }), true);
  }
});

test("settings and health decode before caching and deduplicate simultaneous readers", async (t) => {
  const { client } = clientFor(t);
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(settings));
  const options = transcriptSettingsOptions(project);
  const [first, second] = await Promise.all([client.fetchQuery(options), client.fetchQuery(options)]);
  assert.equal(first, second);
  assert.equal(fetch.mock.callCount(), 1);
  fetch.mock.mockImplementation(async () => Response.json({ project_id: other }));
  await assert.rejects(client.fetchQuery(transcriptHealthOptions(project)), /Invalid transcript health/);
  assert.equal(client.getQueryData(transcriptKeys.health(project)), undefined);
});
