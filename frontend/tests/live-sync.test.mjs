import assert from "node:assert/strict";
import test from "node:test";
import {
  connectLiveSync,
  liveSyncUrl,
  parseLiveSyncMessage
} from "../lib/live-sync.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";

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

test("live sync accepts only the documented data-free message contract", () => {
  assert.deepEqual(
    parseLiveSyncMessage(JSON.stringify({ type: "ready", revision: 7 })),
    { type: "ready", revision: 7 }
  );
  assert.deepEqual(
    parseLiveSyncMessage(JSON.stringify({
      type: "invalidate",
      revision: 8,
      scope: "work-items",
      project_id: project,
      work_item_id: work
    })),
    {
      type: "invalidate",
      revision: 8,
      scope: "work-items",
      project_id: project,
      work_item_id: work
    }
  );
  for (const invalid of [
    "not json",
    JSON.stringify([]),
    JSON.stringify({ type: "ready", revision: -1 }),
    JSON.stringify({ type: "invalidate", revision: 1, scope: "work-items", project_id: null, work_item_id: work }),
    JSON.stringify({ type: "invalidate", revision: 1, scope: "projects", project_id: project, work_item_id: work }),
    JSON.stringify({ type: "invalidate", revision: 1, scope: "projects", project_id: "not-a-uuid", work_item_id: null })
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
