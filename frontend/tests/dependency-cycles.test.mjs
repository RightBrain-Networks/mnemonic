import { decodeMutationError } from "../lib/mutation-responses.ts";
import assert from "node:assert/strict";
import test from "node:test";
import { decodeDependencyIssue } from "../lib/dependency-cycles.ts";
import { decodeReadiness } from "../lib/readiness-codecs.ts";
import { relationshipConflictMessage } from "../lib/work-relationships.ts";
import { readinessAfterWorkSave } from "../lib/work-item-view.ts";

const parent = "00000000-0000-4000-8000-000000000001";
const child = "00000000-0000-4000-8000-000000000002";
const issue = {
  kind: "deadlock", role: "cycle_member", cycle_id: "a".repeat(64), cycle_length: 2,
  cycle: [
    { relationship_id: null, relationship_type: "blocks", source_work_item_id: parent, target_work_item_id: child },
    { relationship_id: null, relationship_type: "parent-child", source_work_item_id: parent, target_work_item_id: child }
  ]
};
const readiness = {
  lifecycle_status: "pending", is_duplicate: false, canonical_work_item_id: parent,
  is_terminal: false, has_active_lease: false, has_dropped_lease: false, active_lease: null,
  unresolved_blocker_count: 0, is_blocked: false, unresolved_gate_count: 0, is_gated: false,
  is_ready: false, display_state: "deadlocked", dependency_issue: issue
};

test("a reversed parent edge closes the validated cycle and prevents ready", () => {
  assert.deepEqual(decodeDependencyIssue(issue), issue);
  assert.equal(decodeReadiness(readiness, "pending", parent).display_state, "deadlocked");
  assert.throws(() => decodeReadiness({ ...readiness, is_ready: true }, "pending", parent));
  assert.throws(() => decodeReadiness({ ...readiness, display_state: "blocked" }, "pending", parent));
  const { dependency_issue, ...missing } = readiness;
  assert.throws(() => decodeReadiness(missing, "pending", parent));
});

test("descriptive planning cycles stay distinct from deadlocks", () => {
  const warning = { ...readiness, is_ready: true, display_state: "pending", dependency_issue: { ...issue, kind: "planning-cycle" } };
  assert.equal(decodeReadiness(warning, "pending", parent).is_ready, true);
  assert.throws(() => decodeDependencyIssue({ ...issue, cycle_length: 3 }));
  assert.throws(() => decodeDependencyIssue({ ...issue, cycle: [issue.cycle[0], issue.cycle[0]] }));
  assert.throws(() => decodeDependencyIssue({ ...issue, cycle: [{ ...issue.cycle[0], title: "untrusted" }, issue.cycle[1]] }));
  const message = `This relationship closes ${parent} --blocks--> ${child}; revise its parent assignment.`;
  assert.equal(relationshipConflictMessage("relationship_cycle", message), message);
});

test("deferring work preserves its deadlock until a terminal closeout", () => {
  const deferred = readinessAfterWorkSave(readiness, "pending", "deferred");
  assert.equal(deferred.display_state, "deadlocked");
  assert.deepEqual(deferred.dependency_issue, issue);
  assert.equal(decodeReadiness(deferred, "deferred", parent).is_ready, false);
  const retired = readinessAfterWorkSave(readiness, "pending", "wont-do");
  assert.equal(retired.display_state, "wont-do");
  assert.equal(retired.dependency_issue, undefined);
});

test("structured cycle refusal is definitive and preserves only validated path evidence", () => {
  const error = { detail: { code: "relationship_cycle", message: "refused", context: { dependency_issue: issue } } };
  const decoded = decodeMutationError(error);
  assert.equal(decoded.category, "application");
  assert.equal(decoded.code, "relationship_cycle");
  assert.match(decoded.message, new RegExp(parent));
  assert.match(decoded.message, /parent-child \(child to parent\)/);
  const broken = structuredClone(error);
  broken.detail.context.dependency_issue.cycle[0].source_work_item_id = "private prose";
  assert.equal(decodeMutationError(broken), null);
});
