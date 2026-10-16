import assert from "node:assert/strict";
import test from "node:test";
import { decodePinnedWork, pinnedWorkKey } from "../lib/pinned-work.ts";
import { workSearchParams, childSearchParams } from "../lib/work-item-search.ts";
import { workSearchRequest } from "../lib/unified-search.ts";
import { validSearchRequest } from "../lib/search-request.ts";

test("pin preferences isolate projects and reject malformed storage", () => {
  const id = "ABCDEF01-1234-5678-9012-123456789012";
  assert.deepEqual(decodePinnedWork(JSON.stringify([id, id.toLowerCase(), null, 4, "bad"])), [id.toLowerCase()]);
  for (const raw of [null, "null", "{}", "broken"]) assert.deepEqual(decodePinnedWork(raw), []);
  assert.notEqual(pinnedWorkKey("one"), pinnedWorkKey("two"));
});

test("pins travel with hierarchy, child and search requests before pagination", () => {
  const ids = ["abcdef01-1234-5678-9012-123456789012"];
  const options = { status: "pending", sort: "updated", limit: 20, offset: 20, query: "", pinnedWorkItemIds: ids };
  assert.deepEqual(workSearchParams(options).getAll("pinned_work_item_ids"), ids);
  assert.deepEqual(childSearchParams(options).getAll("pinned_work_item_ids"), ids);
  assert.deepEqual(workSearchRequest({ ...options, query: "pin search" }).pinned_work_item_ids, ids);
  assert.ok(validSearchRequest(workSearchRequest({ ...options, query: "pin search" })));

});
