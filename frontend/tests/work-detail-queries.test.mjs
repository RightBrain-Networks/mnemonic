import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import {
  fetchFreshWorkContext, invalidateWorkDetailReads, workCheckpointsOptions,
  workContextOptions, workDetailKeys
} from "../lib/work-detail-queries.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const otherProject = "3e805646-8014-47df-9f53-27572bb1d85b";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const otherWork = "f1cf3691-7d28-4716-94a9-4867b341a685";
const checkpointId = "1dfa9455-4a17-4cd4-938b-010ea17ccaf0";
const createdAt = "2026-09-01T12:00:00Z";

function checkpoint(workId = work) {
  return {
    id: checkpointId, work_item_id: workId, kind: "context", prompt: "Exact current context.",
    source_client: "dashboard", source_session_id: "tab-1", source_model: null,
    source_session_url: null, repository_branch: null, verified_against: null, tags: [],
    source_metadata: {}, migration_origin: null, legacy_record_id: null, created_at: createdAt
  };
}

// Minimal canonical context using the wire fixture in duplicate-handling.test.mjs.
function context({ projectId = project, workId = work, version = 1 } = {}) {
  return {
    lease_settings: { default_minutes: 15, minimum_minutes: 10, maximum_minutes: 120 },
    artifacts: [], artifact_total: 0, omitted_artifact_count: 0,
    work_item: {
      id: workId, project_id: projectId, title: "Durable work", summary: `Summary revision ${version}.`,
      status: "pending", priority: 7, initial_checkpoint_id: checkpointId, version,
      created_at: createdAt, updated_at: createdAt
    },
    merge_review_revision: { work_version: version, context_checkpoint_id: checkpointId, work_event_count: 1 },
    canonical: {
      is_duplicate: false, direct_destination: null,
      canonical_work_item: { id: workId, title: "Durable work", status: "pending" },
      path: [], duplicate_member_count: 0
    },
    duplicate_members: [], duplicate_member_total: 0, omitted_duplicate_member_count: 0,
    initial_checkpoint: checkpoint(workId), current_context: null, current_context_is_initial: true,
    recent_checkpoints: [], checkpoint_total: 1, omitted_checkpoint_count: 0,
    readiness: {
      lifecycle_status: "pending", is_terminal: false, has_active_lease: false, has_dropped_lease: false,
      active_lease: null, unresolved_blocker_count: 0, is_blocked: false, unresolved_gate_count: 0,
      is_gated: false, is_duplicate: false, canonical_work_item_id: workId, is_ready: true, display_state: "pending"
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
}

function createClient(t) {
  const client = createDashboardQueryClient();
  const stops = [];
  t.after(() => { for (const stop of stops) stop(); client.clear(); });
  return {
    client,
    observe(options) {
      const observer = new QueryObserver(client, options);
      stops.push(observer.subscribe(() => {}));
      return observer;
    }
  };
}

test("context and checkpoint keys isolate project, work, request limits, and page offsets", () => {
  const contextKey = workContextOptions(project, work).queryKey;
  assert.deepEqual(contextKey.slice(0, 5), workDetailKeys.work(project, work));
  for (const other of [
    workContextOptions(otherProject, work), workContextOptions(project, otherWork),
    workContextOptions(project, work, 0, 10), workContextOptions(project, work, 5, 0)
  ]) assert.notDeepEqual(other.queryKey, contextKey);
  const checkpoints = workCheckpointsOptions(project, work, 20, 0).queryKey;
  for (const other of [
    workCheckpointsOptions(otherProject, work, 20, 0), workCheckpointsOptions(project, otherWork, 20, 0),
    workCheckpointsOptions(project, work, 100, 0), workCheckpointsOptions(project, work, 20, 20)
  ]) assert.notDeepEqual(other.queryKey, checkpoints);
});

test("fresh reconciliation cancels an older context request and caches only the newly decoded result", async (t) => {
  const { client } = createClient(t);
  const responses = [];
  t.mock.method(globalThis, "fetch", (url, init) => new Promise((resolve) => responses.push({ url, init, resolve })));
  const options = workContextOptions(project, work);
  const old = client.fetchQuery(options);
  const oldResult = Promise.allSettled([old]);
  const fresh = fetchFreshWorkContext(client, project, work);
  await setImmediate();
  assert.equal(responses.length, 2);
  assert.equal(responses[0].init.signal.aborted, true);
  assert.equal(responses[1].init.signal.aborted, false);
  assert.equal(responses[1].url, `/api/mnemonic/projects/${project}/work-items/${work}/context?recent_limit=5&recent_event_limit=10`);
  assert.equal((await oldResult)[0].status, "rejected");
  responses[1].resolve(Response.json(context({ version: 2 })));
  const loaded = await fresh;
  assert.equal(loaded.work_item.version, 2);
  assert.equal(loaded.initial_checkpoint.affected_paths.length, 0);
  responses[0].resolve(Response.json(context()));
  await setImmediate();
  assert.equal(client.getQueryData(options.queryKey), loaded);
});

test("fresh context reads bypass cache freshness while disabled selection observers only follow explicit loads", async (t) => {
  const { client, observe } = createClient(t);
  let version = 1;
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(context({ version })));
  const options = workContextOptions(project, work);
  await client.fetchQuery(options);
  const observer = observe({ ...options, enabled: false });
  await invalidateWorkDetailReads(client, project);
  assert.equal(fetch.mock.callCount(), 1);
  assert.equal(observer.getCurrentResult().data.work_item.version, 1);
  version = 2;
  const fresh = await fetchFreshWorkContext(client, project, work);
  assert.equal(fetch.mock.callCount(), 2);
  assert.equal(fresh.work_item.version, 2);
  assert.deepEqual(observer.getCurrentResult().data, fresh);
  // A second explicit reconciliation is fresh even within the normal staleTime.
  version = 3;
  assert.equal((await fetchFreshWorkContext(client, project, work)).work_item.version, 3);
  assert.equal(fetch.mock.callCount(), 3);
});

test("failed reconciliation rejects without converting retained context into success", async (t) => {
  const { client, observe } = createClient(t);
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(context()));
  const options = workContextOptions(project, work);
  const loaded = await client.fetchQuery(options);
  const observer = observe({ ...options, enabled: false });
  fetch.mock.mockImplementation(async () => Response.json({ detail: "Temporary read failure." }, { status: 503 }));
  await assert.rejects(fetchFreshWorkContext(client, project, work), /Temporary read failure/);
  assert.equal(client.getQueryData(options.queryKey), loaded);
  assert.equal(observer.getCurrentResult().isRefetchError, true);
  assert.equal(observer.getCurrentResult().data, loaded);
  fetch.mock.mockImplementation(async () => Response.json(context({ version: 2 })));
  assert.equal((await fetchFreshWorkContext(client, project, work)).work_item.version, 2);
  assert.equal(observer.getCurrentResult().isError, false);
});

