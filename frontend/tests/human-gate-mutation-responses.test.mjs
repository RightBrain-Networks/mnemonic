import assert from "node:assert/strict";
import test from "node:test";
import { classifyMutationResponse } from "../lib/mutation-responses.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const gateId = "f1cf3691-7d28-4716-94a9-4867b341a685";
const checkpoint = "1dfa9455-4a17-4cd4-938b-010ea17ccaf0";
const operation = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";

function revision(overrides = {}) {
  return {
    work_version: 2,
    context_checkpoint_id: checkpoint,
    relationship_event_count: 3,
    ...overrides
  };
}

function response(overrides = {}) {
  return {
    id: gateId,
    project_id: project,
    work_item_id: work,
    gate_type: "human",
    question: "Which option should continue?",
    requested_by_client: "claude-code",
    requested_by_session_id: "agent-session",
    requested_by_model: "model",
    requested_context_revision: revision(),
    created_at: "2026-09-01T12:00:00Z",
    status: "resolved",
    current_context_revision: revision(),
    work_changed_since_request: false,
    context_checkpoint_changed_since_request: false,
    relationships_changed_since_request: false,
    context_changed_since_request: false,
    resolved_at: "2026-09-01T13:00:00Z",
    resolution: "Choose option B.",
    resolved_by_client: "dashboard",
    resolved_by_session_id: "dashboard-tab",
    resolved_by_model: null,
    resolved_context_revision: revision(),
    context_changed_at_resolution: false,
    ...overrides
  };
}

function request(payload = {}) {
  return {
    kind: "resolve_human_input",
    method: "POST",
    path: `/projects/${project}/work-items/${work}/gates/${gateId}/resolve`,
    operationId: operation,
    body: JSON.stringify({
      resolution: "Choose option B.",
      resolved_by_client: "dashboard",
      resolved_by_session_id: "dashboard-tab",
      resolved_by_model: null,
      reviewed_context_revision: revision(),
      ...payload,
      client_operation_id: operation
    })
  };
}

async function classify(spec, status, body) {
  return classifyMutationResponse(spec, new Response(JSON.stringify(body), { status }));
}

test("resolution success binds path, answer, dashboard provenance, and unchanged revision", async () => {
  const outcome = await classify(request(), 200, response());
  assert.equal(outcome.type, "success");
  assert.equal(outcome.value.resolution, "Choose option B.");
  for (const poisoned of [
    response({ id: checkpoint }),
    response({ work_item_id: gateId }),
    response({ resolution: "Different answer" }),
    response({ resolved_by_session_id: "another-tab" }),
    response({ status: "unresolved", resolved_at: null, resolution: null })
  ]) assert.equal((await classify(request(), 200, poisoned)).type, "unresolved");
});

test("reviewed resolution accepts only the exact reviewed and resolved revision", async () => {
  const reviewed = revision({ work_version: 3, relationship_event_count: 4 });
  const spec = request({
    reviewed_context_revision: reviewed
  });
  const value = response({
    current_context_revision: reviewed,
    work_changed_since_request: true,
    relationships_changed_since_request: true,
    context_changed_since_request: true,
    resolved_context_revision: reviewed,
    context_changed_at_resolution: true,
  });
  assert.equal((await classify(spec, 200, value)).type, "success");
  assert.equal((await classify(request({
    reviewed_context_revision: undefined
  }), 200, response())).type, "unresolved");
  assert.equal((await classify(request({
    reviewed_context_revision: { ...reviewed, extra: true }
  }), 200, value)).type, "unresolved");
  assert.equal((await classify(spec, 200, {
    ...value,
    resolved_context_revision: revision({ work_version: 4, relationship_event_count: 4 })
  })).type, "unresolved");
  assert.equal((await classify(request({
    reviewed_context_revision: revision({ work_version: 4, relationship_event_count: 4 })
  }), 200, value)).type, "unresolved");
});

test("changed-context rejection is definite while malformed success remains retryable", async () => {
  const rejected = await classify(request(), 409, {
    detail: {
      code: "gate_context_changed",
      message: "Review current work context.",
      context: {}
    }
  });
  assert.equal(rejected.type, "rejected");
  assert.equal(rejected.error.code, "gate_context_changed");
  assert.equal((await classify(request(), 200, { malformed: true })).type, "unresolved");
  assert.equal((await classify(request(), 502, { detail: "lost" })).type, "unresolved");
});
