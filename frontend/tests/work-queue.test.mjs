import assert from "node:assert/strict";
import test from "node:test";
import {
  WORK_PAGE_SIZE,
  appendWorkPage,
  hasMoreWork,
  listScrollTopFor,
  loadedOffsets,
  mergeWorkPages,
  moreFiltersForced,
  nextQueueSelection,
  resultCountLabel,
  sortDescription
} from "../lib/work-queue.ts";

function entry(id) {
  return { summary: { work_item: { id } } };
}

function page(ids, total, offset = 0, limit = WORK_PAGE_SIZE) {
  return { items: ids.map(entry), total, limit, offset };
}

test("the queue page size stays on the wire contract", () => {
  assert.equal(WORK_PAGE_SIZE, 20);
});

test("loadedOffsets covers every page that has been loaded and always includes the first", () => {
  assert.deepEqual(loadedOffsets(0, 20), [0]);
  assert.deepEqual(loadedOffsets(1, 20), [0]);
  assert.deepEqual(loadedOffsets(20, 20), [0]);
  assert.deepEqual(loadedOffsets(21, 20), [0, 20]);
  assert.deepEqual(loadedOffsets(45, 20), [0, 20, 40]);
  assert.deepEqual(loadedOffsets(60, 20), [0, 20, 40]);
  assert.deepEqual(loadedOffsets(-5, 20), [0]);
  assert.deepEqual(loadedOffsets(40, 0), [0]);
});

test("mergeWorkPages flattens pages in order and takes the total from the first page", () => {
  const merged = mergeWorkPages([
    page(["a", "b"], 5, 0, 2),
    page(["c", "d"], 6, 2, 2),
    page(["e"], 5, 4, 2)
  ]);
  assert.deepEqual(merged.items.map((item) => item.summary.work_item.id), ["a", "b", "c", "d", "e"]);
  assert.equal(merged.loaded, 5);
  assert.equal(merged.total, 5);
});

test("mergeWorkPages drops trailing empty pages but keeps the first page's total", () => {
  const merged = mergeWorkPages([
    page(["a", "b"], 2, 0, 2),
    page([], 2, 2, 2),
    page([], 2, 4, 2)
  ]);
  assert.deepEqual(merged.items.map((item) => item.summary.work_item.id), ["a", "b"]);
  assert.equal(merged.loaded, 2);
  assert.equal(merged.total, 2);

  const empty = mergeWorkPages([page([], 0)]);
  assert.deepEqual(empty, { items: [], loaded: 0, total: 0 });
  assert.deepEqual(mergeWorkPages([]), { items: [], loaded: 0, total: 0 });
});

test("mergeWorkPages keeps one entry per work item when a record straddles a page boundary", () => {
  const merged = mergeWorkPages([
    page(["a", "b"], 4, 0, 2),
    page(["B", "c"], 4, 2, 2)
  ]);
  assert.deepEqual(merged.items.map((item) => item.summary.work_item.id), ["a", "b", "c"]);
  assert.equal(merged.loaded, 4, "the raw loaded count still advances the next offset");
  assert.equal(merged.total, 4);
});

test("appendWorkPage extends the accumulated items and adopts the freshest total", () => {
  const first = mergeWorkPages([page(["a", "b"], 3, 0, 2)]);
  const appended = appendWorkPage(first, page(["c"], 3, 2, 2));
  assert.deepEqual(appended.items.map((item) => item.summary.work_item.id), ["a", "b", "c"]);
  assert.equal(appended.loaded, 3);
  assert.equal(appended.total, 3);

  const shrunk = appendWorkPage(first, page([], 2, 2, 2));
  assert.equal(shrunk.loaded, 2);
  assert.equal(shrunk.total, 2);
  assert.equal(hasMoreWork(shrunk.loaded, shrunk.total), false);

  const overlapping = appendWorkPage(first, page(["b", "c"], 4, 2, 2));
  assert.deepEqual(overlapping.items.map((item) => item.summary.work_item.id), ["a", "b", "c"]);
  assert.equal(overlapping.loaded, 4);
});

test("hasMoreWork compares the loaded count with a known total", () => {
  assert.equal(hasMoreWork(0, null), false);
  assert.equal(hasMoreWork(0, 0), false);
  assert.equal(hasMoreWork(20, 45), true);
  assert.equal(hasMoreWork(45, 45), false);
  assert.equal(hasMoreWork(46, 45), false);
});

