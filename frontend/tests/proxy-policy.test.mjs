import assert from "node:assert/strict";
import test from "node:test";
import {
  allowedQueryKeys,
  classifyRequestBody,
  configuredOrigins,
  forbiddenMutationField,
  invalidMutationBody,
  trustedRequest,
  upstreamTimeoutMs
} from "../lib/proxy-policy.ts";

const origins = configuredOrigins();
const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const other = "f1cf3691-7d28-4716-94a9-4867b341a685";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
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

test("the route allowlist exposes canonical Phase 3 work, hierarchy, and relationship operations and compatibility aliases", () => {
  assert.deepEqual(allowedQueryKeys("projects", "GET"), ["limit", "offset"]);
  assert.deepEqual(allowedQueryKeys("projects", "POST"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}`, "PATCH"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/settings`, "GET"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/settings`, "PATCH"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items`, "GET"), ["q", "semantic", "status", "sort", "tag", "source_client", "source_session_id", "view", "limit", "offset"]);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items`, "POST"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}`, "GET"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}`, "PATCH"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/context`, "GET"), ["recent_limit", "recent_event_limit"]);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/children`, "GET"), ["status", "sort", "tag", "source_client", "source_session_id", "limit", "offset"]);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/relationships`, "GET"), ["direction", "type", "limit", "offset"]);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/relationships`, "POST"), []);
  assert.equal(allowedQueryKeys(`projects/${project}/relationships/${other}`, "GET"), null);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/relationships/${other}`, "DELETE"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/checkpoints`, "GET"), ["order", "limit", "offset"]);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/checkpoints`, "POST"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/events`, "GET"), ["order", "event_type", "limit", "offset"]);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/events`, "POST"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/complete`, "POST"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/delete`, "POST"), []);
  for (const path of ["sync", "healthz", "readyz", "docs", "openapi.json", "projects/../docs", "projects/%2e%2e/docs", "projects/not-a-uuid", `projects/${project}/unknown-collection`, "https://attacker.example", "//attacker.example"]) {
    assert.equal(allowedQueryKeys(path, "GET"), null);
  }
  assert.equal(allowedQueryKeys(`projects/${project}`, "DELETE"), null);
  assert.equal(allowedQueryKeys(`projects/${project}/settings`, "POST"), null);
  assert.equal(allowedQueryKeys(`projects/${project}/settings`, "DELETE"), null);
  assert.equal(allowedQueryKeys(`projects/${project}/work-items/${work}`, "DELETE"), null);
  assert.equal(allowedQueryKeys(`projects/${project}/work-items/${work}/checkpoints`, "PATCH"), null);
  assert.equal(allowedQueryKeys(`projects/${project}/work-items/${work}/context`, "POST"), null);
});

test("project settings expose only the exact recall-pointer patch", () => {
  const path = `projects/${project}/settings`;
  assert.equal(invalidMutationBody(path, "PATCH", {
    recall_pointer_template: "Recall $WORK_ITEM_ID"
  }), null);
  assert.equal(invalidMutationBody(path, "PATCH", {
    recall_pointer_template: null
  }), null);
  for (const body of [
    {},
    { recall_pointer_template: 17 },
    { recall_pointer_template: { nested: "value" } },
    { recall_pointer_template: " \r\n\t" },
    { recall_pointer_template: "NUL\0byte" },
    { recall_pointer_template: "Invalid Unicode \ud800" },
    { recall_pointer_template: "x".repeat(100001) },
    { recall_pointer_template: "Recall $WORK_ITEM_ID", project_id: project }
  ]) {
    assert.match(invalidMutationBody(path, "PATCH", body), /allowlist/);
  }
});

test("Phase 4 ready discovery is not exposed through the browser proxy", () => {
  for (const method of ["GET", "POST", "PATCH", "DELETE"]) {
    assert.equal(allowedQueryKeys(`projects/${project}/ready-work`, method), null);
  }
});

