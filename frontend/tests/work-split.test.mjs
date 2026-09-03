import assert from "node:assert/strict";
import test from "node:test";
import {
  WORK_SPLIT_DEFAULT,
  WORK_SPLIT_MAX,
  WORK_SPLIT_MIN,
  WORK_SPLIT_STEP,
  clampWorkSplit,
  stepWorkSplit,
  workSplitFromPointer,
  workSplitPreference
} from "../lib/work-split.ts";

test("the split is bounded to a readable range and rounded to a tenth", () => {
  assert.equal(clampWorkSplit(35), 35);
  assert.equal(clampWorkSplit(35.04), 35);
  assert.equal(clampWorkSplit(35.06), 35.1);
  assert.equal(clampWorkSplit(-40), WORK_SPLIT_MIN);
  assert.equal(clampWorkSplit(95), WORK_SPLIT_MAX);
  assert.equal(clampWorkSplit(Number.NaN), WORK_SPLIT_DEFAULT);
  assert.equal(clampWorkSplit(Number.POSITIVE_INFINITY), WORK_SPLIT_DEFAULT);
});

test("a stored preference restores only when it is a finite number", () => {
  assert.equal(workSplitPreference(null), null);
  assert.equal(workSplitPreference(""), null);
  assert.equal(workSplitPreference("  "), null);
  assert.equal(workSplitPreference("wide"), null);
  assert.equal(workSplitPreference("NaN"), null);
  assert.equal(workSplitPreference("42.5"), 42.5);
  assert.equal(workSplitPreference("5"), WORK_SPLIT_MIN);
  assert.equal(workSplitPreference("99"), WORK_SPLIT_MAX);
});

test("the pointer position maps to the queue's share of the surface", () => {
  assert.equal(workSplitFromPointer({ pointerX: 500, left: 100, width: 1000 }), 40);
  assert.equal(workSplitFromPointer({ pointerX: 100, left: 100, width: 1000 }), WORK_SPLIT_MIN);
  assert.equal(workSplitFromPointer({ pointerX: 1100, left: 100, width: 1000 }), WORK_SPLIT_MAX);
  assert.equal(workSplitFromPointer({ pointerX: 300, left: 100, width: 0 }), WORK_SPLIT_DEFAULT);
});

test("keyboard steps move by a fixed increment and clamp at the ends", () => {
  assert.equal(stepWorkSplit(35, "left"), 35 - WORK_SPLIT_STEP);
  assert.equal(stepWorkSplit(35, "right"), 35 + WORK_SPLIT_STEP);
  assert.equal(stepWorkSplit(WORK_SPLIT_MIN, "left"), WORK_SPLIT_MIN);
  assert.equal(stepWorkSplit(WORK_SPLIT_MAX, "right"), WORK_SPLIT_MAX);
  assert.equal(stepWorkSplit(50, "start"), WORK_SPLIT_MIN);
  assert.equal(stepWorkSplit(50, "end"), WORK_SPLIT_MAX);
});
