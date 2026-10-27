import test from "node:test";
import assert from "node:assert/strict";
import { combineQueuePages, queueItemId } from "../lib/combined-work-queue.ts";

const item = (id, date, review = false, priority = 40) => {
  const timestamp = date.includes("T") ? date : `${date}T00:00:00Z`;
  const row = { id, updated_at: timestamp, created_at: timestamp, priority };
  const summary = { work_item: row };
  return review ? { summary, review_task: row } : { summary };
};
const options = { sort: "updated", pins: [], workHasMore: false, reviewsHaveMore: false, semanticSearch: false };
const ids = (items) => items.map(queueItemId);

test("reviews and implementations share ordering without collapsing their identity", () => {
  assert.deepEqual(ids(combineQueuePages([item("work", "2026-03-03")], [item("review", "2026-03-04", true)], options)), ["review", "work"]);
});
test("an incomplete source withholds rows whose global position is not known yet", () => {
  const work = [item("w5", "2026-03-05"), item("w2", "2026-03-02")];
  const reviews = [item("r6", "2026-03-06", true), item("r4", "2026-03-04", true)];
  assert.deepEqual(ids(combineQueuePages(work, reviews, { ...options, reviewsHaveMore: true })), ["r6", "w5", "r4"]);
  assert.deepEqual(ids(combineQueuePages(work, [...reviews, item("r1", "2026-03-01", true)], options)), ["r6", "w5", "r4", "w2", "r1"]);
});
test("priority and pins apply across both kinds while semantic search keeps source ranking", () => {
  const work = [item("w", "2026-03-01", false, 10)];
  const reviews = [item("r", "2026-03-02", true, 90)];
  assert.deepEqual(ids(combineQueuePages(work, reviews, { ...options, sort: "priority" })), ["r", "w"]);
  assert.deepEqual(ids(combineQueuePages(work, reviews, { ...options, sort: "priority", pins: ["w"] })), ["w", "r"]);
  assert.deepEqual(ids(combineQueuePages(work, reviews, { ...options, semanticSearch: true, workHasMore: true })), ["w"]);
});

test("equal creation dates use the same UUID tie-break as the server", () => {
  const a = item("a", "2026-01-01");
  a.summary.work_item.updated_at = "2026-12-01";
  const b = item("b", "2026-01-01", true);
  assert.deepEqual(ids(combineQueuePages([a], [b], { ...options, sort: "created" })), ["b", "a"]);
});

test("timestamp ordering retains whole-second and submillisecond boundaries", () => {
  const work = [item("z", "2026-01-01T00:00:00Z")];
  const reviews = [item("a", "2026-01-01T00:00:00.000002Z", true)];
  assert.deepEqual(ids(combineQueuePages(work, reviews, options)), ["a", "z"]);
  work[0].summary.work_item.updated_at = "2026-01-01T00:00:00.000001Z";
  assert.deepEqual(ids(combineQueuePages(work, reviews, options)), ["a", "z"]);
  assert.deepEqual(ids(combineQueuePages(work, reviews, { ...options, reviewsHaveMore: true })), ["a"]);
});
