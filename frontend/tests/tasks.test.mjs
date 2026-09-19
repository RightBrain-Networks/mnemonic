import assert from "node:assert/strict";
import test from "node:test";
import { decodeTaskPage, taskPath } from "../lib/tasks.ts";
import { hideNemoPreference } from "../lib/application-settings.ts";
import { workSearchParams } from "../lib/work-item-search.ts";
import { workSearchRequest } from "../lib/unified-search.ts";
import { validSearchRequest } from "../lib/search-request.ts";

const project = "11111111-1111-4111-8111-111111111111";
const work = "22222222-2222-4222-8222-222222222222";
const review = "33333333-3333-4333-8333-333333333333";
const task = { id: review, kind: "code_review", project_id: project, work_item_id: work, work_version: 3,
  title: "Review cache changes", summary: "Check concurrent readers.", status: "pending", review_state: "requested",
  updated_at: "2026-09-19T12:00:00Z", lease: null };
const page = { project_id: project, work_items: { active: 0, pending: 5 }, code_reviews: { active: 0, pending: 1 }, next_lease_expires_at: null, items: [task], total: 1, limit: 20, offset: 0 };

test("task links pin project, parent work and the exact review episode", () => {
  assert.equal(taskPath(task), `/code-reviews?project=${project}&work=${work}&review=${review}`);
  assert.equal(taskPath({ ...task, kind: "work_item", id: work }), `/work-items?project=${project}&work=${work}`);
});

test("task pages reject cross-project rows, wrong task kinds and incoherent leases", () => {
  assert.deepEqual(decodeTaskPage(page, project, "pending", "code_review", 0), page);
  for (const change of [{ project_id: work }, { kind: "work_item" }, { status: "active" }, { id: "invalid" }, { work_version: 0 }, { work_version: 1.5 }, { review_state: null }, { review_state: "invalid" }]) {
    assert.throws(() => decodeTaskPage({ ...page, items: [{ ...task, ...change }] }, project, "pending", "code_review", 0));
  }
  assert.throws(() => decodeTaskPage({ ...page, total: 30 }, project, "pending", "code_review", 0));
});

test("Nemo is hidden by default and shown only by an explicit saved preference", () => {
  for (const value of [null, "true", "", "garbage"]) assert.equal(hideNemoPreference(value), true);
  assert.equal(hideNemoPreference("false"), false);
});

test("work item browsing and text search both filter implementation status", () => {
  const options = { status: "done", statusScope: "work_item", sort: "updated", limit: 20, offset: 0, query: "" };
  assert.equal(workSearchParams(options).get("status_scope"), "work_item");
  const request = workSearchRequest({ ...options, query: "cache" });
  assert.equal(request.filters.work_items.status_scope, "work_item");
  assert.equal(validSearchRequest(request), true);
  assert.equal(validSearchRequest({ ...request, filters: { work_items: { status_scope: null } } }), false);
});
