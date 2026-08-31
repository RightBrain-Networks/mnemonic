import assert from "node:assert/strict";
import test from "node:test";
import { earliestLeaseExpiry, scheduleLeaseExpiryRefresh } from "../lib/lease-refresh.ts";

class FakeTimers {
  nowMs;
  nextId = 1;
  tasks = new Map();

  constructor(nowMs) {
    this.nowMs = nowMs;
  }

  clock = {
    now: () => this.nowMs,
    setTimeout: (callback, delayMs) => {
      const id = this.nextId++;
      this.tasks.set(id, { callback, at: this.nowMs + delayMs });
      return id;
    },
    clearTimeout: (id) => this.tasks.delete(id)
  };

  advanceBy(milliseconds) {
    const target = this.nowMs + milliseconds;
    while (true) {
      const next = [...this.tasks.entries()]
        .filter(([, task]) => task.at <= target)
        .sort((left, right) => left[1].at - right[1].at || left[0] - right[0])[0];
      if (!next) break;
      const [id, task] = next;
      this.tasks.delete(id);
      this.nowMs = task.at;
      task.callback();
    }
    this.nowMs = target;
  }
}

test("the earliest valid displayed lease expiry is selected", () => {
  assert.equal(earliestLeaseExpiry([
    null,
    "not-a-date",
    "2026-08-31T18:15:00Z",
    "2026-08-31T18:05:00Z"
  ]), "2026-08-31T18:05:00Z");
  assert.equal(earliestLeaseExpiry([undefined, "invalid"]), null);
});

test("expiry refresh fires at the displayed boundary under fake timers", () => {
  const timers = new FakeTimers(Date.parse("2026-08-31T18:00:00Z"));
  let refreshes = 0;
  scheduleLeaseExpiryRefresh(
    "2026-08-31T18:01:00Z",
    () => { refreshes += 1; },
    timers.clock
  );

  timers.advanceBy(59_999);
  assert.equal(refreshes, 0);
  timers.advanceBy(1);
  assert.equal(refreshes, 1);
});

test("an unchanged already-due expiry retries at a bounded interval", () => {
  const timers = new FakeTimers(Date.parse("2026-08-31T18:00:00Z"));
  let refreshes = 0;
  scheduleLeaseExpiryRefresh(
    "2026-08-31T18:01:00Z",
    () => { refreshes += 1; },
    timers.clock
  );

  timers.advanceBy(60_000);
  assert.equal(refreshes, 1);
  timers.advanceBy(64_999);
  assert.equal(refreshes, 1);
  timers.advanceBy(1);
  assert.equal(refreshes, 2);
  timers.advanceBy(130_000);
  assert.equal(refreshes, 4);
});

test("cancelling after the first expiry refresh stops scheduled retries", () => {
  const timers = new FakeTimers(Date.parse("2026-08-31T18:00:00Z"));
  let refreshes = 0;
  let cancel = () => undefined;
  cancel = scheduleLeaseExpiryRefresh(
    "2026-08-31T18:01:00Z",
    () => {
      refreshes += 1;
      cancel();
    },
    timers.clock
  );

  timers.advanceBy(60_000);
  assert.equal(refreshes, 1);
  timers.advanceBy(195_000);
  assert.equal(refreshes, 1);
});

test("cancelled expiry refreshes do not fire", () => {
  const timers = new FakeTimers(Date.parse("2026-08-31T18:00:00Z"));
  let refreshes = 0;
  const cancel = scheduleLeaseExpiryRefresh(
    "2026-08-31T18:01:00Z",
    () => { refreshes += 1; },
    timers.clock
  );
  cancel();
  timers.advanceBy(60_000);
  assert.equal(refreshes, 0);
});
