import assert from "node:assert/strict";
import test from "node:test";
import { allowedQueryKeys, configuredOrigins, trustedRequest, upstreamTimeoutMs } from "../lib/proxy-policy.ts";

const origins = configuredOrigins();
const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const handoff = "f1cf3691-7d28-4716-94a9-4867b341a685";
const headers = (overrides = {}) => new Headers({ host: "localhost:3000", ...overrides });

test("ordinary same-origin browser reads work without an Origin header", () => {
  assert.equal(trustedRequest(headers(), "GET", origins), true);
  assert.equal(trustedRequest(headers({ "sec-fetch-site": "same-origin" }), "GET", origins), true);
  assert.equal(trustedRequest(headers({ host: "127.0.0.1:3000" }), "GET", origins), true);
});

test("mutations require an explicit matching Origin and Host", () => {
  for (const method of ["POST", "PATCH", "DELETE"]) {
    assert.equal(trustedRequest(headers(), method, origins), false);
    assert.equal(trustedRequest(headers({ origin: "http://localhost:3000" }), method, origins), true);
    assert.equal(trustedRequest(headers({ origin: "http://127.0.0.1:3000" }), method, origins), false);
    assert.equal(trustedRequest(headers({ origin: "https://attacker.example" }), method, origins), false);
    assert.equal(trustedRequest(headers({ origin: "null" }), method, origins), false);
  }
});

test("untrusted hosts and forwarding headers cannot rebind the local API proxy", () => {
  for (const host of ["attacker.example", "localhost:4000", "localhost", "localhost:3000.attacker.example", "localhost:3000,attacker.example"]) {
    assert.equal(trustedRequest(headers({ host, "x-forwarded-host": "localhost:3000", "x-forwarded-proto": "http" }), "GET", origins), false);
  }
  assert.equal(trustedRequest(new Headers(), "GET", origins), false);
});

test("cross-site and same-site fetches are rejected even if other headers match", () => {
  for (const site of ["cross-site", "same-site"]) {
    assert.equal(trustedRequest(headers({ origin: "http://localhost:3000", "sec-fetch-site": site }), "GET", origins), false);
    assert.equal(trustedRequest(headers({ origin: "http://localhost:3000", "sec-fetch-site": site }), "PATCH", origins), false);
  }
});

test("origins must be canonical HTTP origins, not credentials, paths, or wildcard hosts", () => {
  for (const value of ["", "null", "*", "file:///tmp", "https://user:password@example.com", "https://example.com/path", "https://example.com?query=x", "https://example.com#fragment"]) {
    assert.throws(() => configuredOrigins(value));
  }
  const custom = configuredOrigins("https://mnemonic.example, http://localhost:4567/");
  assert.deepEqual([...custom], ["https://mnemonic.example", "http://localhost:4567"]);
  assert.equal(trustedRequest(new Headers({ host: "mnemonic.example", origin: "https://mnemonic.example" }), "POST", custom), true);
  assert.equal(trustedRequest(headers({ origin: "http://localhost:3000/" }), "POST", origins), false);
});

test("the route allowlist exposes only project and hand-off operations", () => {
  assert.deepEqual(allowedQueryKeys("projects", "GET"), ["limit", "offset"]);
  assert.deepEqual(allowedQueryKeys("projects", "POST"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}`, "PATCH"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/handoffs`, "GET"), ["q", "semantic", "status", "tag", "source_client", "source_session_id", "limit", "offset"]);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/handoffs/${handoff}`, "GET"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/handoffs/${handoff}`, "PATCH"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/handoffs/${handoff}`, "DELETE"), ["expected_version"]);
  for (const path of ["healthz", "readyz", "docs", "openapi.json", "projects/../docs", "projects/%2e%2e/docs", "projects/not-a-uuid", `projects/${project}/handoffs/invalid`, "https://attacker.example", "//attacker.example"]) {
    assert.equal(allowedQueryKeys(path, "GET"), null);
  }
  assert.equal(allowedQueryKeys(`projects/${project}`, "DELETE"), null);
  assert.equal(allowedQueryKeys(`projects/${project}/handoffs/${handoff}`, "PUT"), null);
});

test("only nonblank semantic searches receive the warmup timeout", () => {
  assert.equal(upstreamTimeoutMs(new URLSearchParams("q=database&semantic=true")), 60_000);
  for (const query of [
    "q=database",
    "q=database&semantic=false",
    "q=database&semantic=1",
    "semantic=true",
    "q=%20%20&semantic=true"
  ]) {
    assert.equal(upstreamTimeoutMs(new URLSearchParams(query)), 15_000);
  }
});
