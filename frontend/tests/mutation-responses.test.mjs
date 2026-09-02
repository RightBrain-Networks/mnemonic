import assert from "node:assert/strict";
import test from "node:test";
import { classifyMutationResponse } from "../lib/mutation-responses.ts";
import {
  DEFINITIVE_PROXY_ERRORS,
  unsupportedMutationFieldError
} from "../lib/proxy-policy.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const counterpart = "f1cf3691-7d28-4716-94a9-4867b341a685";
const checkpointId = "1dfa9455-4a17-4cd4-938b-010ea17ccaf0";
const relationshipId = "26a3a437-0af3-405a-ab82-7932d17869e0";
const operation = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";
const createdAt = "2026-09-01T12:00:00Z";

function checkpointInput(prompt = "Exact context") {
  return {
    prompt,
    source_client: "dashboard",
    source_session_id: "tab-1",
    source_model: null,
    source_session_url: null,
    repository_branch: null,
    verified_against: null,
    tags: [],
    source_metadata: {}
  };
}

function checkpoint(kind, prompt = "Exact context", overrides = {}) {
  return {
    id: checkpointId,
    work_item_id: work,
    kind,
    ...checkpointInput(prompt),
    migration_origin: null,
    legacy_record_id: null,
    created_at: createdAt,
    ...overrides
  };
}

function workItem(overrides = {}) {
  return {
    id: work,
    project_id: project,
    title: "Durable objective",
    summary: "Keep this context",
    status: "pending",
    priority: 5,
    initial_checkpoint_id: checkpointId,
    version: 1,
    created_at: createdAt,
    updated_at: createdAt,
    ...overrides
  };
}

function relationship(overrides = {}) {
  return {
    id: relationshipId,
    project_id: project,
    relationship_type: "blocks",
    source_work_item_id: work,
    target_work_item_id: counterpart,
    context_checkpoint_work_item_id: null,
    context_checkpoint_id: null,
    created_by_client: "dashboard",
    created_by_session_id: "tab-1",
    created_by_model: null,
    created_at: createdAt,
    ...overrides
  };
}

function event(metadata = {}) {
  return {
    id: 1,
    project_id: project,
    work_item_id: work,
    event_type: "progress",
    actor_kind: "client",
    actor_client: "dashboard",
    actor_session_id: "tab-1",
    actor_model: null,
    body: "Exact progress",
    checkpoint_id: null,
    lease_generation_id: null,
    lease_release_id: null,
    relationship_id: null,
    relationship_source_work_item_id: null,
    relationship_target_work_item_id: null,
    relationship_context_checkpoint_work_item_id: null,
    relationship_context_checkpoint_id: null,
    relationship_direction: null,
    counterpart_work_item_id: null,
    metadata_version: 1,
    metadata,
    origin: "live",
    created_at: createdAt
  };
}

function request(kind, method, path, payload) {
  return {
    kind,
    method,
    path,
    operationId: operation,
    body: JSON.stringify({ ...payload, client_operation_id: operation })
  };
}

async function classify(spec, status, value) {
  return classifyMutationResponse(
    spec,
    new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } })
  );
}

test("all nine dashboard mutation response contracts decode with path/result coherence", async () => {
  const initial = checkpointInput();
  const actor = { actor_client: "dashboard", actor_session_id: "tab-1" };
  const cases = [
    [
      request("create_work", "POST", `/projects/${project}/work-items`, {
        title: "Durable objective",
        summary: "Keep this context",
        priority: 5,
        status: "pending",
        initial_checkpoint: initial
      }),
      201,
      { work_item: workItem(), initial_checkpoint: checkpoint("context"), initial_relationships: [] }
    ],
    [
      request("add_checkpoint", "POST", `/projects/${project}/work-items/${work}/checkpoints`, {
        kind: "progress",
        ...checkpointInput("New context")
      }),
      201,
      checkpoint("progress", "New context")
    ],
    [
      request("append_event", "POST", `/projects/${project}/work-items/${work}/events`, {
        event_type: "progress",
        body: "Exact progress",
        metadata: {},
        actor
      }),
      201,
      event()
    ],
    [
      request("add_relationship", "POST", `/projects/${project}/relationships`, {
        relationship_type: "blocks",
        source_work_item_id: work,
        target_work_item_id: counterpart,
        created_by_client: "dashboard",
        created_by_session_id: "tab-1",
        created_by_model: null
      }),
      200,
      { relationship: relationship(), created: true }
    ],
    [
      request("update_work", "PATCH", `/projects/${project}/work-items/${work}`, {
        expected_version: 1,
        title: "Renamed objective",
        actor
      }),
      200,
      workItem({ title: "Renamed objective", version: 2 })
    ],
    [
      request("defer_work", "POST", `/projects/${project}/work-items/${work}/defer`, {
        expected_version: 1,
        actor
      }),
      200,
      workItem({ status: "deferred", version: 2 })
    ],
    [
      request("complete_work", "POST", `/projects/${project}/work-items/${work}/complete`, {
        expected_version: 1,
        checkpoint: checkpointInput("Completion summary")
      }),
      200,
      {
        work_item: workItem({ status: "done", version: 2 }),
        checkpoint: checkpoint("completion", "Completion summary")
      }
    ],
    [
      request("delete_work", "POST", `/projects/${project}/work-items/${work}/delete`, {
        expected_version: 1,
        actor
      }),
      200,
      { deleted: true, project_id: project, work_item_id: work, version: 2 }
    ],
    [
      request("remove_relationship", "DELETE", `/projects/${project}/relationships/${relationshipId}`, {
        actor
      }),
      200,
      { project_id: project, relationship_id: relationshipId, removed: true }
    ]
  ];

  for (const [spec, status, value] of cases) {
    const outcome = await classify(spec, status, value);
    assert.equal(outcome.type, "success", spec.kind);
  }
});

