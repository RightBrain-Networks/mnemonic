import assert from "node:assert/strict";
import test from "node:test";
import { validSearchPagination, searchPagination } from "../lib/search-pagination.ts";
import { movePageOffset, nextPageOffset } from "../lib/page-offsets.ts";
import { reloadWorkPages } from "../lib/work-queue.ts";

test("short pages require explicit advancing continuation and retain requested limits", () => {
  const page = { items: [1, 2], offset: 4, total: 30, limit: 20, next_offset: 6, page_truncated: true };
  assert.deepEqual(searchPagination(page), { next_offset: 6, page_truncated: true });
  for (const patch of [{ next_offset: 4 }, { next_offset: 24 }, { next_offset: null }, { items: [] }, { page_truncated: false }]) {
    assert.equal(validSearchPagination({ ...page, ...patch }), false);
  }
  assert.equal(validSearchPagination({ ...page, items: [], total: 0, next_offset: null, page_truncated: false }), true);
});

test("search Next and Previous revisit actual uneven pages and scope reset clears history", () => {
  let state = { offset: 0, visited: [] };
  state = movePageOffset(state, nextPageOffset({ items: [1, 2, 3], offset: 0, total: 8, next_offset: 3 }));
  state = movePageOffset(state, nextPageOffset({ items: [4, 5], offset: 3, total: 8, next_offset: 5 }));
  assert.deepEqual(state, { offset: 5, visited: [0, 3] });
  state = movePageOffset(state, state.visited.at(-1));
  assert.deepEqual(state, { offset: 3, visited: [0] });
  state = movePageOffset(state, 0);
  assert.deepEqual(state, { offset: 0, visited: [] });
  // The unchanged artifact directory has count-based pagination, including its last page.
  assert.equal(nextPageOffset({ items: [1, 2], offset: 0, total: 3 }), 2);
  assert.equal(nextPageOffset({ items: [3], offset: 2, total: 3 }), null);
});

test("queue refresh follows changed page boundaries without gaps or zero-progress loops", async () => {
  const offsets = [];
  const pages = await reloadWorkPages(async (offset) => {
    offsets.push(offset);
    const end = Math.min(7, offset + 2);
    return { items: Array.from({ length: end - offset }, (_, index) => offset + index), offset,
      total: 7, limit: 20, next_offset: end < 7 ? end : null, page_truncated: end < 7 };
  }, 7);
  assert.deepEqual(offsets, [0, 2, 4, 6]);
  assert.deepEqual(pages.flatMap((page) => page.items), [0, 1, 2, 3, 4, 5, 6]);
  assert.equal((await reloadWorkPages(async () => ({ items: [], total: 0, limit: 20, offset: 0, next_offset: null }), 7)).length, 1);
});
