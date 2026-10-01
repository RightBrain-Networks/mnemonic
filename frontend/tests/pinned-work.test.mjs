import assert from "node:assert/strict";
import test from "node:test";
import { decodePinnedWork, pinnedWorkKey } from "../lib/pinned-work.ts";
import { workSearchParams, childSearchParams } from "../lib/work-item-search.ts";
import { workSearchRequest } from "../lib/unified-search.ts";
import { validSearchRequest } from "../lib/search-request.ts";
import { allowedQueryKeys, invalidMutationBody, validQueryValues } from "../lib/proxy-policy.ts";

test("pin preferences isolate projects and reject malformed storage", () => {
  const id = "ABCDEF01-1234-5678-9012-123456789012";
  assert.deepEqual(decodePinnedWork(JSON.stringify([id, id.toLowerCase(), null, 4, "bad"])), [id.toLowerCase()]);
  for (const raw of [null, "null", "{}", "broken"]) assert.deepEqual(decodePinnedWork(raw), []);
  assert.notEqual(pinnedWorkKey("one"), pinnedWorkKey("two"));
});

test("summary review proxy requires an explicit human request and scoped cold mode", () => {
  const id = "abcdef01-1234-5678-9012-123456789012";
  const path = `projects/${id}/work-items/${id}`;
  const body = { expected_version: 2, request_code_review: true, request_code_review_mode: "warm",
    request_code_review_checkpoint_id: id, client_operation_id: id,
    actor: { actor_client: "dashboard", actor_session_id: "human-review" } };
  assert.equal(invalidMutationBody(path, "PATCH", body), null);
  assert.ok(invalidMutationBody(path, "PATCH", { ...body, request_code_review_mode: "cold" }));
  const handoff = { scope: { repositories: [{ repository_key: "main", checkout_path: "/repo", object_format: "sha1", base_commit: "a".repeat(40), head_commit: "b".repeat(40) }] },
    handoff: { change_summary: "Completed work", decisions: [], focus_areas: [], traps: [], validation_summary: "No additional validation supplied." } };
  assert.equal(invalidMutationBody(path, "PATCH", { ...body, request_code_review_mode: "cold", code_review_handoff: handoff }), null);
  assert.ok(invalidMutationBody(path, "PATCH", { ...body, request_code_review: false }));
  assert.ok(invalidMutationBody(path, "PATCH", { ...body, actor: { actor_client: "codex", actor_session_id: "agent" } }));
  assert.ok(invalidMutationBody(path, "PATCH", { ...body, request_code_review_checkpoint_id: null }));
});

test("pins travel with hierarchy, child and search requests before pagination", () => {
  const ids = ["abcdef01-1234-5678-9012-123456789012"];
  const options = { status: "pending", sort: "updated", limit: 20, offset: 20, query: "", pinnedWorkItemIds: ids };
  assert.deepEqual(workSearchParams(options).getAll("pinned_work_item_ids"), ids);
  assert.deepEqual(childSearchParams(options).getAll("pinned_work_item_ids"), ids);
  assert.deepEqual(workSearchRequest({ ...options, query: "pin search" }).pinned_work_item_ids, ids);
  assert.ok(validSearchRequest(workSearchRequest({ ...options, query: "pin search" })));
  assert.ok(allowedQueryKeys(`projects/${ids[0]}/work-items`, "GET").includes("pinned_work_item_ids"));
  assert.ok(validQueryValues("pinned_work_item_ids", [ids[0], ids[0]]));
  assert.equal(validQueryValues("pinned_work_item_ids", ["bad"]), false);
  assert.equal(validQueryValues("status", ["pending", "done"]), false);
});