test("relationship natural no-op accepts the existing edge's original provenance", async () => {
  const spec = request("add_relationship", "POST", `/projects/${project}/relationships`, {
    relationship_type: "blocks",
    source_work_item_id: work,
    target_work_item_id: counterpart,
    created_by_client: "dashboard",
    created_by_session_id: "new-tab",
    created_by_model: null
  });
  const outcome = await classify(spec, 200, {
    relationship: relationship({
      created_by_client: "mcp",
      created_by_session_id: "original-session",
      created_by_model: "original-model"
    }),
    created: false
  });
  assert.equal(outcome.type, "success");
  assert.equal(outcome.value.created, false);
  assert.equal(outcome.value.relationship.created_by_session_id, "original-session");
});

test("create-work relationships match canonical identities after sorting and de-duplication", async () => {
  const thirdWork = "3c6215a7-e560-4b18-a2f7-5a503e82df19";
  const relatedId = "d978d687-3acb-4d9e-a73f-f4b895b14d6f";
  const initial = checkpointInput();
  const spec = request("create_work", "POST", `/projects/${project}/work-items`, {
    title: "Durable objective",
    summary: "Keep this context",
    priority: 5,
    status: "pending",
    initial_checkpoint: initial,
    initial_relationships: [
      {
        type: "related",
        direction: "outgoing",
        other_work_item_id: thirdWork
      },
      {
        type: "blocks",
        direction: "outgoing",
        other_work_item_id: counterpart
      },
      {
        type: "blocks",
        direction: "outgoing",
        other_work_item_id: counterpart
      }
    ]
  });
  const outcome = await classify(spec, 201, {
    work_item: workItem(),
    initial_checkpoint: checkpoint("context"),
    initial_relationships: [
      relationship(),
      relationship({
        id: relatedId,
        relationship_type: "related",
        source_work_item_id: thirdWork,
        target_work_item_id: work
      })
    ]
  });
  assert.equal(outcome.type, "success");
  assert.equal(outcome.value.initial_relationships.length, 2);
});

test("response coherence expands REST defaults before comparison", async () => {
  const actor = { actor_client: "dashboard", actor_session_id: "tab-1" };
  const createOutcome = await classify(
    request("create_work", "POST", `/projects/${project}/work-items`, {
      title: "Durable objective",
      summary: "Keep this context",
      initial_checkpoint: checkpointInput()
    }),
    201,
    {
      work_item: workItem({ priority: 0 }),
      initial_checkpoint: checkpoint("context"),
      initial_relationships: []
    }
  );
  assert.equal(createOutcome.type, "success");

  const checkpointOutcome = await classify(
    request(
      "add_checkpoint",
      "POST",
      `/projects/${project}/work-items/${work}/checkpoints`,
      checkpointInput()
    ),
    201,
    checkpoint("context")
  );
  assert.equal(checkpointOutcome.type, "success");

  const eventOutcome = await classify(
    request(
      "append_event",
      "POST",
      `/projects/${project}/work-items/${work}/events`,
      {
        event_type: "progress",
        body: "Exact progress",
        actor
      }
    ),
    201,
    event()
  );
  assert.equal(eventOutcome.type, "success");
});

