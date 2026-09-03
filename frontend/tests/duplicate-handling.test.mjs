import assert from "node:assert/strict";
import test from "node:test";
import {
  dashboardMergeInput,
  decodeCanonicalWorkProjection,
  decodeMergeReviewRevision,
  decodeWorkContext,
  decodeWorkItemDetail,
  decodeWorkSearchPage,
  duplicateMergeEligibilityReasons,
  mergeWorkPath
} from "../lib/duplicate-handling.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const destination = "f1cf3691-7d28-4716-94a9-4867b341a685";
const root = "11111111-1111-4111-8111-111111111111";
const checkpointId = "1dfa9455-4a17-4cd4-938b-010ea17ccaf0";
const createdAt = "2026-09-01T12:00:00Z";

function pointer(id = work, title = "Durable work", status = "pending") {
  return { id, title, status };
}

function workItem(id = work, overrides = {}) {
  return {
    id,
    project_id: project,
    title: "Durable work",
    summary: "Exact durable summary.",
    status: "pending",
    priority: 7,
    initial_checkpoint_id: checkpointId,
    version: 1,
    created_at: createdAt,
    updated_at: createdAt,
    ...overrides
  };
}

function checkpoint(id = work) {
  return {
    id: checkpointId,
    work_item_id: id,
    kind: "context",
    prompt: "Exact current context.",
    source_client: "dashboard",
    source_session_id: "tab-1",
    source_model: null,
    source_session_url: null,
    repository_branch: null,
    verified_against: null,
    tags: [],
    source_metadata: {},
    migration_origin: null,
    legacy_record_id: null,
    created_at: createdAt
  };
}

function checkpointPointer(id = work) {
  const { prompt: ignoredPrompt, source_session_url: ignoredUrl, source_metadata: ignoredMetadata, ...value } = checkpoint(id);
  return value;
}

function readiness(id = work, overrides = {}) {
  return {
    lifecycle_status: "pending",
    is_terminal: false,
    has_active_lease: false,
    has_dropped_lease: false,
    active_lease: null,
    unresolved_blocker_count: 0,
    is_blocked: false,
    unresolved_gate_count: 0,
    is_gated: false,
    is_duplicate: false,
    canonical_work_item_id: id,
    is_ready: true,
    display_state: "pending",
    ...overrides
  };
}

function projection(id = work) {
  return {
    is_duplicate: false,
    direct_destination: null,
    canonical_work_item: pointer(id),
    path: [],
    duplicate_member_count: 0
  };
}

function summary(id = work, overrides = {}) {
  return {
    work_item: workItem(id),
    checkpoint_count: 1,
    ancestor_path: [],
    ancestor_path_truncated: false,
    current_context: checkpointPointer(id),
    readiness: readiness(id),
    ...overrides
  };
}

function context(id = work) {
  return {
    work_item: workItem(id),
    merge_review_revision: {
      work_version: 1,
      context_checkpoint_id: checkpointId,
      work_event_count: 1
    },
    canonical: projection(id),
    duplicate_members: [],
    duplicate_member_total: 0,
    omitted_duplicate_member_count: 0,
    initial_checkpoint: checkpoint(id),
    current_context: null,
    current_context_is_initial: true,
    recent_checkpoints: [],
    checkpoint_total: 1,
    omitted_checkpoint_count: 0,
    readiness: readiness(id),
    unresolved_gates: [],
    unresolved_gate_total: 0,
    omitted_unresolved_gate_count: 0,
    recent_resolved_gates: [],
    resolved_gate_total: 0,
    omitted_resolved_gate_count: 0,
    incoming_relationships: [],
    outgoing_relationships: [],
    undirected_relationships: [],
    relationship_counts: { incoming: 0, outgoing: 0, undirected: 0, total: 0 },
    omitted_relationship_counts: { incoming: 0, outgoing: 0, undirected: 0, total: 0 },
    duplicate_merge_eligibility: {
      incident_blocks_count: 0,
      incident_parent_child_count: 0,
      has_unresolved_gate: false,
      source_lease_state: "none"
    },
    recent_events: [],
    event_total: 1,
    omitted_event_count: 1,
    pre_phase5_history_may_be_incomplete: false
  };
}

function progressEvent(id, created_at) {
  return {
    id,
    project_id: project,
    work_item_id: work,
    event_type: "progress",
    actor_kind: "client",
    actor_client: "dashboard",
    actor_session_id: "tab-1",
    actor_model: null,
    body: "Exact progress.",
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
    metadata: {},
    origin: "live",
    created_at
  };
}

function incomingRelationship(id, counterpartId, created_at = createdAt) {
  return {
    relationship: {
      id,
      project_id: project,
      relationship_type: "blocks",
      source_work_item_id: counterpartId,
      target_work_item_id: work,
      context_checkpoint_work_item_id: null,
      context_checkpoint_id: null,
      created_by_client: "dashboard",
      created_by_session_id: "tab-1",
      created_by_model: null,
      created_at
    },
    relative_to_work_item_id: work,
    direction: "incoming",
    counterpart: {
      ...pointer(counterpartId),
      readiness: readiness(counterpartId)
    }
  };
}

