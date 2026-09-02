import assert from "node:assert/strict";
import test from "node:test";
import {
  EASE_IN_OUT_QUINT,
  WORK_ITEM_FADE_DURATION_MS,
  WORK_ITEM_SLIDE_DURATION_MS,
  easeOutBounce,
  planWorkItemMotion,
  workItemSlideKeyframes
} from "../lib/work-item-motion.ts";

test("work-item motion exposes the intended timings and quint fade easing", () => {
  assert.equal(WORK_ITEM_SLIDE_DURATION_MS, 700);
  assert.equal(WORK_ITEM_FADE_DURATION_MS, 1000);
  assert.equal(EASE_IN_OUT_QUINT, "cubic-bezier(0.83, 0, 0.17, 1)");
});

test("easeOutBounce follows the canonical piecewise bounce curve", () => {
  const d1 = 2.75;
  assert.equal(easeOutBounce(0), 0);
  assert.equal(easeOutBounce(1), 1);
  assert.equal(easeOutBounce(1.5 / d1), 0.75);
  assert.equal(easeOutBounce(2.25 / d1), 0.9375);
  assert.equal(easeOutBounce(2.625 / d1), 0.984375);
  assert.equal(easeOutBounce(-0.1), 0);
  assert.equal(easeOutBounce(1.1), 1);
});

test("slide keyframes densely sample bounce progress with linear offsets and exact endpoints", () => {
  const deltaY = -120;
  const keyframes = workItemSlideKeyframes(deltaY);

  assert.ok(keyframes.length >= 60);
  assert.deepEqual(keyframes[0], { transform: "translateY(-120px)", offset: 0 });
  assert.deepEqual(keyframes.at(-1), { transform: "translateY(0px)", offset: 1 });

  for (let index = 0; index < keyframes.length; index += 1) {
    const offset = index / (keyframes.length - 1);
    assert.equal(keyframes[index].offset, offset);
    const translateY = Number.parseFloat(keyframes[index].transform.slice("translateY(".length));
    assert.ok(Math.abs(translateY - deltaY * (1 - easeOutBounce(offset))) < 1e-10);
  }

  const firstLanding = Math.round((keyframes.length - 1) / 2.75);
  const rebound = Number.parseFloat(keyframes[firstLanding + 3].transform.slice("translateY(".length));
  assert.ok(rebound < 0, "the card should rebound upward before settling at its final position");
});

test("insertion planning detects additions while preserving retained order", () => {
  assert.deepEqual(
    planWorkItemMotion(["old-a", "old-b"], 2, ["new-a", "old-a", "new-b", "old-b"], 4),
    { addedIds: ["new-a", "new-b"], removedIds: [], retainedIds: ["old-a", "old-b"] }
  );
});

test("insertion planning fades the first item added to a loaded empty view", () => {
  assert.deepEqual(
    planWorkItemMotion([], 0, ["new"], 1),
    { addedIds: ["new"], removedIds: [], retainedIds: [] }
  );
});

test("motion planning fades an item evicted at a page boundary", () => {
  assert.deepEqual(
    planWorkItemMotion(["old-a", "old-b", "old-c"], 8, ["new", "old-a", "old-b"], 9),
    { addedIds: ["new"], removedIds: ["old-c"], retainedIds: ["old-a", "old-b"] }
  );
});

test("motion planning detects removals while preserving retained order", () => {
  assert.deepEqual(
    planWorkItemMotion(["old-a", "old-b"], 2, ["old-b"], 1),
    { addedIds: [], removedIds: ["old-a"], retainedIds: ["old-b"] }
  );
});

test("motion planning skips initial loads and unsafe list changes", () => {
  assert.equal(planWorkItemMotion(["old"], 1, ["new", "old"], 1), null);
  assert.equal(planWorkItemMotion(["old-a", "old-b"], 2, ["old-a", "old-b"], 3), null);
  assert.equal(planWorkItemMotion(["old-a", "old-b"], 2, ["old-b"], 2), null);
  assert.equal(planWorkItemMotion(["old-a", "old-b"], 2, ["old-b", "new", "old-a"], 3), null);
});