test("canonical lowercase commit responses resolve uppercase create, checkpoint, and completion intents", async () => {
  const uppercaseCommit = "ABCDEF1234567";
  const lowercaseCommit = uppercaseCommit.toLowerCase();
  const checkpointRequest = {
    ...checkpointInput(),
    verified_against: uppercaseCommit
  };
  const checkpointResponse = (kind, prompt = "Exact context") => checkpoint(
    kind,
    prompt,
    { verified_against: lowercaseCommit }
  );
  const cases = [
    [
      request("create_work", "POST", `/projects/${project}/work-items`, {
        title: "Durable objective",
        summary: "Keep this context",
        priority: 5,
        status: "pending",
        initial_checkpoint: checkpointRequest
      }),
      201,
      {
        work_item: workItem(),
        initial_checkpoint: checkpointResponse("context"),
        initial_relationships: []
      }
    ],
    [
      request(
        "add_checkpoint",
        "POST",
        `/projects/${project}/work-items/${work}/checkpoints`,
        { kind: "progress", ...checkpointRequest }
      ),
      201,
      checkpointResponse("progress")
    ],
    [
      request("complete_work", "POST", `/projects/${project}/work-items/${work}/complete`, {
        expected_version: 1,
        checkpoint: { ...checkpointRequest, prompt: "Completion summary" }
      }),
      200,
      {
        work_item: workItem({ status: "done", version: 2 }),
        checkpoint: checkpointResponse("completion", "Completion summary")
      }
    ]
  ];

  for (const [spec, status, value] of cases) {
    assert.equal((await classify(spec, status, value)).type, "success", spec.kind);
    assert.equal((await classify(spec, status, value)).type, "success", `${spec.kind} replay`);
  }
});

test("semantic metadata equality ignores object key order but preserves array order", async () => {
  const spec = request("append_event", "POST", `/projects/${project}/work-items/${work}/events`, {
    event_type: "progress",
    body: "Exact progress",
    metadata: { outer: { alpha: 1, beta: 2 }, sequence: [1, 2] },
    actor: { actor_client: "dashboard", actor_session_id: "tab-1" }
  });
  const reordered = await classify(spec, 201, event({
    sequence: [1, 2],
    outer: { beta: 2, alpha: 1 }
  }));
  assert.equal(reordered.type, "success");

  const changedArray = await classify(spec, 201, event({
    outer: { alpha: 1, beta: 2 },
    sequence: [2, 1]
  }));
  assert.equal(changedArray.type, "unresolved");
});

test("wrong statuses, extra fields, malformed bodies, and incoherent IDs stay unresolved", async () => {
  const spec = request("delete_work", "POST", `/projects/${project}/work-items/${work}/delete`, {
    expected_version: 1,
    actor: { actor_client: "dashboard", actor_session_id: "tab-1" }
  });
  const valid = { deleted: true, project_id: project, work_item_id: work, version: 2 };
  assert.equal((await classify(spec, 201, valid)).type, "unresolved");
  assert.equal((await classify(spec, 200, { ...valid, unexpected: true })).type, "unresolved");
  assert.equal((await classify(spec, 200, { ...valid, work_item_id: counterpart })).type, "unresolved");
  assert.equal((await classifyMutationResponse(
    spec,
    new Response("{broken", { status: 200 })
  )).type, "unresolved");
});

test("deferral resolves only from the canonical deferred work snapshot", async () => {
  const spec = request(
    "defer_work",
    "POST",
    `/projects/${project}/work-items/${work}/defer`,
    {
      expected_version: 1,
      actor: { actor_client: "dashboard", actor_session_id: "tab-1" }
    }
  );
  assert.equal((await classify(
    spec,
    200,
    workItem({ status: "pending", version: 2 })
  )).type, "unresolved");
  assert.equal((await classify(
    spec,
    200,
    workItem({ status: "deferred", version: 3 })
  )).type, "unresolved");
});

test("created work and live checkpoints reject impossible historical snapshots", async () => {
  const createSpec = request("create_work", "POST", `/projects/${project}/work-items`, {
    title: "Durable objective",
    summary: "Keep this context",
    priority: 5,
    status: "pending",
    initial_checkpoint: checkpointInput()
  });
  assert.equal((await classify(createSpec, 201, {
    work_item: workItem({ version: 2 }),
    initial_checkpoint: checkpoint("context"),
    initial_relationships: []
  })).type, "unresolved");

  const checkpointSpec = request(
    "add_checkpoint",
    "POST",
    `/projects/${project}/work-items/${work}/checkpoints`,
    { kind: "progress", ...checkpointInput("New context") }
  );
  for (const poisoned of [
    { migration_origin: "legacy-comment" },
    { legacy_record_id: "91c258a2-8b07-4f9e-a437-02133677cb20" }
  ]) {
    assert.equal((await classify(
      checkpointSpec,
      201,
      checkpoint("progress", "New context", poisoned)
    )).type, "unresolved");
  }
});