test("moreFiltersForced opens the panel only when a hidden filter is active", () => {
  const idle = {
    duplicateScope: "canonical",
    canonicalWorkItemId: "",
    tag: "",
    sourceClient: "",
    sourceSessionId: ""
  };
  assert.equal(moreFiltersForced(idle), false);
  assert.equal(moreFiltersForced({ ...idle, tag: "   " }), false);
  assert.equal(moreFiltersForced({ ...idle, duplicateScope: "aliases" }), true);
  assert.equal(moreFiltersForced({ ...idle, duplicateScope: "all" }), true);
  assert.equal(moreFiltersForced({ ...idle, canonicalWorkItemId: "5e0b4b2a-7f6c-4c9a-9a1e-2d3f4a5b6c7d" }), true);
  assert.equal(moreFiltersForced({ ...idle, tag: "bug" }), true);
  assert.equal(moreFiltersForced({ ...idle, sourceClient: "claude-code" }), true);
  assert.equal(moreFiltersForced({ ...idle, sourceSessionId: "session-1" }), true);
});

test("resultCountLabel reports progress, flat-search records, and hierarchy roots", () => {
  assert.equal(resultCountLabel({ loading: true, pendingQuery: false, flatSearch: false, total: 3 }), "Finding work…");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: true, flatSearch: true, total: 3 }), "Finding work…");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: false, total: null }), "");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: true, total: 1 }), "1 work record");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: true, total: 3 }), "3 work records");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: false, total: 1 }), "1 root branch");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: false, total: 6 }), "6 root branches");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: false, total: 0 }), "0 root branches");
});

test("sortDescription names every sort order", () => {
  assert.equal(sortDescription("updated"), "Sorted by last activity");
  assert.equal(sortDescription("created"), "Sorted by creation");
  assert.equal(sortDescription("priority"), "Sorted by priority");
});

test("nextQueueSelection walks the visible options and clamps at both ends", () => {
  const ids = ["a", "b", "c"];
  assert.equal(nextQueueSelection([], null, "down"), null);
  assert.equal(nextQueueSelection([], "a", "up"), null);
  assert.equal(nextQueueSelection(ids, null, "down"), "a");
  assert.equal(nextQueueSelection(ids, null, "up"), "a");
  assert.equal(nextQueueSelection(ids, "missing", "down"), "a");
  assert.equal(nextQueueSelection(ids, "a", "down"), "b");
  assert.equal(nextQueueSelection(ids, "b", "down"), "c");
  assert.equal(nextQueueSelection(ids, "c", "down"), "c");
  assert.equal(nextQueueSelection(ids, "c", "up"), "b");
  assert.equal(nextQueueSelection(ids, "b", "up"), "a");
  assert.equal(nextQueueSelection(ids, "a", "up"), "a");
});

test("listScrollTopFor leaves an already visible option where it is", () => {
  assert.equal(listScrollTopFor({
    listTop: 100,
    listHeight: 400,
    scrollTop: 250,
    optionTop: 200,
    optionHeight: 120
  }), 250);
  assert.equal(listScrollTopFor({
    listTop: 100,
    listHeight: 400,
    scrollTop: 0,
    optionTop: 108,
    optionHeight: 120
  }), 0);
});

test("listScrollTopFor scrolls up to reveal an option above the viewport", () => {
  // The option starts 60px above the list's top edge while scrolled 300px.
  assert.equal(listScrollTopFor({
    listTop: 100,
    listHeight: 400,
    scrollTop: 300,
    optionTop: 40,
    optionHeight: 120
  }), 232);
  assert.equal(listScrollTopFor({
    listTop: 100,
    listHeight: 400,
    scrollTop: 300,
    optionTop: 40,
    optionHeight: 120,
    padding: 0
  }), 240);
  assert.equal(listScrollTopFor({
    listTop: 100,
    listHeight: 400,
    scrollTop: 4,
    optionTop: 98,
    optionHeight: 120
  }), 0, "never scrolls above the top of the list");
});

test("listScrollTopFor scrolls down to reveal an option below the viewport", () => {
  // The option's bottom edge sits 80px past the list's bottom edge.
  assert.equal(listScrollTopFor({
    listTop: 100,
    listHeight: 400,
    scrollTop: 0,
    optionTop: 460,
    optionHeight: 120
  }), 88);
  assert.equal(listScrollTopFor({
    listTop: 100,
    listHeight: 400,
    scrollTop: 0,
    optionTop: 460,
    optionHeight: 120,
    padding: 0
  }), 80);
  assert.equal(listScrollTopFor({
    listTop: 100,
    listHeight: 400,
    scrollTop: 300,
    optionTop: 460,
    optionHeight: 120
  }), 388, "scrolls relative to the current position");
  assert.equal(listScrollTopFor({
    listTop: 100,
    listHeight: 200,
    scrollTop: 0,
    optionTop: 300,
    optionHeight: 400
  }), 192, "an option taller than the list aligns its top edge instead of its bottom");
});
