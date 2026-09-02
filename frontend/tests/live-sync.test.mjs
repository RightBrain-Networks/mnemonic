import assert from "node:assert/strict";
import test from "node:test";
import {
  connectLiveSync,
  liveSyncUrl,
  parseLiveSyncMessage
} from "../lib/live-sync.ts";

class FakeSocket {
  listeners = new Map();
  closeCalls = [];

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  emit(type, event = {}) {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  close(...args) {
    this.closeCalls.push(args);
    this.emit("close");
  }
}

test("live sync uses the dashboard origin and matching WebSocket security", () => {
  assert.equal(
    liveSyncUrl({ protocol: "http:", host: "localhost:3000" }),
    "ws://localhost:3000/api/mnemonic/sync"
  );
  assert.equal(
    liveSyncUrl({ protocol: "https:", host: "mnemonic.example" }),
    "wss://mnemonic.example/api/mnemonic/sync"
  );
  assert.throws(() => liveSyncUrl({ protocol: "file:", host: "" }));
});

test("live sync accepts only exact identifier-free control messages", () => {
  assert.deepEqual(
    parseLiveSyncMessage(JSON.stringify({ type: "ready", revision: 7 })),
    { type: "ready", revision: 7 }
  );
  assert.deepEqual(
    parseLiveSyncMessage(JSON.stringify({
      type: "invalidate",
      revision: 8,
      scope: "work-items"
    })),
    {
      type: "invalidate",
      revision: 8,
      scope: "work-items"
    }
  );
  for (const invalid of [
    "not json",
    JSON.stringify([]),
    JSON.stringify({ type: "ready", revision: -1 }),
    JSON.stringify({ type: "ready", revision: 1, project_id: "forbidden" }),
    JSON.stringify({ type: "invalidate", revision: 1, scope: "work-items", project_id: "forbidden" }),
    JSON.stringify({ type: "invalidate", revision: 1, scope: "projects", work_item_id: "forbidden" }),
    JSON.stringify({ type: "invalidate", revision: 1, scope: "unknown" })
  ]) {
    assert.equal(parseLiveSyncMessage(invalid), null);
  }
});

test("live sync reconnects and reports connection state", async () => {
  const sockets = [];
  const statuses = [];
  const messages = [];
  const disconnect = connectLiveSync(
    (message) => messages.push(message),
    (status) => statuses.push(status),
    {
      location: { protocol: "http:", host: "localhost:3000" },
      createSocket: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket;
      },
      initialRetryMs: 1,
      maxRetryMs: 1
    }
  );
  assert.deepEqual(statuses, ["connecting"]);
  sockets[0].emit("open");
  assert.deepEqual(statuses, ["connecting"]);
  sockets[0].emit("message", { data: JSON.stringify({ type: "ready", revision: 0 }) });
  assert.deepEqual(statuses, ["connecting", "live"]);
  assert.deepEqual(messages, [{ type: "ready", revision: 0 }]);

  sockets[0].emit("close");
  assert.equal(statuses.at(-1), "retrying");
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(sockets.length, 2);
  sockets[1].emit("open");
  sockets[1].emit("message", { data: JSON.stringify({ type: "ready", revision: 1 }) });
  assert.equal(statuses.at(-1), "live");

  disconnect();
  assert.deepEqual(sockets[1].closeCalls, [[1000, "Dashboard closed"]]);
});