function aliasContext() {
  const value = context(work);
  value.canonical = {
    is_duplicate: true,
    direct_destination: pointer(destination, "Intermediate destination"),
    canonical_work_item: pointer(root, "Current canonical root"),
    path: [
      pointer(destination, "Intermediate destination"),
      pointer(root, "Current canonical root")
    ],
    duplicate_member_count: 2
  };
  value.duplicate_members = [
    pointer(work, "Durable work"),
    pointer(destination, "Intermediate destination")
  ];
  value.duplicate_member_total = 2;
  value.readiness = readiness(work, {
    is_duplicate: true,
    canonical_work_item_id: root,
    is_ready: false,
    display_state: "duplicate"
  });
  return value;
}

test("canonical projections preserve exact direction and reject ambiguous or corrupt paths", () => {
  assert.deepEqual(
    decodeCanonicalWorkProjection(projection(), workItem()),
    projection()
  );
  const alias = aliasContext();
  assert.deepEqual(
    decodeCanonicalWorkProjection(alias.canonical, alias.work_item).path.map((item) => item.id),
    [destination, root]
  );
  assert.throws(() => decodeCanonicalWorkProjection({ ...projection(), extra: true }, workItem()), /invalid canonical/);
  assert.throws(() => decodeCanonicalWorkProjection({
    ...alias.canonical,
    direct_destination: pointer(root, "Current canonical root")
  }, alias.work_item), /incoherent canonical/);
  assert.throws(() => decodeCanonicalWorkProjection({
    ...alias.canonical,
    path: [...alias.canonical.path, pointer(destination, "Intermediate destination")]
  }, alias.work_item), /invalid canonical work path/);
  assert.throws(() => decodeCanonicalWorkProjection({
    ...alias.canonical,
    path: Array.from({ length: 51 }, (_, index) => pointer(
      `${String(index).padStart(8, "0")}-1111-4111-8111-111111111111`
    ))
  }, alias.work_item), /invalid canonical/);
});

test("direct detail remains a strict wrapper without widening the receipt-safe work item", () => {
  const decoded = decodeWorkItemDetail({ work_item: workItem(), canonical: projection() }, project, work);
  assert.equal(decoded.work_item.id, work);
  assert.equal(decoded.canonical.canonical_work_item.id, work);
  assert.throws(() => decodeWorkItemDetail(workItem(), project, work), /invalid work-item detail/);
  assert.throws(() => decodeWorkItemDetail({
    work_item: { ...workItem(), canonical_work_item_id: work },
    canonical: projection()
  }, project, work), /invalid work-item detail/);
});

test("search guards enforce canonical, alias, group, and matched-member modes", () => {
  const canonicalHit = {
    summary: summary(),
    matched_member: pointer(destination, "Matching immutable member")
  };
  const page = { items: [canonicalHit], total: 1, limit: 20, offset: 0 };
  assert.equal(decodeWorkSearchPage(page, project, {
    duplicateScope: "canonical",
    query: "member",
    expectedLimit: 20,
    expectedOffset: 0
  }).items[0].matched_member.id, destination);
  assert.throws(() => decodeWorkSearchPage(page, project, {
    duplicateScope: "canonical",
    query: ""
  }), /incoherent work search hit/);

  const alias = aliasContext();
  const aliasSummary = summary(work, {
    readiness: alias.readiness
  });
  const aliasPage = {
    items: [{ summary: aliasSummary, matched_member: pointer(work) }],
    total: 1,
    limit: 20,
    offset: 0
  };
  assert.equal(decodeWorkSearchPage(aliasPage, project, {
    duplicateScope: "aliases",
    canonicalWorkItemId: root,
    query: ""
  }).items[0].summary.readiness.display_state, "duplicate");
  assert.throws(() => decodeWorkSearchPage({
    ...aliasPage,
    items: [{ ...aliasPage.items[0], matched_member: pointer(destination) }]
  }, project, {
    duplicateScope: "aliases",
    canonicalWorkItemId: root,
    query: "work"
  }), /incoherent work search hit/);
  assert.throws(() => decodeWorkSearchPage({
    ...aliasPage,
    items: [aliasPage.items[0], aliasPage.items[0]],
    total: 2
  }, project, { duplicateScope: "aliases", query: "work" }), /repeated work search hits/);
  assert.throws(() => decodeWorkSearchPage({
    ...aliasPage,
    total: 5,
    offset: 10
  }, project, { duplicateScope: "aliases", query: "work" }), /invalid work search page/);
  assert.throws(() => decodeWorkSearchPage(aliasPage, project, {
    duplicateScope: "canonical",
    query: "work"
  }), /incoherent work search hit/);
  assert.throws(() => decodeWorkSearchPage({ ...aliasPage, private_fk: root }, project, {
    duplicateScope: "aliases"
  }), /invalid work search page/);
});

