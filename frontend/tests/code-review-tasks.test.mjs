import assert from "node:assert/strict";
import test from "node:test";
import { reviewStatusActions, reviewDecisionStatus, retainedReviewPointer } from "../lib/code-review-tasks.ts";

test("review menus return deferred and human-closed episodes to Pending without creating new reviews", () => {
  assert.deepEqual(reviewStatusActions({ status: "pending" }).map((item) => item.value), ["done", "wont-do", "promoted"]);
  assert.ok(!reviewStatusActions({ status: "active" }).some((item) => item.value === "to-review"));
  assert.ok(reviewStatusActions({ status: "deferred" }).some((item) => item.value === "to-review"));
  assert.equal(reviewDecisionStatus("defer"), "deferred");
  assert.equal(reviewDecisionStatus("pending"), "to-review");
  assert.throws(() => reviewDecisionStatus("review"));
});

test("retained review pointers pin their original episode without execution authority", () => {
  const review = { id: "review-one", project_id: "project-one", work_item_id: "parent-one" };
  const pointer = retainedReviewPointer({ review });
  assert.match(pointer, /does not authorize execution or reopening/);
  assert.deepEqual(JSON.parse(pointer.split("\n\n")[1]), { project_id: review.project_id, work_item_id: review.work_item_id, code_review_id: review.id });
});
