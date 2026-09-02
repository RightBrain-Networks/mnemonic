import assert from "node:assert/strict";
import test from "node:test";
import {
  decodeHumanAttentionPage,
  decodeHumanGate,
  decodeHumanGatePage,
  humanAttentionSearchParams,
  humanGateChangedLabels,
  humanGateHistorySearchParams,
  humanGatePath,
  hasCompleteRelationshipReview,
  sameHumanGateRevision
} from "../lib/human-gates.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const gateId = "f1cf3691-7d28-4716-94a9-4867b341a685";
const checkpoint = "1dfa9455-4a17-4cd4-938b-010ea17ccaf0";
const nextCheckpoint = "26a3a437-0af3-405a-ab82-7932d17869e0";
const incomingWork = "11111111-1111-4111-8111-111111111111";
const outgoingWork = "22222222-2222-4222-8222-222222222222";
const relatedWork = "33333333-3333-4333-8333-333333333333";
const incomingRelationship = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const outgoingRelationship = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const relatedRelationship = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const createdAt = "2026-09-01T12:00:00Z";

function revision(overrides = {}) {
  return {
    work_version: 2,
    context_checkpoint_id: checkpoint,
    relationship_event_count: 3,
    ...overrides
  };
}

function gate(overrides = {}) {
  return {
    id: gateId,
    project_id: project,
    work_item_id: work,
    gate_type: "human",
    question: "<script>render this literally</script>",
    requested_by_client: "claude-code",
    requested_by_session_id: "session-1",
    requested_by_model: "model-1",
    requested_work_version: 2,
    requested_context_checkpoint_id: checkpoint,
    requested_relationship_event_count: 3,
    created_at: createdAt,
    status: "unresolved",
    current_context_revision: revision(),
    work_changed_since_request: false,
    context_checkpoint_changed_since_request: false,
    relationships_changed_since_request: false,
    context_changed_since_request: false,
    resolved_at: null,
    resolution: null,
    resolved_by_client: null,
    resolved_by_session_id: null,
    resolved_by_model: null,
    resolved_context_revision: null,
    context_changed_at_resolution: null,
    context_change_acknowledged: null,
    ...overrides
  };
}

function resolvedGate(overrides = {}) {
  return gate({
    status: "resolved",
    resolved_at: "2026-09-01T13:00:00Z",
    resolution: "Proceed only after a fresh policy check.",
    resolved_by_client: "dashboard",
    resolved_by_session_id: "dashboard-tab",
    resolved_by_model: null,
    resolved_context_revision: revision(),
    context_changed_at_resolution: false,
    context_change_acknowledged: false,
    ...overrides
  });
}

function summary() {
  return {
    work_item: {
      id: work,
      project_id: project,
      title: "Durable work",
      summary: "Summary",
      status: "pending",
      priority: 7,
      initial_checkpoint_id: checkpoint,
      version: 2,
      created_at: createdAt,
      updated_at: createdAt
    },
    checkpoint_count: 1,
    ancestor_path: [],
    ancestor_path_truncated: false,
    current_context: {
      id: checkpoint,
      work_item_id: work,
      kind: "context",
      source_client: "dashboard",
      source_session_id: "tab",
      source_model: null,
      repository_branch: null,
      verified_against: null,
      tags: [],
      migration_origin: null,
      legacy_record_id: null,
      created_at: createdAt
    },
    readiness: {
      lifecycle_status: "pending",
      is_terminal: false,
      has_active_lease: false,
      has_dropped_lease: false,
      active_lease: null,
      unresolved_blocker_count: 0,
      is_blocked: false,
      unresolved_gate_count: 1,
      is_gated: true,
      is_ready: false,
      display_state: "waiting"
    }
  };
}

function reviewedRelationship({
  id,
  relationshipType,
  sourceWorkItemId,
  targetWorkItemId,
  direction,
  counterpartId,
  projectId = project
}) {
  return {
    relationship: {
      id,
      project_id: projectId,
      relationship_type: relationshipType,
      source_work_item_id: sourceWorkItemId,
      target_work_item_id: targetWorkItemId
    },
    relative_to_work_item_id: work,
    direction,
    counterpart: { id: counterpartId }
  };
}

test("strict gate decoding preserves literal text and enforces scope and nullability", () => {
  const decoded = decodeHumanGate(gate(), {
    projectId: project,
    workItemId: work,
    gateId,
    status: "unresolved"
  });
  assert.equal(decoded.question, "<script>render this literally</script>");
  assert.throws(() => decodeHumanGate({ ...gate(), extra: true }), /invalid human gate/);
  assert.throws(() => decodeHumanGate(gate({ resolution: "forged" })), /incoherent unresolved/);
  assert.throws(() => decodeHumanGate(gate(), { workItemId: gateId }), /invalid human gate/);
  assert.throws(() => decodeHumanGate(gate({ created_at: "2026-02-30T12:00:00Z" })), /invalid human gate/);
});

test("current and resolved drift booleans must match their exact revision tuples", () => {
  const current = revision({
    work_version: 4,
    context_checkpoint_id: nextCheckpoint,
    relationship_event_count: 8
  });
  const drifted = gate({
    current_context_revision: current,
    work_changed_since_request: true,
    context_checkpoint_changed_since_request: true,
    relationships_changed_since_request: true,
    context_changed_since_request: true
  });
  assert.deepEqual(humanGateChangedLabels(decodeHumanGate(drifted)), [
    "work fields", "current context checkpoint", "relationships"
  ]);
  assert.throws(() => decodeHumanGate({ ...drifted, relationships_changed_since_request: false }), /incoherent human gate/);

  const resolved = decodeHumanGate(resolvedGate({
    current_context_revision: current,
    work_changed_since_request: true,
    context_checkpoint_changed_since_request: true,
    relationships_changed_since_request: true,
    context_changed_since_request: true,
    resolved_context_revision: current,
    context_changed_at_resolution: true,
    context_change_acknowledged: true
  }));
  assert.equal(sameHumanGateRevision(resolved.current_context_revision, resolved.resolved_context_revision), true);
  assert.throws(() => decodeHumanGate(resolvedGate({
    resolved_context_revision: revision({ work_version: 3 }),
    context_changed_at_resolution: false,
    context_change_acknowledged: false
  })), /incoherent resolved/);
});

