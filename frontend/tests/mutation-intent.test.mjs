import assert from "node:assert/strict";
import test from "node:test";
import {
  MutationIntentError,
  MutationIntentRegistry,
  mutationGateKey,
  mutationWorkKey
} from "../lib/mutation-intent.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const destination = "f1cf3691-7d28-4716-94a9-4867b341a685";
const operation = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";

function deletionInput() {
  return {
    kind: "delete_work",
    slot: `delete-work:${project}:${work}`,
    projectId: project,
    conflictKeys: [mutationWorkKey(project, work)],
    method: "POST",
    path: `/projects/${project}/work-items/${work}/delete`,
    payload: {
      expected_version: 2,
      actor: { actor_client: "dashboard", actor_session_id: "tab-1" }
    }
  };
}

function deletionResponse() {
  return new Response(JSON.stringify({
    deleted: true,
    project_id: project,
    work_item_id: work,
    version: 3
  }), { status: 200, headers: { "Content-Type": "application/json" } });
}

test("unknown outcome retry reuses one UUID and the exact frozen serialized body", async () => {
  const calls = [];
  let uuidCount = 0;
  const registry = new MutationIntentRegistry(async (url, init) => {
    calls.push({ url, method: init.method, body: init.body });
    return calls.length === 1
      ? new Response(JSON.stringify({ detail: "Upstream unavailable." }), { status: 502 })
      : deletionResponse();
  }, () => {
    uuidCount += 1;
    return operation;
  });

  await assert.rejects(
    registry.execute(deletionInput()),
    (error) => error instanceof MutationIntentError && error.state === "unresolved"
  );
  const retained = registry.get(deletionInput().slot);
  assert.equal(retained.state, "unresolved");
  assert.equal(JSON.parse(retained.body).client_operation_id, operation);

  const recovered = [];
  registry.subscribeRecovered((intent) => recovered.push(intent));
  const result = await registry.retry(deletionInput().slot);
  assert.equal(result.version, 3);
  assert.equal(uuidCount, 1);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(registry.get(deletionInput().slot), undefined);
  assert.deepEqual(recovered.map((intent) => intent.slot), [deletionInput().slot]);
  assert.equal("operationId" in recovered[0], false);
});

test("merge ambiguity freezes both work keys, both revisions, and byte-identical retry", async () => {
  const calls = [];
  const registry = new MutationIntentRegistry(async (url, init) => {
    calls.push({ url: String(url), method: init.method, body: init.body });
    return new Response(JSON.stringify({ detail: "Lost merge response." }), { status: 502 });
  }, () => operation);
  const sourceRevision = {
    work_version: 3,
    context_checkpoint_id: "26a3a437-0af3-405a-ab82-7932d17869e0",
    work_event_count: 7
  };
  const destinationRevision = {
    work_version: 5,
    context_checkpoint_id: "11111111-1111-4111-8111-111111111111",
    work_event_count: 9
  };
  const slot = `merge-work:${project}:${work}`;
  const input = {
    kind: "merge_work",
    slot,
    projectId: project,
    conflictKeys: [
      mutationWorkKey(project, work),
      mutationWorkKey(project, destination)
    ],
    method: "POST",
    path: `/projects/${project}/work-items/${work}/merge`,
    payload: {
      destination_work_item_id: destination,
      reviewed_source_revision: sourceRevision,
      reviewed_destination_revision: destinationRevision,
      rationale: "Exact rationale with bidi text: العربية \u202E literal.",
      merged_by_client: "dashboard",
      merged_by_session_id: "tab-1",
      merged_by_model: null
    }
  };

  await assert.rejects(
    registry.execute(input),
    (error) => error instanceof MutationIntentError && error.state === "unresolved"
  );
  sourceRevision.work_version = 99;
  destinationRevision.work_event_count = 99;
  const retained = registry.get(slot);
  assert.deepEqual(retained.conflictKeys, [
    `work:${project}:${work}`,
    `work:${project}:${destination}`
  ]);
  assert.equal(Object.isFrozen(retained), true);
  assert.equal(Object.isFrozen(retained.conflictKeys), true);
  assert.deepEqual(JSON.parse(retained.body), {
    destination_work_item_id: destination,
    reviewed_source_revision: {
      work_version: 3,
      context_checkpoint_id: "26a3a437-0af3-405a-ab82-7932d17869e0",
      work_event_count: 7
    },
    reviewed_destination_revision: {
      work_version: 5,
      context_checkpoint_id: "11111111-1111-4111-8111-111111111111",
      work_event_count: 9
    },
    rationale: "Exact rationale with bidi text: العربية \u202E literal.",
    merged_by_client: "dashboard",
    merged_by_session_id: "tab-1",
    merged_by_model: null,
    client_operation_id: operation
  });

  await assert.rejects(
    registry.retry(slot),
    (error) => error instanceof MutationIntentError && error.state === "unresolved"
  );
  assert.deepEqual(calls[0], calls[1]);
});