test("context decoding rejects another project's work and incoherent revisions without replacing cache", async (t) => {
  const { client } = createClient(t);
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(context()));
  const options = workContextOptions(project, work);
  const loaded = await client.fetchQuery(options);
  for (const response of [
    context({ projectId: otherProject }), context({ workId: otherWork }),
    { ...context(), merge_review_revision: { work_version: 2, context_checkpoint_id: checkpointId, work_event_count: 1 } }
  ]) {
    fetch.mock.mockImplementation(async () => Response.json(response));
    await assert.rejects(fetchFreshWorkContext(client, project, work), /invalid|incoherent/);
    assert.equal(client.getQueryData(options.queryKey), loaded);
  }
});

test("checkpoint observers share a decoded request and cancel it when every observer leaves the exact scope", async (t) => {
  const { client, observe } = createClient(t);
  const requests = [];
  t.mock.method(globalThis, "fetch", (url, init) => new Promise((resolve) => requests.push({ url, init, resolve })));
  const oldOptions = workCheckpointsOptions(project, work, 20, 0);
  const first = observe(oldOptions);
  const second = observe(oldOptions);
  assert.equal(requests.length, 1);
  first.setOptions(workCheckpointsOptions(project, otherWork, 20, 0));
  assert.equal(requests[0].init.signal.aborted, false);
  second.setOptions(workCheckpointsOptions(project, otherWork, 20, 0));
  assert.equal(requests[0].init.signal.aborted, true);
  assert.equal(requests.length, 2);
  requests[1].resolve(Response.json({ items: [checkpoint(otherWork)], total: 1, limit: 20, offset: 0 }));
  await setImmediate();
  assert.equal(first.getCurrentResult().data.items[0].work_item_id, otherWork);
  assert.equal(first.getCurrentResult().data, second.getCurrentResult().data);
  requests[0].resolve(Response.json({ items: [checkpoint()], total: 1, limit: 20, offset: 0 }));
  await setImmediate();
  assert.equal(client.getQueryData(oldOptions.queryKey), undefined);
});

test("checkpoint refresh rejects wrong paging and ownership while retaining the verified page", async (t) => {
  const { client, observe } = createClient(t);
  const page = { items: [checkpoint()], total: 1, limit: 20, offset: 0 };
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(page));
  const options = workCheckpointsOptions(project, work, 20, 0);
  const loaded = await client.fetchQuery(options);
  const observer = observe(options);
  for (const response of [
    { ...page, limit: 100 }, { ...page, total: 21, offset: 20 },
    { ...page, items: [checkpoint(otherWork)] }
  ]) {
    fetch.mock.mockImplementation(async () => Response.json(response));
    const result = await observer.refetch();
    assert.equal(result.isRefetchError, true);
    assert.equal(result.data, loaded);
  }
  assert.equal(fetch.mock.calls[0].arguments[0], `/api/mnemonic/projects/${project}/work-items/${work}/checkpoints?order=newest&limit=20&offset=0`);
});