test("Phase 5 mutation bodies use exact route-specific actor and event allowlists", () => {
  const actor = { actor_client: "dashboard", actor_session_id: "tab-1" };
  assert.equal(invalidMutationBody(
    `projects/${project}/work-items/${work}/events`,
    "POST",
    { event_type: "progress", body: "Safe text", metadata: {}, actor }
  ), null);
  assert.match(invalidMutationBody(
    `projects/${project}/work-items/${work}/events`,
    "POST",
    { event_type: "work_completed", body: "forged", metadata: {}, actor }
  ), /allowlist/);
  assert.match(invalidMutationBody(
    `projects/${project}/work-items/${work}/events`,
    "POST",
    { event_type: "progress", body: "text", metadata: {}, actor, lease_token: "secret" }
  ), /unsupported field/);
  assert.match(invalidMutationBody(
    `projects/${project}/work-items/${work}/events`,
    "POST",
    { event_type: "progress", body: "text", metadata: {}, actor: { client: "wrong" } }
  ), /allowlist/);

  assert.equal(invalidMutationBody(
    `projects/${project}/work-items/${work}`,
    "PATCH",
    { expected_version: 2, title: "Updated", actor }
  ), null);
  assert.match(invalidMutationBody(
    `projects/${project}/work-items/${work}`,
    "PATCH",
    { expected_version: 2, title: "Updated", actor, holder_client: "forged" }
  ), /allowlist/);
  assert.equal(invalidMutationBody(
    `projects/${project}/work-items/${work}/delete`,
    "POST",
    { expected_version: 2, actor }
  ), null);
  assert.equal(invalidMutationBody(
    `projects/${project}/relationships/${other}`,
    "DELETE",
    { actor }
  ), null);
  assert.match(invalidMutationBody(
    `projects/${project}/relationships/${other}`,
    "DELETE",
    { actor, relationship_id: other }
  ), /allowlist/);
});
test("Phase 5 progress proxy validation enforces recursive metadata and text bounds", () => {
  const path = `projects/${project}/work-items/${work}/events`;
  const actor = { actor_client: "dashboard", actor_session_id: "tab-1" };
  const invalid = (overrides) => invalidMutationBody(path, "POST", {
    event_type: "progress",
    body: "Progress",
    metadata: {},
    actor,
    ...overrides
  });

  for (const body of ["", " \t\n", `ok\0not-ok`, "x".repeat(4001)]) {
    assert.match(invalid({ body }), /allowlist/);
  }
  for (const invalidActor of [
    { actor_client: "x".repeat(81), actor_session_id: "tab-1" },
    { actor_client: "dashboard", actor_session_id: "x".repeat(201) },
    { actor_client: "dashboard", actor_session_id: "tab-1", actor_model: " " },
    { actor_client: "dashboard", actor_session_id: "tab-1", actor_model: "x".repeat(121) }
  ]) {
    assert.match(invalid({ actor: invalidActor }), /allowlist/);
  }
  for (const metadata of [
    { nested: [{ SeCrEt: "blocked by key" }] },
    { nested: `ok\0not-ok` },
    { note: "x".repeat(16_384) },
    { number: Number.POSITIVE_INFINITY }
  ]) {
    assert.match(invalid({ metadata }), /allowlist/);
  }
  assert.equal(invalid({
    body: "<script>kept as inert text</script>",
    metadata: { nested: ["safe", { count: 2 }] }
  }), null);
});


test("all lease-capability routes are denied to the browser proxy", () => {
  for (const operation of ["claim", "claim-and-recall", "renew-claim", "release-claim"]) {
    const path = `projects/${project}/work-items/${work}/${operation}`;
    for (const method of ["GET", "POST", "PATCH", "DELETE"]) {
      assert.equal(allowedQueryKeys(path, method), null, `${method} ${operation}`);
    }
  }
});

test("mutation bodies reject capability tokens at any nesting level", () => {
  assert.equal(forbiddenMutationField({ title: "Keep me", initial_checkpoint: { prompt: "Context" } }), null);
  assert.equal(forbiddenMutationField({ lease_token: "secret" }), "lease_token");
  assert.equal(forbiddenMutationField({ checkpoint: { source_metadata: [{ lease_token: "secret" }] } }), "lease_token");
});

test("canonical mutation bodies cannot carry lease tokens", () => {
  const browserMutations = [
    [`projects/${project}/work-items/${work}`, "PATCH"],
    [`projects/${project}/work-items/${work}/complete`, "POST"],
    [`projects/${project}/work-items/${work}/delete`, "POST"],
    [`projects/${project}/work-items/${work}/checkpoints`, "POST"],
    [`projects/${project}/relationships`, "POST"]
  ];
  for (const [path, method] of browserMutations) {
    assert.notEqual(allowedQueryKeys(path, method), null, `${method} ${path} should otherwise be allowed`);
    assert.equal(forbiddenMutationField({ expected_version: 1, lease_token: "browser-secret" }), "lease_token");
  }
});

test("bodyless DELETE requests survive fetch stream normalization", async () => {
  const url = "http://localhost:3000/api/mnemonic/relationship";
  assert.equal(
    await classifyRequestBody(new Request(url, { method: "DELETE" }), 1024),
    "empty"
  );
  assert.equal(
    await classifyRequestBody(new Request(url, { method: "DELETE", body: "" }), 1024),
    "empty"
  );
  assert.equal(
    await classifyRequestBody(new Request(url, { method: "DELETE", body: "{}" }), 1024),
    "present"
  );
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