test("attention pages are scope coherent and limit zero transmits no gate text", () => {
  const count = decodeHumanAttentionPage({
    items: [], total: 4, limit: 0, next_cursor: null
  }, project, { limit: 0 });
  assert.deepEqual(count, { items: [], total: 4, limit: 0, next_cursor: null });
  assert.throws(() => decodeHumanAttentionPage({
    items: [{ gate: gate(), summary: summary() }],
    total: 4,
    limit: 0,
    next_cursor: null
  }, project, { limit: 0 }), /invalid cursor page/);

  const page = decodeHumanAttentionPage({
    items: [{ gate: gate(), summary: summary() }],
    total: 1,
    limit: 30,
    next_cursor: "opaque-cursor"
  }, project, { workItemId: work, limit: 30 });
  assert.equal(page.items[0].gate.id, gateId);
  assert.equal(page.items[0].summary.readiness.display_state, "waiting");
  assert.throws(() => decodeHumanAttentionPage({
    items: [{ gate: gate(), summary: {
      ...summary(),
      readiness: { ...summary().readiness, display_state: "blocked" }
    } }],
    total: 1,
    limit: 30,
    next_cursor: null
  }, project, { limit: 30 }), /incoherent attention readiness/);
  assert.throws(() => decodeHumanAttentionPage({
    items: [{ gate: gate(), summary: summary() }],
    total: 0,
    limit: 30,
    next_cursor: null
  }, project, { limit: 30 }), /invalid cursor page/);
  assert.throws(() => decodeHumanAttentionPage({
    items: [{ gate: gate({ status: "resolved" }), summary: summary() }],
    total: 1,
    limit: 30,
    next_cursor: null
  }, project, { limit: 30 }));
});

test("relationship drift review is complete only when every directional count is materialized", () => {
  const incoming = reviewedRelationship({
    id: incomingRelationship,
    relationshipType: "blocks",
    sourceWorkItemId: incomingWork,
    targetWorkItemId: work,
    direction: "incoming",
    counterpartId: incomingWork
  });
  const outgoing = reviewedRelationship({
    id: outgoingRelationship,
    relationshipType: "blocks",
    sourceWorkItemId: work,
    targetWorkItemId: outgoingWork,
    direction: "outgoing",
    counterpartId: outgoingWork
  });
  const undirected = reviewedRelationship({
    id: relatedRelationship,
    relationshipType: "related",
    sourceWorkItemId: work,
    targetWorkItemId: relatedWork,
    direction: "undirected",
    counterpartId: relatedWork
  });
  const complete = {
    work_item: { id: work, project_id: project },
    incoming_relationships: [incoming],
    outgoing_relationships: [outgoing],
    undirected_relationships: [undirected],
    relationship_counts: { incoming: 1, outgoing: 1, undirected: 1, total: 3 }
  };
  assert.equal(hasCompleteRelationshipReview(complete), true);
  assert.equal(hasCompleteRelationshipReview({
    ...complete,
    undirected_relationships: [],
    relationship_counts: { incoming: 1, outgoing: 1, undirected: 1, total: 3 }
  }), false);
  assert.equal(hasCompleteRelationshipReview({
    ...complete,
    relationship_counts: { incoming: 0, outgoing: 2, undirected: 1, total: 3 }
  }), false);
  assert.equal(hasCompleteRelationshipReview({
    ...complete,
    incoming_relationships: [{
      ...incoming,
      relationship: { ...incoming.relationship, project_id: outgoingWork }
    }]
  }), false);
  assert.equal(hasCompleteRelationshipReview({
    ...complete,
    incoming_relationships: [{ ...incoming, direction: "outgoing" }]
  }), false);
  assert.equal(hasCompleteRelationshipReview({
    ...complete,
    incoming_relationships: [{ ...incoming, counterpart: { id: outgoingWork } }]
  }), false);
  assert.equal(hasCompleteRelationshipReview({
    ...complete,
    outgoing_relationships: [{
      ...outgoing,
      relationship: { ...outgoing.relationship, id: incomingRelationship }
    }]
  }), false);
});

test("paired history and cursor helpers preserve endpoint-specific filters", () => {
  const page = decodeHumanGatePage({
    items: [resolvedGate(), gate()],
    total: 2,
    limit: 30,
    next_cursor: null
  }, project, work, { status: "all", limit: 30 });
  assert.deepEqual(page.items.map((item) => item.status), ["resolved", "unresolved"]);
  assert.throws(() => decodeHumanGatePage({
    items: [gate()], total: 1, limit: 30, next_cursor: null
  }, project, work, { status: "resolved", limit: 30 }));

  assert.equal(
    humanAttentionSearchParams({ workItemId: work, limit: 30, cursor: "next" }).toString(),
    `limit=30&work_item_id=${work}&cursor=next`
  );
  assert.equal(
    humanGateHistorySearchParams({ status: "resolved", limit: 20, cursor: "older" }).toString(),
    "status=resolved&limit=20&cursor=older"
  );
  assert.equal(
    humanGatePath(project, work, gateId),
    `/projects/${project}/work-items/${work}/gates/${gateId}`
  );
});