test("checkpoint ambiguity freezes ordered affected paths into the exact retry intent", async () => {
  const calls = [];
  const registry = new MutationIntentRegistry(async (url, init) => {
    calls.push({ url: String(url), method: init.method, body: init.body });
    return new Response(JSON.stringify({ detail: "Lost checkpoint response." }), { status: 502 });
  }, () => operation);
  const affectedPaths = ["src/**", "Tests/test_*.py", "README.md"];
  const slot = `add-checkpoint:${project}:${work}`;
  const input = {
    kind: "add_checkpoint",
    slot,
    projectId: project,
    conflictKeys: [mutationWorkKey(project, work)],
    method: "POST",
    path: `/projects/${project}/work-items/${work}/checkpoints`,
    payload: {
      kind: "context",
      prompt: "Exact scoped context",
      source_client: "dashboard",
      source_session_id: "tab-1",
      source_model: null,
      source_session_url: null,
      repository_branch: "work/phase10",
      verified_against: "abcdef1234567",
      affected_paths: affectedPaths,
      tags: [],
      source_metadata: {}
    }
  };

  await assert.rejects(
    registry.execute(input),
    (error) => error instanceof MutationIntentError && error.state === "unresolved"
  );
  affectedPaths.reverse();
  const retained = registry.get(slot);
  assert.deepEqual(JSON.parse(retained.body).affected_paths, [
    "src/**",
    "Tests/test_*.py",
    "README.md"
  ]);
  assert.equal(JSON.parse(retained.body).client_operation_id, operation);

  await assert.rejects(
    registry.execute(input),
    (error) => error instanceof MutationIntentError && error.state === "blocked"
  );
  await assert.rejects(
    registry.retry(slot),
    (error) => error instanceof MutationIntentError && error.state === "unresolved"
  );
  assert.deepEqual(calls[0], calls[1]);
});

test("double submission coalesces one in-flight request", async () => {
  let fetchCount = 0;
  let release;
  const registry = new MutationIntentRegistry(() => {
    fetchCount += 1;
    return new Promise((resolve) => { release = resolve; });
  }, () => operation);

  const first = registry.execute(deletionInput());
  const second = registry.execute(deletionInput());
  assert.equal(fetchCount, 1);
  assert.equal(registry.get(deletionInput().slot).state, "in_flight");
  release(deletionResponse());
  const [left, right] = await Promise.all([first, second]);
  assert.deepEqual(left, right);
  assert.equal(fetchCount, 1);
});

test("a hung fetch becomes retryable with the exact frozen request", { timeout: 1_000 }, async () => {
  const calls = [];
  const registry = new MutationIntentRegistry((url, init) => {
    calls.push({ url: String(url), method: init.method, body: init.body, signal: init.signal });
    if (calls.length > 1) return Promise.resolve(deletionResponse());
    return new Promise((_resolve, reject) => {
      init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
    });
  }, () => operation, 10);

  await assert.rejects(
    registry.execute(deletionInput()),
    (error) => error instanceof MutationIntentError && error.state === "unresolved"
  );
  const retained = registry.get(deletionInput().slot);
  assert.equal(retained.state, "unresolved");
  assert.equal(calls[0].signal.aborted, true);

  const result = await registry.retry(deletionInput().slot);
  assert.equal(result.version, 3);
  assert.deepEqual(
    calls.map(({ url, method, body }) => ({ url, method, body })),
    [calls[0], calls[0]].map(({ url, method, body }) => ({ url, method, body }))
  );
  assert.equal(JSON.parse(calls[1].body).client_operation_id, operation);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(calls[1].signal.aborted, false);
});

