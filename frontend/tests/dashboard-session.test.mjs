import assert from "node:assert/strict";
import test from "node:test";
import { sessionIdReader } from "../lib/dashboard-session.ts";

test("dashboard sessions persist once per browser tab", () => {
  const values = new Map();
  let generated = 0;
  const read = sessionIdReader({
    storage: () => ({
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value)
    }),
    uuid: () => `session-${++generated}`
  });
  assert.equal(read(), "session-1");
  assert.equal(read(), "session-1");
  assert.equal(generated, 1);
});

test("blocked or unavailable session storage still uses one stable fallback", () => {
  let generated = 0;
  const unavailable = sessionIdReader({
    storage: () => null,
    uuid: () => `fallback-${++generated}`
  });
  assert.equal(unavailable(), "dashboard-fallback-1");
  assert.equal(unavailable(), "dashboard-fallback-1");
  assert.equal(generated, 1);

  const denied = sessionIdReader({
    storage: () => { throw new Error("denied"); },
    uuid: () => `denied-${++generated}`
  });
  assert.equal(denied(), "dashboard-denied-2");
  assert.equal(denied(), "dashboard-denied-2");
});

test("a fallback identity remains pinned if storage later recovers", () => {
  let available = false;
  const read = sessionIdReader({
    storage: () => {
      if (!available) throw new Error("denied");
      return {
        getItem: () => "different-session",
        setItem: () => {}
      };
    },
    uuid: () => "pinned"
  });
  assert.equal(read(), "dashboard-pinned");
  available = true;
  assert.equal(read(), "dashboard-pinned");
});

test("a stored identity remains pinned if storage later becomes unavailable", () => {
  let available = true;
  let generated = 0;
  const read = sessionIdReader({
    storage: () => {
      if (!available) throw new Error("denied");
      return {
        getItem: () => "stored-session",
        setItem: () => {}
      };
    },
    uuid: () => `unexpected-${++generated}`
  });
  assert.equal(read(), "stored-session");
  available = false;
  assert.equal(read(), "stored-session");
  assert.equal(generated, 0);
});