test("bounded contexts bind revisions, canonical facts, omissions, eligibility, and alias ordering", () => {
  assert.equal(decodeWorkContext(context(), project, work).readiness.is_ready, true);
  assert.equal(decodeWorkContext(aliasContext(), project, work).duplicate_members[0].id, work);
  const invalidCases = [
    { merge_review_revision: { ...context().merge_review_revision, work_event_count: 2 } },
    { omitted_checkpoint_count: 1 },
    { relationship_counts: { incoming: 1, outgoing: 0, undirected: 0, total: 1 } },
    { omitted_relationship_counts: { incoming: 1, outgoing: 0, undirected: 0, total: 1 } },
    { duplicate_merge_eligibility: { ...context().duplicate_merge_eligibility, source_lease_state: "active" } },
    { event_total: 2 },
    { private_work_duplicate_merge_id: root }
  ];
  for (const override of invalidCases) {
    assert.throws(() => decodeWorkContext({ ...context(), ...override }, project, work));
  }
  const misordered = aliasContext();
  misordered.duplicate_members.reverse();
  assert.throws(() => decodeWorkContext(misordered, project, work), /duplicate member slice/);

  assert.throws(() => decodeWorkContext({
    ...context(),
    recent_checkpoints: Array.from({ length: 21 }, () => checkpoint())
  }, project, work), /invalid checkpoint slice/);
  assert.throws(() => decodeWorkContext({
    ...context(),
    recent_events: Array.from({ length: 21 }, () => progressEvent(1, createdAt))
  }, project, work), /invalid event slice/);
});

test("bounded context events preserve the API's chronological newest slice", () => {
  const value = context();
  value.recent_events = [
    progressEvent(8, "2026-09-01T12:00:00Z"),
    progressEvent(7, "2026-09-01T12:00:00.000001Z")
  ];
  value.event_total = 2;
  value.omitted_event_count = 0;
  value.merge_review_revision.work_event_count = 2;
  assert.deepEqual(decodeWorkContext(value, project, work).recent_events.map((event) => event.id), [8, 7]);

  value.recent_events.reverse();
  assert.throws(() => decodeWorkContext(value, project, work), /incoherent event slice/);
});

test("bounded context relationships preserve canonical timestamp and UUID order", () => {
  const lowerId = "22222222-2222-4222-8222-222222222222";
  const higherId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
  const value = context();
  value.incoming_relationships = [
    incomingRelationship(lowerId, destination),
    incomingRelationship(higherId, root)
  ];
  value.relationship_counts = { incoming: 2, outgoing: 0, undirected: 0, total: 2 };
  value.duplicate_merge_eligibility.incident_blocks_count = 2;
  assert.equal(decodeWorkContext(value, project, work).incoming_relationships.length, 2);

  value.incoming_relationships.reverse();
  assert.throws(() => decodeWorkContext(value, project, work), /relationship omissions/);
});

test("merge review input is exact, retains rationale bytes, and never carries a lease token", () => {
  const sourceRevision = decodeMergeReviewRevision({
    work_version: 3,
    context_checkpoint_id: checkpointId,
    work_event_count: 7
  });
  const destinationRevision = decodeMergeReviewRevision({
    work_version: 5,
    context_checkpoint_id: root,
    work_event_count: 9
  });
  const rationale = "  Exact rationale — العربية \u200B \u202E  ";
  const input = dashboardMergeInput(
    destination,
    sourceRevision,
    destinationRevision,
    rationale,
    "tab-1"
  );
  assert.equal(input.rationale, rationale);
  assert.equal("lease_token" in input, false);
  assert.deepEqual(Object.keys(input), [
    "destination_work_item_id",
    "reviewed_source_revision",
    "reviewed_destination_revision",
    "rationale",
    "merged_by_client",
    "merged_by_session_id",
    "merged_by_model"
  ]);
  assert.equal(mergeWorkPath(project, work), `/projects/${project}/work-items/${work}/merge`);
  assert.throws(() => decodeMergeReviewRevision({ ...sourceRevision, extra: true }), /invalid merge review/);
  assert.throws(() => dashboardMergeInput(
    destination,
    { ...sourceRevision, work_event_count: 0 },
    destinationRevision,
    "Rationale",
    "tab-1"
  ), /invalid merge review/);
  assert.throws(() => dashboardMergeInput(destination, sourceRevision, destinationRevision, "   ", "tab-1"), /incomplete/);
});

test("browser merge eligibility reports every source-only blocker including active lease guidance", () => {
  assert.deepEqual(duplicateMergeEligibilityReasons({
    incident_blocks_count: 2,
    incident_parent_child_count: 1,
    has_unresolved_gate: true,
    source_lease_state: "active"
  }), [
    "Reconcile 2 incident blocker relationships.",
    "Reconcile 1 incident parent/child relationship.",
    "Resolve the source’s unresolved human question before rereading both contexts.",
    "Release the source’s active lease, or wait for it to expire, before merging in the browser."
  ]);
  assert.deepEqual(duplicateMergeEligibilityReasons({
    incident_blocks_count: 0,
    incident_parent_child_count: 0,
    has_unresolved_gate: false,
    source_lease_state: "expired"
  }), []);
});
