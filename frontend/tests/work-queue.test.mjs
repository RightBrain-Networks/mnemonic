import assert from "node:assert/strict";
import test from "node:test";
import {
  WORK_PAGE_SIZE,
  cycleStatusFilter,
  listScrollTopFor,
  mergeWorkPages,
  nextQueueSelection,
  resultCountLabel,
  sortDescription,
  statusFilterLabels,
  statusFilterOrder,
  statusFilterTransition
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

test("the filter row offers every labelled lifecycle filter exactly once", () => {
  assert.deepEqual(statusFilterOrder, [
    "pending", "active", "to-review", "dropped", "deferred", "done", "wont-do", "promoted", "all"
  ]);
  assert.deepEqual([...statusFilterOrder].sort(), Object.keys(statusFilterLabels).sort());
  assert.equal(new Set(statusFilterOrder).size, statusFilterOrder.length);
});

test("the horizontal arrows walk the filter row in its rendered order", () => {
  for (const [index, filter] of statusFilterOrder.entries()) {
    const forward = statusFilterOrder[(index + 1) % statusFilterOrder.length];
    const back = statusFilterOrder[(index + statusFilterOrder.length - 1) % statusFilterOrder.length];
    assert.equal(cycleStatusFilter(filter, "next"), forward);
    assert.equal(cycleStatusFilter(filter, "previous"), back);
  }
});

test("the filter row is a ring, so neither end dead-ends", () => {
  assert.equal(cycleStatusFilter("all", "next"), "pending");
  assert.equal(cycleStatusFilter("pending", "previous"), "all");
});

test("a step off an unknown filter lands on the first one", () => {
  assert.equal(cycleStatusFilter("open", "next"), "pending");
  assert.equal(cycleStatusFilter("open", "previous"), "pending");
});

test("changing the lifecycle filter drops an open selection", () => {
  const filters = statusFilterOrder;
  for (const current of filters) {
    for (const next of filters) {
      if (current === next) continue;
      assert.equal(
        statusFilterTransition(current, next, "work-1"),
        "refilter-and-deselect",
        `${current} to ${next} must not strand the open pane`
      );
    }
  }
});

test("reselecting the current lifecycle filter changes nothing", () => {
  assert.equal(statusFilterTransition("pending", "pending", "work-1"), "unchanged");
  assert.equal(statusFilterTransition("deferred", "deferred", "work-1"), "unchanged");
  assert.equal(statusFilterTransition("all", "all", null), "unchanged");
});

test("with no work open a lifecycle change only refilters the queue", () => {
  assert.equal(statusFilterTransition("pending", "deferred", null), "refilter");
  assert.equal(statusFilterTransition("all", "wont-do", null), "refilter");
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

test("resultCountLabel reports progress, flat-search records, and hierarchy roots", () => {
  assert.equal(resultCountLabel({ loading: true, pendingQuery: false, flatSearch: false, total: 3 }), "Finding work…");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: true, flatSearch: true, total: 3 }), "Finding work…");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: false, total: null }), "");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: true, total: 1 }), "1 work record");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: true, total: 3 }), "3 work records");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: true, total: 3, ranked: true }), "3 ranked candidates");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: false, total: 1 }), "1 work item");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: false, total: 6 }), "6 work items");
  assert.equal(resultCountLabel({ loading: false, pendingQuery: false, flatSearch: false, total: 0 }), "0 work items");
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
