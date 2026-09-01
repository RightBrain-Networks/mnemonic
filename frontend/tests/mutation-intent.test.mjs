import assert from "node:assert/strict";
import test from "node:test";
import {
  MutationIntentError,
  MutationIntentRegistry,
  mutationWorkKey
} from "../lib/mutation-intent.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
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
