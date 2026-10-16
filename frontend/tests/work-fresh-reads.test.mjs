import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { CancelledError, QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import {
  fetchFreshWorkContext, fetchFreshWorkSettings, workContextOptions, workSettingsOptions
} from "../lib/work-detail-queries.ts";

const project = "11111111-1111-4111-8111-111111111111";
const work = "22222222-2222-4222-8222-222222222222";
const checkpoint = "33333333-3333-4333-8333-333333333333";
const timestamp = "2026-09-01T12:00:00Z";
const freshContext = {
  lease_settings: { default_minutes: 15, minimum_minutes: 10, maximum_minutes: 120 },
  artifacts: [], artifact_total: 0, omitted_artifact_count: 0,
  work_item: {
    id: work, project_id: project, title: "Current work", summary: "Verified current transport.",
    status: "pending", priority: 7, initial_checkpoint_id: checkpoint, version: 2,
    created_at: timestamp, updated_at: timestamp
  },
  merge_review_revision: { work_version: 2, context_checkpoint_id: checkpoint, work_event_count: 1 },
  canonical: {
    is_duplicate: false, direct_destination: null,
    canonical_work_item: { id: work, title: "Current work", status: "pending" },
    path: [], duplicate_member_count: 0
  },
  duplicate_members: [], duplicate_member_total: 0, omitted_duplicate_member_count: 0,
  initial_checkpoint: {
    id: checkpoint, work_item_id: work, kind: "context", prompt: "Current context.",
    source_client: "dashboard", source_session_id: "tab-1", source_model: null,
    source_session_url: null, repository_branch: null, verified_against: null, tags: [],
    source_metadata: {}, migration_origin: null, legacy_record_id: null, created_at: timestamp
  },
  current_context: null, current_context_is_initial: true,
  recent_checkpoints: [], checkpoint_total: 1, omitted_checkpoint_count: 0,
  readiness: {
    lifecycle_status: "pending", is_terminal: false, has_active_lease: false, has_dropped_lease: false,
    active_lease: null, unresolved_blocker_count: 0, is_blocked: false, unresolved_gate_count: 0,
    is_gated: false, is_duplicate: false, canonical_work_item_id: work, is_ready: true, display_state: "pending"
  },
  unresolved_gates: [], unresolved_gate_total: 0, omitted_unresolved_gate_count: 0,
  recent_resolved_gates: [], resolved_gate_total: 0, omitted_resolved_gate_count: 0,
  incoming_relationships: [], outgoing_relationships: [], undirected_relationships: [],
  relationship_counts: { incoming: 0, outgoing: 0, undirected: 0, total: 0 },
  omitted_relationship_counts: { incoming: 0, outgoing: 0, undirected: 0, total: 0 },
  duplicate_merge_eligibility: {
    incident_blocks_count: 0, incident_parent_child_count: 0, has_unresolved_gate: false, source_lease_state: "none"
  },
  recent_events: [], event_total: 1, omitted_event_count: 1, pre_phase5_history_may_be_incomplete: false
};
const freshPolicy = {
  project_id: project, revision: "4", recall_pointer_template: null,
  job_completion_report_prompt: "Verified current instructions.",
  code_review_required_min_priority: 100, code_review_optional_min_priority: 100,
  allow_remediation_code_reviews: false,
  lease_default_minutes: 15, lease_minimum_minutes: 10, lease_maximum_minutes: 120
};

for (const [name, optionsFor, fetchFresh, response] of [
  ["context", workContextOptions, fetchFreshWorkContext, freshContext],
  ["policy", workSettingsOptions, fetchFreshWorkSettings, freshPolicy]
]) {
  test(`simultaneous fresh ${name} reads let the newest request verify its own transport`, async (t) => {
    const client = createDashboardQueryClient();
    t.after(() => client.clear());
    const requests = [];
    t.mock.method(globalThis, "fetch", (_url, init) => new Promise((resolve) => {
      requests.push({ signal: init.signal, resolve });
    }));
    // Start both calls in one turn to expose a gap between cancellation and dispatch.
    const older = fetchFresh(client, project, work);
    const newest = fetchFresh(client, project, work);
    const results = Promise.allSettled([older, newest]);
    await setImmediate();
    assert.equal(requests.filter((request) => !request.signal.aborted).length, 1);
    for (const request of requests) request.resolve(Response.json(response));
    const [olderResult, newestResult] = await results;
    assert.equal(newestResult.status, "fulfilled",
      "the newest explicit verification must not fail after joining the older request");
    assert.deepEqual(newestResult.value, client.getQueryData(optionsFor(project, work).queryKey));
    assert.equal(name === "context" ? newestResult.value.work_item.version : newestResult.value.revision,
      name === "context" ? 2 : "4");
    assert.equal(olderResult.status, "rejected", "the superseded caller must not publish a verified result");
    assert.ok(olderResult.reason instanceof CancelledError);
  });

  test(`cancelled fresh ${name} reads reject retained cache after a disabled observer detaches`, async (t) => {
    const client = createDashboardQueryClient();
    const options = optionsFor(project, work);
    const retained = { retained: name, work_item: { id: work, project_id: project } };
    client.setQueryData(options.queryKey, retained);
    const observer = new QueryObserver(client, { ...options, enabled: false });
    const stop = observer.subscribe(() => {});
    t.after(() => { stop(); client.clear(); });
    let signal;
    const held = Promise.withResolvers();
    t.mock.method(globalThis, "fetch", (_url, init) => { signal = init.signal; return held.promise; });
    const pending = fetchFresh(client, project, work);
    const rejected = assert.rejects(pending, CancelledError);
    await setImmediate();
    assert.equal(signal.aborted, false);
    observer.setOptions({ queryKey: ["mnemonic", "disabled", name], enabled: false });
    assert.equal(signal.aborted, true);
    await rejected;
    assert.equal(client.getQueryData(options.queryKey), retained,
      "read-only display cache survives without becoming fresh verification");
    held.resolve(Response.json({ malformed: "late response must be ignored" }));
    await setImmediate();
    assert.equal(client.getQueryData(options.queryKey), retained);
  });

  test(`fresh ${name} helper rejects a cached result when its own transport never ran`, async (t) => {
    const client = createDashboardQueryClient();
    t.after(() => client.clear());
    t.mock.method(client, "fetchQuery", async () => ({ retained: name }));
    await assert.rejects(fetchFresh(client, project, work), CancelledError);
  });
}
