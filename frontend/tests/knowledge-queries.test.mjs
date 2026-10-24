import assert from "node:assert/strict";
import test from "node:test";
import { QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import { decisionChangeOptions, invalidateKnowledgeReads, knowledgeKeys } from "../lib/knowledge-queries.ts";
import { queryKeys } from "../lib/query-keys.ts";

const source = "10000000-0000-4000-8000-000000000001";
const destination = "10000000-0000-4000-8000-000000000002";
const identity = "20000000-0000-4000-8000-000000000001";
const now = "2026-10-24T12:00:00Z";
const group = (projectId, version) => ({
  id: identity, project_id: projectId, origin_reference: "ADR-00001-1",
  review_work_item_id: null, review_work_project_id: null, review_closed: false,
  parent_change_id: null, successor_change_id: null, scope_version: version,
  acknowledged_scope_version: 1, assessment_count: version, revision_count: version,
  pending_revision_count: 0, created_at: now, updated_at: now
});

test("identifier-free live work updates refresh active review scopes across source projects", async (t) => {
  const client = createDashboardQueryClient();
  const stops = [];
  t.after(() => { stops.forEach((stop) => stop()); client.clear(); });
  const reads = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    reads.push(url);
    return Response.json(group(url.includes(source) ? source : destination, 2));
  });
  const observers = [source, destination].map((projectId) => {
    const options = decisionChangeOptions(projectId, identity);
    client.setQueryData(options.queryKey, group(projectId, 1));
    const observer = new QueryObserver(client, { ...options, staleTime: Infinity });
    stops.push(observer.subscribe(() => {}));
    return observer;
  });
  const workKey = [...queryKeys.work(destination), "untouched"];
  const inactive = [...knowledgeKeys.root(source), "assessment", identity];
  client.setQueryData(workKey, { version: 1 });
  client.setQueryData(inactive, { retained: true });
  await invalidateKnowledgeReads(client, null);
  assert.equal(reads.length, 2);
  assert.deepEqual(observers.map((observer) => observer.getCurrentResult().data.scope_version), [2, 2]);
  assert.equal(client.getQueryState(inactive).isInvalidated, true);
  assert.equal(client.getQueryState(workKey).isInvalidated, false);
});

test("project-specific knowledge invalidation retains unrelated project cache", async () => {
  const client = createDashboardQueryClient();
  try {
    const keys = [source, destination].map((projectId) => knowledgeKeys.domains(projectId));
    keys.forEach((key) => client.setQueryData(key, []));
    await invalidateKnowledgeReads(client, source);
    assert.equal(client.getQueryState(keys[0]).isInvalidated, true);
    assert.equal(client.getQueryState(keys[1]).isInvalidated, false);
  } finally { client.clear(); }
});
