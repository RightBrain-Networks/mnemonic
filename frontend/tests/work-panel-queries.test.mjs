import assert from "node:assert/strict";
import test from "node:test";
import { InfiniteQueryObserver, QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import {
  codeReviewDetailOptions, completionEvidenceOptions, humanGateHistoryOptions,
  mergeReviewHistory, reviewHistoryOptions, workEventsOptions, workFollowUpOptions, workPanelKeys
} from "../lib/work-panel-queries.ts";

const project = "10000000-0000-4000-8000-000000000001";
const work = "20000000-0000-4000-8000-000000000001";
const episode = (id) => ({
  completion_event_id: String(id), completion_checkpoint: { id: `30000000-0000-4000-8000-${String(id).padStart(12, "0")}` },
  verification_results: [], artifact_references: []
});
function evidencePage(ids, cursor, head = "3", total = 3) {
  return {
    work_item_id: work, work_version: "1", lifecycle_status: "done", is_duplicate: false,
    canonical_work_item_id: work, current_completion_checkpoint_id: null,
    as_of_completion_event_id: head, total, structured_completion_total: 0,
    items: ids.map(episode), next_cursor: cursor
  };
}

test("panel keys retain exact work, filters, cursors, and review episode identities", () => {
  const options = [
    workEventsOptions(project, work, "", 0), completionEvidenceOptions(project, work),
    humanGateHistoryOptions(project, work, null), reviewHistoryOptions(project, work, "reviews"),
    codeReviewDetailOptions(project, work, "review-a"), workFollowUpOptions(project, work, "question-a")
  ];
  for (const value of options) assert.deepEqual(value.queryKey.slice(0, 5), workPanelKeys.detail(project, work));
  assert.notDeepEqual(workEventsOptions(project, work, "", 0).queryKey, workEventsOptions(project, work, "progress", 0).queryKey);
  assert.notDeepEqual(workEventsOptions(project, work, "", 0).queryKey, workEventsOptions(project, work, "", 20).queryKey);
  assert.notDeepEqual(humanGateHistoryOptions(project, work, null).queryKey, humanGateHistoryOptions(project, work, "cursor").queryKey);
  assert.notDeepEqual(reviewHistoryOptions(project, work, "reviews").queryKey, reviewHistoryOptions(project, work, "questions").queryKey);
  assert.notDeepEqual(codeReviewDetailOptions(project, work, "review-a").queryKey, codeReviewDetailOptions(project, work, "review-b").queryKey);
  assert.notDeepEqual(workFollowUpOptions(project, work, "question-a").queryKey, workFollowUpOptions(project, work, "question-b").queryKey);
  assert.notDeepEqual(workPanelKeys.detail(project, work), workPanelKeys.detail("other-project", work));
  assert.notDeepEqual(workPanelKeys.detail(project, work), workPanelKeys.detail(project, "other-work"));
});

test("completion evidence retries the exact cursor while rejecting mixed revisions before publication", async () => {
  const client = createDashboardQueryClient();
  let invalid = true;
  const cursors = [];
  const options = completionEvidenceOptions(project, work, async (cursor) => {
    cursors.push(cursor);
    return cursor === null ? evidencePage([3, 2], "opaque-cursor") : {
      ...evidencePage([1], null), work_version: invalid ? "2" : "1"
    };
  });
  const observer = new InfiniteQueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  try {
    await observer.refetch();
    const retained = observer.getCurrentResult().data;
    await observer.fetchNextPage();
    assert.equal(observer.getCurrentResult().isFetchNextPageError, true);
    assert.equal(observer.getCurrentResult().data, retained);
    assert.match(observer.getCurrentResult().error.message, /changed while older history/);
    invalid = false;
    await observer.fetchNextPage();
    assert.deepEqual(cursors, [null, "opaque-cursor", "opaque-cursor"]);
    assert.deepEqual(observer.getCurrentResult().data.pages.flatMap((page) => page.items.map((row) => row.completion_event_id)), ["3", "2", "1"]);
    assert.equal(observer.getCurrentResult().hasNextPage, false);
  } finally { stop(); client.clear(); }
});

test("completion evidence refresh rebuilds every cursor from the fresh head", async () => {
  const client = createDashboardQueryClient();
  let newer = false;
  const cursors = [];
  const options = completionEvidenceOptions(project, work, async (cursor) => {
    cursors.push(cursor);
    if (newer) return cursor === null ? evidencePage([4, 3], "new-cursor", "4", 4) : evidencePage([2, 1], null, "4", 4);
    return cursor === null ? evidencePage([3, 2], "old-cursor") : evidencePage([1], null);
  });
  const observer = new InfiniteQueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  try {
    await observer.refetch();
    await observer.fetchNextPage();
    newer = true;
    await client.invalidateQueries({ queryKey: workPanelKeys.detail(project, work) });
    assert.deepEqual(cursors, [null, "old-cursor", null, "new-cursor"]);
    assert.deepEqual(observer.getCurrentResult().data.pages.flatMap((page) => page.items.map((row) => row.completion_event_id)), ["4", "3", "2", "1"]);
  } finally { stop(); client.clear(); }
});

test("invalidation cancels an older evidence append and keeps the visible snapshot on read failure", async () => {
  const client = createDashboardQueryClient();
  let failure = false;
  let release;
  let appendSignal;
  const options = completionEvidenceOptions(project, work, async (cursor, signal) => {
    if (failure) throw new Error("temporarily unavailable");
    if (cursor) {
      appendSignal = signal;
      await new Promise((resolve) => { release = resolve; });
      return evidencePage([1], null);
    }
    return evidencePage([3, 2], "next");
  });
  const observer = new InfiniteQueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  try {
    await observer.refetch();
    const retained = observer.getCurrentResult().data;
    const append = observer.fetchNextPage();
    failure = true;
    await client.invalidateQueries({ queryKey: workPanelKeys.detail(project, work) });
    assert.equal(appendSignal.aborted, true);
    assert.equal(observer.getCurrentResult().data, retained);
    assert.equal(observer.getCurrentResult().isRefetchError, true);
    release();
    await append;
    assert.equal(observer.getCurrentResult().data, retained);
  } finally { release?.(); stop(); client.clear(); }
});

test("review history retains first rows without losing the last server continuation", () => {
  const merged = mergeReviewHistory([
    { project_id: project, items: [{ id: "one", state: "requested" }], has_more: true, next_cursor: "first" },
    { project_id: project, items: [{ id: "one", state: "completed" }, { id: "two" }], has_more: true, next_cursor: "second" }
  ]);
  assert.equal(merged.items.length, 2);
  assert.equal(merged.items[0].state, "requested");
  assert.equal(merged.next_cursor, "second");
  assert.equal(merged.has_more, true);
});

test("switching review episodes aborts the old read and does not replace the selected detail", async () => {
  const client = createDashboardQueryClient();
  let release;
  let signal;
  const oldOptions = { ...codeReviewDetailOptions(project, work, "old"), queryFn: async (request) => {
    signal = request.signal;
    await new Promise((resolve) => { release = resolve; });
    return { review: { id: "old" } };
  } };
  const observer = new QueryObserver(client, oldOptions);
  const stop = observer.subscribe(() => {});
  try {
    observer.setOptions({ ...codeReviewDetailOptions(project, work, "new"), queryFn: async () => ({ review: { id: "new" } }) });
    assert.equal(signal.aborted, true);
    release();
    await observer.refetch();
    assert.equal(observer.getCurrentResult().data.review.id, "new");
    assert.equal(client.getQueryData(oldOptions.queryKey), undefined);
  } finally { release?.(); stop(); client.clear(); }
});

test("hiding gate history detaches its reader and cancels the obsolete request", async () => {
  const client = createDashboardQueryClient();
  const held = Promise.withResolvers();
  let signal;
  const openOptions = { ...humanGateHistoryOptions(project, work, null), queryFn: async (request) => {
    signal = request.signal;
    return held.promise;
  } };
  const observer = new QueryObserver(client, openOptions);
  const stop = observer.subscribe(() => {});
  try {
    observer.setOptions(humanGateHistoryOptions(project, work, null, false));
    assert.equal(signal.aborted, true);
    assert.equal(observer.getCurrentResult().isFetching, false);
    assert.equal(observer.getCurrentResult().data, undefined);
    held.resolve({ items: ["obsolete"] });
    observer.setOptions({ ...openOptions, queryFn: async () => ({ items: ["fresh"] }) });
    await observer.refetch();
    assert.deepEqual(observer.getCurrentResult().data, { items: ["fresh"] });
  } finally { held.resolve({ items: [] }); stop(); client.clear(); }
});