test("a hung response body becomes retryable with the exact frozen request", {
  timeout: 1_000
}, async () => {
  const calls = [];
  let bodyReadStarted = false;
  const registry = new MutationIntentRegistry(async (url, init) => {
    calls.push({ url: String(url), method: init.method, body: init.body, signal: init.signal });
    if (calls.length > 1) return deletionResponse();
    return {
      status: 200,
      text: () => {
        bodyReadStarted = true;
        return new Promise(() => {});
      }
    };
  }, () => operation, 10);

  await assert.rejects(
    registry.execute(deletionInput()),
    (error) => error instanceof MutationIntentError && error.state === "unresolved"
  );
  assert.equal(bodyReadStarted, true);
  assert.equal(calls[0].signal.aborted, true);
  assert.equal(registry.get(deletionInput().slot).state, "unresolved");

  const result = await registry.retry(deletionInput().slot);
  assert.equal(result.version, 3);
  assert.deepEqual(
    calls.map(({ url, method, body }) => ({ url, method, body })),
    [calls[0], calls[0]].map(({ url, method, body }) => ({ url, method, body }))
  );
  assert.equal(JSON.parse(calls[1].body).client_operation_id, operation);
});

test("prepared intents can be discarded, but dispatched ambiguity blocks intersecting work", async () => {
  let uuidCount = 0;
  const registry = new MutationIntentRegistry(
    async () => new Response(JSON.stringify({ detail: "Lost response." }), { status: 502 }),
    () => `91b9168a-37d1-4a6a-aa1f-bb538b65cb5${++uuidCount}`
  );
  const prepared = registry.prepare(deletionInput());
  assert.equal(prepared.state, "prepared");
  assert.equal(registry.hasDispatched(), false);
  assert.equal(registry.discardPrepared(prepared.slot), true);
  assert.equal(registry.get(prepared.slot), undefined);

  await assert.rejects(registry.execute(deletionInput()), MutationIntentError);
  assert.equal(registry.hasDispatchedForProject(project), true);
  await assert.rejects(
    registry.execute({
      kind: "complete_work",
      slot: `complete-work:${project}:${work}`,
      projectId: project,
      conflictKeys: [mutationWorkKey(project, work)],
      method: "POST",
      path: `/projects/${project}/work-items/${work}/complete`,
      payload: { expected_version: 2, checkpoint: {} }
    }),
    (error) => error instanceof MutationIntentError && error.state === "blocked"
  );
  assert.equal(uuidCount, 2);
});

test("gate resolution freezes the reviewed revision and conflicts on work plus gate only", () => {
  const gate = "1dfa9455-4a17-4cd4-938b-010ea17ccaf0";
  const reviewedCheckpoint = "26a3a437-0af3-405a-ab82-7932d17869e0";
  const registry = new MutationIntentRegistry(async () => {
    throw new Error("not dispatched");
  }, () => operation);
  const intent = registry.prepare({
    kind: "resolve_human_input",
    slot: `gate-resolution:${gate}`,
    projectId: project,
    conflictKeys: [mutationWorkKey(project, work), mutationGateKey(project, gate)],
    method: "POST",
    path: `/projects/${project}/work-items/${work}/gates/${gate}/resolve`,
    payload: {
      resolution: "Exact answer",
      resolved_by_client: "dashboard",
      resolved_by_session_id: "tab-1",
      resolved_by_model: null,
      reviewed_context_revision: {
        work_version: 4,
        context_checkpoint_id: reviewedCheckpoint,
        relationship_event_count: 7
      }
    }
  });
  assert.deepEqual(JSON.parse(intent.body), {
    resolution: "Exact answer",
    resolved_by_client: "dashboard",
    resolved_by_session_id: "tab-1",
    resolved_by_model: null,
    reviewed_context_revision: {
      work_version: 4,
      context_checkpoint_id: reviewedCheckpoint,
      relationship_event_count: 7
    },
    client_operation_id: operation
  });
  assert.deepEqual(intent.conflictKeys, [
    `work:${project}:${work}`,
    `gate:${project}:${gate}`
  ]);
  assert.equal(intent.conflictKeys.some((key) => key.startsWith("project:")), false);
});