test("new relationship responses must preserve request provenance and context", async () => {
  const context = "555b506c-50ec-4664-9907-c993afd1237d";
  const spec = request("add_relationship", "POST", `/projects/${project}/relationships`, {
    relationship_type: "blocks",
    source_work_item_id: work,
    target_work_item_id: counterpart,
    created_by_client: "dashboard",
    created_by_session_id: "tab-1",
    created_by_model: null,
    context_checkpoint_id: context
  });
  const valid = relationship({
    context_checkpoint_work_item_id: work,
    context_checkpoint_id: context
  });
  assert.equal((await classify(spec, 200, {
    relationship: { ...valid, created_by_session_id: "wrong-session" },
    created: true
  })).type, "unresolved");
  assert.equal((await classify(spec, 200, {
    relationship: { ...valid, context_checkpoint_id: checkpointId },
    created: true
  })).type, "unresolved");
});

test("every shared definitive proxy rejection is classified without retaining the intent", async () => {
  const spec = request(
    "delete_work",
    "POST",
    "/projects/" + project + "/work-items/" + work + "/delete",
    {
      expected_version: 1,
      actor: { actor_client: "dashboard", actor_session_id: "tab-1" }
    }
  );
  for (const [name, error] of Object.entries(DEFINITIVE_PROXY_ERRORS)) {
    const outcome = await classify(spec, error.status, { detail: error.detail });
    assert.equal(outcome.type, "rejected", name);
  }

  const unsupported = unsupportedMutationFieldError("gate_id");
  assert.equal(
    (await classify(spec, unsupported.status, { detail: unsupported.detail })).type,
    "rejected"
  );
  for (const [status, detail] of [
    [400, "The request body contains an unsupported field: ."],
    [400, "The request body contains an unsupported field: gate_id"],
    [409, unsupported.detail],
    [400, "Client operation IDs are accepted only in supported JSON request bodies."]
  ]) {
    assert.equal((await classify(spec, status, { detail })).type, "unresolved", detail);
  }
});

test("finite error envelopes distinguish rejection, safety conflict, and unknown outcome", async () => {
  const spec = request("delete_work", "POST", `/projects/${project}/work-items/${work}/delete`, {
    expected_version: 1,
    actor: { actor_client: "dashboard", actor_session_id: "tab-1" }
  });
  const rejected = await classify(spec, 422, {
    detail: [{ type: "value_error", loc: ["body", "expected_version"], msg: "Value is invalid." }]
  });
  assert.equal(rejected.type, "rejected");
  const conflict = await classify(spec, 409, {
    detail: {
      code: "client_operation_conflict",
      message: "The operation ID is bound to another request.",
      context: {}
    }
  });
  assert.equal(conflict.type, "safety_conflict");
  const unavailable = await classify(spec, 503, {
    detail: {
      code: "client_operation_unavailable",
      message: "The operation receipt is unavailable.",
      context: {}
    }
  });
  assert.equal(unavailable.type, "unresolved");
  assert.equal(
    unavailable.message,
    "Mnemonic cannot verify the mutation outcome yet. Retry the same pending action."
  );
  const unknown = await classify(spec, 409, { detail: { widened: true } });
  assert.equal(unknown.type, "unresolved");
  const unknownCode = await classify(spec, 409, {
    detail: {
      code: "new_receipt_safety_code",
      message: "A future server knows more than this browser.",
      context: {}
    }
  });
  assert.equal(unknownCode.type, "unresolved");
  assert.equal((await classify(spec, 409, { detail: "Unknown old-style conflict." })).type, "unresolved");
  const versionConflict = await classify(spec, 409, {
    detail: {
      code: "version_conflict",
      message: "Recall the current work item.",
      context: {}
    }
  });
  assert.equal(versionConflict.type, "rejected");
  const workNotPending = await classify(spec, 409, {
    detail: {
      code: "work_not_pending",
      message: "Only pending work can perform this operation.",
      context: {}
    }
  });
  assert.equal(workNotPending.type, "rejected");
});

test("operation IDs are never accepted back in a success or error body", async () => {
  const spec = request("delete_work", "POST", `/projects/${project}/work-items/${work}/delete`, {
    expected_version: 1,
    actor: { actor_client: "dashboard", actor_session_id: "tab-1" }
  });
  for (const leaked of [operation, operation.toUpperCase()]) {
    const outcome = await classify(spec, 409, {
      detail: { code: "conflict", message: `Leaked ${leaked}`, context: {} }
    });
    assert.equal(outcome.type, "unresolved");
  }
});
