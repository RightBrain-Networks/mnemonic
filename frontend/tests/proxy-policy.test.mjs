import assert from "node:assert/strict";
import test from "node:test";
import {
  allowedQueryKeys,
  browserTransportEffect,
  clientOperationMatchesSecret,
  configuredOrigins,
  forbiddenMutationField,
  forbiddenControlTransport,
  invalidMutationBody,
  trustedRequest,
  upstreamTimeoutMs
} from "../lib/proxy-policy.ts";

const origins = configuredOrigins();
const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const other = "f1cf3691-7d28-4716-94a9-4867b341a685";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const operation = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";
const gate = "1dfa9455-4a17-4cd4-938b-010ea17ccaf0";
const checkpoint = "26a3a437-0af3-405a-ab82-7932d17869e0";
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
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items`, "GET"), ["q", "semantic", "status", "sort", "tag", "source_client", "source_session_id", "view", "duplicate_scope", "canonical_work_item_id", "limit", "offset"]);
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
  assert.deepEqual(
    allowedQueryKeys(`projects/${project}/work-items/${work}/completion-evidence`, "GET"),
    ["limit", "cursor"]
  );
  assert.equal(
    allowedQueryKeys(`projects/${project}/work-items/${work}/completion-evidence`, "POST"),
    null
  );
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/complete`, "POST"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/defer`, "POST"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/delete`, "POST"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/merge`, "POST"), []);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/human-attention`, "GET"), ["work_item_id", "limit", "cursor"]);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/gates`, "GET"), ["status", "limit", "cursor"]);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/gates/${gate}/context`, "GET"), ["recent_limit", "recent_event_limit"]);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/work-items/${work}/gates/${gate}/resolve`, "POST"), []);
  assert.equal(allowedQueryKeys(`projects/${project}/work-items/${work}/gates`, "POST"), null);
  assert.equal(allowedQueryKeys(`projects/${project}/work-items/${work}/gates/${gate}`, "PATCH"), null);
  assert.equal(allowedQueryKeys(`projects/${project}/work-items/${work}/gates/${gate}`, "DELETE"), null);
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

test("browser completion accepts only the exact nested Phase 11 evidence contract", () => {
  const path = `projects/${project}/work-items/${work}/complete`;
  const checkpointBody = {
    prompt: "Completion summary",
    source_client: "dashboard",
    source_session_id: "tab-1",
    source_model: null,
    repository_branch: null,
    verified_against: null,
    tags: [],
    source_metadata: {}
  };
  const body = {
    expected_version: 3,
    checkpoint: checkpointBody,
    completion_evidence: {
      verification_results: [{
        verification_type: "command",
        name: "Frontend tests",
        outcome: "passed",
        summary: "The unit suite passed.",
        command: "npm test",
        exit_code: 0
      }],
      artifact_references: [{
        artifact_type: "branch",
        label: "Delivered branch",
        reference: "work/Phase11 exact"
      }]
    },
    client_operation_id: operation
  };
  assert.equal(invalidMutationBody(path, "POST", body), null);
  assert.equal(invalidMutationBody(path, "POST", {
    ...body,
    completion_evidence: {}
  }), null);
  for (const completion_evidence of [
    null,
    { verification_results: null },
    {
      verification_results: [{
        verification_type: "command",
        name: "Frontend tests",
        outcome: "passed",
        summary: "Contradictory status.",
        command: "npm test",
        exit_code: 1
      }]
    },
    {
      artifact_references: [{
        artifact_type: "pull_request",
        label: "Unsafe",
        reference: "https://example.test/pull/1?token=no"
      }]
    },
    { verification_results: [], artifact_references: [], extra: true }
  ]) {
    assert.match(
      invalidMutationBody(path, "POST", { ...body, completion_evidence }),
      /allowlist/
    );
  }
  assert.match(
    invalidMutationBody(path, "POST", {
      ...body,
      lease_token: "browser-forbidden",
      completion_evidence: body.completion_evidence
    }),
    /unsupported field/
  );
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
    { event_type: "progress", body: "Safe text", metadata: {}, actor, client_operation_id: operation }
  ), null);
  assert.match(invalidMutationBody(
    `projects/${project}/work-items/${work}/events`,
    "POST",
    { event_type: "work_completed", body: "forged", metadata: {}, actor, client_operation_id: operation }
  ), /allowlist/);
  assert.match(invalidMutationBody(
    `projects/${project}/work-items/${work}/events`,
    "POST",
    { event_type: "progress", body: "text", metadata: {}, actor, lease_token: "secret" }
  ), /unsupported field/);
  assert.match(invalidMutationBody(
    `projects/${project}/work-items/${work}/events`,
    "POST",
    { event_type: "progress", body: "text", metadata: {}, actor: { client: "wrong" }, client_operation_id: operation }
  ), /allowlist/);

  assert.equal(invalidMutationBody(
    `projects/${project}/work-items/${work}`,
    "PATCH",
    { expected_version: 2, title: "Updated", actor, client_operation_id: operation }
  ), null);
  assert.match(invalidMutationBody(
    `projects/${project}/work-items/${work}`,
    "PATCH",
    { expected_version: 2, status: "deferred", actor, client_operation_id: operation }
  ), /allowlist/);
  assert.equal(invalidMutationBody(
    `projects/${project}/work-items/${work}/defer`,
    "POST",
    { expected_version: 2, actor, client_operation_id: operation }
  ), null);
  assert.match(invalidMutationBody(
    `projects/${project}/work-items/${work}/defer`,
    "POST",
    { expected_version: 2, status: "deferred", actor, client_operation_id: operation }
  ), /allowlist/);
  assert.match(invalidMutationBody(
    `projects/${project}/work-items/${work}`,
    "PATCH",
    { expected_version: 2, title: "Updated", actor, holder_client: "forged", client_operation_id: operation }
  ), /allowlist/);
  assert.equal(invalidMutationBody(
    `projects/${project}/work-items/${work}/delete`,
    "POST",
    { expected_version: 2, actor, client_operation_id: operation }
  ), null);
  assert.equal(invalidMutationBody(
    `projects/${project}/relationships/${other}`,
    "DELETE",
    { actor, client_operation_id: operation }
  ), null);
  assert.match(invalidMutationBody(
    `projects/${project}/relationships/${other}`,
    "DELETE",
    { actor, relationship_id: other, client_operation_id: operation }
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
    client_operation_id: operation,
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
  assert.match(invalid({ metadata: { nested: [{ gate_id: gate }] } }), /gate_id/);
  assert.match(invalid({ metadata: { nested: [{ GaTe_TyPe: "human" }] } }), /GaTe_TyPe/);
  assert.equal(invalid({
    body: "<script>kept as inert text</script>",
    metadata: { nested: ["safe", { count: 2 }] }
  }), null);
  assert.match(invalid({ metadata: { nested: { ClIeNt_OpErAtIoN_Id: operation } } }), /top level/);
});

test("Phase 6 proxy accepts the operation UUID on all nine dashboard mutation bodies", () => {
  const actor = { actor_client: "dashboard", actor_session_id: "tab-1" };
  const checkpoint = {
    prompt: "Exact context",
    source_client: "dashboard",
    source_session_id: "tab-1",
    source_model: null,
    repository_branch: null,
    verified_against: null,
    tags: [],
    source_metadata: {}
  };
  const covered = [
    [`projects/${project}/work-items`, "POST", {
      title: "Durable work",
      summary: "Summary",
      priority: 0,
      status: "pending",
      initial_checkpoint: checkpoint,
      client_operation_id: operation
    }],
    [`projects/${project}/work-items/${work}/checkpoints`, "POST", {
      kind: "context",
      ...checkpoint,
      client_operation_id: operation
    }],
    [`projects/${project}/work-items/${work}/events`, "POST", {
      event_type: "progress",
      body: "Progress",
      metadata: {},
      actor,
      client_operation_id: operation
    }],
    [`projects/${project}/relationships`, "POST", {
      relationship_type: "blocks",
      source_work_item_id: work,
      target_work_item_id: other,
      created_by_client: "dashboard",
      created_by_session_id: "tab-1",
      created_by_model: null,
      client_operation_id: operation
    }],
    [`projects/${project}/work-items/${work}`, "PATCH", {
      expected_version: 1,
      title: "Updated",
      actor,
      client_operation_id: operation
    }],
    [`projects/${project}/work-items/${work}/defer`, "POST", {
      expected_version: 1,
      actor,
      client_operation_id: operation
    }],
    [`projects/${project}/work-items/${work}/complete`, "POST", {
      expected_version: 1,
      checkpoint,
      client_operation_id: operation
    }],
    [`projects/${project}/work-items/${work}/delete`, "POST", {
      expected_version: 1,
      actor,
      client_operation_id: operation
    }],
    [`projects/${project}/relationships/${other}`, "DELETE", {
      actor,
      client_operation_id: operation
    }]
  ];
  for (const [path, method, body] of covered) {
    assert.equal(invalidMutationBody(path, method, body), null, `${method} ${path}`);
    assert.match(
      invalidMutationBody(path, method, { ...body, client_operation_id: "not-a-uuid" }),
      /must be a UUID/
    );
    const { client_operation_id: ignored, ...withoutOperation } = body;
    assert.match(invalidMutationBody(path, method, withoutOperation), /must be a UUID/);
  }
});

test("repository scope is accepted only on the three checkpoint-bearing mutations", () => {
  const checkpointPayload = {
    prompt: "Exact context",
    source_client: "dashboard",
    source_session_id: "tab-1",
    source_model: null,
    repository_branch: "work/phase10",
    verified_against: "abcdef1234567",
    tags: [],
    source_metadata: {}
  };
  const paths = ["src/**", "tests/test_*.py", "README.md"];
  const bodies = [
    [
      `projects/${project}/work-items`,
      {
        title: "Durable work",
        summary: "Summary",
        priority: 0,
        status: "pending",
        initial_checkpoint: checkpointPayload,
        client_operation_id: operation
      },
      "initial_checkpoint"
    ],
    [
      `projects/${project}/work-items/${work}/checkpoints`,
      { kind: "context", ...checkpointPayload, client_operation_id: operation },
      null
    ],
    [
      `projects/${project}/work-items/${work}/complete`,
      { expected_version: 1, checkpoint: checkpointPayload, client_operation_id: operation },
      "checkpoint"
    ]
  ];
  const withPaths = (body, nested, value) => nested
    ? { ...body, [nested]: { ...body[nested], affected_paths: value } }
    : { ...body, affected_paths: value };

  for (const [path, body, nested] of bodies) {
    assert.equal(invalidMutationBody(path, "POST", body), null, `${path}: omitted`);
    assert.equal(
      invalidMutationBody(path, "POST", withPaths(body, nested, [])),
      null,
      `${path}: explicit empty`
    );
    assert.equal(
      invalidMutationBody(path, "POST", withPaths(body, nested, paths)),
      null,
      `${path}: non-empty`
    );
  }

  for (const [path, body, nested] of bodies) {
    const withoutBaseline = withPaths(body, nested, paths);
    if (nested) withoutBaseline[nested] = { ...withoutBaseline[nested], verified_against: null };
    else withoutBaseline.verified_against = null;
    assert.match(invalidMutationBody(path, "POST", withoutBaseline), /allowlist/);

    for (const invalidPaths of [
      ["unsafe path"],
      ["src/**", "src/**"],
      ["src/**tail"],
      ["x".repeat(513)],
      Array.from({ length: 65 }, (_, index) => `path-${index}`)
    ]) {
      assert.match(
        invalidMutationBody(path, "POST", withPaths(body, nested, invalidPaths)),
        /allowlist/
      );
    }
  }

  const nonCheckpoint = {
    expected_version: 1,
    title: "Updated",
    actor: { actor_client: "dashboard", actor_session_id: "tab-1" },
    client_operation_id: operation,
    affected_paths: paths
  };
  assert.match(
    invalidMutationBody(`projects/${project}/work-items/${work}`, "PATCH", nonCheckpoint),
    /allowlist/
  );
  for (const field of ["repository_root", "freshness_state", "assessment_result"]) {
    assert.match(
      invalidMutationBody(
        `projects/${project}/work-items/${work}/checkpoints`,
        "POST",
        { kind: "context", ...checkpointPayload, [field]: "forbidden", client_operation_id: operation }
      ),
      /allowlist/
    );
    assert.match(
      invalidMutationBody(
        `projects/${project}/work-items/${work}/checkpoints`,
        "POST",
        {
          kind: "context",
          ...checkpointPayload,
          source_metadata: { nested: { [field]: "forbidden" } },
          client_operation_id: operation
        }
      ),
      /allowlist/
    );
  }
  assert.match(
    invalidMutationBody(
      `projects/${project}/work-items/${work}/checkpoints`,
      "POST",
      {
        kind: "context",
        ...checkpointPayload,
        source_metadata: { affected_paths: paths },
        client_operation_id: operation
      }
    ),
    /allowlist/
  );
});

test("human-gate resolution is the only browser gate mutation and binds exact reviewed context", () => {
  const path = `projects/${project}/work-items/${work}/gates/${gate}/resolve`;
  const base = {
    resolution: "Proceed only after checking current policy.",
    resolved_by_client: "dashboard",
    resolved_by_session_id: "tab-1",
    resolved_by_model: null,
    reviewed_context_revision: {
      work_version: 3,
      context_checkpoint_id: checkpoint,
      relationship_event_count: 8
    },
    client_operation_id: operation
  };
  assert.equal(invalidMutationBody(path, "POST", base), null);
  const { reviewed_context_revision: ignored, ...withoutRevision } = base;

  for (const invalid of [
    { ...base, resolution: "   " },
    { ...base, resolved_by_client: "agent" },
    { ...base, resolved_by_session_id: "x".repeat(201) },
    { ...base, resolved_by_model: "model" },
    withoutRevision,
    { ...base, reviewed_context_revision: { ...base.reviewed_context_revision, work_version: 0 } },
    { ...base, gate_id: gate },
    { ...base, answer_suggestion: "forged" },
    { ...base, client_operation_id: "not-a-uuid" }
  ]) assert.ok(invalidMutationBody(path, "POST", invalid));

  assert.equal(forbiddenMutationField({ nested: { gate_id: gate } }), "gate_id");
  assert.equal(forbiddenMutationField({ nested: { Gate_ID: gate } }), "Gate_ID");
  assert.equal(forbiddenMutationField({ nested: { GaTe_TyPe: "human" } }), "GaTe_TyPe");
});

test("proxy rejects gate and operation IDs in excluded transport locations", () => {
  const actor = { actor_client: "dashboard", actor_session_id: "tab-1" };
  assert.match(invalidMutationBody("projects", "POST", {
    name: "Project",
    client_operation_id: operation
  }), /not supported/);
  assert.match(invalidMutationBody(`projects/${project}/settings`, "PATCH", {
    recall_pointer_template: null,
    CLIENT_OPERATION_ID: operation
  }), /not supported/);
  assert.match(invalidMutationBody(`projects/${project}/work-items`, "POST", {
    title: "Durable work",
    summary: "Summary",
    priority: 0,
    status: "pending",
    initial_checkpoint: {
      prompt: "Context",
      source_client: "dashboard",
      source_session_id: "tab-1",
      source_metadata: { client_operation_id: operation }
    },
    client_operation_id: operation
  }), /top level/);
  assert.match(invalidMutationBody(`projects/${project}/work-items/${work}`, "PATCH", {
    expected_version: 1,
    title: "Updated",
    actor: { ...actor, client_operation_id: operation },
    client_operation_id: operation
  }), /top level/);

  const protectedQueryKeys = allowedQueryKeys(
    `projects/${project}/work-items/${work}/events`,
    "POST"
  );
  assert.ok(protectedQueryKeys);
  assert.equal(protectedQueryKeys.includes("client_operation_id"), false);

  assert.equal(
    forbiddenControlTransport(new Headers({ client_operation_id: operation })),
    "header"
  );
  assert.equal(forbiddenControlTransport(new Headers({ "Idempotency-Key": operation })), "header");
  assert.equal(forbiddenControlTransport(new Headers({ "X-Client-Operation-Id": operation })), "header");
  assert.equal(forbiddenControlTransport(new Headers({ "Gate-Id": gate })), "header");
  assert.equal(forbiddenControlTransport(new Headers({ "X-Gate-Id": gate })), "header");
  for (const name of [
    "client_operation_id",
    "client-operation-id",
    "idempotency-key",
    "x-idempotency-key",
    "x-client-operation-id",
    "gate_id",
    "gate-id",
    "human-gate-id",
    "x-gate-id",
    "x-human-gate-id"
  ]) {
    assert.equal(
      forbiddenControlTransport(new Headers({ cookie: `theme=dark; ${name}=${operation}` })),
      "cookie",
      name
    );
  }
  assert.equal(forbiddenControlTransport(new Headers({ cookie: "theme=dark" })), null);
  assert.equal(clientOperationMatchesSecret(
    JSON.stringify({ client_operation_id: operation }),
    operation
  ), true);
  assert.equal(clientOperationMatchesSecret(
    JSON.stringify({ client_operation_id: operation }),
    "different-secret"
  ), false);
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
    [`projects/${project}/work-items/${work}/defer`, "POST"],
    [`projects/${project}/work-items/${work}/delete`, "POST"],
    [`projects/${project}/work-items/${work}/checkpoints`, "POST"],
    [`projects/${project}/relationships`, "POST"],
    [`projects/${project}/work-items/${work}/merge`, "POST"]
  ];
  for (const [path, method] of browserMutations) {
    assert.notEqual(allowedQueryKeys(path, method), null, `${method} ${path} should otherwise be allowed`);
    assert.equal(forbiddenMutationField({ expected_version: 1, lease_token: "browser-secret" }), "lease_token");
  }
});

test("Core merge is a receipt-protected browser mutation with one exact body", () => {
  const path = `projects/${project}/work-items/${work}/merge`;
  const revision = {
    work_version: 3,
    context_checkpoint_id: checkpoint,
    work_event_count: 8
  };
  const body = {
    destination_work_item_id: other,
    reviewed_source_revision: revision,
    reviewed_destination_revision: { ...revision, work_version: 4 },
    rationale: "These records describe the same durable objective.",
    merged_by_client: "dashboard",
    merged_by_session_id: "tab-1",
    merged_by_model: null,
    client_operation_id: operation
  };
  assert.equal(invalidMutationBody(path, "POST", body), null);
  assert.equal(browserTransportEffect(path, "POST"), "receipt_protected_write");
  assert.equal(browserTransportEffect(`projects/${project}/work-items`, "GET"), "safe_read");
  assert.equal(
    browserTransportEffect(`projects/${project}/work-items/${work}/claim`, "POST"),
    "lease_claim"
  );
  for (const invalid of [
    { ...body, destination_work_item_id: "not-a-uuid" },
    { ...body, reviewed_source_revision: { ...revision, work_event_count: 0 } },
    { ...body, reviewed_destination_revision: { ...revision, extra: true } },
    { ...body, rationale: "   " },
    { ...body, merged_by_client: "agent" },
    { ...body, merged_by_model: "forged" },
    { ...body, lease_token: "browser-secret" },
    { ...body, nested: { lease_token: "browser-secret" } }
  ]) assert.match(invalidMutationBody(path, "POST", invalid), /(allowlist|unsupported field)/);
});

test("fresh generic duplicate marks are absent from both browser creation paths", () => {
  assert.match(invalidMutationBody(`projects/${project}/relationships`, "POST", {
    relationship_type: "duplicate-of",
    source_work_item_id: work,
    target_work_item_id: other,
    created_by_client: "dashboard",
    created_by_session_id: "tab-1",
    created_by_model: null,
    client_operation_id: operation
  }), /allowlist/);
  assert.match(invalidMutationBody(`projects/${project}/work-items`, "POST", {
    title: "Duplicate",
    summary: "Summary",
    priority: 0,
    status: "pending",
    initial_checkpoint: {
      prompt: "Context",
      source_client: "dashboard",
      source_session_id: "tab-1",
      tags: [],
      source_metadata: {}
    },
    initial_relationships: [{
      type: "duplicate-of",
      direction: "outgoing",
      other_work_item_id: other
    }],
    client_operation_id: operation
  }), /allowlist/);
});

test("lease controls are denied recursively and across browser transport locations", () => {
  for (const name of ["lease_token", "Lease_Token"]) {
    assert.equal(forbiddenMutationField({ outer: [{ [name]: "secret" }] }), name);
  }
  for (const name of ["lease_token", "lease-token", "x-lease-token"]) {
    assert.equal(forbiddenControlTransport(new Headers({ [name]: "secret" })), "header");
    assert.equal(
      forbiddenControlTransport(new Headers({ cookie: `theme=dark; ${name}=secret` })),
      "cookie"
    );
  }
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