test("exact-key conflict remains a non-discardable safety incident", async () => {
  const registry = new MutationIntentRegistry(async () => new Response(JSON.stringify({
    detail: {
      code: "client_operation_conflict",
      message: "The operation ID is already bound to another request.",
      context: {}
    }
  }), { status: 409 }), () => operation);

  await assert.rejects(
    registry.execute(deletionInput()),
    (error) => error instanceof MutationIntentError && error.state === "safety_conflict"
  );
  assert.equal(registry.get(deletionInput().slot).state, "safety_conflict");
  assert.equal(registry.discardPrepared(deletionInput().slot), false);
  await assert.rejects(
    registry.retry(deletionInput().slot),
    (error) => error instanceof MutationIntentError && error.state === "safety_conflict"
  );
});

test("recognized definite 4xx clears recovery while malformed 2xx retains it", async () => {
  const responses = [
    new Response(JSON.stringify({
      detail: [{ type: "value_error", loc: ["body", "expected_version"], msg: "Value is invalid." }]
    }), { status: 422 }),
    new Response("{not-json", { status: 200 })
  ];
  let operationIndex = 0;
  const registry = new MutationIntentRegistry(
    async () => responses.shift(),
    () => operationIndex++ === 0 ? operation : "2b456f86-87fb-48df-a75a-762f93a9f2e9"
  );

  await assert.rejects(registry.execute(deletionInput()), (error) => error.status === 422);
  assert.equal(registry.get(deletionInput().slot), undefined);
  await assert.rejects(
    registry.execute(deletionInput()),
    (error) => error instanceof MutationIntentError && error.state === "unresolved"
  );
  assert.equal(registry.get(deletionInput().slot).state, "unresolved");
});

test("unknown outcome followed by a definitive conflict clears only after exact retry", async () => {
  const calls = [];
  const responses = [
    new Response(JSON.stringify({ detail: "Upstream unavailable." }), { status: 502 }),
    new Response(JSON.stringify({
      detail: {
        code: "version_conflict",
        message: "The work item changed.",
        context: {}
      }
    }), { status: 409 })
  ];
  const registry = new MutationIntentRegistry(async (url, init) => {
    calls.push({ url, method: init.method, body: init.body });
    return responses.shift();
  }, () => operation);

  await assert.rejects(
    registry.execute(deletionInput()),
    (error) => error instanceof MutationIntentError && error.state === "unresolved"
  );
  await assert.rejects(
    registry.retry(deletionInput().slot),
    (error) => error.status === 409 && error.code === "version_conflict"
  );
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(registry.get(deletionInput().slot), undefined);
});

test("unknown structured and string 4xx responses retain the byte-identical request", async () => {
  const responses = [
    {
      detail: {
        code: "new_receipt_safety_code",
        message: "A future server knows more than this browser.",
        context: {}
      }
    },
    { detail: "Unknown old-style conflict." }
  ];

  for (const responseBody of responses) {
    const calls = [];
    const registry = new MutationIntentRegistry(async (url, init) => {
      calls.push({ url, method: init.method, body: init.body });
      return new Response(JSON.stringify(responseBody), { status: 409 });
    }, () => operation);

    await assert.rejects(
      registry.execute(deletionInput()),
      (error) => error instanceof MutationIntentError && error.state === "unresolved"
    );
    const retained = registry.get(deletionInput().slot);
    const frozenBody = retained.body;
    assert.equal(retained.state, "unresolved");
    assert.equal(JSON.parse(frozenBody).client_operation_id, operation);

    await assert.rejects(
      registry.retry(deletionInput().slot),
      (error) => error instanceof MutationIntentError && error.state === "unresolved"
    );
    assert.equal(registry.get(deletionInput().slot).body, frozenBody);
    assert.deepEqual(calls[0], calls[1]);
  }
});

test("invalid UUID generation fails before dispatch and network failure retains recovery", async () => {
  let dispatched = false;
  const invalid = new MutationIntentRegistry(async () => {
    dispatched = true;
    return deletionResponse();
  }, () => "not-a-uuid");
  assert.throws(
    () => invalid.prepare(deletionInput()),
    /operation ID generator returned an invalid UUID/
  );
  assert.equal(dispatched, false);
  assert.equal(invalid.get(deletionInput().slot), undefined);

  const offline = new MutationIntentRegistry(async () => {
    throw new Error("socket reset");
  }, () => operation);
  await assert.rejects(
    offline.execute(deletionInput()),
    (error) => error instanceof MutationIntentError && error.state === "unresolved"
  );
  assert.equal(offline.get(deletionInput().slot).state, "unresolved");
});
