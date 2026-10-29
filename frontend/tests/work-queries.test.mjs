import assert from "node:assert/strict";
import test from "node:test";
import { InfiniteQueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import { queryKeys } from "../lib/query-keys.ts";
import { canonicalWorkQueryOptions, workQueueQueryOptions } from "../lib/work-queries.ts";
import { mergeWorkPages } from "../lib/work-queue.ts";

const filters = {
  projectId: "project-a", status: "pending", sort: "updated", search: "", semantic: false,
  duplicateScope: "canonical", canonicalWorkItemId: "", tag: "", sourceClient: "", sourceSessionId: "", pinnedWorkItemIds: []
};
function page(offset, size, total = 9) {
  const end = Math.min(total, offset + size);
  return {
    items: Array.from({ length: end - offset }, (_, index) => ({ summary: { work_item: { id: String(offset + index) } } })),
    offset, total, limit: 20, next_offset: end < total ? end : null, page_truncated: end < total,
    coverage: { fixture: "preserved" }
  };
}
function items(data) { return data.pages.flatMap((chunk) => chunk.pages.flatMap((page) => page.items.map((item) => item.summary.work_item.id))); }

test("queue keys distinguish every request filter and canonical scopes", () => {
  const client = createDashboardQueryClient();
  try {
    const key = workQueueQueryOptions(client, filters).queryKey;
    for (const [field, value] of Object.entries({ group: false, projectId: "other", status: "done", sort: "priority", search: "query", semantic: true, duplicateScope: "all", canonicalWorkItemId: "canonical", tag: "tag", sourceClient: "codex", sourceSessionId: "session", pinnedWorkItemIds: ["pinned"] })) {
      assert.notDeepEqual(workQueueQueryOptions(client, { ...filters, [field]: value }).queryKey, key, field);
    }
    assert.notDeepEqual(canonicalWorkQueryOptions({ projectId: filters.projectId, query: "query", excludedWorkId: "a", limit: 10 }).queryKey,
      canonicalWorkQueryOptions({ projectId: filters.projectId, query: "query", excludedWorkId: "b", limit: 10 }).queryKey);
    assert.deepEqual(canonicalWorkQueryOptions({ projectId: filters.projectId, query: " query ", excludedWorkId: "a", limit: 10 }).queryKey,
      canonicalWorkQueryOptions({ projectId: filters.projectId, query: "query", excludedWorkId: "a", limit: 10 }).queryKey);
  } finally { client.clear(); }
});

test("queue appends follow server offsets and refresh preserves a prefix across shorter pages", async () => {
  const client = createDashboardQueryClient();
  let size = 3;
  const offsets = [];
  const options = workQueueQueryOptions(client, filters, async (_, offset) => { offsets.push(offset); return page(offset, size); });
  const observer = new InfiniteQueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  try {
    await observer.refetch();
    await observer.fetchNextPage();
    assert.deepEqual(offsets, [0, 3]);
    assert.deepEqual(items(observer.getCurrentResult().data), ["0", "1", "2", "3", "4", "5"]);
    assert.deepEqual(observer.getCurrentResult().data.pages[0].pages[0].coverage, { fixture: "preserved" });
    size = 2;
    offsets.length = 0;
    await observer.refetch();
    assert.deepEqual(offsets, [0, 2, 4]);
    assert.deepEqual(items(observer.getCurrentResult().data), ["0", "1", "2", "3", "4", "5"]);
    offsets.length = 0;
    await observer.fetchNextPage();
    assert.deepEqual(offsets, [6]);
    assert.deepEqual(items(observer.getCurrentResult().data), ["0", "1", "2", "3", "4", "5", "6", "7"]);
    await observer.fetchNextPage();
    assert.equal(observer.getCurrentResult().hasNextPage, false);
  } finally { stop(); client.clear(); }
});

test("failed append retains cached pages and retry resumes the exact continuation", async () => {
  const client = createDashboardQueryClient();
  let fail = true;
  const offsets = [];
  const options = workQueueQueryOptions(client, filters, async (_, offset) => {
    offsets.push(offset);
    if (offset === 3 && fail) throw new Error("Temporary read failure");
    return page(offset, 3);
  });
  const observer = new InfiniteQueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  try {
    await observer.refetch();
    await observer.fetchNextPage();
    assert.equal(observer.getCurrentResult().isFetchNextPageError, true);
    assert.deepEqual(items(observer.getCurrentResult().data), ["0", "1", "2"]);
    fail = false;
    await observer.fetchNextPage();
    assert.deepEqual(offsets, [0, 3, 3]);
    assert.deepEqual(items(observer.getCurrentResult().data), ["0", "1", "2", "3", "4", "5"]);
  } finally { stop(); client.clear(); }
});

test("multiple queue observers share one read and project invalidation is scoped", async () => {
  const client = createDashboardQueryClient();
  let reads = 0;
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const options = workQueueQueryOptions(client, filters, async () => { reads += 1; await held; return page(0, 9); });
  const first = new InfiniteQueryObserver(client, options);
  const second = new InfiniteQueryObserver(client, options);
  const stopFirst = first.subscribe(() => {});
  const stopSecond = second.subscribe(() => {});
  try {
    const firstRead = first.refetch();
    const secondRead = second.refetch();
    release();
    await Promise.all([firstRead, secondRead]);
    assert.equal(reads, 1);
    await client.invalidateQueries({ queryKey: queryKeys.work("project-b") });
    assert.equal(reads, 1);
    await client.invalidateQueries({ queryKey: queryKeys.work("project-a") });
    assert.equal(reads, 2);
  } finally { stopFirst(); stopSecond(); client.clear(); }
});

test("leaving a queue scope aborts an outstanding read and cannot populate its successor", async () => {
  const client = createDashboardQueryClient();
  let oldSignal;
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const options = workQueueQueryOptions(client, filters, async (_, offset, signal) => { oldSignal = signal; await held; return page(offset, 3); });
  const observer = new InfiniteQueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  try {
    observer.setOptions(workQueueQueryOptions(client, { ...filters, projectId: "project-b" }, async () => page(0, 1, 1)));
    assert.equal(oldSignal.aborted, true);
    release();
    await observer.refetch();
    assert.deepEqual(items(observer.getCurrentResult().data), ["0"]);
    assert.equal(client.getQueryData(options.queryKey), undefined);
  } finally { stop(); client.clear(); }
});

test("duplicate visible records do not affect cached server continuation or stopped short pages", async () => {
  const client = createDashboardQueryClient();
  const options = workQueueQueryOptions(client, filters, async (_, offset) => {
    const result = page(offset, 2, 4);
    if (offset === 2) result.items[0].summary.work_item.id = "1";
    return result;
  });
  const observer = new InfiniteQueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  try {
    await observer.refetch();
    await observer.fetchNextPage();
    const data = observer.getCurrentResult().data;
    const merged = mergeWorkPages(data.pages.flatMap((chunk) => chunk.pages));
    assert.equal(merged.items.length, 3);
    assert.equal(merged.loaded, 4);
    assert.equal(observer.getCurrentResult().hasNextPage, false);
    assert.equal(options.retry, false);
    assert.equal(options.refetchOnWindowFocus, false);
    assert.equal(options.refetchOnReconnect, false);
  } finally { stop(); client.clear(); }
});

test("refresh does not append beyond the prior prefix when wire pages grow", async () => {
  const client = createDashboardQueryClient();
  let size = 2;
  const offsets = [];
  const options = workQueueQueryOptions(client, filters, async (_, offset) => { offsets.push(offset); return page(offset, size); });
  const observer = new InfiniteQueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  try {
    await observer.refetch();
    await observer.fetchNextPage();
    await observer.fetchNextPage();
    size = 3;
    offsets.length = 0;
    await observer.refetch();
    assert.deepEqual(offsets, [0, 3]);
    assert.deepEqual(items(observer.getCurrentResult().data), ["0", "1", "2", "3", "4", "5"]);
    assert.equal(observer.getCurrentResult().hasNextPage, true);
    await observer.fetchNextPage();
    assert.deepEqual(offsets, [0, 3, 6]);
    assert.equal(observer.getCurrentResult().hasNextPage, false);
  } finally { stop(); client.clear(); }
});

test("invalidation cancels an append and retains its loaded empty or populated snapshot until refresh succeeds", async () => {
  const client = createDashboardQueryClient();
  const gates = [];
  const signals = [];
  let hold = false;
  let total = 4;
  const options = workQueueQueryOptions(client, filters, async (_, offset, signal) => {
    if (hold) {
      signals.push(signal);
      await new Promise((resolve) => gates.push(resolve));
    }
    return page(offset, 2, total);
  });
  const observer = new InfiniteQueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  try {
    await observer.refetch();
    const loaded = observer.getCurrentResult().data;
    hold = true;
    const append = observer.fetchNextPage();
    const refresh = client.invalidateQueries({ queryKey: queryKeys.work(filters.projectId) });
    assert.equal(signals[0].aborted, true);
    assert.equal(observer.getCurrentResult().data, loaded);
    assert.deepEqual(items(observer.getCurrentResult().data), ["0", "1"]);
    total = 0;
    gates.splice(0).forEach((release) => release());
    await Promise.all([append, refresh]);
    assert.deepEqual(items(observer.getCurrentResult().data), []);
    const empty = observer.getCurrentResult().data;
    const emptyRefresh = client.invalidateQueries({ queryKey: queryKeys.work(filters.projectId) });
    assert.equal(observer.getCurrentResult().data, empty);
    assert.equal(observer.getCurrentResult().isPending, false);
    gates.splice(0).forEach((release) => release());
    await emptyRefresh;
    assert.deepEqual(items(observer.getCurrentResult().data), []);
  } finally { gates.forEach((release) => release()); stop(); client.clear(); }
});
