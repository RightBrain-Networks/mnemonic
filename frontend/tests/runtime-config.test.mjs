import assert from "node:assert/strict";
import test from "node:test";
import { decodeRuntimeConfig, loadRuntimeConfig } from "../lib/runtime-config.ts";

const valid = {
  timezone: "America/Detroit", work_summary_max_chars: 2048,
  artifact_max_bytes: 67_108_864, backup_max_bytes: 67_108_864
};

test("runtime settings accept the presentation contract and disabled artifacts", () => {
  assert.deepEqual(decodeRuntimeConfig({ ...valid, artifact_max_bytes: 0 }), {
    ...valid, artifact_max_bytes: 0
  });
  assert.deepEqual(decodeRuntimeConfig({ ...valid, backup_max_bytes: Number.MAX_SAFE_INTEGER }), {
    ...valid, backup_max_bytes: Number.MAX_SAFE_INTEGER
  });
});

test("runtime settings reject missing, coerced and unsafe limits instead of using defaults", () => {
  for (const invalid of [null, [], {}, "config"]) assert.throws(() => decodeRuntimeConfig(invalid));
  assert.throws(() => decodeRuntimeConfig({ ...valid, unexpected: "not part of the contract" }));
  for (const key of ["work_summary_max_chars", "artifact_max_bytes", "backup_max_bytes"]) {
    for (const value of [undefined, null, "1024", -1, 1.5, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
      assert.throws(() => decodeRuntimeConfig({ ...valid, [key]: value }), `${key}: ${value}`);
    }
  }
  assert.throws(() => decodeRuntimeConfig({ ...valid, work_summary_max_chars: 0 }));
  assert.throws(() => decodeRuntimeConfig({ ...valid, backup_max_bytes: 0 }));
  assert.throws(() => decodeRuntimeConfig({ ...valid, artifact_max_bytes: 1_073_741_825 }));
  for (const timezone of [null, "", "invalid/timezone"]) {
    assert.throws(() => decodeRuntimeConfig({ ...valid, timezone }));
  }
});

test("runtime config uses the public uncached boundary and refuses failed reads", async (t) => {
  const signal = new AbortController().signal;
  let request;
  t.mock.method(globalThis, "fetch", async (...args) => {
    request = args;
    return Response.json(valid);
  });
  assert.deepEqual(await loadRuntimeConfig(signal), valid);
  assert.equal(request[0], "/api/mnemonic/runtime-config");
  assert.equal(request[1].cache, "no-store");
  assert.equal(request[1].credentials, "same-origin");
  assert.equal(request[1].signal, signal);
  globalThis.fetch.mock.mockImplementation(async () => Response.json(valid, { status: 503 }));
  await assert.rejects(loadRuntimeConfig(), /could not be loaded/);
  globalThis.fetch.mock.mockImplementation(async () => Response.json({}));
  await assert.rejects(loadRuntimeConfig(), /invalid/);
});
